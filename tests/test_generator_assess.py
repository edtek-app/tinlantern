"""Tests for assessment and submission emission.

The archetype weights that only existed as numbers until now —
`deadline_affinity`, `attempt_persistence`, the score trend — have to show
up as observable differences between learners here, or they are decoration.
Each gets a test that compares archetypes against each other rather than
against a hardcoded expectation.
"""

from datetime import timedelta
from pathlib import Path

import pytest

from app.xapi import VERB_IRIS, Statement
from data.generator.assess import ASSESSMENT_VERBS, MAX_ATTEMPTS, assessment_events
from data.generator.calendar import build_schedule, term_bounds
from data.generator.config import CohortConfig, load_config
from data.generator.course import build_courses
from data.generator.emit import SESSION_VERBS
from data.generator.roster import build_roster
from data.generator.stream import learner_course_statements, to_statements

pytestmark = pytest.mark.m0

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "data" / "generator" / "cohort.example.yaml"


@pytest.fixture
def config() -> CohortConfig:
    return load_config(EXAMPLE)


def learner_of(config: CohortConfig, archetype: str):
    for learner in build_roster(config):
        if learner.archetype == archetype:
            return learner
    raise AssertionError(f"no {archetype} learner in the example cohort")


def events_for(config: CohortConfig, archetype: str):
    course = build_courses(config)[0]
    schedule = build_schedule(config)[0]
    return assessment_events(config, learner_of(config, archetype), course, schedule)


def statements_for(config: CohortConfig, archetype: str) -> tuple[Statement, ...]:
    course = build_courses(config)[0]
    schedule = build_schedule(config)[0]
    return learner_course_statements(
        config, learner_of(config, archetype), course, schedule
    )


def results(config: CohortConfig, archetype: str, verb: str) -> list[float]:
    """Scaled scores from every statement carrying a given verb."""
    return [
        statement.result.score.scaled
        for statement in statements_for(config, archetype)
        if statement.verb.id == VERB_IRIS[verb]
        and statement.result is not None
        and statement.result.score is not None
        and statement.result.score.scaled is not None
    ]


# --------------------------------------------------------------------------
# The contract and the verb set
# --------------------------------------------------------------------------


def test_every_statement_survives_the_xapi_contract(config: CohortConfig) -> None:
    for statement in statements_for(config, "struggling"):
        wire = statement.model_dump(by_alias=True, exclude_none=True, mode="json")
        assert Statement.model_validate(wire) == statement


def test_the_full_design_verb_set_now_appears(config: CohortConfig) -> None:
    """DESIGN.md's minimum verb set, complete for the first time."""
    seen = {statement.verb.id for statement in statements_for(config, "struggling")}
    expected = {VERB_IRIS[name] for name in SESSION_VERBS | ASSESSMENT_VERBS}
    assert seen == expected


def test_assessment_events_use_only_assessment_verbs(config: CohortConfig) -> None:
    for event in events_for(config, "coasting"):
        assert event.verb in ASSESSMENT_VERBS


def test_scheduled_items_all_get_activity(config: CohortConfig) -> None:
    """Every deadline must produce work; a silent skip would be invisible."""
    schedule = build_schedule(config)[0]
    touched = {event.activity.id for event in events_for(config, "thriving")}
    assert touched == {deadline.activity_iri for deadline in schedule.deadlines()}


def test_events_are_chronological_and_inside_the_term(config: CohortConfig) -> None:
    start, end = term_bounds(config.term)
    events = events_for(config, "procrastinator")
    assert [e.moment for e in events] == sorted(e.moment for e in events)
    assert all(start <= e.moment <= end for e in events)


# --------------------------------------------------------------------------
# Scores and pass/fail
# --------------------------------------------------------------------------


def test_pass_and_fail_agree_with_the_configured_threshold(
    config: CohortConfig,
) -> None:
    threshold = config.risk.pass_threshold
    for archetype in ("thriving", "struggling"):
        for statement in statements_for(config, archetype):
            if statement.verb.id == VERB_IRIS["passed"]:
                assert statement.result.score.scaled >= threshold
            elif statement.verb.id == VERB_IRIS["failed"]:
                assert statement.result.score.scaled < threshold


def test_every_graded_result_carries_a_coherent_score(config: CohortConfig) -> None:
    graded = {VERB_IRIS[name] for name in ("passed", "failed", "submitted")}
    for statement in statements_for(config, "coasting"):
        if statement.verb.id in graded:
            score = statement.result.score
            assert score is not None
            assert 0.0 <= score.scaled <= 1.0
            assert score.min <= score.raw <= score.max


def test_struggling_learners_fail_more_than_thriving_ones(
    config: CohortConfig,
) -> None:
    def failures(archetype: str) -> int:
        return sum(
            1
            for statement in statements_for(config, archetype)
            if statement.verb.id == VERB_IRIS["failed"]
        )

    assert failures("struggling") > failures("thriving")


def test_recovering_scores_climb_across_the_term(config: CohortConfig) -> None:
    """The score trend must survive into emitted results, not just profiles."""
    scores = results(config, "recovering", "submitted")
    assert len(scores) >= 6
    half = len(scores) // 2
    early = sum(scores[:half]) / half
    late = sum(scores[half:]) / (len(scores) - half)
    assert late > early, f"early={early:.3f} late={late:.3f}"


# --------------------------------------------------------------------------
# Archetype weights reaching the data
# --------------------------------------------------------------------------


def test_procrastinators_work_closer_to_the_deadline(config: CohortConfig) -> None:
    """`deadline_affinity` must be observable, not merely configured.

    Deliberately compares the EXTREMES — procrastinator (0.90) against
    thriving (0.20) — and not mid-range archetypes. Each learner produces
    only ~14 deadline-driven events, so a median over that sample carries
    real noise: `disengaging` (0.35) can land a later median than
    `thriving` (0.20) despite a higher affinity, purely by chance.

    Do NOT "strengthen" this by adding a disengaging-vs-thriving
    assertion. It would flake. The extremes are separated by enough
    affinity that the ordering survives the noise; adjacent archetypes are
    not, and asserting on them would be testing the sample, not the model.
    """
    schedule = build_schedule(config)[0]
    due_by_iri = {d.activity_iri: d.due for d in schedule.deadlines()}

    def median_lead_hours(archetype: str) -> float:
        leads = sorted(
            (due_by_iri[event.activity.id] - event.moment).total_seconds() / 3600
            for event in events_for(config, archetype)
            if event.verb in ("attempted", "submitted")
        )
        return leads[len(leads) // 2]

    assert median_lead_hours("procrastinator") < median_lead_hours("thriving")


def test_struggling_learners_retry_most(config: CohortConfig) -> None:
    """`attempt_persistence` is highest for struggling; it must show."""

    def attempts(archetype: str) -> int:
        return sum(
            1 for event in events_for(config, archetype) if event.verb == "attempted"
        )

    assert attempts("struggling") > attempts("coasting")


def test_retries_are_capped(config: CohortConfig) -> None:
    """A learner must give up; unbounded retries would be a runaway loop."""
    per_activity: dict[str, int] = {}
    for event in events_for(config, "struggling"):
        if event.verb == "attempted":
            per_activity[event.activity.id] = per_activity.get(event.activity.id, 0) + 1
    assert per_activity
    assert max(per_activity.values()) <= MAX_ATTEMPTS


def test_a_passed_assessment_is_never_retried(config: CohortConfig) -> None:
    """Retrying after a pass would be nonsense in the record."""
    for archetype in ("thriving", "struggling", "recovering"):
        by_activity: dict[str, list[str]] = {}
        for event in events_for(config, archetype):
            by_activity.setdefault(event.activity.id, []).append(event.verb)
        for verbs in by_activity.values():
            if "passed" in verbs:
                after = verbs[verbs.index("passed") + 1 :]
                assert "attempted" not in after


def test_answers_accompany_every_attempt(config: CohortConfig) -> None:
    verbs = [e.verb for e in events_for(config, "coasting")]
    assert verbs.count("answered") > verbs.count("attempted")


# --------------------------------------------------------------------------
# Reproducibility and the merged stream
# --------------------------------------------------------------------------


def test_assessment_events_are_reproducible(config: CohortConfig) -> None:
    assert events_for(config, "thriving") == events_for(
        load_config(EXAMPLE), "thriving"
    )


def test_scores_are_independent_of_timestamp_draws(config: CohortConfig) -> None:
    """Stream namespacing: more sessions must not move a single score.

    Sessions draw from `timestamps`, assessments score from `scores`. If
    they shared a stream, changing activity volume would silently reshuffle
    every result in the cohort.
    """
    busier = config.model_copy(deep=True)
    busier.activity.sessions_per_week = config.activity.sessions_per_week * 4
    course = build_courses(config)[0]
    schedule = build_schedule(config)[0]
    learner = learner_of(config, "coasting")

    before = assessment_events(config, learner, course, schedule)
    after = assessment_events(busier, learner, course, schedule)
    assert [e.result for e in before] == [e.result for e in after]


def test_merged_stream_ids_are_unique(config: CohortConfig) -> None:
    """Both producers in one stream — the collision case, on real data."""
    ids = [statement.id for statement in statements_for(config, "struggling")]
    assert len(ids) == len(set(ids))


def test_merged_stream_covers_both_producers(config: CohortConfig) -> None:
    course = build_courses(config)[0]
    schedule = build_schedule(config)[0]
    learner = learner_of(config, "thriving")
    with_schedule = learner_course_statements(config, learner, course, schedule)
    without = learner_course_statements(config, learner, course)
    assert len(with_schedule) > len(without)


def test_merged_stream_is_chronological(config: CohortConfig) -> None:
    times = [s.timestamp for s in statements_for(config, "procrastinator")]
    assert times == sorted(times)


def test_attempt_sequences_stay_ordered_within_an_attempt(
    config: CohortConfig,
) -> None:
    """attempted, then answered, then the result — never out of order."""
    events = [
        event
        for event in events_for(config, "struggling")
        if event.verb in ("attempted", "answered", "passed", "failed")
    ]
    open_attempt = False
    for event in events:
        if event.verb == "attempted":
            open_attempt = True
        elif event.verb in ("passed", "failed"):
            assert open_attempt, "a result arrived with no attempt open"
            open_attempt = False


def test_no_email_shaped_data_in_assessment_statements(config: CohortConfig) -> None:
    import json

    dumped = json.dumps(
        [
            s.model_dump(by_alias=True, exclude_none=True, mode="json")
            for s in statements_for(config, "struggling")
        ]
    )
    assert "@" not in dumped
    assert "mbox" not in dumped


def test_to_statements_handles_an_empty_schedule(config: CohortConfig) -> None:
    course = build_courses(config)[0]
    learner = learner_of(config, "thriving")
    assert to_statements(learner, course, ()) == ()


def test_deadline_lead_never_exceeds_the_term(config: CohortConfig) -> None:
    start, _ = term_bounds(config.term)
    for event in events_for(config, "thriving"):
        assert event.moment >= start
        assert event.moment - start >= timedelta(0)
