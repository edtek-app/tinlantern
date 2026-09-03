"""Configuration schema for the synthetic cohort generator.

Every run is parameterised by an explicit seed and a YAML file, so a cohort
is reproducible from its configuration alone (DESIGN.md). The models below
validate that configuration up front — a malformed mix or an inverted term
should fail before a single statement is generated, not halfway through a
120-learner run.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.xapi.models import IRI

#: Behavioural generators, not labels. Risk must emerge from behaviour so
#: the M3 model learns patterns rather than a leaked target (DESIGN.md).
ARCHETYPES: tuple[str, ...] = (
    "thriving",
    "coasting",
    "procrastinator",
    "disengaging",
    "struggling",
    "recovering",
)

# Archetype weights are authored by hand in YAML, so their sum is compared
# with a tolerance rather than for exact float equality.
_MIX_TOLERANCE = 1e-6

# A term shorter than this cannot express the behaviours the archetypes
# describe — a disengagement curve needs weeks, not days.
_MIN_TERM_DAYS = 14


class _Strict(BaseModel):
    """Base for config models: an unknown key is a typo, not a comment."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CourseSpec(_Strict):
    """Shape of one course: how many modules, and what is in each."""

    key: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    title: str = Field(min_length=1)
    modules: int = Field(ge=1, le=52)
    videos_per_module: int = Field(default=0, ge=0, le=50)
    assessments_per_module: int = Field(default=0, ge=0, le=50)
    assignments_per_module: int = Field(default=0, ge=0, le=50)

    @model_validator(mode="after")
    def _modules_contain_something(self) -> CourseSpec:
        per_module = (
            self.videos_per_module
            + self.assessments_per_module
            + self.assignments_per_module
        )
        if per_module == 0:
            raise ValueError(
                f"course {self.key!r} has modules with no videos, assessments, "
                "or assignments — there would be nothing for a learner to do"
            )
        return self


class TermSpec(_Strict):
    """The academic calendar the cohort's activity is spread across."""

    start: date
    end: date
    timezone: str = "UTC"

    @field_validator("timezone")
    @classmethod
    def _timezone_is_resolvable(cls, value: str) -> str:
        """Reject a timezone the standard library cannot resolve."""
        try:
            ZoneInfo(value)
        except Exception as error:  # ZoneInfoNotFoundError, ValueError, OSError
            raise ValueError(f"unknown IANA timezone: {value!r}") from error
        return value

    @model_validator(mode="after")
    def _term_runs_forwards_and_long_enough(self) -> TermSpec:
        if self.end <= self.start:
            raise ValueError(f"term end ({self.end}) must fall after start")
        if (self.end - self.start).days < _MIN_TERM_DAYS:
            raise ValueError(
                f"term is shorter than {_MIN_TERM_DAYS} days; archetype "
                "behaviour cannot develop over that span"
            )
        return self

    @property
    def days(self) -> int:
        """Length of the term in days."""
        return (self.end - self.start).days


class ActivitySpec(_Strict):
    """How much activity a fully-engaged learner generates.

    Engagement curves are relative (0–1); this sets the absolute scale.
    It lives in configuration because it decides dataset size, generation
    runtime, and how much signal the M3 model has to work with — all
    contestable, so all visible.
    """

    #: Sessions per week for a learner at full engagement. An archetype's
    #: curve scales this down across the term.
    sessions_per_week: float = Field(gt=0.0, le=100.0)
    min_events_per_session: int = Field(ge=1, le=200)
    max_events_per_session: int = Field(ge=1, le=200)
    #: How readily engagement converts into turning work in. A learner
    #: attempts a scheduled item with probability
    #: ``min(1, engagement_intensity * submission_diligence)``.
    #:
    #: This is the third-most contestable number in the dataset: missing
    #: work is the loudest early-alert signal there is, and this constant
    #: decides how much of it exists. Calibrated to 2.4 against archetype
    #: intent: thriving and coasting learners submit essentially
    #: everything, while a disengaging learner (intensity ~0.06 by term
    #: end) misses roughly a quarter of the term's work. Lower it and
    #: coasting learners start failing, which contradicts their
    #: "middling scores"; raise it and disengagement stops being visible
    #: in the gradebook at all. See ADR-0004.
    submission_diligence: float = Field(gt=0.0, le=10.0)

    @model_validator(mode="after")
    def _event_range_is_ordered(self) -> ActivitySpec:
        if self.min_events_per_session > self.max_events_per_session:
            raise ValueError(
                f"min_events_per_session ({self.min_events_per_session}) exceeds "
                f"max_events_per_session ({self.max_events_per_session})"
            )
        return self


class RiskSpec(_Strict):
    """What counts as an at-risk outcome when deriving ground truth.

    The outcome in the evaluation sidecar is *measured* from the record a
    learner generates, never assigned alongside their archetype (ADR-0004).
    These are the thresholds that measurement uses. They live in
    configuration rather than in code so "at risk" stays a visible, tunable
    decision that a reader can disagree with.

    Consumed by the ground-truth sidecar; declared here so the definition
    is versioned with the cohort it describes.
    """

    #: Mean scaled score below which a learner's term counts as failing.
    pass_threshold: float = Field(ge=0.0, le=1.0)
    #: An inactivity gap at least this long counts as engagement collapse.
    collapse_gap_days: int = Field(ge=1, le=365)
    #: How much of the term M3 may use as features. The outcome is measured
    #: over the WHOLE term, so a model given the whole term could simply
    #: recompute the label and score perfectly while predicting nothing.
    #: "How early is early" is a product decision, so it is configured
    #: rather than buried in the modelling code. See ADR-0004.
    feature_window_weeks: int = Field(ge=1, le=52)


class CohortConfig(_Strict):
    """A complete, reproducible description of a synthetic cohort."""

    seed: int = Field(ge=0)
    learners: int = Field(ge=1, le=100_000)
    base_iri: IRI
    term: TermSpec
    courses: list[CourseSpec] = Field(min_length=1)
    archetype_mix: dict[str, float]
    activity: ActivitySpec
    risk: RiskSpec

    @field_validator("base_iri")
    @classmethod
    def _no_trailing_slash(cls, value: str) -> str:
        """Activity IRIs are built by appending; a trailing slash doubles."""
        if value.endswith("/"):
            raise ValueError(f"base_iri must not end with '/': {value!r}")
        return value

    @field_validator("courses")
    @classmethod
    def _course_keys_are_unique(cls, value: list[CourseSpec]) -> list[CourseSpec]:
        """Duplicate keys would collide in the generated activity IRIs."""
        keys = [course.key for course in value]
        duplicates = sorted({key for key in keys if keys.count(key) > 1})
        if duplicates:
            raise ValueError(f"duplicate course keys: {duplicates}")
        return value

    @field_validator("archetype_mix")
    @classmethod
    def _mix_is_a_distribution(cls, value: dict[str, float]) -> dict[str, float]:
        """Weights must name known archetypes and form a distribution."""
        unknown = sorted(set(value) - set(ARCHETYPES))
        if unknown:
            raise ValueError(
                f"unknown archetypes: {unknown}; known: {sorted(ARCHETYPES)}"
            )
        negative = sorted(name for name, weight in value.items() if weight < 0)
        if negative:
            raise ValueError(f"negative archetype weights: {negative}")
        total = sum(value.values())
        if abs(total - 1.0) > _MIX_TOLERANCE:
            raise ValueError(f"archetype_mix must sum to 1.0; got {total}")
        return value


def load_config(path: str | Path) -> CohortConfig:
    """Load and validate a cohort configuration from a YAML file.

    Args:
        path: Path to the YAML configuration.

    Returns:
        The validated configuration.

    Raises:
        ValueError: If the file does not contain a YAML mapping.
        pydantic.ValidationError: If the configuration is invalid.
    """
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(
            f"{path} must contain a YAML mapping, got {type(raw).__name__}"
        )
    return CohortConfig.model_validate(raw)
