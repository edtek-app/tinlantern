"""Session and content-consumption statements.

A learner's term is modelled as a series of **sessions**, not as scattered
independent events. That is deliberate: M3's headline features are
engagement recency and widening gaps, and a gap is something that exists
*between sessions*. Scattering unrelated events across the term would
leave those features measuring noise — session boundaries are the thing a
disengaging learner disengages from.

Each session opens with `initialized` against the course, then runs a
short burst of content events inside the module the learner has reached:
`experienced` for reading, `played`/`paused`/`completed` for video. How
many sessions a week, and how long each runs, comes from the archetype's
engagement curve scaled by the configured activity volume.

Assessment mechanics — `attempted`, `answered`, `passed`, `failed`,
`submitted` — are deadline-driven and land separately.

Statement ids are `uuid5` over a fixed namespace and a
`learner:course:sequence` key, so regenerating a cohort from the same seed
and the same generator code reproduces byte-identical ids. See ADR-0003
for the exact guarantee and its limits.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.xapi import Activity, Result
from data.generator.archetypes import PROFILES
from data.generator.calendar import term_bounds
from data.generator.clock import sample_in_term
from data.generator.config import CohortConfig
from data.generator.course import CourseStructure
from data.generator.events import Event
from data.generator.rng import stream_rng
from data.generator.roster import Learner

#: Verbs this module emits. Assessment verbs are emitted elsewhere.
SESSION_VERBS: frozenset[str] = frozenset(
    {"initialized", "experienced", "played", "paused", "completed"}
)

# Gap between consecutive events inside one session.
_MIN_EVENT_GAP_SECONDS = 45
_MAX_EVENT_GAP_SECONDS = 900


@dataclass(frozen=True, slots=True)
class SessionWindow:
    """One session's boundaries and the engagement it was scheduled under.

    Windows are the unit M3's engagement features are computed over — a
    gap is the distance between one window's close and the next one's
    open — so they are part of the public surface rather than an internal
    detail of emission.
    """

    opened: datetime
    closes: datetime
    progress: float
    intensity: float


def _sessions_in_week(rng: random.Random, expected: float) -> int:
    """Turn an expected session count into a whole number of sessions.

    The fractional part becomes a probability, so an expectation of 2.4
    yields two sessions most weeks and three sometimes — rather than
    silently truncating every archetype's activity downward.
    """
    whole = int(expected)
    return whole + (1 if rng.random() < expected - whole else 0)


def _module_for_progress(course: CourseStructure, progress: float) -> int:
    """Index of the module a learner has reached at this point in the term."""
    reached = int(progress * len(course.modules))
    return min(reached, len(course.modules) - 1)


def _content_targets(course: CourseStructure, module_index: int) -> list[Activity]:
    """Activities a learner can consume in a module: the module and its videos."""
    module = course.modules[module_index]
    return [module.activity, *module.videos]


def _is_video(course: CourseStructure, module_index: int, activity: Activity) -> bool:
    return activity in course.modules[module_index].videos


def _plan_sessions(
    rng: random.Random,
    config: CohortConfig,
    learner: Learner,
    course: CourseStructure,
) -> tuple[SessionWindow, ...]:
    """Lay out a learner's sessions across the term, closing each one off.

    Sessions are sampled independently within a week, so two can land close
    enough to overlap. A learner is not in two sessions at once, and
    interleaved bursts would corrupt exactly the between-session gaps M3
    measures — so every session ends no later than the next one's start.

    Args:
        rng: The learner-course timestamp stream. Consumed here first, then
            by event generation, so planning is reproducible on its own.
        config: The cohort configuration.
        learner: The learner, carrying their archetype.
        course: The course being scheduled.

    Returns:
        Non-overlapping windows in chronological order.
    """
    profile = PROFILES[learner.archetype]
    start, end = term_bounds(config.term)
    timezone = config.term.timezone
    weeks = max(1, math.ceil(config.term.days / 7))

    opened: list[tuple[datetime, float, float]] = []
    for week in range(weeks):
        week_start = start + timedelta(weeks=week)
        week_end = min(week_start + timedelta(weeks=1), end)
        if week_end <= week_start:
            break

        progress = min((week + 0.5) / weeks, 1.0)
        intensity = profile.engagement.at(progress)
        expected = config.activity.sessions_per_week * intensity

        for _ in range(_sessions_in_week(rng, expected)):
            moment = sample_in_term(rng, week_start, week_end, timezone)
            opened.append((moment, progress, intensity))

    opened.sort(key=lambda entry: entry[0])
    return tuple(
        SessionWindow(
            opened=moment,
            closes=opened[index + 1][0] if index + 1 < len(opened) else end,
            progress=progress,
            intensity=intensity,
        )
        for index, (moment, progress, intensity) in enumerate(opened)
    )


def session_windows(
    config: CohortConfig, learner: Learner, course: CourseStructure
) -> tuple[SessionWindow, ...]:
    """Return a learner's session boundaries for a course.

    Reproduces exactly the windows ``emit_content_statements`` uses, since
    planning consumes the stream before any event draws.
    """
    rng = stream_rng(config.seed, "timestamps", learner.index, course.key)
    return _plan_sessions(rng, config, learner, course)


def content_events(
    config: CohortConfig, learner: Learner, course: CourseStructure
) -> tuple[Event, ...]:
    """Generate one learner's session and content events for a course.

    Args:
        config: The cohort configuration.
        learner: The learner, carrying their archetype.
        course: The course structure to generate activity against.

    Returns:
        Events in chronological order, without ids — those are assigned
        once every producer's events have been merged
        (``data.generator.stream``).
    """
    rng = stream_rng(config.seed, "timestamps", learner.index, course.key)
    windows = _plan_sessions(rng, config, learner, course)

    events: list[Event] = []
    for window in windows:
        events.extend(_session_events(config, rng, course, window))

    # Within a session a video keeps playing while the learner moves on, so
    # its `completed` can fall after the next content event. Sessions no
    # longer overlap, so this orders events inside one session only.
    events.sort(key=lambda event: event.moment)
    return tuple(events)


def _session_events(
    config: CohortConfig,
    rng: random.Random,
    course: CourseStructure,
    window: SessionWindow,
) -> list[Event]:
    """Build one session: an `initialized`, then a burst of content events.

    Args:
        config: The cohort configuration.
        rng: The learner-course timestamp stream.
        course: The course being worked on.
        window: The session's boundaries and engagement.

    Returns:
        Events inside the window, in the order they were produced.
    """
    closes = window.closes
    module_index = _module_for_progress(course, window.progress)
    targets = _content_targets(course, module_index)
    events: list[Event] = [
        Event(moment=window.opened, verb="initialized", activity=course.activity)
    ]

    moment = window.opened
    count = rng.randint(
        config.activity.min_events_per_session, config.activity.max_events_per_session
    )
    for _ in range(count):
        moment = moment + timedelta(
            seconds=rng.randint(_MIN_EVENT_GAP_SECONDS, _MAX_EVENT_GAP_SECONDS)
        )
        if moment >= closes:
            break

        activity = rng.choice(targets)
        if _is_video(course, module_index, activity):
            events.extend(
                event
                for event in _video_events(rng, activity, moment, window.intensity)
                if event.moment < closes
            )
        else:
            events.append(Event(moment=moment, verb="experienced", activity=activity))

    return events


def _video_events(
    rng: random.Random, video: Activity, started: datetime, intensity: float
) -> list[Event]:
    """Play a video, sometimes pause it, and finish it if engaged enough.

    Completion probability tracks engagement, so a thriving learner's high
    video completion (DESIGN.md) is a consequence of their curve rather
    than a separate switch.
    """
    events: list[Event] = [Event(moment=started, verb="played", activity=video)]
    watched = rng.randint(60, 900)

    if rng.random() < 0.35:
        paused_at = started + timedelta(seconds=rng.randint(20, max(21, watched)))
        events.append(Event(moment=paused_at, verb="paused", activity=video))

    if rng.random() < intensity:
        finished = started + timedelta(seconds=watched)
        events.append(
            Event(
                moment=finished,
                verb="completed",
                activity=video,
                result=Result(completion=True, duration=_iso(watched)),
            )
        )

    return events


def _iso(seconds: int) -> str:
    """Format a whole number of seconds as an ISO 8601 duration."""
    minutes, remainder = divmod(seconds, 60)
    return f"PT{minutes}M{remainder}S" if minutes else f"PT{remainder}S"
