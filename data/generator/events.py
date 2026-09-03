"""The intermediate form every emitter produces.

An ``Event`` is a statement without its id: what happened, to what, when.
Ids are assigned later, once all of a learner-course's events have been
merged into a single chronological stream (``data.generator.stream``).

That ordering matters. Statement ids key on position in the stream, so
they cannot be assigned by a producer that only sees its own events —
two producers numbering independently would generate identical keys and
therefore identical UUIDs for different statements.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.xapi import VERB_IRIS, Activity, Result, Verb


@dataclass(frozen=True, slots=True)
class Event:
    """A statement-to-be: everything except its id."""

    moment: datetime
    verb: str
    activity: Activity
    result: Result | None = None

    def __post_init__(self) -> None:
        if self.verb not in VERB_IRIS:
            raise ValueError(f"unknown verb {self.verb!r}; known: {sorted(VERB_IRIS)}")
        if self.moment.tzinfo is None:
            raise ValueError("event timestamps must be timezone-aware")


def verb(name: str) -> Verb:
    """Build an xAPI verb with an English display name."""
    return Verb(id=VERB_IRIS[name], display={"en-US": name})
