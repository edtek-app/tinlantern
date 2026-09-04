"""Synthetic xAPI cohort generator (see DESIGN.md).

The sole data source for development and demos; no real learner data ever
enters this repository (ADR-0002). Statements are emitted against the
``app.xapi`` contract — this package is a *client* of that schema and never
its owner (ADR-0001).
"""

from data.generator.archetypes import PROFILES, ArchetypeProfile, profile
from data.generator.assess import ASSESSMENT_VERBS, assessment_events
from data.generator.calendar import (
    CourseSchedule,
    Deadline,
    build_schedule,
    term_bounds,
)
from data.generator.clock import sample_before_deadline, sample_in_term
from data.generator.config import (
    ARCHETYPES,
    CohortConfig,
    CourseSpec,
    TermSpec,
    load_config,
)
from data.generator.course import CourseStructure, ModuleStructure, build_courses
from data.generator.emit import (
    SESSION_VERBS,
    SessionWindow,
    content_events,
    session_windows,
)
from data.generator.enroll import ENROLLMENT_VERBS, enrollment_event
from data.generator.events import Event
from data.generator.rng import STREAMS, derive_seed, stream_rng
from data.generator.roster import Learner, build_roster, realized_mix
from data.generator.stream import (
    learner_course_statements,
    learner_statements,
    registration_id,
    statement_id,
    to_statements,
)
from data.generator.truth import (
    GRADED_VERBS,
    GroundTruth,
    at_risk_rate,
    derive_cohort_truth,
    derive_ground_truth,
)
from data.generator.writer import WriteResult, write_cohort

__all__ = [
    "ARCHETYPES",
    "ASSESSMENT_VERBS",
    "ENROLLMENT_VERBS",
    "PROFILES",
    "SESSION_VERBS",
    "STREAMS",
    "ArchetypeProfile",
    "CohortConfig",
    "CourseSchedule",
    "CourseSpec",
    "CourseStructure",
    "Deadline",
    "GRADED_VERBS",
    "Event",
    "GroundTruth",
    "Learner",
    "ModuleStructure",
    "SessionWindow",
    "TermSpec",
    "WriteResult",
    "assessment_events",
    "at_risk_rate",
    "build_courses",
    "build_roster",
    "build_schedule",
    "content_events",
    "derive_cohort_truth",
    "derive_ground_truth",
    "derive_seed",
    "enrollment_event",
    "learner_course_statements",
    "learner_statements",
    "load_config",
    "profile",
    "realized_mix",
    "registration_id",
    "sample_before_deadline",
    "sample_in_term",
    "session_windows",
    "statement_id",
    "stream_rng",
    "term_bounds",
    "write_cohort",
    "to_statements",
]
