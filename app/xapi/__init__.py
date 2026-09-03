"""xAPI statement contract.

This package owns the statement schema. `data/generator/` is a *client* of
this contract and imports from here; the dependency never runs the other
way. See ADR-0001.
"""

from app.xapi.models import (
    VERB_IRIS,
    Account,
    Activity,
    ActivityDefinition,
    Agent,
    Context,
    ContextActivities,
    Result,
    Score,
    Statement,
    Verb,
)

__all__ = [
    "VERB_IRIS",
    "Account",
    "Activity",
    "ActivityDefinition",
    "Agent",
    "Context",
    "ContextActivities",
    "Result",
    "Score",
    "Statement",
    "Verb",
]
