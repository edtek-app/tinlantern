"""Term calendar: module windows and assignment deadlines.

Deterministic from the configuration, exactly like the course structure —
the seed does not reach it. A demo cohort's deadlines should be the same
every time the cohort is regenerated.

Deadlines are 23:59 in the course's local timezone, then converted to UTC
for storage. That hour is safe to construct anywhere: IANA transitions
happen around 02:00–03:00 local, so 23:59 always exists on every date in
every zone. No other part of the generator builds a datetime from local
wall-clock components (see ``clock``).

Videos have no deadline — they are consumed, not submitted. Only
assessments and assignments are scheduled.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.xapi import Activity
from data.generator.config import CohortConfig, TermSpec
from data.generator.course import CourseStructure, build_courses

_UTC = ZoneInfo("UTC")

#: Deadlines land at 23:59 local — late enough to be realistic, and clear
#: of every DST transition.
DEADLINE = time(hour=23, minute=59)


@dataclass(frozen=True, slots=True)
class Deadline:
    """One submittable item and when it is due, in UTC."""

    activity_iri: str
    due: datetime


@dataclass(frozen=True, slots=True)
class ModuleSchedule:
    """A module's window and the deadlines inside it. All times UTC."""

    index: int
    opens: datetime
    closes: datetime
    deadlines: tuple[Deadline, ...]


@dataclass(frozen=True, slots=True)
class CourseSchedule:
    """A course's module windows, in order."""

    key: str
    modules: tuple[ModuleSchedule, ...]

    def deadlines(self) -> tuple[Deadline, ...]:
        """Every deadline in the course, in chronological order."""
        return tuple(d for module in self.modules for d in module.deadlines)


def term_bounds(term: TermSpec) -> tuple[datetime, datetime]:
    """Return the term's UTC start and end instants.

    The term runs from local midnight on its start date to local midnight
    on the day after its end date, so the final day is fully included.
    """
    tz = ZoneInfo(term.timezone)
    start = datetime.combine(term.start, time.min, tzinfo=tz)
    end = datetime.combine(term.end + timedelta(days=1), time.min, tzinfo=tz)
    return start.astimezone(_UTC), end.astimezone(_UTC)


def _deadline_at(day: date, tz: ZoneInfo) -> datetime:
    """Build the UTC instant of 23:59 local on a given date."""
    return datetime.combine(day, DEADLINE, tzinfo=tz).astimezone(_UTC)


def build_course_schedule(term: TermSpec, course: CourseStructure) -> CourseSchedule:
    """Spread a course's modules evenly across the term and place deadlines.

    Args:
        term: The term calendar.
        course: The course structure to schedule.

    Returns:
        The course's schedule, with module windows and UTC deadlines.

    Raises:
        ValueError: If a module window has fewer days than it has
            submittable items, so deadlines could not fall on distinct days.
    """
    tz = ZoneInfo(term.timezone)
    total_days = term.days
    module_count = len(course.modules)
    modules: list[ModuleSchedule] = []

    for module in course.modules:
        position = module.index - 1
        opens_offset = (position * total_days) // module_count
        closes_offset = ((position + 1) * total_days) // module_count - 1
        window_days = closes_offset - opens_offset

        items = module.assessments + module.assignments
        if items and window_days < len(items) + 1:
            raise ValueError(
                f"course {course.key!r} module {module.index} has a "
                f"{window_days}-day window but {len(items)} submittable "
                "items; deadlines could not fall on distinct days. Shorten "
                "the module count or lengthen the term."
            )

        deadlines = _place_deadlines(term, tz, opens_offset, window_days, items)
        modules.append(
            ModuleSchedule(
                index=module.index,
                opens=_utc_midnight(term, tz, opens_offset),
                closes=_deadline_at(term.start + timedelta(days=closes_offset), tz),
                deadlines=deadlines,
            )
        )

    return CourseSchedule(key=course.key, modules=tuple(modules))


def _utc_midnight(term: TermSpec, tz: ZoneInfo, day_offset: int) -> datetime:
    """Local midnight `day_offset` days into the term, as UTC."""
    day = term.start + timedelta(days=day_offset)
    return datetime.combine(day, time.min, tzinfo=tz).astimezone(_UTC)


def _place_deadlines(
    term: TermSpec,
    tz: ZoneInfo,
    opens_offset: int,
    window_days: int,
    items: tuple[Activity, ...],
) -> tuple[Deadline, ...]:
    """Spread deadlines evenly through a module window, one per day."""
    count = len(items)
    if count == 0:
        return ()

    spacing = window_days / (count + 1)
    deadlines: list[Deadline] = []
    previous_offset = -1

    for position, activity in enumerate(items, start=1):
        offset = opens_offset + round(position * spacing)
        if offset <= previous_offset:
            raise ValueError(
                f"deadlines collided on day offset {offset}; the module "
                "window is too short for its item count"
            )
        previous_offset = offset
        deadlines.append(
            Deadline(
                activity_iri=activity.id,
                due=_deadline_at(term.start + timedelta(days=offset), tz),
            )
        )

    return tuple(deadlines)


def build_schedule(config: CohortConfig) -> tuple[CourseSchedule, ...]:
    """Build the schedule for every course in a cohort configuration."""
    return tuple(
        build_course_schedule(config.term, course) for course in build_courses(config)
    )
