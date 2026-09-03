"""Deterministic course structure built from a cohort configuration.

The structure is a **pure function of the configuration**. The RNG seed
does not reach it: two configs that differ only in `seed` produce identical
courses. Seeded randomness enters later, with learner behaviour — where the
variation actually belongs. Keeping the skeleton fixed means a demo cohort
can be described in prose ("two courses, twelve modules each") and stay
true across regenerations.

Activities are emitted as `app.xapi.Activity` instances: the generator is a
client of the statement contract, never its owner (ADR-0001).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.xapi import Activity, ActivityDefinition
from data.generator.config import CohortConfig, CourseSpec

#: Standard xAPI activity type IRIs for the shapes this platform models.
ACTIVITY_TYPES: dict[str, str] = {
    "course": "http://adlnet.gov/expapi/activities/course",
    "module": "http://adlnet.gov/expapi/activities/module",
    "video": "https://w3id.org/xapi/video/activity-type/video",
    "assessment": "http://adlnet.gov/expapi/activities/assessment",
    "assignment": "http://adlnet.gov/expapi/activities/assignment",
}


def _activity(iri: str, name: str, kind: str) -> Activity:
    """Build a typed xAPI Activity with an English display name."""
    return Activity(
        id=iri,
        definition=ActivityDefinition(
            name={"en-US": name},
            type=ACTIVITY_TYPES[kind],
        ),
    )


@dataclass(frozen=True, slots=True)
class ModuleStructure:
    """One module and everything a learner can act on inside it."""

    index: int
    activity: Activity
    videos: tuple[Activity, ...]
    assessments: tuple[Activity, ...]
    assignments: tuple[Activity, ...]

    def activities(self) -> tuple[Activity, ...]:
        """Every activity in this module, the module itself included."""
        return (self.activity, *self.videos, *self.assessments, *self.assignments)


@dataclass(frozen=True, slots=True)
class CourseStructure:
    """A course and its ordered modules."""

    key: str
    activity: Activity
    modules: tuple[ModuleStructure, ...]

    def activities(self) -> tuple[Activity, ...]:
        """Every activity in this course, the course itself included."""
        nested = tuple(a for module in self.modules for a in module.activities())
        return (self.activity, *nested)


def build_course(base_iri: str, spec: CourseSpec) -> CourseStructure:
    """Build one course's activity tree.

    Args:
        base_iri: Absolute IRI prefix, without a trailing slash.
        spec: The course's configured shape.

    Returns:
        The course structure, with 1-indexed module and item numbering.
    """
    course_iri = f"{base_iri}/course/{spec.key}"
    modules: list[ModuleStructure] = []

    for index in range(1, spec.modules + 1):
        module_iri = f"{course_iri}/module/{index}"
        modules.append(
            ModuleStructure(
                index=index,
                activity=_activity(
                    module_iri, f"{spec.title} — Module {index}", "module"
                ),
                videos=_items(module_iri, index, spec.videos_per_module, "video"),
                assessments=_items(
                    module_iri, index, spec.assessments_per_module, "assessment"
                ),
                assignments=_items(
                    module_iri, index, spec.assignments_per_module, "assignment"
                ),
            )
        )

    return CourseStructure(
        key=spec.key,
        activity=_activity(course_iri, spec.title, "course"),
        modules=tuple(modules),
    )


def _items(
    module_iri: str, module_index: int, count: int, kind: str
) -> tuple[Activity, ...]:
    """Build the numbered items of one kind within a module."""
    return tuple(
        _activity(
            f"{module_iri}/{kind}/{number}",
            f"Module {module_index} {kind.capitalize()} {number}",
            kind,
        )
        for number in range(1, count + 1)
    )


def build_courses(config: CohortConfig) -> tuple[CourseStructure, ...]:
    """Build every course described by a cohort configuration."""
    return tuple(build_course(config.base_iri, spec) for spec in config.courses)
