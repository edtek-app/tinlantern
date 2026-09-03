"""Synthetic xAPI cohort generator (see DESIGN.md).

The sole data source for development and demos; no real learner data ever
enters this repository (ADR-0002). Statements are emitted against the
``app.xapi`` contract — this package is a *client* of that schema and never
its owner (ADR-0001).
"""

from data.generator.calendar import (
    CourseSchedule,
    Deadline,
    build_schedule,
    term_bounds,
)
from data.generator.config import (
    ARCHETYPES,
    CohortConfig,
    CourseSpec,
    TermSpec,
    load_config,
)
from data.generator.course import CourseStructure, ModuleStructure, build_courses
from data.generator.rng import STREAMS, derive_seed, stream_rng

__all__ = [
    "ARCHETYPES",
    "STREAMS",
    "CohortConfig",
    "CourseSchedule",
    "CourseSpec",
    "CourseStructure",
    "Deadline",
    "ModuleStructure",
    "TermSpec",
    "build_courses",
    "build_schedule",
    "derive_seed",
    "load_config",
    "stream_rng",
    "term_bounds",
]
