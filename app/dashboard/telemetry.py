"""Recording what happened on every provider call.

Durable rather than in-process. A counter resets on restart and a demo
restart would discard the only real measurement of how often the LLM
layer misbehaves — the eval harness has 0 malformed responses in 90
question-runs, which is enough to call it rare and far too thin to size
a retry policy against.

**Writing a row must never fail the request it describes.** A metrics
write that takes down an endpoint is worse than a missing metric, so
`record` swallows its own failures and says so rather than propagating.
The one thing it will not do is hide them silently from a developer:
the exception is returned, and the caller may log it.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import Connection, text


class Outcome(StrEnum):
    """What a provider call did.

    Distinguished because they need different responses: a refusal is
    the system working, a fallback is degraded but serving, a retry is
    a transient absorbed, and the last two are faults.
    """

    OK = "ok"
    RETRIED = "retried"
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
    connection: Connection,
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

    Returns:
        None on success, or the exception that stopped it. Returned
        rather than raised: the request this describes has already been
        served, and failing it now to report a metrics problem would
        turn an observability gap into an outage.
    """
    try:
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
