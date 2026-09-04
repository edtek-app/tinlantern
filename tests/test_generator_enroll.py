"""Tests for enrollment.

Found by the M0 audit: MILESTONES.md asked for enrollments as a statement
category and DESIGN.md's verb set had none, so the generator matched its
own spec while missing the milestone's. The documents now agree.

The motivating case is `test_a_learner_who_never_engages_is_still_enrolled`.
Inferring enrollment from a learner's first `initialized` would leave the
least engaged learners with no enrollment at all — exactly the population
an early-alert system exists to find.
"""

from pathlib import Path

import pytest

from app.xapi import VERB_IRIS, Statement
from data.generator.calendar import build_schedule, term_bounds
from data.generator.config import CohortConfig, load_config
from data.generator.course import build_courses
from data.generator.enroll import ENROLLMENT_VERBS, enrollment_event
from data.generator.roster import build_roster
from data.generator.stream import learner_course_statements, registration_id

pytestmark = pytest.mark.m0

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "data" / "generator" / "cohort.example.yaml"
REGISTERED = VERB_IRIS["registered"]


@pytest.fixture
def config() -> CohortConfig:
    return load_config(EXAMPLE)


def registrations(statements: tuple[Statement, ...]) -> list[Statement]:
    return [s for s in statements if s.verb.id == REGISTERED]


def stream_for(config: CohortConfig, learner, course_index: int = 0):
    course = build_courses(config)[course_index]
    schedule = build_schedule(config)[course_index]
    return learner_course_statements(config, learner, course, schedule)


# --------------------------------------------------------------------------
# Exactly one, always first
# --------------------------------------------------------------------------


def test_exactly_one_registration_per_learner_course(config: CohortConfig) -> None:
    for learner in build_roster(config)[:6]:
        for index in range(len(build_courses(config))):
            assert len(registrations(stream_for(config, learner, index))) == 1


def test_registration_is_the_earliest_statement_in_the_stream(
    config: CohortConfig,
) -> None:
    """Enrollment precedes activity; an out-of-order fact would be wrong."""
    for learner in build_roster(config)[:6]:
        statements = stream_for(config, learner)
        assert statements[0].verb.id == REGISTERED
        assert statements[0].timestamp == term_bounds(config.term)[0]


def test_registration_count_equals_learners_times_courses(
    config: CohortConfig,
) -> None:
    small = config.model_copy(update={"learners": 5})
    courses = build_courses(small)
    schedules = build_schedule(small)
    total = sum(
        len(registrations(learner_course_statements(small, learner, course, schedule)))
        for learner in build_roster(small)
        for course, schedule in zip(courses, schedules, strict=True)
    )
    assert total == small.learners * len(courses)


def test_registration_targets_the_course_not_a_module(config: CohortConfig) -> None:
    course = build_courses(config)[0]
    statement = registrations(stream_for(config, build_roster(config)[0]))[0]
    assert statement.object_.id == course.activity.id


def test_registration_shares_the_course_registration_context(
    config: CohortConfig,
) -> None:
    """The enrollment carries the same registration as the activity it opens."""
    course = build_courses(config)[0]
    learner = build_roster(config)[0]
    statement = registrations(stream_for(config, learner))[0]
    assert statement.context is not None
    assert statement.context.registration == registration_id(
        learner.identifier, course.key
    )


# --------------------------------------------------------------------------
# The case that motivated the ruling
# --------------------------------------------------------------------------


def test_a_learner_who_never_engages_is_still_enrolled(
    config: CohortConfig,
) -> None:
    """Enrollment cannot be inferred from activity, because there may be none.

    A cohort of pure disengagement with almost no sessions still produces
    one registration per learner-course. Under the inference shortcut these
    learners would vanish from the enrolment records entirely — and they
    are the ones the platform most needs to see.
    """
    ghosts = config.model_copy(deep=True)
    ghosts.learners = 4
    ghosts.archetype_mix = {"disengaging": 1.0}
    ghosts.activity.sessions_per_week = 0.05

    courses = build_courses(ghosts)
    schedules = build_schedule(ghosts)
    for learner in build_roster(ghosts):
        for course, schedule in zip(courses, schedules, strict=True):
            statements = learner_course_statements(ghosts, learner, course, schedule)
            assert len(registrations(statements)) == 1


# --------------------------------------------------------------------------
# The contract
# --------------------------------------------------------------------------


def test_registration_survives_the_xapi_contract(config: CohortConfig) -> None:
    statement = registrations(stream_for(config, build_roster(config)[0]))[0]
    wire = statement.model_dump(by_alias=True, exclude_none=True, mode="json")
    assert Statement.model_validate(wire) == statement


def test_registered_is_in_the_accepted_verb_set() -> None:
    """Widening the receiver contract is deliberate — see ADR-0001."""
    assert {"registered"} == ENROLLMENT_VERBS
    assert VERB_IRIS["registered"] == "http://adlnet.gov/expapi/verbs/registered"


def test_enrollment_is_structural_not_seeded(config: CohortConfig) -> None:
    """No RNG: the same event regardless of seed or archetype."""
    course = build_courses(config)[0]
    reseeded = config.model_copy(update={"seed": config.seed + 1})
    assert enrollment_event(config, course) == enrollment_event(reseeded, course)
