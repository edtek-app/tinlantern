"""create warehouse.etl_state and warehouse.etl_rejections

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-04

Machinery for the load, kept separate from the dimensional model that
`0003` created: schema for the model, schema for the thing that fills it.

`etl_rejections` is *not* `raw.rejections`. They record different failures
by different actors:

* `raw.rejections`       — the sender sent something invalid.
* `warehouse.etl_rejections` — we accepted it and cannot model it.

Different fix, different owner, so different tables.
"""

from __future__ import annotations

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Create the watermark table and the ETL's own rejection log."""
    op.execute(
        """
        CREATE TABLE warehouse.etl_state (
            job_name        TEXT PRIMARY KEY,
            last_ingest_seq BIGINT NOT NULL DEFAULT 0,
            last_run_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            statements_seen BIGINT NOT NULL DEFAULT 0
        )
        """
    )
    op.execute(
        "COMMENT ON TABLE warehouse.etl_state IS "
        "'Per-job high-water mark over raw.statements.ingest_seq. The "
        "marker is opaque (ADR-0005): compare it, store the maximum, page "
        "above it. Never read it as event time or join on it.'"
    )

    op.execute(
        """
        CREATE TABLE warehouse.etl_rejections (
            etl_rejection_id BIGSERIAL PRIMARY KEY,
            statement_id     UUID NOT NULL,
            ingest_seq       BIGINT NOT NULL,
            reason           TEXT NOT NULL,
            detail           TEXT,
            payload          JSONB NOT NULL,
            rejected_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "COMMENT ON TABLE warehouse.etl_rejections IS "
        "'Statements the ETL accepted from raw but could not model — a "
        "missing course context, an unresolvable dimension. NOT the same "
        "as raw.rejections, which means the sender sent something "
        "invalid; this means we took it and could not place it. Different "
        "actor, different fix. A data-quality check fails the run if this "
        "table is not empty (ADR-0006).'"
    )
    op.execute(
        "CREATE INDEX etl_rejections_statement_idx "
        "ON warehouse.etl_rejections (statement_id)"
    )


def downgrade() -> None:
    """Drop the ETL machinery, leaving the dimensional model intact."""
    op.execute("DROP TABLE IF EXISTS warehouse.etl_rejections")
    op.execute("DROP TABLE IF EXISTS warehouse.etl_state")
