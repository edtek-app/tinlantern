"""Append-only storage for ingested xAPI statements.

`raw.statements` keeps every accepted statement verbatim as JSONB. It is
append-only in the database, not merely by convention: a trigger raises on
UPDATE, DELETE, and TRUNCATE (ADR-0005).

**Storing is idempotent, but never silently lossy.** Re-POSTing an
identical statement succeeds and stores nothing new — that is what a
client retrying a timed-out request needs. Re-using an existing statement
id with *different* content is a different event wearing a taken name, and
is refused and recorded in `raw.rejections` rather than swallowed. Losing
that write would surface much later as numbers that fail to reconcile in
the warehouse, with nothing to explain them.

Payloads are compared canonically — sorted keys, fixed separators — so a
well-behaved client that serialises the same statement with its keys in a
different order is treated as a retry, not as a conflict.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from sqlalchemy import Connection, text

from app.xapi import Statement


#: Machine-readable reasons written to ``raw.rejections.reason``. The
#: ingestion endpoint reuses these, so a rejection means the same thing
#: wherever it came from.
class RejectionReason(StrEnum):
    """Why a statement did not land in ``raw.statements``."""

    ID_CONFLICT = "id_conflict"
    INVALID_STATEMENT = "invalid_statement"


class StoreStatus(StrEnum):
    """What happened to one statement."""

    STORED = "stored"
    #: Byte-identical to a statement already held; the retry succeeded.
    DUPLICATE = "duplicate"
    #: Same id, different content. Refused and recorded.
    CONFLICT = "conflict"


@dataclass(frozen=True, slots=True)
class StoreOutcome:
    """The result of storing one statement."""

    statement_id: UUID
    status: StoreStatus
    ingest_seq: int | None = None


_INSERT = text(
    """
    INSERT INTO raw.statements (statement_id, payload)
    VALUES (:statement_id, CAST(:payload AS JSONB))
    ON CONFLICT (statement_id) DO NOTHING
    RETURNING ingest_seq
    """
)

_SELECT_PAYLOAD = text(
    "SELECT payload FROM raw.statements WHERE statement_id = :statement_id"
)

_INSERT_REJECTION = text(
    """
    INSERT INTO raw.rejections (statement_id, payload, reason, detail)
    VALUES (:statement_id, CAST(:payload AS JSONB), :reason, :detail)
    RETURNING rejection_id
    """
)


def canonical(payload: dict) -> str:
    """Serialise a payload so equal statements compare equal.

    Sorted keys and fixed separators, so a client that serialises the same
    statement with its keys in another order is recognised as retrying
    rather than reported as a conflict.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def wire_payload(statement: Statement) -> dict:
    """Render a statement as the JSON that will be stored."""
    return statement.model_dump(by_alias=True, exclude_none=True, mode="json")


def record_rejection(
    connection: Connection,
    payload: dict,
    reason: RejectionReason,
    detail: str,
    statement_id: UUID | None = None,
) -> int:
    """Record a statement that was refused, keeping the payload verbatim.

    M1 requires that invalid statements are logged, not dropped silently.
    Every rejection path — id conflicts here, validation failures at the
    endpoint — writes through this one function so a rejection means the
    same thing wherever it came from.

    Args:
        connection: An open connection inside a transaction.
        payload: What the client sent, unmodified.
        reason: Machine-readable category.
        detail: Human-readable explanation.
        statement_id: The id, when one could be parsed out.

    Returns:
        The new rejection's id.
    """
    return connection.execute(
        _INSERT_REJECTION,
        {
            "statement_id": statement_id,
            "payload": json.dumps(payload),
            "reason": str(reason),
            "detail": detail,
        },
    ).scalar_one()


def store_statement(connection: Connection, statement: Statement) -> StoreOutcome:
    """Store one statement, idempotently.

    Args:
        connection: An open connection inside a transaction.
        statement: A validated statement. Its ``id`` must be set — the
            caller assigns one if the client did not supply it.

    Returns:
        Whether the statement was stored, was a duplicate, or conflicted.

    Raises:
        ValueError: If the statement has no id.
    """
    if statement.id is None:
        raise ValueError(
            "cannot store a statement without an id; assign one before storing"
        )

    payload = wire_payload(statement)
    ingest_seq = connection.execute(
        _INSERT,
        {"statement_id": statement.id, "payload": json.dumps(payload)},
    ).scalar_one_or_none()

    if ingest_seq is not None:
        return StoreOutcome(statement.id, StoreStatus.STORED, ingest_seq)

    # The id was taken. Only a genuinely different payload is a conflict;
    # an identical re-POST is the client retrying, and succeeds.
    held = connection.execute(
        _SELECT_PAYLOAD, {"statement_id": statement.id}
    ).scalar_one()
    if canonical(held) == canonical(payload):
        return StoreOutcome(statement.id, StoreStatus.DUPLICATE)

    record_rejection(
        connection,
        payload,
        RejectionReason.ID_CONFLICT,
        f"statement id {statement.id} is already held with different content",
        statement_id=statement.id,
    )
    return StoreOutcome(statement.id, StoreStatus.CONFLICT)


def store_statements(
    connection: Connection, statements: Iterable[Statement]
) -> tuple[StoreOutcome, ...]:
    """Store a batch, one outcome per statement, in the order given."""
    return tuple(store_statement(connection, s) for s in statements)


def high_water_mark(connection: Connection) -> int:
    """Return the largest ``ingest_seq`` stored, or 0 when empty.

    M2 pages through new rows above this value. It is ingestion metadata,
    not statement data, and should be treated as an opaque marker.
    """
    return connection.execute(
        text("SELECT COALESCE(MAX(ingest_seq), 0) FROM raw.statements")
    ).scalar_one()
