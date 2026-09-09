"""Recording what happened on every provider call.

Durable rather than in-process. A counter resets on restart and a demo
restart would discard the only real measurement of how often the LLM
layer misbehaves — the eval harness has 0 malformed responses in 90
question-runs, which is enough to call it rare and far too thin to size
a retry policy against.

**Writing a row must never fail the request it describes.** A metrics
write that takes down an endpoint is worse than a missing metric, so
`record` swallows its own failures rather than propagating them.

**It writes in its OWN transaction**, following the precedent M1 set for
rejections: the record must outlive the constraints of the thing it
describes. The Q&A path proved why — a generated query sets
`transaction_read_only` for the remainder of the caller's transaction,
so every telemetry write after one silently failed. Two correct
decisions (a read-only boundary; metrics that never break a request)
combined into a hole, and the swallowing is what hid it.

**A swallowed failure is still logged.** Silence is right for one
transient and wrong for a persistent fault: a metrics write that fails
every time is a misconfiguration, and it currently looks identical to a
system with nothing to report.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import Connection, text

from app.db import transaction

logger = logging.getLogger(__name__)


class Outcome(StrEnum):
    """What a provider call did.

    Distinguished because they need different responses: a refusal is
    the system working, a fallback is degraded but serving, a retry is
    a transient absorbed, and the last two are faults.
    """

    OK = "ok"
    RETRIED = "retried"
    #: Something the endpoint could not classify. Recorded before it is
    #: re-raised, because an unclassified failure leaving no trace means
    #: the table shows a healthy system while every request 500s — the
    #: same silent hole as the read-only transaction bug, arriving from
    #: the other direction.
    UNCLASSIFIED = "unclassified"
    MALFORMED = "malformed"
    REFUSED = "refused"
    FALLBACK = "fallback"
    TRANSPORT_ERROR = "transport_error"


_INSERT = text(
    """
    INSERT INTO warehouse.llm_call
        (operation, outcome, provider, model, latency_ms, model_version, detail)
    VALUES (:operation, :outcome, :provider, :model, :latency_ms,
            :model_version, :detail)
    """
)


def record(
    *,
    operation: str,
    outcome: Outcome,
    provider: str,
    model: str,
    latency_ms: int,
    model_version: str | None = None,
    detail: str | None = None,
) -> Exception | None:
    """Write one call's outcome.

    Opens its own transaction. The caller's may be read-only — a
    generated query makes it so for its remainder — and a record that
    cannot survive the constraints of what it describes is not a record.

    Returns:
        None on success, or the exception that stopped it. Returned
        rather than raised: the request this describes has already been
        served, and failing it now to report a metrics problem would
        turn an observability gap into an outage. It is also LOGGED,
        because a write that fails every time is a misconfiguration and
        must not look like a system with nothing to report.
    """
    try:
        with transaction() as connection:
            connection.execute(
                _INSERT,
                {
                    "operation": operation,
                    "outcome": str(outcome),
                    "provider": provider,
                    "model": model,
                    "latency_ms": latency_ms,
                    "model_version": model_version,
                    "detail": detail[:2000] if detail else None,
                },
            )
    except Exception as failure:  # noqa: BLE001 - deliberately broad, see above
        logger.error(
            "telemetry write failed for %s/%s: %s. The request was served; "
            "this row is lost. If this repeats, it is a misconfiguration "
            "rather than a transient, and the table will look like a "
            "system with nothing to report.",
            operation,
            outcome,
            failure,
        )
        return failure
    return None


@dataclass
class Timer:
    """Elapsed milliseconds for one call."""

    started: float

    @property
    def elapsed_ms(self) -> int:
        return int((time.monotonic() - self.started) * 1000)


@contextmanager
def timed() -> Iterator[Timer]:
    """Time a block, whether it succeeds or raises."""
    timer = Timer(started=time.monotonic())
    yield timer


def outcome_counts(connection: Connection) -> dict[str, int]:
    """How many calls of each outcome have been recorded.

    The rate a retry policy is sized against. Derived from the rows
    actually written rather than from a counter, so it cannot drift
    from what happened.
    """
    rows = connection.execute(
        text("SELECT outcome, count(*) FROM warehouse.llm_call GROUP BY outcome")
    )
    return {outcome: count for outcome, count in rows}
