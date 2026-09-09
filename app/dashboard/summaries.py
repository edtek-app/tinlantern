"""Storing and reading advisor summaries.

**The scoring job writes; the web tier reads.** A summary is two model
calls and 5-15 seconds — generating one per page view would make the
drill-down unusable and bill for every refresh. So M6's deployment story
is "scoring writes summaries", not "the web tier calls a language model
on every request", and this module has a clean write path and a clean
read path with nothing that does both.

Keyed by `(student, window_close, model_version)` — exactly the key
`risk_score` uses — so a summary **cannot outlive the score it
describes**. Re-score under a new model and the key changes with it;
staleness is impossible by construction rather than by a cache policy
someone has to maintain.

A missing summary is reported as missing. It is not generated inline and
it is not rendered as an empty one: the same stance the unscored cohort
takes, for the same reason — absence and emptiness read identically to
whoever is looking at the screen, and only one of them is true.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import Connection, text

from app.dashboard.facts import facts_for
from app.dashboard.queries import LearnerDetail
from app.dashboard.telemetry import Outcome, record, timed
from app.llm.client import Provider
from app.llm.summary import AdvisorSummary, summarise


@dataclass(frozen=True, slots=True)
class StoredSummary:
    """A summary as the dashboard reads it back.

    Carries its provenance, because a template summary and a model
    summary read alike and only one of them was written by a model.
    """

    learner_identifier: str
    window_close: date
    model_version: str
    summary: str
    from_model: bool
    fallback_reason: str | None
    synthetic: bool


_UPSERT = text(
    """
    INSERT INTO warehouse.learner_summary
        (student_key, window_close, model_version, summary, from_model,
         fallback_reason, synthetic)
    SELECT s.student_key, :window_close, :model_version, :summary,
           :from_model, :fallback_reason, :synthetic
    FROM warehouse.dim_student s
    WHERE s.learner_identifier = :learner
    ON CONFLICT (student_key, window_close, model_version) DO UPDATE SET
        summary = EXCLUDED.summary,
        from_model = EXCLUDED.from_model,
        fallback_reason = EXCLUDED.fallback_reason,
        synthetic = EXCLUDED.synthetic,
        generated_at = now()
    """
)

_READ = text(
    """
    SELECT s.learner_identifier, ls.window_close, ls.model_version,
           ls.summary, ls.from_model, ls.fallback_reason, ls.synthetic
    FROM warehouse.learner_summary ls
    JOIN warehouse.dim_student s USING (student_key)
    WHERE s.learner_identifier = :learner
      AND ls.model_version = :model_version
    ORDER BY ls.window_close DESC
    LIMIT 1
    """
)


def store(
    connection: Connection, detail: LearnerDetail, result: AdvisorSummary
) -> None:
    """Write one learner's summary, replacing any for the same key."""
    connection.execute(
        _UPSERT,
        {
            "learner": detail.learner_identifier,
            "window_close": detail.window_close,
            "model_version": detail.model_version,
            "summary": result.text,
            "from_model": result.from_model,
            "fallback_reason": (
                str(result.fallback_reason) if result.fallback_reason else None
            ),
            "synthetic": result.synthetic,
        },
    )


def read(
    connection: Connection, learner: str, model_version: str
) -> StoredSummary | None:
    """The stored summary for a learner under one model version.

    Returns:
        The summary, or None when none was generated. None means
        missing, never empty.
    """
    row = connection.execute(
        _READ, {"learner": learner, "model_version": model_version}
    ).fetchone()
    if row is None:
        return None
    return StoredSummary(
        learner_identifier=row.learner_identifier,
        window_close=row.window_close.date(),
        model_version=row.model_version,
        summary=row.summary,
        from_model=row.from_model,
        fallback_reason=row.fallback_reason,
        synthetic=row.synthetic,
    )


def _detail(result: AdvisorSummary) -> str | None:
    """The reason AND what it objected to, for the telemetry row."""
    if result.from_model:
        return None
    reason = str(result.fallback_reason) if result.fallback_reason else "fallback"
    if not result.problems:
        return reason
    return f"{reason}: " + " | ".join(result.problems)


def generate(
    connection: Connection, detail: LearnerDetail, provider: Provider
) -> AdvisorSummary:
    """Produce and store one summary, recording what the call did.

    Called by the scoring job, never by a request. The outcome is
    recorded whether the model wrote the summary or the template did —
    a fallback rate nobody can see is the failure mode `fallback_rate`
    exists to prevent.
    """
    with timed() as timer:
        result = summarise(facts_for(connection, detail), provider)

    store(connection, detail, result)
    record(
        operation="summary",
        outcome=Outcome.OK if result.from_model else Outcome.FALLBACK,
        provider=provider.name,
        model=getattr(provider, "model", provider.name),
        latency_ms=timer.elapsed_ms,
        model_version=detail.model_version,
        # What verification OBJECTED TO, not merely that it did. The
        # reason alone duplicates the `outcome` column beside it, and a
        # stored reason that repeats its neighbour is a diagnostic gap
        # wearing the shape of observability — recovering these cost 25
        # live API calls to re-derive what this line had in hand.
        detail=_detail(result),
    )
    return result
