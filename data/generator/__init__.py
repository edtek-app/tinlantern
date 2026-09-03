"""Synthetic xAPI cohort generator (see DESIGN.md).

The sole data source for development and demos; no real learner data ever
enters this repository (ADR-0002). Statements are emitted against the
``app.xapi`` contract — this package is a *client* of that schema and never
its owner (ADR-0001).
"""

from data.generator.config import (
    ARCHETYPES,
    CohortConfig,
    CourseSpec,
    TermSpec,
    load_config,
)
from data.generator.course import CourseStructure, ModuleStructure, build_courses

__all__ = [
    "ARCHETYPES",
    "CohortConfig",
    "CourseSpec",
    "CourseStructure",
    "ModuleStructure",
    "TermSpec",
    "build_courses",
    "load_config",
]
