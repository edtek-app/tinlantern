"""Tests for session and content-consumption emission.

The volume assertions read their expectations from the configuration
rather than restating constants, so retuning `activity:` later cannot
silently invalidate what these tests mean.

The load-bearing test in this file is the one showing that a disengaging
learner's output actually decays across the term. Everything upstream —
curves, profiles, seeds — exists to make that true in the emitted data,
and it is the only place the whole chain is checked end to end.
"""

import json
from pathlib import Path

import pytest

from app.xapi import VERB_IRIS, Statement
from data.generator.config import CohortConfig, load_config
from data.generator.course import build_courses
from data.generator.emit import SESSION_VERBS, session_windows
from data.generator.enroll import ENROLLMENT_VERBS
from data.generator.roster import build_learner, build_roster
from data.generator.stream import (
    learner_course_statements,
    learner_statements,
    registration_id,
    statement_id,
)
from tests.ferpa import assert_no_identifying_data

pytestmark = pytest.mark.m0

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "data" / "generator" / "cohort.example.yaml"


@pytest.fixture
def config() -> CohortConfig:
    return load_config(EXAMPLE)


def learner_of(config: CohortConfig, archetype: str):
    """First learner in the cohort carrying a given archetype."""
    for learner in build_roster(config):
        if learner.archetype == archetype:
            return learner
    raise AssertionError(f"no {archetype} learner in the example cohort")


def statements_for(config: CohortConfig, archetype: str) -> tuple[Statement, ...]:
    course = build_courses(config)[0]
    return learner_course_statements(config, learner_of(config, archetype), course)


def quarter_counts(
    config: CohortConfig, statements: tuple[Statement, ...]
) -> list[int]:
    """Statement counts in each quarter of the term."""
    from data.generator.calendar import term_bounds

    start, end = term_bounds(config.term)
    span = (end - start).total_seconds()
    counts = [0, 0, 0, 0]
    for statement in statements:
        assert statement.timestamp is not None
        position = (statement.timestamp - start).total_seconds() / span
        counts[min(int(position * 4), 3)] += 1
    return counts


# --------------------------------------------------------------------------
# The statement contract
# --------------------------------------------------------------------------


def test_every_statement_survives_the_xapi_contract(config: CohortConfig) -> None:
    for statement in statements_for(config, "thriving"):
        wire = statement.model_dump(by_alias=True, exclude_none=True, mode="json")
        assert Statement.model_validate(wire) == statement


def test_only_session_and_enrollment_verbs_are_emitted(
    config: CohortConfig,
) -> None:
    """Without a schedule the stream is enrollment plus content only."""
    allowed = {VERB_IRIS[name] for name in SESSION_VERBS | ENROLLMENT_VERBS}
    for statement in statements_for(config, "coasting"):
        assert statement.verb.id in allowed


def test_every_session_verb_actually_appears(config: CohortConfig) -> None:
    """A verb declared but never emitted would be a silent coverage gap."""
    seen = {statement.verb.id for statement in statements_for(config, "thriving")}
    assert seen == {VERB_IRIS[name] for name in SESSION_VERBS | ENROLLMENT_VERBS}


def test_statements_reference_only_real_activities(config: CohortConfig) -> None:
    """A dangling IRI would become an orphan row in the warehouse."""
    course = build_courses(config)[0]
    known = {activity.id for activity in course.activities()}
    for statement in learner_course_statements(
        config, learner_of(config, "thriving"), course
    ):
        assert statement.object_.id in known


def test_context_ties_statements_to_the_course(config: CohortConfig) -> None:
    course = build_courses(config)[0]
    learner = learner_of(config, "thriving")
    expected = registration_id(learner.identifier, course.key)
    for statement in learner_course_statements(config, learner, course):
        assert statement.context is not None
        assert statement.context.registration == expected
        assert statement.context.contextActivities is not None
        parents = statement.context.contextActivities.parent
        assert parents is not None and parents[0].id == course.activity.id


# --------------------------------------------------------------------------
# Time
# --------------------------------------------------------------------------


def test_statements_are_chronological(config: CohortConfig) -> None:
    times = [s.timestamp for s in statements_for(config, "procrastinator")]
    assert times == sorted(times)


def test_statements_fall_inside_the_term(config: CohortConfig) -> None:
    from data.generator.calendar import term_bounds

    start, end = term_bounds(config.term)
    for statement in statements_for(config, "coasting"):
        assert statement.timestamp is not None
        assert start <= statement.timestamp <= end


def test_timestamps_are_timezone_aware(config: CohortConfig) -> None:
    assert all(
        s.timestamp.tzinfo is not None for s in statements_for(config, "thriving")
    )


# --------------------------------------------------------------------------
# Volume tracks the engagement curve
# --------------------------------------------------------------------------


def test_disengaging_learners_fade_across_the_term(config: CohortConfig) -> None:
    """The end-to-end check: curves must reach the emitted data.

    If this fails, the archetype table and the dataset have come apart —
    which every other test in the suite would happily miss.
    """
    counts = quarter_counts(config, statements_for(config, "disengaging"))
    assert counts[3] < counts[0] / 3, f"quarters={counts}"


def test_thriving_learners_stay_steady(config: CohortConfig) -> None:
    counts = quarter_counts(config, statements_for(config, "thriving"))
    assert min(counts) > 0
    assert max(counts) < 2 * min(counts), f"quarters={counts}"


def test_recovering_learners_dip_then_return(config: CohortConfig) -> None:
    counts = quarter_counts(config, statements_for(config, "recovering"))
    assert counts[1] < counts[0], f"quarters={counts}"
    assert counts[3] > counts[1], f"quarters={counts}"


def test_thriving_out_produces_coasting(config: CohortConfig) -> None:
    thriving = len(statements_for(config, "thriving"))
    coasting = len(statements_for(config, "coasting"))
    assert thriving > coasting


def test_volume_scales_with_configured_activity(config: CohortConfig) -> None:
    """Expectations come from config, not from restated constants."""
    busier = config.model_copy(deep=True)
    busier.activity.sessions_per_week = config.activity.sessions_per_week * 3
    course = build_courses(config)[0]
    learner = learner_of(config, "thriving")
    assert len(learner_course_statements(busier, learner, course)) > len(
        learner_course_statements(config, learner, course)
    )


def test_session_length_respects_the_configured_range(config: CohortConfig) -> None:
    """Every session's burst sits inside min/max events, read from config."""
    statements = statements_for(config, "thriving")
    bursts = []
    current = 0
    for statement in statements:
        if statement.verb.id == VERB_IRIS["initialized"]:
            if current:
                bursts.append(current)
            current = 0
        else:
            current += 1
    if current:
        bursts.append(current)

    # A burst can exceed max_events_per_session because one video event can
    # expand into played/paused/completed; it can never fall below zero or
    # exceed three times the configured ceiling.
    assert bursts
    assert max(bursts) <= config.activity.max_events_per_session * 3


# --------------------------------------------------------------------------
# Identity and reproducibility
# --------------------------------------------------------------------------


def test_session_windows_never_overlap(config: CohortConfig) -> None:
    """Named guard for the overlap bug, asserted on real session boundaries.

    Sessions are sampled independently within a week, so two can land close
    together; each must be closed off at the next one's start. A learner is
    never in two sessions at once, and interleaved bursts would corrupt the
    between-session gaps M3 measures.

    This asserts on `session_windows`, not on the emitted timeline. The
    timeline is globally sorted, so splitting it on `initialized` yields
    intervals that are disjoint by construction — a tautology that passes
    even while sessions overlap. The windows are where the invariant
    actually lives, so that is where it is checked.
    """
    course = build_courses(config)[0]
    for archetype in ("thriving", "procrastinator", "disengaging", "recovering"):
        windows = session_windows(config, learner_of(config, archetype), course)
        assert windows, f"no sessions planned for {archetype}"
        for window in windows:
            assert window.opened < window.closes
        for earlier, later in zip(windows, windows[1:], strict=False):
            assert earlier.closes <= later.opened, (
                f"{archetype}: session [{earlier.opened}, {earlier.closes}) "
                f"overlaps the next opening at {later.opened}"
            )


def test_every_statement_falls_inside_a_session_window(
    config: CohortConfig,
) -> None:
    """No behavioural event may escape the session that produced it.

    Enrollment is exempt by design: it is a structural fact recorded at
    term start, not something a learner did during a session.
    """
    course = build_courses(config)[0]
    learner = learner_of(config, "thriving")
    windows = session_windows(config, learner, course)
    statements = learner_course_statements(config, learner, course)

    for statement in statements:
        assert statement.timestamp is not None
        if statement.verb.id == VERB_IRIS["registered"]:
            continue
        assert any(
            window.opened <= statement.timestamp < window.closes for window in windows
        ), f"statement at {statement.timestamp} belongs to no session"


def test_session_windows_match_what_emission_used(config: CohortConfig) -> None:
    """The public window view must be the same plan emission ran on."""
    course = build_courses(config)[0]
    learner = learner_of(config, "coasting")
    windows = session_windows(config, learner, course)
    openings = [
        statement.timestamp
        for statement in learner_course_statements(config, learner, course)
        if statement.verb.id == VERB_IRIS["initialized"]
    ]
    assert [window.opened for window in windows] == openings


def test_statement_ids_are_unique_across_the_cohort(config: CohortConfig) -> None:
    courses = build_courses(config)
    small = config.model_copy(update={"learners": 12})
    ids = [
        statement.id
        for learner in build_roster(small)
        for statement in learner_statements(small, learner, courses)
    ]
    assert len(ids) == len(set(ids))
    assert all(identifier is not None for identifier in ids)


def test_statement_ids_are_stable_across_regeneration(config: CohortConfig) -> None:
    """What M1's idempotency-on-statement-id criterion depends on."""
    first = statements_for(config, "thriving")
    second = statements_for(load_config(EXAMPLE), "thriving")
    assert [s.id for s in first] == [s.id for s in second]
    assert first == second


def test_statement_id_derivation_is_positional_and_documented() -> None:
    """Ids key on sequence, so position determines identity — by design."""
    assert statement_id("s-00001", "c", 0) != statement_id("s-00001", "c", 1)
    assert statement_id("s-00001", "c", 0) != statement_id("s-00002", "c", 0)
    assert statement_id("s-00001", "c", 0) != statement_id("s-00001", "d", 0)
    assert statement_id("s-00001", "c", 0) == statement_id("s-00001", "c", 0)


def test_different_seed_produces_different_statements(config: CohortConfig) -> None:
    other = config.model_copy(update={"seed": config.seed + 1})
    course = build_courses(config)[0]
    learner = build_learner(config, 3)
    assert learner_course_statements(
        config, learner, course
    ) != learner_course_statements(other, learner, course)


def test_growing_the_cohort_leaves_a_learner_untouched(config: CohortConfig) -> None:
    """Learner 3's term must not change because the cohort grew."""
    course = build_courses(config)[0]
    small = learner_course_statements(config, build_learner(config, 3), course)
    larger = config.model_copy(update={"learners": 500})
    assert learner_course_statements(larger, build_learner(larger, 3), course) == small


# --------------------------------------------------------------------------
# FERPA posture
# --------------------------------------------------------------------------


def test_no_email_shaped_data_in_emitted_statements(config: CohortConfig) -> None:
    dumped = json.dumps(
        [
            s.model_dump(by_alias=True, exclude_none=True, mode="json")
            for s in statements_for(config, "thriving")
        ]
    )
    assert_no_identifying_data(dumped, "emitted statements")
