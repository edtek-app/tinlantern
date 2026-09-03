"""Contract tests for the xAPI statement models.

These are written from the RECEIVER's point of view. A suite containing only
statements the generator could plausibly emit would prove nothing about
ingestion — it would just confirm the models fit their own emitter. So the
rejection cases below deliberately include shapes the generator will never
produce: unknown keys at every nesting level, batch-shaped payloads, naive
timestamps, and incoherent scores.
"""

from copy import deepcopy
from typing import Any

import pytest
from pydantic import ValidationError

from app.xapi import VERB_IRIS, Agent, Score, Statement

pytestmark = pytest.mark.m0

COURSE = "https://tinlantern.test/course/analytics-101"


def statement(**overrides: Any) -> dict[str, Any]:
    """A minimal valid statement, with overrides merged at the top level."""
    base: dict[str, Any] = {
        "actor": {"objectType": "Agent", "mbox": "mailto:learner@tinlantern.test"},
        "verb": {
            "id": VERB_IRIS["experienced"],
            "display": {"en-US": "experienced"},
        },
        "object": {"objectType": "Activity", "id": f"{COURSE}/module/1"},
        "timestamp": "2026-03-01T10:00:00+00:00",
    }
    base.update(overrides)
    return base


def assert_rejected(payload: Any) -> ValidationError:
    """Assert the receiver refuses a payload, and hand back the error."""
    with pytest.raises(ValidationError) as caught:
        Statement.model_validate(payload)
    return caught.value


# --------------------------------------------------------------------------
# Acceptance
# --------------------------------------------------------------------------


@pytest.mark.parametrize("verb_name", sorted(VERB_IRIS))
def test_every_supported_verb_validates(verb_name: str) -> None:
    """The minimum verb set in data/generator/DESIGN.md must all parse."""
    parsed = Statement.model_validate(
        statement(verb={"id": VERB_IRIS[verb_name], "display": {"en": verb_name}})
    )
    assert parsed.verb.id == VERB_IRIS[verb_name]


def test_full_statement_with_result_and_context_validates() -> None:
    parsed = Statement.model_validate(
        statement(
            id="0f7f2b1e-3f2a-4a1e-9f4b-2c8a1d5e6f70",
            verb={"id": VERB_IRIS["passed"]},
            result={
                "score": {"scaled": 0.82, "raw": 82, "min": 0, "max": 100},
                "success": True,
                "completion": True,
                "duration": "PT4M13S",
            },
            context={
                "registration": "3b9c1a2d-4e5f-4a6b-8c7d-9e0f1a2b3c4d",
                "platform": "TinLantern LMS",
                "language": "en-US",
                "contextActivities": {
                    "parent": [{"objectType": "Activity", "id": COURSE}]
                },
            },
        )
    )
    assert parsed.result is not None
    assert parsed.result.score is not None
    assert parsed.result.score.raw == 82
    assert parsed.context is not None
    assert parsed.context.contextActivities is not None


def test_agent_may_be_identified_by_account() -> None:
    agent = Agent.model_validate(
        {"account": {"homePage": "https://lms.tinlantern.test", "name": "s-00417"}}
    )
    assert agent.account is not None
    assert agent.mbox is None


# --------------------------------------------------------------------------
# Serialisation — the wire format must be real xAPI
# --------------------------------------------------------------------------


def test_dump_by_alias_emits_the_wire_name_for_object() -> None:
    dumped = Statement.model_validate(statement()).model_dump(
        by_alias=True, exclude_none=True, mode="json"
    )
    assert "object" in dumped
    assert "object_" not in dumped


def test_statement_round_trips_through_its_own_wire_format() -> None:
    """What we emit must be re-acceptable by the receiver, unchanged."""
    original = Statement.model_validate(statement())
    dumped = original.model_dump(by_alias=True, exclude_none=True, mode="json")
    assert Statement.model_validate(dumped) == original


def test_wire_format_rejects_the_python_field_name() -> None:
    """`object_` is a Python spelling, not a valid xAPI key."""
    payload = statement()
    payload["object_"] = payload.pop("object")
    assert_rejected(payload)


# --------------------------------------------------------------------------
# Rejection — shapes the generator would never emit
# --------------------------------------------------------------------------


def test_missing_actor_is_rejected() -> None:
    payload = statement()
    del payload["actor"]
    assert "actor" in str(assert_rejected(payload))


def test_unknown_verb_iri_is_rejected() -> None:
    """An unmodelled verb would land in the warehouse uninterpretable."""
    payload = statement(verb={"id": "http://adlnet.gov/expapi/verbs/interacted"})
    assert "unsupported verb" in str(assert_rejected(payload))


@pytest.mark.parametrize(
    "timestamp",
    [
        "2026-03-01T10:00:00",  # naive: unorderable across timezones
        "not-a-timestamp",
        "2026-13-01T10:00:00+00:00",  # month 13
        12345,
    ],
)
def test_bad_timestamps_are_rejected(timestamp: Any) -> None:
    assert_rejected(statement(timestamp=timestamp))


@pytest.mark.parametrize(
    "score",
    [
        {"raw": 120, "min": 0, "max": 100},  # above max
        {"raw": -5, "min": 0, "max": 100},  # below min
        {"min": 100, "max": 0},  # inverted bounds
        {"scaled": 1.5},  # outside -1..1
        {"scaled": "high"},
    ],
)
def test_malformed_scores_are_rejected(score: dict[str, Any]) -> None:
    assert_rejected(statement(result={"score": score}))


def test_extra_top_level_key_is_rejected() -> None:
    """extra='forbid': a key we do not model is an error, not decoration."""
    assert_rejected(statement(sneaky="value"))


@pytest.mark.parametrize(
    "path",
    [
        ("actor",),
        ("verb",),
        ("object",),
    ],
)
def test_extra_nested_key_is_rejected(path: tuple[str, ...]) -> None:
    payload = statement()
    target = payload
    for key in path:
        target = target[key]
    target["unmodelled"] = "value"
    assert_rejected(payload)


def test_extra_deeply_nested_key_is_rejected() -> None:
    """Strictness has to survive several levels down, not just the surface."""
    payload = statement(
        result={"score": {"raw": 10, "min": 0, "max": 20, "curve": "bell"}}
    )
    assert_rejected(payload)

    payload = statement(
        context={
            "contextActivities": {
                "parent": [{"objectType": "Activity", "id": COURSE, "weight": 1}]
            }
        }
    )
    assert_rejected(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {},  # empty object
        [],  # empty batch
        [statement()],  # batch-shaped, not a statement
        {"statements": [statement()]},  # envelope-shaped
        None,
        "",
    ],
)
def test_empty_and_batch_shaped_payloads_are_rejected(payload: Any) -> None:
    """Batching is a transport concern (M1); a Statement is never a list."""
    assert_rejected(payload)


@pytest.mark.parametrize(
    "actor",
    [
        {"objectType": "Agent", "name": "No Identifier"},  # zero identifiers
        {
            "mbox": "mailto:learner@tinlantern.test",
            "account": {"homePage": "https://lms.tinlantern.test", "name": "s-1"},
        },  # two identifiers: ambiguous attribution
        {"mbox": "learner@tinlantern.test"},  # missing mailto: scheme
    ],
)
def test_ambiguous_or_unidentified_actors_are_rejected(actor: dict[str, Any]) -> None:
    assert_rejected(statement(actor=actor))


@pytest.mark.parametrize(
    "activity_id",
    ["/module/1", "module-1", ""],
)
def test_relative_activity_ids_are_rejected(activity_id: str) -> None:
    """Activity ids are join keys downstream; they must be absolute IRIs."""
    assert_rejected(statement(object={"objectType": "Activity", "id": activity_id}))


@pytest.mark.parametrize("duration", ["4 minutes", "PT", "P", "13S"])
def test_malformed_durations_are_rejected(duration: str) -> None:
    assert_rejected(statement(result={"duration": duration}))


def test_wrong_object_type_is_rejected() -> None:
    payload = statement(object={"objectType": "Agent", "id": COURSE})
    assert_rejected(payload)


def test_score_model_rejects_incoherent_bounds_directly() -> None:
    """The guard lives on Score itself, not only via Statement."""
    with pytest.raises(ValidationError):
        Score.model_validate({"raw": 5, "min": 10, "max": 20})


def test_helper_produces_a_valid_statement() -> None:
    """Guard the test helper: a broken baseline would fake every rejection."""
    baseline = deepcopy(statement())
    assert Statement.model_validate(baseline)
