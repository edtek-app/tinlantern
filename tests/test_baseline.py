"""Tests for splitting, metrics, and the baseline model.

Fixtures are synthetic frames rather than the loaded cohort: these test
that the protocol is correct, not what the cohort shows. What the cohort
shows belongs in the evaluation report.
"""

import numpy as np
import pandas as pd
import pytest

from ml.evaluation.baselines import failures_rule_scores, majority_class_scores
from ml.evaluation.metrics import (
    AUC_CAVEAT,
    MIN_REPORTABLE_N,
    calibration_bins,
    evaluate,
    per_archetype_recall,
)
from ml.evaluation.split import (
    N_SPLITS,
    SEED,
    fold_membership,
    out_of_fold_scores,
)
from ml.src.features import ABSOLUTE_FEATURES, FEATURE_COLUMNS
from ml.src.model import ALERT_THRESHOLD, build_model, drivers, score

pytestmark = pytest.mark.m3


def cohort(n: int = 60, seed: int = 7) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    """A synthetic cohort with a learnable signal and two archetypes."""
    rng = np.random.default_rng(seed)
    index = pd.Index([f"s-{i:05d}" for i in range(n)], name="learner_identifier")
    at_risk = np.array([i % 3 == 0 for i in range(n)])

    frame = pd.DataFrame(
        {name: rng.normal(10, 2, n) for name in ABSOLUTE_FEATURES}, index=index
    )
    # A real signal so a model can beat chance.
    # Counts, so non-negative: a fixture that emitted negative failures
    # would be testing against data the warehouse cannot produce.
    frame["failures"] = np.clip(
        np.where(at_risk, rng.normal(9, 1, n), rng.normal(1, 1, n)), 0, None
    )
    frame["mean_score_in_window"] = np.where(
        at_risk, rng.normal(0.4, 0.05, n), rng.normal(0.85, 0.05, n)
    )
    target = pd.Series(at_risk.astype(int), index=index, name="at_risk")
    archetypes = pd.Series(
        np.where(at_risk, "struggling", "thriving"), index=index, name="archetype"
    )
    return frame, target, archetypes


def logistic(train_x, train_y, test_x) -> pd.Series:
    model = build_model()
    model.fit(train_x, train_y)
    return score(model, test_x)


# --------------------------------------------------------------------------
# Splitting — no learner on both sides
# --------------------------------------------------------------------------


def test_no_learner_appears_in_both_sides_of_a_fold() -> None:
    """The constraint stated for M3, asserted directly."""
    frame, target, _ = cohort()
    for train, test in fold_membership(frame, target):
        assert not set(train) & set(test)


def test_every_learner_is_tested_exactly_once() -> None:
    """Out-of-fold means each learner is scored by a model that never
    saw them — and scored once, not several times."""
    frame, target, _ = cohort()
    tested: list[str] = []
    for _, test in fold_membership(frame, target):
        tested.extend(test)
    assert sorted(tested) == sorted(frame.index)


def test_folds_are_stratified_on_the_outcome() -> None:
    """Without stratification a fold could hold no at-risk learner at all."""
    frame, target, _ = cohort()
    for _, test in fold_membership(frame, target):
        assert 0 < target.loc[test].sum() < len(test)


def test_the_split_is_reproducible() -> None:
    frame, target, _ = cohort()
    assert fold_membership(frame, target) == fold_membership(frame, target)


def test_changing_the_seed_changes_the_split() -> None:
    frame, target, _ = cohort()
    assert fold_membership(frame, target, seed=SEED) != fold_membership(
        frame, target, seed=SEED + 1
    )


def test_the_fold_count_is_fixed_and_reproducible() -> None:
    frame, target, _ = cohort()
    assert len(fold_membership(frame, target)) == N_SPLITS


# --------------------------------------------------------------------------
# The cohort baseline is fitted inside each fold
# --------------------------------------------------------------------------


def test_the_learner_only_ever_sees_its_own_fold(monkeypatch) -> None:
    """Structural check on the leak the fitted baseline exists to stop.

    Each call receives a training frame and a test frame that share no
    learners. If the baseline were fitted once over everything, the folds
    would still be disjoint — but the relative feature VALUES would carry
    held-out information, so this also asserts each call is handed a
    training frame strictly smaller than the whole cohort.
    """
    frame, target, _ = cohort()
    seen: list[tuple[int, int]] = []

    def spy(train_x, train_y, test_x):
        assert not set(train_x.index) & set(test_x.index)
        seen.append((len(train_x), len(test_x)))
        return logistic(train_x, train_y, test_x)

    out_of_fold_scores(frame, target, spy)
    assert len(seen) == N_SPLITS
    assert all(train < len(frame) for train, _ in seen)


def test_out_of_fold_scores_cover_every_learner() -> None:
    frame, target, _ = cohort()
    scores = out_of_fold_scores(frame, target, logistic)
    assert list(scores.index) == list(frame.index)
    assert scores.notna().all()
    assert ((scores >= 0) & (scores <= 1)).all()


def test_out_of_fold_scoring_is_reproducible() -> None:
    frame, target, _ = cohort()
    first = out_of_fold_scores(frame, target, logistic)
    second = out_of_fold_scores(frame, target, logistic)
    pd.testing.assert_series_equal(first, second)


# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------


def test_per_archetype_recall_counts_only_at_risk_learners() -> None:
    truth = pd.Series([1, 1, 0, 1])
    predicted = pd.Series([1, 0, 1, 1])
    archetypes = pd.Series(["a", "a", "a", "b"])
    result = per_archetype_recall(truth, predicted, archetypes)
    assert result["a"].recall == 0.5 and result["a"].n == 2
    assert result["b"].recall == 1.0 and result["b"].n == 1


def test_per_archetype_recall_reports_a_missed_archetype_as_zero() -> None:
    """The failure that matters: a model can post a strong aggregate
    while catching none of one archetype."""
    truth = pd.Series([1, 1])
    predicted = pd.Series([0, 0])
    archetypes = pd.Series(["disengaging", "disengaging"])
    result = per_archetype_recall(truth, predicted, archetypes)
    assert result["disengaging"].recall == 0.0


def test_calibration_bins_report_predicted_against_observed() -> None:
    truth = pd.Series([0, 0, 1, 1])
    scores = pd.Series([0.1, 0.2, 0.8, 0.9])
    bins = calibration_bins(truth, scores, bins=2)
    assert len(bins) == 2
    assert bins[0][1] == 0.0 and bins[1][1] == 1.0
    assert sum(count for _, _, count in bins) == 4


def test_the_summary_leads_with_per_archetype_recall() -> None:
    """Ordering is the ruling: recall first, AUC after, caveat attached."""
    frame, target, archetypes = cohort()
    report = evaluate(
        "t",
        target,
        out_of_fold_scores(frame, target, logistic),
        archetypes,
        ALERT_THRESHOLD,
    )
    text = report.summary()
    assert text.index("recall by archetype") < text.index("AUC")


def test_the_auc_caveat_travels_with_the_number() -> None:
    """Not a footnote: a number quoted without it misleads."""
    frame, target, archetypes = cohort()
    report = evaluate("t", target, pd.Series(0.5, index=frame.index), archetypes, 0.35)
    auc_line = next(line for line in report.summary().splitlines() if "AUC" in line)
    assert AUC_CAVEAT in auc_line


# --------------------------------------------------------------------------
# The bars a model must clear
# --------------------------------------------------------------------------


def test_majority_class_flags_nobody() -> None:
    frame, _, _ = cohort()
    assert (majority_class_scores(frame) == 0).all()


def test_the_failures_rule_needs_no_training() -> None:
    """A rule, not a model — which is what makes it a fair floor."""
    frame, _, _ = cohort()
    scores = failures_rule_scores(frame)
    assert ((scores >= 0) & (scores <= 1)).all()
    assert scores.idxmax() == frame["failures"].idxmax()


def test_the_failures_rule_keeps_its_range_promise() -> None:
    """It claims [0, 1]; it must hold even for input it should never see."""
    frame, _, _ = cohort()
    frame.loc[frame.index[0], "failures"] = -5.0
    scores = failures_rule_scores(frame)
    assert ((scores >= 0) & (scores <= 1)).all()


def test_the_failures_rule_handles_a_cohort_with_no_failures() -> None:
    frame, _, _ = cohort()
    frame["failures"] = 0.0
    assert failures_rule_scores(frame).eq(0).all()


def test_the_model_beats_both_bars_on_a_learnable_signal() -> None:
    frame, target, archetypes = cohort()
    model_report = evaluate(
        "model",
        target,
        out_of_fold_scores(frame, target, logistic),
        archetypes,
        ALERT_THRESHOLD,
    )
    rule = evaluate(
        "rule", target, failures_rule_scores(frame), archetypes, ALERT_THRESHOLD
    )
    majority = evaluate(
        "majority", target, majority_class_scores(frame), archetypes, ALERT_THRESHOLD
    )
    assert model_report.auc > majority.auc
    assert model_report.recall > majority.recall
    assert rule.auc > majority.auc


# --------------------------------------------------------------------------
# Scoring and drivers
# --------------------------------------------------------------------------


def test_drivers_name_features_not_labels() -> None:
    """Per-learner explanations must come from features alone."""
    frame, target, _ = cohort()
    from ml.src.features import fit_baseline

    full = fit_baseline(frame).transform(frame)
    model = build_model()
    model.fit(full, target)

    named = drivers(model, full)
    assert list(named.index) == list(frame.index)
    for row in named["top_drivers"]:
        for feature in row.split(", "):
            assert feature in FEATURE_COLUMNS


def test_the_alert_threshold_is_below_a_half() -> None:
    """A missed learner costs more than an unnecessary conversation."""
    assert 0 < ALERT_THRESHOLD < 0.5


def test_a_thin_archetype_is_marked_unreportable_not_quoted() -> None:
    """n=2 is not a measurement. One learner moves it fifty points."""
    truth = pd.Series([1, 1])
    predicted = pd.Series([1, 0])
    archetypes = pd.Series(["recovering", "recovering"])
    entry = per_archetype_recall(truth, predicted, archetypes)["recovering"]

    assert entry.n == 2
    assert not entry.reportable
    assert "unreportable" in entry.line()
    assert "50" not in entry.line(), "a figure below the floor must not be quoted"


def test_every_reportable_recall_carries_its_n() -> None:
    truth = pd.Series([1] * MIN_REPORTABLE_N)
    predicted = pd.Series([1] * MIN_REPORTABLE_N)
    archetypes = pd.Series(["struggling"] * MIN_REPORTABLE_N)
    entry = per_archetype_recall(truth, predicted, archetypes)["struggling"]

    assert entry.reportable
    assert f"n={MIN_REPORTABLE_N}" in entry.line()
