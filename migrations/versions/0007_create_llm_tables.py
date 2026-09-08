"""create warehouse.llm_call and warehouse.learner_summary

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-08

Two tables the dashboard needs, both derived and rebuildable.

**`llm_call` records what happened on every provider call.** An
in-process counter was the cheaper option and was rejected: it resets on
restart, and a demo restart would discard the only real measurement of
how often the LLM layer misbehaves. The eval harness has 0 malformed
responses in 90 question-runs — enough to call it rare, far too thin to
size a retry policy against — so production is where the rate actually
comes from, and M6 reads observability out of this table rather than
rebuilding it.

**`learner_summary` stores advisor summaries, keyed exactly like the
score.** A summary is two model calls and 5-15 seconds; generating one
per page view would make the drill-down unusable and bill for every
refresh. Keyed by `(student, window_close, model_version)` — the same
key `risk_score` uses — so a summary cannot outlive the score it
describes: change the model and the key changes with it. Demo mode's
"cached LLM responses" is this table, not a second mechanism built
later.

The scoring job writes both; the web tier only reads. M6's deployment
story is therefore "scoring writes summaries", not "the web tier calls
a language model on every request".
"""

from __future__ import annotations

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Create both tables."""
    op.execute(
        """
        CREATE TABLE warehouse.llm_call (
            llm_call_key  BIGSERIAL PRIMARY KEY,
            occurred_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            operation     TEXT NOT NULL,
            outcome       TEXT NOT NULL,
            provider      TEXT NOT NULL,
            model         TEXT NOT NULL,
            latency_ms    INTEGER NOT NULL,
            model_version TEXT,
            detail        TEXT
        )
        """
    )
    op.execute(
        "COMMENT ON TABLE warehouse.llm_call IS "
        "'One row per provider call, for observability. outcome is one of "
        "ok, retried, malformed, refused, fallback, transport_error. "
        "Writing a row must NEVER fail the request it describes: a metrics "
        "write that takes down an endpoint is worse than a missing metric.'"
    )
    op.execute(
        "CREATE INDEX llm_call_recent_idx "
        "ON warehouse.llm_call (occurred_at DESC, outcome)"
    )

    op.execute(
        """
        CREATE TABLE warehouse.learner_summary (
            learner_summary_key BIGSERIAL PRIMARY KEY,
            student_key   BIGINT NOT NULL REFERENCES warehouse.dim_student,
            window_close  TIMESTAMPTZ NOT NULL,
            model_version TEXT NOT NULL,
            summary       TEXT NOT NULL,
            from_model    BOOLEAN NOT NULL,
            fallback_reason TEXT,
            synthetic     BOOLEAN NOT NULL,
            generated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (student_key, window_close, model_version)
        )
        """
    )
    op.execute(
        "COMMENT ON TABLE warehouse.learner_summary IS "
        "'Advisor summaries, keyed exactly like warehouse.risk_score so a "
        "summary cannot outlive the score it describes. Written by the "
        "scoring job; the web tier only reads. from_model is false when "
        "the deterministic template produced it, and fallback_reason says "
        "why — a summary whose provenance is invisible reads the same as "
        "one the model wrote.'"
    )


def downgrade() -> None:
    """Drop both. Neither holds anything that cannot be regenerated."""
    op.execute("DROP TABLE IF EXISTS warehouse.learner_summary")
    op.execute("DROP TABLE IF EXISTS warehouse.llm_call")
