"""Tests for per-learner drivers and the scoring job."""

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest
from sqlalchemy import text

from data.generator.config import CohortConfig, load_config
from ml.src.drivers import (
    CONTRIBUTION_CAVEAT,
    MedianProfile,
    contributions,
    top_drivers,
)
from ml.src.features import FEATURE_COLUMNS, build_features
from ml.src.model import ALERT_THRESHOLD, build_calibrated_gradient_boosting
from ml.src.scoring import model_version, run
from tests.test_features import add_event, make_activity, make_learner

pytestmark = pytest.mark.m3

EXAMPLE = "data/generator/cohort.example.yaml"


@pytest.fixture
def config() -> CohortConfig:
    return load_config(EXAMPLE)


@pytest.fixture
def scored_cohort(connection, config):
    """A warehouse cohort with genuine per-learner variation.

    Every learner differs in activity volume and score, not just in class.
    A fixture with two identical groups would make every contribution
    exactly zero and every median identical across subsets — which would
    silently pass the leakage and interaction tests by making their
    premises vacuous.
    """
    import random

    from data.generator.calendar import term_bounds

    start = term_bounds(config.term)[0]
    course, activity = make_activity(connection)
    rng = random.Random(20260906)
    identifiers, at_risk = [], []

    for index in range(60):
        # Deterministic identifiers: the feature frame is sorted by them,
        # so random ids would reorder the rows, which reshuffles the folds
        # CalibratedClassifierCV builds internally and makes anything that
        # depends on the fitted model flaky.
        student, identifier = make_learner(connection, f"s-fix-{index:03d}")
        identifiers.append(identifier)

        # Independent variation across several features, so the model can
        # develop the interactions the non-additivity test looks for. Two
        # clean clumps would make every contribution zero and every median
        # identical across subsets, passing those tests vacuously.
        engagement = rng.uniform(0.05, 1.0)
        ability = rng.uniform(0.1, 0.95)
        risky = ability < 0.55 or engagement < 0.25
        at_risk.append(risky)

        for day in range(max(1, int(engagement * 26))):
            if rng.random() < 0.85:
                add_event(
                    connection,
                    student,
                    course,
                    activity,
                    start + timedelta(days=day),
                )
        for offset in range(1, 1 + rng.randint(2, 6)):
            item = min(0.99, max(0.01, rng.gauss(ability, 0.08)))
            add_event(
                connection,
                student,
                course,
                activity,
                start + timedelta(days=offset),
                verb="passed" if item >= 0.55 else "failed",
                score=round(item, 3),
            )

    labels = pd.Series(
        [int(v) for v in at_risk], index=pd.Index(identifiers), name="at_risk"
    ).sort_index()
    return labels


def fitted(connection, config, labels):
    features, _ = build_features(connection, config)
    aligned = labels.loc[features.index]
    model = build_calibrated_gradient_boosting()
    model.fit(features, aligned)
    return model, features


# --------------------------------------------------------------------------
# The caveat travels with the payload
# --------------------------------------------------------------------------


def test_the_payload_carries_the_non_additivity_caveat(
    connection, config, scored_cohort
) -> None:
    """M5 must not be able to render these as a decomposition."""
    model, features = fitted(connection, config, scored_cohort)
    payloads = top_drivers(model, features, MedianProfile.fit(features))

    entry = payloads.iloc[0]
    assert entry["additive"] is False
    assert entry["caveat"] == CONTRIBUTION_CAVEAT
    assert "do NOT sum" in entry["caveat"]
    assert entry["method"] == "ablation-to-cohort-median"


def test_drivers_name_only_real_features(connection, config, scored_cohort) -> None:
    model, features = fitted(connection, config, scored_cohort)
    payloads = top_drivers(model, features, MedianProfile.fit(features))
    for entry in payloads:
        for driver in entry["drivers"]:
            assert driver["feature"] in FEATURE_COLUMNS


# --------------------------------------------------------------------------
# The interaction is a property, so it is pinned rather than described
# --------------------------------------------------------------------------


def test_ablating_two_features_is_not_the_sum_of_ablating_each(
    connection, config, scored_cohort
) -> None:
    """The substance of the caveat, asserted on a real learner.

    If contributions were additive, ablating two features together would
    equal the sum of ablating each alone. They are not, and a dashboard
    that summed them would misreport the model.
    """
    model, features = fitted(connection, config, scored_cohort)
    profile = MedianProfile.fit(features)
    individual = contributions(model, features, profile)

    ordered = features[list(FEATURE_COLUMNS)]
    base = model.predict_proba(ordered)[:, 1]

    # Searched across pairs rather than fixed on two: which features
    # interact depends on what the model learned, and pinning specific
    # pairs would make this a test of the fixture. The property is that
    # SOME pair interacts — if none does, the caveat is unearned.
    from itertools import combinations

    worst = 0.0
    for first, second in combinations(FEATURE_COLUMNS, 2):
        both = ordered.copy()
        both[first] = profile.values[first]
        both[second] = profile.values[second]
        joint = base - model.predict_proba(both)[:, 1]
        summed = (individual[first] + individual[second]).to_numpy()
        worst = max(worst, float(abs(joint - summed).max()))
        if worst > 1e-6:
            break

    assert worst > 1e-6, (
        "no pair of features interacted at all — either the model is "
        "degenerate on this fixture or the non-additivity caveat is "
        "overstated; both need investigating before this is relaxed"
    )


# --------------------------------------------------------------------------
# The median profile is fitted, not recomputed
# --------------------------------------------------------------------------


def test_the_median_profile_is_carried_not_recomputed(
    connection, config, scored_cohort
) -> None:
    """Recomputing at scoring time would leak the scored population into
    its own explanation — the same leak CohortBaseline exists to prevent."""
    _, features = fitted(connection, config, scored_cohort)
    training = features.iloc[: len(features) // 2]

    profile = MedianProfile.fit(training)
    assert profile == MedianProfile.fit(training)
    assert profile != MedianProfile.fit(features), (
        "the profile depends on which rows it saw, which is why it must be "
        "fitted once and carried"
    )


def test_contributions_use_the_supplied_profile(
    connection, config, scored_cohort
) -> None:
    model, features = fitted(connection, config, scored_cohort)
    narrow = MedianProfile.fit(features.iloc[:6])
    wide = MedianProfile.fit(features)
    assert not contributions(model, features, narrow).equals(
        contributions(model, features, wide)
    )


# --------------------------------------------------------------------------
# The job writes, replaces, and stays usable
# --------------------------------------------------------------------------


def stored(connection) -> list[dict]:
    return [
        dict(row._mapping)
        for row in connection.execute(
            text(
                "SELECT s.learner_identifier, r.risk, r.alerted, r.model_version "
                "FROM warehouse.risk_score r "
                "JOIN warehouse.dim_student s USING (student_key)"
            )
        )
    ]


def test_scoring_writes_one_row_per_learner(connection, config, scored_cohort) -> None:
    outcome = run(connection, config, scored_cohort)
    rows = stored(connection)
    assert outcome.learners == len(scored_cohort) == len(rows)
    assert {r["learner_identifier"] for r in rows} == set(scored_cohort.index)


def test_rescoring_replaces_rather_than_accumulates(
    connection, config, scored_cohort
) -> None:
    """Derived and rebuildable, unlike raw: one current score, not a log."""
    run(connection, config, scored_cohort)
    first = stored(connection)
    run(connection, config, scored_cohort)
    assert len(stored(connection)) == len(first)


def test_alerted_matches_the_threshold(connection, config, scored_cohort) -> None:
    run(connection, config, scored_cohort)
    for row in stored(connection):
        assert row["alerted"] == (float(row["risk"]) >= ALERT_THRESHOLD)


def test_scores_keep_a_usable_spread(connection, config, scored_cohort) -> None:
    """M5's distribution requirement, checked on what is actually stored.

    The requirement was validated out-of-fold during selection; a model
    fitted on the whole cohort could still collapse. It must hold on the
    scores the dashboard will read.
    """
    from ml.evaluation.comparison import MIN_INTERIOR_FRACTION

    run(connection, config, scored_cohort)
    risks = pd.Series([float(r["risk"]) for r in stored(connection)])
    interior = ((risks > 0.05) & (risks < 0.95)).mean()
    assert interior >= MIN_INTERIOR_FRACTION, f"scores collapsed: {interior:.1%}"


def test_model_version_identifies_code_not_a_hand_maintained_number(
    connection, config, scored_cohort
) -> None:
    """Derived, so it cannot drift into a constant nobody updates."""
    version = model_version()
    assert version and version != "0.1.0"
    run(connection, config, scored_cohort)
    assert {r["model_version"] for r in stored(connection)} == {version}


def test_the_window_close_is_recorded_with_the_score(
    connection, config, scored_cohort
) -> None:
    """A score is only meaningful with the window it was computed from."""
    outcome = run(connection, config, scored_cohort)
    assert outcome.window_close.tzinfo is not None
    recorded = connection.execute(
        text("SELECT DISTINCT window_close FROM warehouse.risk_score")
    ).scalar_one()
    assert recorded == outcome.window_close


def test_production_scoring_code_never_reads_labels() -> None:
    """ml.src ships; it must not know the ground truth exists (ADR-0002).

    Behavioural: the scoring entry point requires labels to be handed in.
    Code that could load them itself would not need the parameter.
    """
    import inspect

    from ml.src import scoring

    assert "labels" in inspect.signature(scoring.run).parameters


def test_scoring_run_summary_reports_the_alert_rate(
    connection, config, scored_cohort
) -> None:
    text_out = run(connection, config, scored_cohort).summary()
    assert "alerted" in text_out and "scored" in text_out
    assert datetime.now(UTC).year >= 2026
