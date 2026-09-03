"""Validation tests for the cohort generator configuration.

A generator run produces tens of thousands of statements. Every check here
exists so a malformed config fails immediately rather than partway through
that run, or — worse — silently produces a cohort nobody can reproduce.
"""

from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from data.generator.config import ARCHETYPES, CohortConfig, load_config

pytestmark = pytest.mark.m0

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "data" / "generator" / "cohort.example.yaml"


def example_dict() -> dict[str, Any]:
    """The committed example config, as a plain mutable dict."""
    return yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))


def config(**overrides: Any) -> dict[str, Any]:
    payload = example_dict()
    payload.update(overrides)
    return payload


def assert_rejected(payload: dict[str, Any]) -> str:
    with pytest.raises(ValidationError) as caught:
        CohortConfig.model_validate(payload)
    return str(caught.value)


# --------------------------------------------------------------------------
# The committed example must itself be valid
# --------------------------------------------------------------------------


def test_example_config_loads() -> None:
    """A broken example would make every override-based test meaningless."""
    loaded = load_config(EXAMPLE)
    assert loaded.learners == 120
    assert len(loaded.courses) == 2


def test_example_mix_covers_every_archetype() -> None:
    """The default cohort should exercise all six behavioural generators."""
    assert set(load_config(EXAMPLE).archetype_mix) == set(ARCHETYPES)


def test_term_length_is_derived() -> None:
    assert load_config(EXAMPLE).term.days > 100


# --------------------------------------------------------------------------
# Archetype mix
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mix",
    [
        {"thriving": 0.5, "coasting": 0.4},  # sums to 0.9
        {"thriving": 0.5, "coasting": 0.7},  # sums to 1.2
        {},  # sums to 0
    ],
)
def test_mix_must_sum_to_one(mix: dict[str, float]) -> None:
    assert "sum to 1.0" in assert_rejected(config(archetype_mix=mix))


def test_unknown_archetype_is_rejected() -> None:
    message = assert_rejected(config(archetype_mix={"overachiever": 1.0}))
    assert "unknown archetypes" in message


def test_negative_weight_is_rejected() -> None:
    """A negative weight sums to 1.0 just fine; it is still nonsense."""
    mix = {"thriving": 1.5, "coasting": -0.5}
    assert "negative archetype weights" in assert_rejected(config(archetype_mix=mix))


def test_a_mix_of_one_archetype_is_allowed() -> None:
    """Useful for isolating a single behaviour when debugging the model."""
    assert CohortConfig.model_validate(config(archetype_mix={"disengaging": 1.0}))


# --------------------------------------------------------------------------
# Term calendar
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("start", "end"),
    [
        ("2026-05-01", "2026-01-12"),  # inverted
        ("2026-01-12", "2026-01-12"),  # zero length
        ("2026-01-12", "2026-01-20"),  # 8 days: too short to disengage
    ],
)
def test_bad_term_bounds_are_rejected(start: str, end: str) -> None:
    payload = config()
    payload["term"] = {**payload["term"], "start": start, "end": end}
    assert_rejected(payload)


def test_unknown_timezone_is_rejected() -> None:
    payload = config()
    payload["term"] = {**payload["term"], "timezone": "Mars/Olympus_Mons"}
    assert "unknown IANA timezone" in assert_rejected(payload)


# --------------------------------------------------------------------------
# Cohort and course shape
# --------------------------------------------------------------------------


@pytest.mark.parametrize("learners", [0, -1])
def test_cohort_must_have_learners(learners: int) -> None:
    assert_rejected(config(learners=learners))


def test_courses_cannot_be_empty() -> None:
    assert_rejected(config(courses=[]))


def test_duplicate_course_keys_are_rejected() -> None:
    """Duplicate keys would collide in the generated activity IRIs."""
    course = example_dict()["courses"][0]
    assert "duplicate course keys" in assert_rejected(
        config(courses=[course, dict(course)])
    )


def test_module_with_nothing_in_it_is_rejected() -> None:
    course = {
        "key": "empty-101",
        "title": "Nothing Here",
        "modules": 3,
        "videos_per_module": 0,
        "assessments_per_module": 0,
        "assignments_per_module": 0,
    }
    assert "nothing for a learner to do" in assert_rejected(config(courses=[course]))


@pytest.mark.parametrize("base_iri", ["/xapi", "tinlantern", ""])
def test_relative_base_iri_is_rejected(base_iri: str) -> None:
    assert_rejected(config(base_iri=base_iri))


def test_trailing_slash_on_base_iri_is_rejected() -> None:
    """IRIs are built by appending; a trailing slash would double it."""
    message = assert_rejected(config(base_iri="https://tinlantern.example/xapi/"))
    assert "must not end with" in message


def test_unknown_top_level_key_is_rejected() -> None:
    """extra='forbid': a typo'd key is a silent misconfiguration otherwise."""
    assert_rejected(config(learner_count=120))


def test_seed_is_required() -> None:
    """Without a seed a cohort is not reproducible, which is the point."""
    payload = config()
    del payload["seed"]
    assert_rejected(payload)


# --------------------------------------------------------------------------
# Loader
# --------------------------------------------------------------------------


def test_load_config_rejects_a_non_mapping(tmp_path: Path) -> None:
    path = tmp_path / "cohort.yaml"
    path.write_text("- just\n- a list\n", encoding="utf-8")
    with pytest.raises(ValueError, match="must contain a YAML mapping"):
        load_config(path)
