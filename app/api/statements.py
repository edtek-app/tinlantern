"""The xAPI ingestion endpoint.

`POST /xapi/statements` accepts one statement or a batch of them.

**Batches are atomic** (xAPI): if any statement in a batch is refused,
none are stored. Partial success would leave the client unable to build a
clean retry — they would have to diff what landed against what they sent —
and would leave `raw.statements` holding a fragment with nothing marking
it as one.

**Rejections outlive the rollback.** Statements are stored in one
transaction that is discarded on failure, but the rejection rows are
written in a separate transaction afterwards. Rolling back the evidence
along with the data would turn a refusal into exactly the silent loss M1's
rejection-path criterion exists to prevent.

Two deliberate deviations from a conformant LRS, both recorded in the
README limitations section:

* An unrecognised property is rejected rather than ignored (`extra="forbid"`).
* A statement without an `id` is refused rather than assigned one. An
  LRS-assigned id cannot be idempotent — the client retries, a second id
  is minted, and the same event is stored twice on precisely the path
  idempotency exists to protect.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Body, HTTPException, status
from pydantic import ValidationError

from app.db import transaction
from app.raw import (
    RejectionReason,
    StoreStatus,
    record_rejection,
    store_statements,
)
from app.xapi import Statement

router = APIRouter(prefix="/xapi", tags=["xapi"])


@dataclass(frozen=True, slots=True)
class Refusal:
    """One statement that will not be stored, and why."""

    index: int
    payload: Any
    reason: RejectionReason
    detail: str
    statement_id: UUID | None = None

    def as_response(self) -> dict[str, Any]:
        """The client-facing view: enough to locate and fix the statement."""
        body: dict[str, Any] = {
            "index": self.index,
            "reason": str(self.reason),
            "detail": self.detail,
        }
        if self.statement_id is not None:
            body["statementId"] = str(self.statement_id)
        return body


def _as_batch(payload: Any) -> list[Any]:
    """Accept a single statement or a list, and normalise to a list."""
    return payload if isinstance(payload, list) else [payload]


def _validate(
    batch: list[Any],
) -> tuple[list[tuple[int, Any, Statement]], list[Refusal]]:
    """Validate every statement, collecting refusals rather than stopping.

    The whole batch is checked even after the first failure, so a client
    fixing a bad upload sees every problem at once instead of discovering
    them one redeploy at a time.
    """
    accepted: list[tuple[int, Any, Statement]] = []
    refused: list[Refusal] = []

    for index, raw in enumerate(batch):
        try:
            statement = Statement.model_validate(raw)
        except ValidationError as error:
            refused.append(
                Refusal(
                    index=index,
                    payload=raw,
                    reason=RejectionReason.INVALID_STATEMENT,
                    detail=_summarise(error),
                    statement_id=_id_if_parseable(raw),
                )
            )
            continue

        if statement.id is None:
            refused.append(
                Refusal(
                    index=index,
                    payload=raw,
                    reason=RejectionReason.INVALID_STATEMENT,
                    detail=(
                        "statement has no id; TinLantern requires a "
                        "client-supplied id because an assigned one cannot be "
                        "idempotent across retries"
                    ),
                )
            )
            continue

        accepted.append((index, raw, statement))

    return accepted, refused


def _summarise(error: ValidationError) -> str:
    """Condense a validation error into one diagnosable line."""
    return "; ".join(
        f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
        for item in error.errors()[:5]
    )


def _id_if_parseable(raw: Any) -> UUID | None:
    """Pull an id out of a payload that failed validation, if it has one."""
    if isinstance(raw, dict):
        try:
            return UUID(str(raw["id"]))
        except (KeyError, ValueError, TypeError):
            return None
    return None


def _persist_refusals(refusals: list[Refusal]) -> None:
    """Record refusals in their own transaction so they survive a rollback."""
    with transaction() as connection:
        for refusal in refusals:
            record_rejection(
                connection,
                refusal.payload if isinstance(refusal.payload, dict) else {},
                refusal.reason,
                f"batch index {refusal.index}: {refusal.detail}",
                statement_id=refusal.statement_id,
            )


@router.post("/statements", status_code=status.HTTP_200_OK)
def post_statements(payload: Annotated[Any, Body()]) -> list[str]:
    """Store one statement or an atomic batch.

    Args:
        payload: A statement object, or a list of them.

    Returns:
        The ids of every statement in the request, in order — the
        conformant xAPI response shape.

    Raises:
        HTTPException: 400 if any statement is invalid or has no id;
            409 if any id is already held with different content. In both
            cases nothing is stored and every refusal is recorded.
    """
    batch = _as_batch(payload)
    if not batch:
        return []

    accepted, refused = _validate(batch)
    if refused:
        _persist_refusals(refused)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": str(RejectionReason.INVALID_STATEMENT),
                "message": (
                    f"{len(refused)} of {len(batch)} statements were refused; "
                    "the batch is atomic, so nothing was stored"
                ),
                "rejections": [refusal.as_response() for refusal in refused],
            },
        )

    statements = [statement for _, _, statement in accepted]
    conflicted: list[tuple[int, Any, Statement]] = []

    with transaction() as connection:
        outcomes = store_statements(connection, statements)
        conflicted = [
            entry
            for entry, outcome in zip(accepted, outcomes, strict=True)
            if outcome.status is StoreStatus.CONFLICT
        ]
        if conflicted:
            # Discard the whole batch. The rejection rows written inside
            # this transaction go with it and are rewritten below, outside
            # it, so the evidence survives.
            connection.rollback()

    if conflicted:
        _persist_refusals(
            [
                Refusal(
                    # The client's payload, not our re-serialisation of it:
                    # a rejection is evidence of what was actually sent.
                    index=index,
                    payload=raw,
                    reason=RejectionReason.ID_CONFLICT,
                    detail=(
                        f"statement id {statement.id} is already held with "
                        "different content"
                    ),
                    statement_id=statement.id,
                )
                for index, raw, statement in conflicted
            ]
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": str(RejectionReason.ID_CONFLICT),
                "message": (
                    f"{len(conflicted)} of {len(batch)} statement ids are "
                    "already held with different content; the batch is "
                    "atomic, so nothing was stored"
                ),
                # A bare 409 on a 10,000-statement batch is undiagnosable.
                "conflicts": [
                    {"index": index, "statementId": str(statement.id)}
                    for index, _, statement in conflicted
                ],
            },
        )

    return [str(statement.id) for _, _, statement in accepted]
