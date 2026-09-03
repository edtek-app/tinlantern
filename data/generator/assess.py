"""Assessment and submission statements.

Where content consumption is driven by an engagement curve, this is driven
by deadlines. Each scheduled item pulls a learner toward it with a force
set by their archetype's ``deadline_affinity``: a procrastinator works in
the final hours, a thriving learner days earlier.

An assessment produces an attempt sequence — ``attempted``, several
``answered``, then ``passed`` or ``failed`` carrying the score. A failing
learner may retry, with ``attempt_persistence`` deciding how doggedly.
An assignment produces a single ``submitted``.

Pass and fail are decided against ``RiskSpec.pass_threshold`` — the same
number the sidecar later applies to a learner's term mean. One knob, one
meaning of passing, at both granularities (ADR-0004).

Timing draws use a distinct ``assessments`` scope on the timestamps
stream, so they are independent of the session draws for the same
learner-course. Scores draw from the ``scores`` stream, so adding an
assessment cannot shift a single timestamp and vice versa (ADR-0003).
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta

from app.xapi import Activity, Result, Score
from data.generator.archetypes import PROFILES, ArchetypeProfile
from data.generator.calendar import CourseSchedule, term_bounds
from data.generator.clock import sample_before_deadline
from data.generator.config import CohortConfig
from data.generator.course import CourseStructure
from data.generator.events import Event
from data.generator.rng import stream_rng
from data.generator.roster import Learner

#: Verbs this module emits.
ASSESSMENT_VERBS: frozenset[str] = frozenset(
    {"attempted", "answered", "passed", "failed", "submitted"}
)

#: A learner gives up after this many tries at one assessment.
MAX_ATTEMPTS = 3

# Deadline pull, in hours before the due time, mapped from deadline_affinity:
# affinity 1.0 works to the wire, affinity 0.0 starts days ahead.
_TIGHTEST_MEAN_HOURS = 6.0
_LOOSEST_MEAN_HOURS = 60.0


def _mean_hours_before(affinity: float) -> float:
    """How far ahead of a deadline this archetype typically works."""
    return _LOOSEST_MEAN_HOURS - affinity * (_LOOSEST_MEAN_HOURS - _TIGHTEST_MEAN_HOURS)


def _submittables(course: CourseStructure) -> dict[str, tuple[Activity, str]]:
    """Map every submittable activity IRI to its activity and kind."""
    found: dict[str, tuple[Activity, str]] = {}
    for module in course.modules:
        for activity in module.assessments:
            found[activity.id] = (activity, "assessment")
        for activity in module.assignments:
            found[activity.id] = (activity, "assignment")
    return found


def _score_result(scaled: float, passed: bool, seconds: int | None = None) -> Result:
    """Build a result carrying a scaled and raw score out of 100."""
    return Result(
        score=Score(
            scaled=round(scaled, 4), raw=round(scaled * 100, 1), min=0, max=100
        ),
        success=passed,
        completion=True,
        duration=_iso(seconds) if seconds is not None else None,
    )


def _iso(seconds: int) -> str:
    """Format a whole number of seconds as an ISO 8601 duration."""
    minutes, remainder = divmod(seconds, 60)
    return f"PT{minutes}M{remainder}S" if minutes else f"PT{remainder}S"


def assessment_events(
    config: CohortConfig,
    learner: Learner,
    course: CourseStructure,
    schedule: CourseSchedule,
) -> tuple[Event, ...]:
    """Generate one learner's assessment and submission events for a course.

    Args:
        config: The cohort configuration.
        learner: The learner, carrying their archetype.
        course: The course structure, used to resolve scheduled activities.
        schedule: The course's deadlines.

    Returns:
        Events in chronological order, without ids.
    """
    profile = PROFILES[learner.archetype]
    clock = stream_rng(
        config.seed, "timestamps", learner.index, course.key, "assessments"
    )
    scores = stream_rng(config.seed, "scores", learner.index, course.key)
    start, end = term_bounds(config.term)
    span = (end - start).total_seconds()
    timezone = config.term.timezone
    submittables = _submittables(course)

    events: list[Event] = []
    for deadline in schedule.deadlines():
        activity, kind = submittables[deadline.activity_iri]
        progress = min(max((deadline.due - start).total_seconds() / span, 0.0), 1.0)
        opened = sample_before_deadline(
            clock,
            deadline.due,
            start,
            timezone,
            mean_hours_before=_mean_hours_before(profile.deadline_affinity),
        )

        if kind == "assignment":
            events.append(
                _submission(scores, profile, config, activity, opened, progress)
            )
        else:
            events.extend(
                _attempts(
                    clock, scores, profile, config, activity, opened, progress, end
                )
            )

    events.sort(key=lambda event: event.moment)
    return tuple(events)


def _submission(
    scores: random.Random,
    profile: ArchetypeProfile,
    config: CohortConfig,
    activity: Activity,
    moment: datetime,
    progress: float,
) -> Event:
    """One `submitted` for an assignment, carrying its score."""
    scaled = profile.score.sample(scores, progress)
    passed = scaled >= config.risk.pass_threshold
    return Event(
        moment=moment,
        verb="submitted",
        activity=activity,
        result=_score_result(scaled, passed),
    )


def _attempts(
    clock: random.Random,
    scores: random.Random,
    profile: ArchetypeProfile,
    config: CohortConfig,
    activity: Activity,
    opened: datetime,
    progress: float,
    term_end: datetime,
) -> list[Event]:
    """An attempt sequence, retried while the learner keeps failing.

    Retries stop on a pass, at ``MAX_ATTEMPTS``, at the end of term, or
    when persistence runs out. A struggling learner (highest persistence)
    therefore leaves a visible trail of repeated attempts, which is the
    behaviour DESIGN.md describes.
    """
    events: list[Event] = []
    moment = opened

    for attempt in range(1, MAX_ATTEMPTS + 1):
        scaled = profile.score.sample(scores, progress)
        passed = scaled >= config.risk.pass_threshold

        events.append(Event(moment=moment, verb="attempted", activity=activity))
        answering = moment
        for _ in range(clock.randint(4, 10)):
            answering = answering + timedelta(seconds=clock.randint(20, 180))
            events.append(
                Event(
                    moment=answering,
                    verb="answered",
                    activity=activity,
                    # Each answer is correct with probability equal to the
                    # attempt's score, so the answer trail is consistent
                    # with the result rather than decorative.
                    result=Result(success=scores.random() < scaled),
                )
            )

        finished = answering + timedelta(seconds=clock.randint(30, 180))
        events.append(
            Event(
                moment=finished,
                verb="passed" if passed else "failed",
                activity=activity,
                result=_score_result(
                    scaled, passed, int((finished - moment).total_seconds())
                ),
            )
        )

        if passed or attempt == MAX_ATTEMPTS:
            break
        if scores.random() >= profile.attempt_persistence:
            break
        moment = finished + timedelta(hours=clock.uniform(2.0, 72.0))
        if moment >= term_end:
            break

    return events
