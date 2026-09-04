"""Load a generated cohort into a running TinLantern API.

This is M1's end-to-end proof: 192k statements through the real HTTP
endpoint, at full scale. Feeding the receiver is the only way to know the
ingestion path works — auditing it otherwise would certify an endpoint
nothing has ever fed.

Development tooling, so it lives under ``data/`` and is never listed in
the deployment manifest. Nothing in the M6 runtime needs to bulk-post to
itself; this exists to get generated data into a local database.

**Re-running is a no-op.** Ingestion is idempotent by statement id, so a
second load re-sends everything and stores nothing. That is what makes it
safely resumable after an interruption: start again from the top rather
than tracking where it stopped.

**Batch size is the blast radius.** Batches are atomic, so one refused
statement rejects its whole batch. The default of 500 keeps a rejection
cheap (500 statements to re-send, ~0.4 MB per request) while holding the
request count sane — 192k statements is 385 requests rather than 192,431.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Statements per request. See the module docstring: this is the blast
#: radius of a single bad statement, not just a throughput knob.
DEFAULT_BATCH_SIZE = 500

#: Post a batch, returning (status code, decoded body).
Poster = Callable[[list[dict[str, Any]]], tuple[int, Any]]


@dataclass(frozen=True, slots=True)
class Rejection:
    """A batch the API refused, and what it said."""

    batch: int
    status: int
    detail: Any


@dataclass(frozen=True, slots=True)
class LoadReport:
    """What a load run did. Printed as evidence, asserted in tests."""

    sent: int
    stored: int
    batches: int
    seconds: float
    rejections: tuple[Rejection, ...] = ()

    @property
    def duplicates(self) -> int:
        """Statements accepted but already held — the re-run case."""
        return self.sent - self.stored - self.refused

    @property
    def refused(self) -> int:
        """Statements in batches the API rejected outright."""
        return sum(_batch_size(r) for r in self.rejections)

    @property
    def per_second(self) -> float:
        return self.sent / self.seconds if self.seconds else 0.0

    @property
    def ok(self) -> bool:
        """A load with any rejection has not succeeded."""
        return not self.rejections

    def summary(self) -> str:
        """One block a human can read and an audit can quote."""
        lines = [
            f"sent        {self.sent:,} in {self.batches:,} batches",
            f"stored      {self.stored:,}",
            f"duplicates  {self.duplicates:,}",
            f"rejected    {self.refused:,}",
            f"elapsed     {self.seconds:.1f}s ({self.per_second:,.0f}/s)",
        ]
        for rejection in self.rejections[:5]:
            lines.append(
                f"  ! batch {rejection.batch} -> {rejection.status}: "
                f"{str(rejection.detail)[:160]}"
            )
        if len(self.rejections) > 5:
            lines.append(f"  ! ...and {len(self.rejections) - 5} more")
        return "\n".join(lines)


def _batch_size(rejection: Rejection) -> int:
    """How many statements a rejected batch carried."""
    return rejection.detail.get("_size", 0) if isinstance(rejection.detail, dict) else 0


def read_statements(path: Path) -> Iterator[dict[str, Any]]:
    """Stream statements from an ndjson file, one per line."""
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def batched(
    statements: Iterable[dict[str, Any]], size: int
) -> Iterator[list[dict[str, Any]]]:
    """Group statements into batches of at most ``size``."""
    if size < 1:
        raise ValueError(f"batch size must be positive, got {size}")
    batch: list[dict[str, Any]] = []
    for statement in statements:
        batch.append(statement)
        if len(batch) == size:
            yield batch
            batch = []
    if batch:
        yield batch


def load_statements(
    statements: Iterable[dict[str, Any]],
    post: Poster,
    count_stored: Callable[[], int],
    batch_size: int = DEFAULT_BATCH_SIZE,
    progress: Callable[[int, int], None] | None = None,
) -> LoadReport:
    """Post statements in batches and report what happened.

    Args:
        statements: The statements to send.
        post: Sends one batch, returning its status code and body.
        count_stored: Reads the current row count from ``raw.statements``.
            The API deliberately does not distinguish stored from
            duplicate — a client that retried does not need telling — so
            the difference is measured at the database instead.
        batch_size: Statements per request, and the blast radius of one
            bad statement.
        progress: Called with (batches done, statements sent).

    Returns:
        Counts, timings, and every rejection encountered.
    """
    before = count_stored()
    started = time.perf_counter()
    sent = 0
    batches = 0
    rejections: list[Rejection] = []

    for batch in batched(statements, batch_size):
        status, body = post(batch)
        batches += 1
        sent += len(batch)
        if status != 200:
            detail = body.get("detail", body) if isinstance(body, dict) else body
            if isinstance(detail, dict):
                detail = {**detail, "_size": len(batch)}
            rejections.append(Rejection(batch=batches, status=status, detail=detail))
        if progress is not None:
            progress(batches, sent)

    seconds = time.perf_counter() - started
    return LoadReport(
        sent=sent,
        stored=count_stored() - before,
        batches=batches,
        seconds=seconds,
        rejections=tuple(rejections),
    )
