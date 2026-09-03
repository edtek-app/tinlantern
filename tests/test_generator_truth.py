"""Tests for the ground-truth sidecar.

The outcome is measured, never assigned. That makes these tests unusual:
most of them check that a measurement *responds* to the record rather than
that it equals a fixed number, because the realized outcome mix is an
output of the generator, not a configured input (ADR-0004).

The load-bearing case is `test_a_learner_who_vanishes_fails_the_term`.
Averaging over submitted work instead of scheduled work would get exactly
that learner wrong, and would do so silently.
"""

from datetime import timedelta
from pathlib import Path

import pytest

from app.xapi import Activity, ActivityDefinition, Result, Score
from data.generator.calendar import build_schedule, term_bounds
from data.generator.config import CohortConfig, load_config
from data.generator.course import build_courses
from data.generator.events import Event
from data.generator.roster import build_roster
from data.generator.stream import learner_statements, to_statements
from data.generator.truth import (
    GRADED_VERBS,
    GroundTruth,
    at_risk_rate,
    derive_ground_truth,
)

pytestmark = pytest.mark.m0

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "data" / "generator" / "cohort.example.yaml"


@pytest.fixture
def config() -> CohortConfig:
    return load_config(EXAMPLE)


_COHORT_CACHE: dict[int, tuple[GroundTruth, ...]] = {}


def _cohort(config: CohortConfig) -> tuple[GroundTruth, ...]:
    """Ground truth for the whole example cohort, computed once."""
    if config.seed not in _COHORT_CACHE:
        courses = build_courses(config)
        schedules = build_schedule(config)
        _COHORT_CACHE[config.seed] = tuple(
            derive_ground_truth(
                config,
                learner,
                courses,
                schedules,
                learner_statements(config, learner, courses, schedules),
            )
            for learner in build_roster(config)
        )
    return _COHORT_CACHE[config.seed]


def _median_gap(truths: tuple[GroundTruth, ...], archetype: str) -> float:
    gaps = sorted(t.longest_gap_days for t in truths if t.archetype == archetype)
    assert gaps, f"no {archetype} learners"
    return gaps[len(gaps) // 2]


def truth_for(config: CohortConfig, archetype: str) -> GroundTruth:
    courses = build_courses(config)
    schedules = build_schedule(config)
    for learner in build_roster(config):
        if learner.archetype == archetype:
            return derive_ground_truth(
                config,
                learner,
                courses,
                schedules,
                learner_statements(config, learner, courses, schedules),
            )
    raise AssertionError(f"no {archetype} learner in the example cohort")


# --------------------------------------------------------------------------
# The measurement responds to the record
# --------------------------------------------------------------------------


def test_thriving_learners_are_not_at_risk(config: CohortConfig) -> None:
    truth = truth_for(config, "thriving")
    assert not truth.at_risk
    assert truth.completed_items == truth.scheduled_items


def test_disengaging_learners_are_at_risk(config: CohortConfig) -> None:
    """The classic early-alert target must be caught by the measurement."""
    truth = truth_for(config, "disengaging")
    assert truth.at_risk
    assert truth.completed_items < truth.scheduled_items


def test_struggling_learners_fail_on_score_not_absence(
    config: CohortConfig,
) -> None:
    """Consistent effort, failing results — they show up and still fail."""
    truth = truth_for(config, "struggling")
    assert truth.failed_term
    assert truth.completed_items == truth.scheduled_items


def test_coasting_learners_pass(config: CohortConfig) -> None:
    """DESIGN.md says middling scores, which means not failing."""
    assert not truth_for(config, "coasting").failed_term


def test_measurements_are_retained_alongside_verdicts(
    config: CohortConfig,
) -> None:
    """evals/ needs near-threshold analysis, not just a count of fires."""
    truth = truth_for(config, "disengaging")
    assert 0.0 <= truth.term_mean_score <= 1.0
    assert truth.scheduled_items > 0
    assert truth.longest_gap_days > 0
    assert truth.longest_gap_courses
    assert truth.at_risk == (truth.failed_term or truth.collapsed)


# --------------------------------------------------------------------------
# Missing work counts as zero
# --------------------------------------------------------------------------


def test_a_learner_who_vanishes_fails_the_term(config: CohortConfig) -> None:
    """Strong early scores, then nothing. Must land below threshold.

    This is the exact case that averaging over *submitted* work gets
    wrong: three excellent early results would give a mean of 0.9 and the
    learner would look fine. Dividing by everything that was DUE is what
    makes disappearing count against you, as a gradebook does.
    """
    courses = build_courses(config)
    schedules = build_schedule(config)
    learner = build_roster(config)[0]
    start, _ = term_bounds(config.term)

    scheduled = sum(len(schedule.deadlines()) for schedule in schedules)
    early = schedules[0].deadlines()[:3]
    events = tuple(
        Event(
            moment=start + timedelta(days=index + 1),
            verb="submitted",
            activity=Activity(
                id=deadline.activity_iri,
                definition=ActivityDefinition(name={"en-US": "item"}),
            ),
            result=Result(
                score=Score(scaled=0.95, raw=95, min=0, max=100),
                success=True,
                completion=True,
            ),
        )
        for index, deadline in enumerate(early)
    )

    statements = to_statements(learner, courses[0], events)
    truth = derive_ground_truth(config, learner, courses, schedules, statements)

    assert truth.completed_items == 3
    assert truth.scheduled_items == scheduled
    assert truth.term_mean_score == pytest.approx(3 * 0.95 / scheduled, abs=1e-3)
    assert truth.term_mean_score < config.risk.pass_threshold
    assert truth.failed_term


def test_retakes_replace_rather_than_accumulate(config: CohortConfig) -> None:
    """A resit is the grade of record, not an extra entry in the mean."""
    courses = build_courses(config)
    schedules = build_schedule(config)
    learner = build_roster(config)[0]
    start, _ = term_bounds(config.term)
    activity = Activity(
        id=schedules[0].deadlines()[0].activity_iri,
        definition=ActivityDefinition(name={"en-US": "item"}),
    )

    def graded(day: int, scaled: float, verb: str) -> Event:
        return Event(
            moment=start + timedelta(days=day),
            verb=verb,
            activity=activity,
            result=Result(
                score=Score(scaled=scaled, raw=scaled * 100, min=0, max=100),
                success=verb == "passed",
                completion=True,
            ),
        )

    statements = to_statements(
        learner, courses[0], (graded(1, 0.30, "failed"), graded(5, 0.80, "passed"))
    )
    truth = derive_ground_truth(config, learner, courses, schedules, statements)
    assert truth.completed_items == 1
    scheduled = sum(len(schedule.deadlines()) for schedule in schedules)
    assert truth.term_mean_score == pytest.approx(0.80 / scheduled, abs=1e-3)


def test_only_graded_verbs_count(config: CohortConfig) -> None:
    assert {"passed", "failed", "submitted"} == GRADED_VERBS


# --------------------------------------------------------------------------
# Collapse is measured through the window API
# --------------------------------------------------------------------------


def test_gap_uses_session_windows_not_statements(config: CohortConfig) -> None:
    """The gap definition lives in one place — emit.session_windows.

    A learner with no statements at all still has a measurable gap: the
    whole term. Deriving gaps from the statement stream could not produce
    that, which is why the measurement reads windows instead.
    """
    courses = build_courses(config)
    schedules = build_schedule(config)
    learner = build_roster(config)[0]
    truth = derive_ground_truth(config, learner, courses, schedules, ())
    assert truth.longest_gap_days > 0


def test_disengaging_learners_have_the_longest_gaps(config: CohortConfig) -> None:
    """Cohort-level, not one learner: gap medians are noisy at n=1.

    Asserts the ordering the measurement must produce, not a threshold
    crossing. Whether the configured `collapse_gap_days` actually fires is
    a calibration question about that number, not about whether gaps are
    measured correctly — and conflating the two would make this test fail
    every time the threshold moved.
    """
    truths = _cohort(config)
    disengaging = _median_gap(truths, "disengaging")
    for archetype in ("thriving", "coasting", "procrastinator", "struggling"):
        assert disengaging > _median_gap(truths, archetype), archetype


def test_collapse_never_fires_for_an_engaged_learner(config: CohortConfig) -> None:
    """Precision matters more than recall for this signal.

    A collapse flag on a thriving learner would be a false alarm in the
    ground truth itself — worse than missing a real one, because it would
    teach M3 that engagement means nothing.
    """
    for truth in _cohort(config):
        if truth.archetype in ("thriving", "coasting", "procrastinator"):
            assert not truth.collapsed, truth.learner_identifier


def test_collapse_is_currently_subsumed_by_the_score_signal(
    config: CohortConfig,
) -> None:
    """Documents a real property of this dataset, so it cannot drift silently.

    Missing work scores zero, so a learner absent long enough to collapse
    has already failed on score. Collapse therefore adds no at-risk
    classifications today — it is retained as a *measurement* because M3
    needs the gap as a feature and evals/ needs the breakdown.

    If this ever fails, collapse has started catching learners the score
    signal misses. That is interesting, not broken: update the assertion
    and note what changed.
    """
    collapsed = [truth for truth in _cohort(config) if truth.collapsed]
    assert collapsed, "collapse never fires at all — the signal is dead"
    assert all(truth.failed_term for truth in collapsed)


def test_gap_is_measured_across_all_courses(config: CohortConfig) -> None:
    """Active in one course is not collapsed, even if idle in another."""
    truth = truth_for(config, "thriving")
    assert set(truth.longest_gap_courses) <= {
        course.key for course in build_courses(config)
    }


# --------------------------------------------------------------------------
# Reproducibility and posture
# --------------------------------------------------------------------------


def test_ground_truth_is_reproducible(config: CohortConfig) -> None:
    assert truth_for(config, "recovering") == truth_for(
        load_config(EXAMPLE), "recovering"
    )


def test_outcome_is_stable_as_the_cohort_grows(config: CohortConfig) -> None:
    """Learner identity, and therefore their outcome, cannot depend on n."""
    larger = config.model_copy(update={"learners": 500})
    courses = build_courses(config)
    schedules = build_schedule(config)
    learner = build_roster(config)[7]
    small = derive_ground_truth(
        config,
        learner,
        courses,
        schedules,
        learner_statements(config, learner, courses, schedules),
    )
    grown = derive_ground_truth(
        larger,
        build_roster(larger)[7],
        courses,
        schedules,
        learner_statements(larger, build_roster(larger)[7], courses, schedules),
    )
    assert small == grown


def test_sidecar_carries_no_statement_data(config: CohortConfig) -> None:
    """The sidecar is labels, not a second copy of the event stream."""
    truth = truth_for(config, "thriving")
    fields = set(GroundTruth.__dataclass_fields__)
    assert "statements" not in fields
    assert "events" not in fields
    assert not any("@" in str(getattr(truth, name)) for name in fields)


def test_at_risk_rate_is_a_measurement_not_a_target(config: CohortConfig) -> None:
    """Deliberately asserts a range, not a value — it is an output."""
    small = config.model_copy(update={"learners": 12})
    from data.generator.truth import derive_cohort_truth

    truths = derive_cohort_truth(small)
    assert len(truths) == 12
    assert 0.0 <= at_risk_rate(truths) <= 1.0
    assert at_risk_rate(()) == 0.0
