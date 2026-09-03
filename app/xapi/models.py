"""Hand-authored Pydantic v2 models for the xAPI statement subset.

These models define the contract from the *receiver's* point of view: what
the ingestion endpoint will accept, not merely what the synthetic generator
happens to emit. Every model sets ``extra="forbid"``, so an unrecognised
key — at any nesting level — is a rejection rather than a silent pass.

That strictness is a deliberate subset, not an oversight. A fully
ADL-conformant LRS must accept the entire statement shape, including
properties this platform does not use. TinLantern accepts a narrower
contract and rejects the rest loudly; see ADR-0001 and the README
limitations section.

No third-party xAPI library is used (ADR-0001).
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

# An absolute IRI: a scheme followed by a colon. Relative references are
# rejected — an activity id must be globally resolvable to be a join key.
IRI = Annotated[
    str,
    StringConstraints(min_length=1, pattern=r"^[A-Za-z][A-Za-z0-9+.\-]*:.+$"),
]

# RFC 5646 language tag, loosely: "en", "en-US", "zh-Hant-TW".
LanguageTag = Annotated[
    str,
    StringConstraints(pattern=r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{1,8})*$"),
]

LanguageMap = dict[LanguageTag, str]

# ISO 8601 duration, e.g. "PT4M13S". Requires at least one component, and a
# "T" only when time components follow it. Checked with Python's `re` rather
# than a StringConstraints pattern: pydantic-core's Rust regex engine has no
# look-around, which these two rules need.
_DURATION_RE = re.compile(
    r"^P(?!$)(\d+Y)?(\d+M)?(\d+W)?(\d+D)?(T(?=\d)(\d+H)?(\d+M)?(\d+(\.\d+)?S)?)?$"
)


def _validate_duration(value: str) -> str:
    """Reject anything that is not a well-formed ISO 8601 duration."""
    if not _DURATION_RE.match(value):
        raise ValueError(f"not an ISO 8601 duration: {value!r}")
    return value


Duration = Annotated[str, AfterValidator(_validate_duration)]

Mbox = Annotated[str, StringConstraints(pattern=r"^mailto:[^@\s]+@[^@\s]+\.[^@\s]+$")]


def _reject_numeric_timestamp(value: Any) -> Any:
    """Refuse a bare number where an ISO 8601 timestamp belongs.

    Pydantic would otherwise read ``12345`` as a Unix epoch and hand back a
    timezone-aware 1970 datetime. xAPI timestamps are ISO 8601 strings; a
    receiver that invents a date from a stray integer corrupts the warehouse
    silently. Strings and ``datetime`` objects both still pass, so callers
    can construct statements naturally.
    """
    # bool is an int subclass, so `True` is caught here too.
    if isinstance(value, int | float):
        raise ValueError(
            "timestamp must be an ISO 8601 string with a UTC offset, not a number"
        )
    return value


#: Timezone-aware only. A naive timestamp cannot be ordered against
#: statements from another timezone, and every downstream analytic —
#: engagement recency, pacing, deadline clustering — depends on ordering.
Timestamp = Annotated[AwareDatetime, BeforeValidator(_reject_numeric_timestamp)]

#: The verbs TinLantern emits and accepts (data/generator/DESIGN.md).
#: A statement carrying any other verb IRI is rejected: an unrecognised verb
#: would land in the warehouse as an event no downstream job knows how to
#: interpret.
VERB_IRIS: dict[str, str] = {
    "initialized": "http://adlnet.gov/expapi/verbs/initialized",
    "experienced": "http://adlnet.gov/expapi/verbs/experienced",
    "played": "https://w3id.org/xapi/video/verbs/played",
    "paused": "https://w3id.org/xapi/video/verbs/paused",
    "completed": "http://adlnet.gov/expapi/verbs/completed",
    "attempted": "http://adlnet.gov/expapi/verbs/attempted",
    "answered": "http://adlnet.gov/expapi/verbs/answered",
    "passed": "http://adlnet.gov/expapi/verbs/passed",
    "failed": "http://adlnet.gov/expapi/verbs/failed",
    "submitted": "http://activitystrea.ms/schema/1.0/submit",
}

KNOWN_VERB_IRIS: frozenset[str] = frozenset(VERB_IRIS.values())


class _Strict(BaseModel):
    """Base for every xAPI model: unknown keys are an error, not noise."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Account(_Strict):
    """An actor identified by an account on some system (e.g. an LMS)."""

    homePage: IRI
    name: str = Field(min_length=1)


class Agent(_Strict):
    """The learner a statement is about.

    xAPI requires exactly one inverse-functional identifier. TinLantern
    supports two of them — ``mbox`` and ``account`` — and requires precisely
    one, so a statement can never be ambiguously attributed.
    """

    objectType: Literal["Agent"] = "Agent"
    name: str | None = Field(default=None, min_length=1)
    mbox: Mbox | None = None
    account: Account | None = None

    @model_validator(mode="after")
    def _exactly_one_identifier(self) -> Agent:
        identifiers = [self.mbox, self.account]
        provided = [value for value in identifiers if value is not None]
        if len(provided) != 1:
            raise ValueError(
                "an Agent needs exactly one identifier (mbox or account); "
                f"got {len(provided)}"
            )
        return self


class Verb(_Strict):
    """What the learner did. Restricted to the verbs this platform models."""

    id: IRI
    display: LanguageMap | None = None

    @model_validator(mode="after")
    def _verb_is_known(self) -> Verb:
        if self.id not in KNOWN_VERB_IRIS:
            raise ValueError(f"unsupported verb IRI: {self.id}")
        return self


class ActivityDefinition(_Strict):
    """Human- and machine-readable description of an activity."""

    name: LanguageMap | None = None
    description: LanguageMap | None = None
    type: IRI | None = None


class Activity(_Strict):
    """What the learner acted on: a module, video, assessment, assignment."""

    objectType: Literal["Activity"] = "Activity"
    id: IRI
    definition: ActivityDefinition | None = None


class Score(_Strict):
    """An assessment score. Bounds are checked, not merely carried."""

    scaled: float | None = Field(default=None, ge=-1.0, le=1.0)
    raw: float | None = None
    min: float | None = None
    max: float | None = None

    @model_validator(mode="after")
    def _bounds_are_coherent(self) -> Score:
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError(f"score.min ({self.min}) exceeds score.max ({self.max})")
        if self.raw is not None:
            if self.min is not None and self.raw < self.min:
                raise ValueError(f"score.raw ({self.raw}) is below score.min")
            if self.max is not None and self.raw > self.max:
                raise ValueError(f"score.raw ({self.raw}) is above score.max")
        return self


class Result(_Strict):
    """The outcome of an attempt: score, success, completion, duration."""

    score: Score | None = None
    success: bool | None = None
    completion: bool | None = None
    duration: Duration | None = None
    response: str | None = None


class ContextActivities(_Strict):
    """Activities situating the statement — its course, module, grouping."""

    parent: list[Activity] | None = None
    grouping: list[Activity] | None = None
    category: list[Activity] | None = None
    other: list[Activity] | None = None


class Context(_Strict):
    """Where the event happened: course, attempt registration, platform."""

    registration: UUID | None = None
    platform: str | None = Field(default=None, min_length=1)
    language: LanguageTag | None = None
    contextActivities: ContextActivities | None = None


class Statement(_Strict):
    """A single xAPI statement: actor did verb to object.

    ``object_`` is spelled with a trailing underscore in Python because
    ``object`` is a builtin; the wire format uses ``object``. Construct with
    the wire name (``Statement(object=...)``) and serialise with
    ``model_dump(by_alias=True)`` to get valid xAPI back out.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: UUID | None = None
    actor: Agent
    verb: Verb
    object_: Activity = Field(alias="object")
    result: Result | None = None
    context: Context | None = None
    timestamp: Timestamp | None = None
