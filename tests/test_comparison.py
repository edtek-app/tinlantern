"""Tests for the head-to-head model comparison and its decision rule.

The decision rule is tested code rather than a judgement recorded in
prose, because this one had to be corrected mid-comparison: its first
version compared recall alone and declared a tie while one model was
strictly better on recall AND precision. A tiebreak that fires when a
model dominates is not a tiebreak.
"""

import pytest

from ml.evaluation.comparison import Candidate, decide, run_comparison
from ml.evaluation.metrics import ArchetypeRecall, Report
from ml.src.model import build_gradient_boosting, build_model

pytestmark = pytest.mark.m3


def report(name: str, recall: float, precision: float, n: int = 20) -> Report:
    return Report(
        name=name,
        threshold=0.35,
        auc=0.9,
        precision=precision,
        recall=recall,
        f1=0.9,
        per_archetype_recall={"a": ArchetypeRecall("a", recall, n)},
    )


LINEAR = Candidate("linear", build_model, interpretable=True)
TREES = Candidate("trees", build_gradient_boosting, interpretable=False)


# --------------------------------------------------------------------------
# Domination beats the tiebreak
# --------------------------------------------------------------------------


def test_a_dominating_model_wins_even_if_uninterpretable() -> None:
    """The defect this rule was corrected for.

    The first version compared recall alone inside a noise band, so a
    model better on BOTH recall and precision could still lose to the
    interpretability tiebreak. On the real cohort that would have shipped
    a model missing two learners and raising five more false alarms.
    """
    outcome = decide(
        (report("linear", 0.976, 0.833), report("trees", 1.000, 0.949)),
        (LINEAR, TREES),
    )
    assert outcome.winner == "trees"
    assert not outcome.tied
    assert "dominating" in outcome.reason


def test_domination_needs_no_margin() -> None:
    """Better on both, however slightly, is still better on both."""
    outcome = decide(
        (report("linear", 0.90, 0.80), report("trees", 0.901, 0.801)),
        (LINEAR, TREES),
    )
    assert outcome.winner == "trees"


def test_the_interpretable_model_wins_when_it_dominates() -> None:
    outcome = decide(
        (report("linear", 1.0, 0.95), report("trees", 0.9, 0.80)),
        (LINEAR, TREES),
    )
    assert outcome.winner == "linear"
    assert not outcome.tied


# --------------------------------------------------------------------------
# Only a genuine trade-off is a tie
# --------------------------------------------------------------------------


def test_a_real_tradeoff_is_broken_by_interpretability() -> None:
    """Neither dominates: one better on recall, the other on precision."""
    outcome = decide(
        (report("linear", 0.90, 0.95), report("trees", 0.98, 0.80)),
        (LINEAR, TREES),
    )
    assert outcome.winner == "linear"
    assert outcome.tied
    assert "no model dominates" in outcome.reason


def test_identical_models_tie_and_interpretability_decides() -> None:
    outcome = decide(
        (report("linear", 0.9, 0.9), report("trees", 0.9, 0.9)), (LINEAR, TREES)
    )
    assert outcome.winner == "linear"
    assert outcome.tied


def test_when_no_candidate_is_interpretable_recall_breaks_the_tie() -> None:
    other = Candidate("other", build_gradient_boosting, interpretable=False)
    outcome = decide(
        (report("trees", 0.90, 0.95), report("other", 0.98, 0.80)), (TREES, other)
    )
    assert outcome.winner == "other"
    assert "none is interpretable" in outcome.reason


def test_unreportable_archetypes_are_excluded_from_the_decision() -> None:
    """A recall resting on two learners must not decide which model ships."""
    thin = Report(
        name="linear",
        threshold=0.35,
        auc=0.9,
        precision=0.9,
        recall=0.9,
        f1=0.9,
        per_archetype_recall={
            "solid": ArchetypeRecall("solid", 0.5, 20),
            "thin": ArchetypeRecall("thin", 1.0, 2),
        },
    )
    fat = Report(
        name="trees",
        threshold=0.35,
        auc=0.9,
        precision=0.9,
        recall=0.9,
        f1=0.9,
        per_archetype_recall={"solid": ArchetypeRecall("solid", 0.6, 20)},
    )
    # linear's mean would be 0.75 if the n=2 archetype counted, beating
    # trees' 0.6; excluding it, linear scores 0.5 and trees dominates.
    assert decide((thin, fat), (LINEAR, TREES)).winner == "trees"


# --------------------------------------------------------------------------
# Both models run through identical folds
# --------------------------------------------------------------------------


def test_both_models_see_the_same_folds() -> None:
    """Any difference must be the model, not the split.

    At this cohort size a different split moves the numbers more than a
    different model does, so a comparison on different folds would measure
    the wrong thing.
    """
    from tests.test_baseline import cohort

    frame, target, archetypes = cohort()
    seen: list[tuple[str, ...]] = []

    def spy_factory(inner):
        def factory():
            model = inner()
            original = model.fit

            def fit(x, y):
                seen.append(tuple(x.index))
                return original(x, y)

            model.fit = fit
            return model

        return factory

    run_comparison(
        frame,
        target,
        archetypes,
        (
            Candidate("linear", spy_factory(build_model), interpretable=True),
            Candidate("trees", spy_factory(build_gradient_boosting), False),
        ),
    )
    half = len(seen) // 2
    assert seen[:half] == seen[half:], "the two models saw different folds"


def test_the_comparison_reports_every_candidate() -> None:
    from tests.test_baseline import cohort

    frame, target, archetypes = cohort()
    outcome = run_comparison(frame, target, archetypes, (LINEAR, TREES))
    assert {r.name for r in outcome.reports} == {"linear", "trees"}
    assert outcome.winner in {"linear", "trees"}


def test_the_summary_states_the_decision_and_its_reason() -> None:
    from tests.test_baseline import cohort

    frame, target, archetypes = cohort()
    text = run_comparison(frame, target, archetypes, (LINEAR, TREES)).summary()
    assert "decision:" in text and "because" in text
