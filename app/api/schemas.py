"""The wire contract.

The dashboard's dataclasses in `app/dashboard/` are the internal shape;
these are what the API promises. Kept separate because they change for
different reasons — an internal field can be renamed freely, a wire
field cannot without a client changing with it.

**Every endpoint declares one.** Before these existed the endpoints
returned bare `dict`, so FastAPI's generated OpenAPI described every
response as `object`: the schema existed and documented nothing. These
also drive `tools/generate_api_types.py`, which is why they stay narrow
and use only constructs a TypeScript interface can express.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel


class Bin(BaseModel):
    """One bar of the risk distribution."""

    label: str
    lower: float
    upper: float
    learners: int


class CohortOverview(BaseModel):
    """The cohort screen's payload."""

    model_version: str
    window_close: date
    threshold: float
    learners: int
    alerted: int
    distribution: list[Bin]


class EngagementPoint(BaseModel):
    """Active learners in one ISO week. Engagement, never risk."""

    week_start: date
    active_learners: int
    events: int


class EngagementTrend(BaseModel):
    """The engagement series.

    Wrapped in an object rather than returned as a bare array so the
    field name travels with it: a client destructuring
    `engagement_trend` cannot silently start rendering it as a risk
    series (ADR-0008's labelling discipline).
    """

    engagement_trend: list[EngagementPoint]


class RankedLearner(BaseModel):
    """One row of the ranked learner list."""

    learner_identifier: str
    risk: float
    alerted: bool


class LearnerRanking(BaseModel):
    """The ranked list, with the cohort total beside it.

    ``total`` is present so a client can say "top 50 of 120" rather than
    implying the list is everything.
    """

    model_version: str
    learners: list[RankedLearner]
    total: int


class Driver(BaseModel):
    """One contribution to a learner's risk."""

    feature: str
    contribution: float


class LearnerDetail(BaseModel):
    """One learner's current score and what moves it."""

    learner_identifier: str
    risk: float
    alerted: bool
    threshold: float
    window_close: date
    model_version: str
    drivers: list[Driver]
    caveat: str
    additive: bool


class LearnerSummary(BaseModel):
    """A stored advisor summary, with the provenance a reader needs."""

    learner_identifier: str
    window_close: date
    model_version: str
    summary: str
    from_model: bool
    fallback_reason: str | None
    synthetic: bool


class Question(BaseModel):
    """A natural-language question about the cohort."""

    question: str


class Answer(BaseModel):
    """An answer, or a refusal, with what it rests on.

    ``citations`` maps a row label to the row it names, and ``sql`` is
    the statement that produced them. A citation panel renders these —
    never an expected column set, which varies between runs while the
    answer does not (ADR-0008's coupling finding).
    """

    answered: bool
    text: str
    refusal_reason: str | None
    citations: dict[str, dict]
    sql: str | None
    problems: list[str]
