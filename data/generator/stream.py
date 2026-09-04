"""Merging events into one ordered statement stream, and assigning ids.

A learner-course produces events from more than one mechanic: sessions and
content consumption drive one, deadlines drive another. An LRS receives a
*single* ordered stream, not several parallel ones, so the generator models
the receiver's reality — the same principle the xAPI models are built on.

Ids are assigned here and nowhere else. `statement_id` keys on position in
the stream, so a producer that numbered its own events would collide with
every other producer: `uuid5` is a pure function, and identical keys give
identical UUIDs for different statements. Merging first and numbering once
makes that collision unrepresentable.
"""

from __future__ import annotations

import uuid

from app.xapi import Context, ContextActivities, Statement
from data.generator.assess import assessment_events
from data.generator.calendar import CourseSchedule
from data.generator.config import CohortConfig
from data.generator.course import CourseStructure
from data.generator.emit import content_events
from data.generator.enroll import enrollment_event
from data.generator.events import Event, verb
from data.generator.roster import Learner

#: Fixed namespace for TinLantern statement ids. Never regenerate this —
#: changing it renames every statement in every cohort ever produced.
STATEMENT_NAMESPACE = uuid.UUID("6f0d3c8a-1b47-5e29-9c14-8a3f2d7b6e50")


def statement_id(learner_identifier: str, course_key: str, sequence: int) -> uuid.UUID:
    """Derive a statement's id.

    Args:
        learner_identifier: The learner's opaque account name.
        course_key: The course the statement belongs to.
        sequence: Zero-based position in that learner-course's merged stream.

    Returns:
        A UUID stable for the same seed and generator code. See ADR-0003
        for the precise guarantee and its limits.
    """
    key = f"{learner_identifier}:{course_key}:{sequence}"
    return uuid.uuid5(STATEMENT_NAMESPACE, key)


def registration_id(learner_identifier: str, course_key: str) -> uuid.UUID:
    """Derive the registration tying a learner's statements to a course."""
    key = f"registration:{learner_identifier}:{course_key}"
    return uuid.uuid5(STATEMENT_NAMESPACE, key)


def to_statements(
    learner: Learner, course: CourseStructure, events: tuple[Event, ...]
) -> tuple[Statement, ...]:
    """Order events and turn them into identified statements.

    Args:
        learner: The learner the events belong to.
        course: The course they belong to.
        events: Events from every producer, in any order.

    Returns:
        Statements in chronological order, ids assigned by position.
    """
    # Stable sort on the timestamp alone: events sharing an instant keep the
    # order their producers emitted them in, which is deterministic, so ties
    # do not wobble between runs.
    ordered = sorted(events, key=lambda event: event.moment)
    registration = registration_id(learner.identifier, course.key)

    return tuple(
        Statement(
            id=statement_id(learner.identifier, course.key, sequence),
            actor=learner.agent,
            verb=verb(event.verb),
            object=event.activity,
            result=event.result,
            context=Context(
                registration=registration,
                platform="TinLantern",
                language="en-US",
                contextActivities=ContextActivities(parent=[course.activity]),
            ),
            timestamp=event.moment,
        )
        for sequence, event in enumerate(ordered)
    )


def learner_course_statements(
    config: CohortConfig,
    learner: Learner,
    course: CourseStructure,
    schedule: CourseSchedule | None = None,
) -> tuple[Statement, ...]:
    """Generate one learner's complete statement stream for one course.

    Args:
        config: The cohort configuration.
        learner: The learner to generate for.
        course: The course structure.
        schedule: The course's deadlines. Without it only content events
            are produced, which is useful for testing one mechanic in
            isolation but is not a complete term.

    Returns:
        The merged, ordered, identified stream.
    """
    # Enrollment leads. It sits at term start and is concatenated first, so
    # the stable sort keeps it ahead of anything sharing that instant.
    events: tuple[Event, ...] = (enrollment_event(config, course),)
    events = events + content_events(config, learner, course)
    if schedule is not None:
        events = events + assessment_events(config, learner, course, schedule)
    return to_statements(learner, course, events)


def learner_statements(
    config: CohortConfig,
    learner: Learner,
    courses: tuple[CourseStructure, ...],
    schedules: tuple[CourseSchedule, ...] | None = None,
) -> tuple[Statement, ...]:
    """Generate one learner's statements across every course.

    Streams stay grouped by course rather than interleaved, since ids are
    scoped to a learner-course.
    """
    by_course = schedules or (None,) * len(courses)
    return tuple(
        statement
        for course, schedule in zip(courses, by_course, strict=True)
        for statement in learner_course_statements(config, learner, course, schedule)
    )
