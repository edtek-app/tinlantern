"""Tests for merging events into one identified statement stream.

Ids are assigned here and nowhere else, because they key on position in
the merged stream. A producer that numbered its own events would collide
with every other producer — `uuid5` is a pure function, so identical keys
give identical UUIDs for different statements.
"""

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.xapi import Activity, ActivityDefinition
from data.generator.config import CohortConfig, load_config
from data.generator.course import build_courses
from data.generator.events import Event
from data.generator.roster import build_learner
from data.generator.stream import registration_id, statement_id, to_statements

pytestmark = pytest.mark.m0

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "data" / "generator" / "cohort.example.yaml"
UTC = ZoneInfo("UTC")
BASE = datetime(2026, 2, 1, 12, 0, tzinfo=UTC)


@pytest.fixture
def config() -> CohortConfig:
    return load_config(EXAMPLE)


def activity(suffix: str) -> Activity:
    return Activity(
        id=f"https://tinlantern.example/xapi/{suffix}",
        definition=ActivityDefinition(name={"en-US": suffix}),
    )


def event(minutes: int, verb: str, suffix: str) -> Event:
    return Event(
        moment=BASE + timedelta(minutes=minutes),
        verb=verb,
        activity=activity(suffix),
    )


# --------------------------------------------------------------------------
# Merging
# --------------------------------------------------------------------------


def test_events_are_ordered_chronologically(config: CohortConfig) -> None:
    learner = build_learner(config, 0)
    course = build_courses(config)[0]
    unordered = (
        event(30, "experienced", "c"),
        event(10, "initialized", "a"),
        event(20, "played", "b"),
    )
    statements = to_statements(learner, course, unordered)
    assert [s.timestamp for s in statements] == sorted(s.timestamp for s in statements)
    assert [s.object_.id.split("/")[-1] for s in statements] == ["a", "b", "c"]


def test_two_producers_get_distinct_ids(config: CohortConfig) -> None:
    """The collision guard: independent numbering would repeat ids.

    Content and assessment mechanics are separate producers. If each
    numbered its own events from zero, `statement_id` would return the same
    UUID for the first event of each — a silent drop under M1's
    idempotency-on-id, which treats a repeated id as a duplicate.
    """
    learner = build_learner(config, 0)
    course = build_courses(config)[0]
    from_one_producer = (event(10, "initialized", "a"), event(30, "experienced", "b"))
    from_another = (event(20, "attempted", "q"), event(40, "passed", "q"))

    statements = to_statements(learner, course, from_one_producer + from_another)
    ids = [s.id for s in statements]
    assert len(ids) == len(set(ids)) == 4


def test_ids_follow_position_in_the_merged_stream(config: CohortConfig) -> None:
    learner = build_learner(config, 0)
    course = build_courses(config)[0]
    statements = to_statements(
        learner, course, (event(20, "played", "b"), event(10, "initialized", "a"))
    )
    assert [s.id for s in statements] == [
        statement_id(learner.identifier, course.key, 0),
        statement_id(learner.identifier, course.key, 1),
    ]


def test_empty_event_set_yields_no_statements(config: CohortConfig) -> None:
    learner = build_learner(config, 0)
    course = build_courses(config)[0]
    assert to_statements(learner, course, ()) == ()


# --------------------------------------------------------------------------
# Context and identity
# --------------------------------------------------------------------------


def test_every_statement_shares_the_course_registration(
    config: CohortConfig,
) -> None:
    learner = build_learner(config, 0)
    course = build_courses(config)[0]
    expected = registration_id(learner.identifier, course.key)
    statements = to_statements(learner, course, (event(10, "initialized", "a"),))
    assert statements[0].context is not None
    assert statements[0].context.registration == expected


def test_registration_differs_per_learner_and_course() -> None:
    assert registration_id("s-00001", "a") != registration_id("s-00002", "a")
    assert registration_id("s-00001", "a") != registration_id("s-00001", "b")
    assert registration_id("s-00001", "a") == registration_id("s-00001", "a")


# --------------------------------------------------------------------------
# Event validation
# --------------------------------------------------------------------------


def test_unknown_verb_is_rejected_at_the_event_boundary() -> None:
    """Catch a bad verb where it is written, not deep in serialization."""
    with pytest.raises(ValueError, match="unknown verb"):
        Event(moment=BASE, verb="procrastinated", activity=activity("a"))


def test_naive_event_timestamp_is_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        Event(
            moment=datetime(2026, 2, 1, 12, 0),
            verb="initialized",
            activity=activity("a"),
        )
