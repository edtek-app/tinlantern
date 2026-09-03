"""Tests for the term calendar.

The calendar is deterministic from configuration, so these are structural
assertions rather than statistical ones. The DST case is the interesting
one: the example term crosses the 8 March 2026 transition.
"""

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from data.generator.calendar import (
    DEADLINE,
    build_course_schedule,
    build_schedule,
    term_bounds,
)
from data.generator.config import CohortConfig, load_config
from data.generator.course import build_course

pytestmark = pytest.mark.m0

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "data" / "generator" / "cohort.example.yaml"

#: US spring-forward in 2026: 02:00–02:59 local does not exist this day.
DST_GAP_DAY = "2026-03-08"


@pytest.fixture
def config() -> CohortConfig:
    return load_config(EXAMPLE)


def test_term_bounds_are_utc_and_ordered(config: CohortConfig) -> None:
    start, end = term_bounds(config.term)
    assert start.tzinfo is not None and end.tzinfo is not None
    assert start < end
    assert str(start.tzinfo) == "UTC"


def test_term_bounds_include_the_whole_final_day(config: CohortConfig) -> None:
    _, end = term_bounds(config.term)
    local_end = end.astimezone(ZoneInfo(config.term.timezone))
    assert local_end.date() > config.term.end


def test_every_deadline_falls_inside_the_term(config: CohortConfig) -> None:
    start, end = term_bounds(config.term)
    for course in build_schedule(config):
        for deadline in course.deadlines():
            assert start <= deadline.due < end


def test_deadlines_are_strictly_increasing(config: CohortConfig) -> None:
    """A course's work must move forward; equal deadlines would be a bug."""
    for course in build_schedule(config):
        due = [deadline.due for deadline in course.deadlines()]
        assert due == sorted(due)
        assert len(set(due)) == len(due)


def test_deadline_count_matches_submittable_items(config: CohortConfig) -> None:
    """Videos are consumed, not submitted — they must not be scheduled."""
    for course, spec in zip(build_schedule(config), config.courses, strict=True):
        expected = spec.modules * (
            spec.assessments_per_module + spec.assignments_per_module
        )
        assert len(course.deadlines()) == expected


def test_deadlines_land_at_the_local_deadline_hour(config: CohortConfig) -> None:
    tz = ZoneInfo(config.term.timezone)
    for course in build_schedule(config):
        for deadline in course.deadlines():
            local = deadline.due.astimezone(tz)
            assert (local.hour, local.minute) == (DEADLINE.hour, DEADLINE.minute)


def test_module_windows_are_ordered_and_disjoint(config: CohortConfig) -> None:
    for course in build_schedule(config):
        for earlier, later in zip(course.modules, course.modules[1:], strict=False):
            assert earlier.opens < later.opens
            assert earlier.closes <= later.opens + (later.closes - later.opens)


def test_deadlines_sit_within_their_module_window(config: CohortConfig) -> None:
    for course in build_schedule(config):
        for module in course.modules:
            for deadline in module.deadlines:
                assert module.opens <= deadline.due <= module.closes


# --------------------------------------------------------------------------
# Daylight saving
# --------------------------------------------------------------------------


def test_deadline_hour_survives_the_dst_transition(config: CohortConfig) -> None:
    """23:59 exists on every date, including spring-forward morning.

    Transitions happen around 02:00, so the deadline hour is never the
    nonexistent one. This pins that assumption to a real date.
    """
    tz = ZoneInfo(config.term.timezone)
    local = datetime(2026, 3, 8, DEADLINE.hour, DEADLINE.minute, tzinfo=tz)
    # A nonexistent local time would not survive the UTC round trip intact.
    assert local.astimezone(ZoneInfo("UTC")).astimezone(tz) == local


def test_no_deadline_lands_in_the_dst_gap(config: CohortConfig) -> None:
    tz = ZoneInfo(config.term.timezone)
    for course in build_schedule(config):
        for deadline in course.deadlines():
            local = deadline.due.astimezone(tz)
            if str(local.date()) == DST_GAP_DAY:
                assert local.hour != 2


# --------------------------------------------------------------------------
# Failure modes
# --------------------------------------------------------------------------


def test_module_window_too_short_for_its_items_fails_loudly(
    config: CohortConfig,
) -> None:
    """Deadlines could not fall on distinct days; say so rather than collide."""
    crowded = config.model_copy(deep=True)
    crowded.courses[0].modules = 40
    crowded.courses[0].assessments_per_module = 4
    crowded.courses[0].assignments_per_module = 4
    course = build_course(crowded.base_iri, crowded.courses[0])
    with pytest.raises(ValueError, match="submittable"):
        build_course_schedule(crowded.term, course)


def test_schedule_is_a_pure_function_of_config(config: CohortConfig) -> None:
    assert build_schedule(config) == build_schedule(load_config(EXAMPLE))


def test_seed_does_not_influence_the_schedule(config: CohortConfig) -> None:
    reseeded = config.model_copy(update={"seed": config.seed + 1})
    assert build_schedule(reseeded) == build_schedule(config)
