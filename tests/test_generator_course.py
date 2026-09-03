"""Tests for the deterministic course structure.

Two properties matter here. First, every activity the generator emits must
survive the receiver's contract — the generator is a client of `app.xapi`,
so structures it invents have to validate as real statements' objects.
Second, the structure must be a pure function of the configuration: the
demo cohort (M7) and the model evaluation (M3) both depend on being able to
regenerate the same courses from the same file.
"""

from pathlib import Path

import pytest

from app.xapi import Activity
from data.generator.config import CohortConfig, load_config
from data.generator.course import ACTIVITY_TYPES, build_courses

pytestmark = pytest.mark.m0

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "data" / "generator" / "cohort.example.yaml"


@pytest.fixture
def config() -> CohortConfig:
    return load_config(EXAMPLE)


def all_activities(config: CohortConfig) -> list[Activity]:
    return [a for course in build_courses(config) for a in course.activities()]


# --------------------------------------------------------------------------
# The statement contract
# --------------------------------------------------------------------------


def test_every_activity_survives_the_xapi_contract(config: CohortConfig) -> None:
    """Round-trip through the wire format the ingestion endpoint will see."""
    for activity in all_activities(config):
        wire = activity.model_dump(by_alias=True, exclude_none=True, mode="json")
        assert Activity.model_validate(wire) == activity


def test_activity_types_are_the_standard_iris(config: CohortConfig) -> None:
    known = set(ACTIVITY_TYPES.values())
    for activity in all_activities(config):
        assert activity.definition is not None
        assert activity.definition.type in known


def test_activity_iris_are_unique(config: CohortConfig) -> None:
    """A collision would merge two activities into one warehouse row."""
    iris = [a.id for a in all_activities(config)]
    assert len(iris) == len(set(iris))


def test_activity_iris_sit_under_the_configured_base(config: CohortConfig) -> None:
    for activity in all_activities(config):
        assert activity.id.startswith(f"{config.base_iri}/")
        assert "//" not in activity.id.removeprefix("https://")


# --------------------------------------------------------------------------
# Shape matches configuration
# --------------------------------------------------------------------------


def test_structure_matches_the_configured_counts(config: CohortConfig) -> None:
    courses = build_courses(config)
    assert len(courses) == len(config.courses)

    for course, spec in zip(courses, config.courses, strict=True):
        assert course.key == spec.key
        assert len(course.modules) == spec.modules
        for module in course.modules:
            assert len(module.videos) == spec.videos_per_module
            assert len(module.assessments) == spec.assessments_per_module
            assert len(module.assignments) == spec.assignments_per_module


def test_modules_are_numbered_from_one(config: CohortConfig) -> None:
    for course in build_courses(config):
        assert [m.index for m in course.modules] == list(
            range(1, len(course.modules) + 1)
        )


# --------------------------------------------------------------------------
# Determinism
# --------------------------------------------------------------------------


def test_structure_is_a_pure_function_of_config(config: CohortConfig) -> None:
    """Two independent builds from the same config must be identical."""
    assert build_courses(config) == build_courses(load_config(EXAMPLE))


def test_seed_does_not_influence_structure(config: CohortConfig) -> None:
    """The documented boundary: the seed governs behaviour, not the skeleton.

    If this ever fails, someone has made the course tree random — and the
    demo cohort can no longer be described in prose that stays true.
    """
    reseeded = config.model_copy(update={"seed": config.seed + 1})
    assert build_courses(reseeded) == build_courses(config)


def test_changing_the_shape_changes_the_structure(config: CohortConfig) -> None:
    """Guard against a builder that ignores its configuration entirely."""
    fewer = config.model_copy(deep=True)
    fewer.courses[0].modules -= 1
    assert build_courses(fewer) != build_courses(config)
