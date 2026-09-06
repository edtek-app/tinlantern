"""Tests for the evaluation report and the usability requirement.

Rendering is tested against synthetic results: the gate proves the report
says what the results say, and `make report` supplies real numbers from a
loaded warehouse. Re-deriving those numbers in CI would cost minutes per
run to verify what the provenance header already declares.
"""

import pytest

from ml.evaluation.comparison import (
    INTERIOR_BOUNDS,
    MIN_INTERIOR_FRACTION,
    Candidate,
    Comparison,
    decide,
    interior_fraction,
)
from ml.evaluation.metrics import ArchetypeRecall, Report
from ml.evaluation.report import Provenance, collect_provenance, render
from ml.evaluation.sensitivity import (
    PRODUCTION_WINDOW_WEEKS,
    WINDOW_WEEKS,
    WindowResult,
    threshold_sweep,
)
from ml.src.model import build_gradient_boosting, build_model

pytestmark = pytest.mark.m3

import pandas as pd  # noqa: E402


def report(name: str, recall: float = 0.98, precision: float = 0.9) -> Report:
    return Report(
        name=name,
        threshold=0.35,
        auc=0.99,
        precision=precision,
        recall=recall,
        f1=0.9,
        per_archetype_recall={
            "disengaging": ArchetypeRecall("disengaging", recall, 21),
            "recovering": ArchetypeRecall("recovering", 1.0, 2),
        },
    )


LINEAR = Candidate("linear", build_model, interpretable=True)
TREES = Candidate("trees", build_gradient_boosting, interpretable=False)


# --------------------------------------------------------------------------
# Score usability is a requirement, checked before metrics
# --------------------------------------------------------------------------


def test_interior_fraction_measures_unpinned_learners() -> None:
    low, high = INTERIOR_BOUNDS
    scores = pd.Series([0.0, 1.0, 0.5, 0.5])
    assert interior_fraction(scores) == 0.5
    assert interior_fraction(pd.Series([low, high])) == 0.0


def test_a_model_that_pins_every_learner_is_disqualified() -> None:
    """The real case: 117 of 120 learners at exactly 0 or 1.

    M5 needs a risk distribution. A histogram of two bars is not one, so
    this fails a stated criterion rather than scoring lower.
    """
    outcome = decide(
        (report("linear", 0.90, 0.80), report("trees", 1.0, 0.99)),
        (LINEAR, TREES),
        usability={"linear": 0.38, "trees": 0.025},
    )
    assert outcome.winner == "linear"
    assert "trees" in outcome.disqualified
    assert "distribution" in outcome.disqualified["trees"]


def test_a_disqualified_model_loses_despite_dominating() -> None:
    """Requirements are not outscored — that is what makes them requirements."""
    outcome = decide(
        (report("linear", 0.90, 0.80), report("trees", 1.0, 0.99)),
        (LINEAR, TREES),
        usability={"linear": 0.40, "trees": 0.01},
    )
    assert outcome.winner == "linear"


def test_a_model_above_the_floor_stays_eligible() -> None:
    outcome = decide(
        (report("linear", 0.90, 0.80), report("trees", 0.98, 0.95)),
        (LINEAR, TREES),
        usability={"linear": 0.38, "trees": MIN_INTERIOR_FRACTION},
    )
    assert outcome.winner == "trees"
    assert not outcome.disqualified


def test_disqualifying_everything_falls_back_rather_than_crashing() -> None:
    outcome = decide(
        (report("linear"), report("trees")),
        (LINEAR, TREES),
        usability={"linear": 0.0, "trees": 0.0},
    )
    assert outcome.winner in {"linear", "trees"}


# --------------------------------------------------------------------------
# The report says what the results say
# --------------------------------------------------------------------------


def synthetic_report() -> str:
    comparison = Comparison(
        reports=(report("linear", 0.95, 0.83), report("trees", 0.95, 0.92)),
        winner="trees",
        reason="dominating",
        tied=False,
        usability={"linear": 0.383, "trees": 0.633},
        disqualified={},
    )
    windows = tuple(
        WindowResult(
            weeks=weeks,
            reports=(report("trees", 0.9, 0.9),),
            is_production=weeks == PRODUCTION_WINDOW_WEEKS,
        )
        for weeks in WINDOW_WEEKS
    )
    sweep = threshold_sweep(
        pd.Series([1, 0, 1]), pd.Series([0.9, 0.1, 0.4]), (0.2, 0.35, 0.6)
    )
    return render(
        comparison,
        windows,
        sweep,
        Provenance("2026-09-06T00:00:00+00:00", "abc123", False, 20260301, 192431, 120),
    )


def test_the_provenance_header_is_present_and_populated() -> None:
    """A stale report must declare its own staleness."""
    text = synthetic_report()
    assert "**Provenance.**" in text
    for token in ("2026-09-06", "abc123", "20260301", "192,431", "120 learners"):
        assert token in text


def test_provenance_records_a_dirty_tree() -> None:
    dirty = Provenance("t", "abc", True, 1, 2, 3).render()
    assert "working tree dirty" in dirty
    assert "working tree dirty" not in Provenance("t", "abc", False, 1, 2, 3).render()


def test_provenance_is_collected_without_crashing_outside_git() -> None:
    captured = collect_provenance(1, 2, 3)
    assert captured.cohort_seed == 1 and captured.generated_at


def test_the_separability_warning_opens_the_report() -> None:
    """Owner ruling: prominent, not buried in limitations."""
    text = synthetic_report()
    assert text.index("more separable than reality") < text.index("Recall by archetype")
    assert "upper bound" in text


def test_the_decision_reversal_is_reported() -> None:
    """A reader cannot otherwise verify the comparison was run honestly."""
    text = synthetic_report()
    for phrase in (
        "first decision rule chose logistic regression",
        "reversed",
        "blind to precision",
    ):
        assert phrase.lower() in text.lower()


def test_an_unreportable_archetype_is_never_quoted() -> None:
    text = synthetic_report()
    line = next(row for row in text.splitlines() if row.startswith("| recovering"))
    assert "unreportable" in line and "100.0%" not in line


def test_the_auc_caveat_is_attached_wherever_auc_appears() -> None:
    text = synthetic_report()
    assert "secondary" in text
    assert text.count("flatters") >= 2


def test_the_window_curve_looks_in_both_directions() -> None:
    """A curve that only looks toward more data argues for waiting."""
    assert min(WINDOW_WEEKS) < PRODUCTION_WINDOW_WEEKS < max(WINDOW_WEEKS)
    text = synthetic_report()
    assert "not a search" in text
    assert "(production)" in text


def test_the_threshold_section_refuses_to_maximise() -> None:
    text = synthetic_report()
    assert "not a curve to maximise" in text
    assert text.index("operational reasoning comes first") < text.index("| threshold |")


def test_threshold_sweep_counts_misses_and_false_alarms() -> None:
    truth = pd.Series([1, 1, 0])
    scores = pd.Series([0.9, 0.2, 0.8])
    rows = {t: (m, f) for t, _, _, m, f in threshold_sweep(truth, scores, (0.5,))}
    assert rows[0.5] == (1, 1)
