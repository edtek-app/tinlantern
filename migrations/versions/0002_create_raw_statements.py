"""create raw.statements and raw.rejections

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-04

The append-only landing zone for ingested xAPI. See ADR-0005 for the
layout and the reasoning behind each column.

Append-only is enforced by the database, not by convention: a statement
-level trigger raises on UPDATE, DELETE, and TRUNCATE. Correcting raw data
therefore requires a deliberate migration that lifts the rule, acts, and
restores it — which is the point.
"""

from __future__ import annotations

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | None = None
depends_on: str | None = None

TABLES: tuple[str, ...] = ("statements", "rejections")


def upgrade() -> None:
    """Create the raw landing tables and lock them append-only."""
    op.execute(
        """
        CREATE TABLE raw.statements (
            statement_id UUID PRIMARY KEY,
            payload      JSONB NOT NULL,
            received_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            ingest_seq   BIGSERIAL NOT NULL
        )
        """
    )
    # M2 pages through new rows by ingest_seq. A timestamp cannot serve as
    # a watermark: a batch insert shares received_at to the microsecond and
    # clocks are not monotonic, so rows would be skipped or replayed.
    op.execute(
        "CREATE UNIQUE INDEX statements_ingest_seq_idx ON raw.statements (ingest_seq)"
    )

    op.execute(
        """
        CREATE TABLE raw.rejections (
            rejection_id BIGSERIAL PRIMARY KEY,
            statement_id UUID,
            payload      JSONB NOT NULL,
            reason       TEXT NOT NULL,
            detail       TEXT,
            received_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    # A rejected statement is looked up by its id when one could be parsed
    # out; malformed payloads have none, so the column stays nullable and
    # the index is partial.
    op.execute(
        "CREATE INDEX rejections_statement_id_idx "
        "ON raw.rejections (statement_id) WHERE statement_id IS NOT NULL"
    )

    op.execute(
        """
        CREATE FUNCTION raw.reject_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION
                'raw.% is append-only; % is not permitted (ADR-0005)',
                TG_TABLE_NAME, TG_OP;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    for table in TABLES:
        # Statement-level, so it fires even when no row matches — an UPDATE
        # that would have been a silent no-op still fails loudly.
        op.execute(
            f"""
            CREATE TRIGGER {table}_append_only
            BEFORE UPDATE OR DELETE OR TRUNCATE ON raw.{table}
            FOR EACH STATEMENT EXECUTE FUNCTION raw.reject_mutation()
            """
        )


def downgrade() -> None:
    """Drop the landing tables and the append-only machinery."""
    for table in TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS {table}_append_only ON raw.{table}")
    op.execute("DROP FUNCTION IF EXISTS raw.reject_mutation()")
    for table in TABLES:
        op.execute(f"DROP TABLE IF EXISTS raw.{table}")
