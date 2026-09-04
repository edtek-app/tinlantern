"""Enrollment: the fact that a learner joined a course.

An enrollment is a fact in its own right, not something to infer from a
learner's first `initialized`. Under that shortcut a learner who never
opens a course would have no enrollment at all — and that is precisely the
population an early-alert system exists to find. M2's dimensional model
wants enrollment as a fact, so it is emitted as one.

Structural, not behavioural: every learner-course gets exactly one
`registered` statement at term start, regardless of archetype. No RNG is
involved.
"""

from __future__ import annotations

from data.generator.calendar import term_bounds
from data.generator.config import CohortConfig
from data.generator.course import CourseStructure
from data.generator.events import Event

#: Verbs this module emits.
ENROLLMENT_VERBS: frozenset[str] = frozenset({"registered"})


def enrollment_event(config: CohortConfig, course: CourseStructure) -> Event:
    """Build the enrollment event for one learner-course.

    Args:
        config: The cohort configuration, for the term calendar.
        course: The course being joined.

    Returns:
        A `registered` event at term start, against the course activity.
        The actor and the shared ``context.registration`` are applied when
        the stream is assembled.
    """
    start, _ = term_bounds(config.term)
    return Event(moment=start, verb="registered", activity=course.activity)
