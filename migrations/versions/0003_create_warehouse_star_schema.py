"""create the warehouse star schema

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-04

Four conformed dimensions and two facts. See ADR-0006 for the grain, the
key strategy, and why the two facts deliberately overlap.

Unlike `raw`, the warehouse is derived and rebuildable — no append-only
trigger. Its integrity guarantee is different: every fact carries a
UNIQUE statement_id, so re-running the ETL over the same statements
cannot create a second row. Idempotency is enforced by the schema rather
than trusted to the loader.
"""

from __future__ import annotations

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | None = None
depends_on: str | None = None

#: dim_date is pre-populated across a span comfortably wider than any
#: cohort, so the ETL never has to create a date on the fly.
DATE_SPAN = ("2024-01-01", "2030-12-31")

DIMENSIONS = ("dim_student", "dim_course", "dim_activity", "dim_date")
FACTS = ("fact_activity", "fact_assessment")


def upgrade() -> None:
    """Create the dimensions, the facts, and populate dim_date."""
    op.execute(
        """
        CREATE TABLE warehouse.dim_student (
            student_key        BIGSERIAL PRIMARY KEY,
            learner_identifier TEXT NOT NULL UNIQUE,
            account_home_page  TEXT NOT NULL
        )
        """
    )
    op.execute(
        "COMMENT ON TABLE warehouse.dim_student IS "
        "'One row per learner. Natural key: the opaque account name "
        "(ADR-0002 — never an email address). Type 1: attributes are "
        "overwritten, no history kept.'"
    )

    op.execute(
        """
        CREATE TABLE warehouse.dim_course (
            course_key BIGSERIAL PRIMARY KEY,
            course_iri TEXT NOT NULL UNIQUE,
            course_slug TEXT NOT NULL,
            title      TEXT
        )
        """
    )

    op.execute(
        """
        CREATE TABLE warehouse.dim_activity (
            activity_key  BIGSERIAL PRIMARY KEY,
            activity_iri  TEXT NOT NULL UNIQUE,
            activity_type TEXT,
            name          TEXT,
            course_key    BIGINT NOT NULL REFERENCES warehouse.dim_course,
            module_index  INT
        )
        """
    )
    op.execute(
        "CREATE INDEX dim_activity_course_idx ON warehouse.dim_activity (course_key)"
    )

    op.execute(
        """
        CREATE TABLE warehouse.dim_date (
            date_key    INT PRIMARY KEY,
            full_date   DATE NOT NULL UNIQUE,
            year        INT NOT NULL,
            quarter     INT NOT NULL,
            month       INT NOT NULL,
            day         INT NOT NULL,
            iso_week    INT NOT NULL,
            day_of_week INT NOT NULL,
            day_name    TEXT NOT NULL,
            is_weekend  BOOLEAN NOT NULL
        )
        """
    )
    op.execute(
        f"""
        INSERT INTO warehouse.dim_date
        SELECT
            CAST(to_char(d, 'YYYYMMDD') AS INT),
            d::date,
            EXTRACT(YEAR FROM d)::int,
            EXTRACT(QUARTER FROM d)::int,
            EXTRACT(MONTH FROM d)::int,
            EXTRACT(DAY FROM d)::int,
            EXTRACT(WEEK FROM d)::int,
            EXTRACT(ISODOW FROM d)::int,
            trim(to_char(d, 'Day')),
            EXTRACT(ISODOW FROM d)::int >= 6
        FROM generate_series(
            DATE '{DATE_SPAN[0]}', DATE '{DATE_SPAN[1]}', INTERVAL '1 day'
        ) AS d
        """
    )

    # ---------------------------------------------------------------- facts
    op.execute(
        """
        CREATE TABLE warehouse.fact_activity (
            activity_event_key BIGSERIAL PRIMARY KEY,
            statement_id       UUID NOT NULL UNIQUE,
            student_key        BIGINT NOT NULL REFERENCES warehouse.dim_student,
            course_key         BIGINT NOT NULL REFERENCES warehouse.dim_course,
            activity_key       BIGINT NOT NULL REFERENCES warehouse.dim_activity,
            date_key           INT NOT NULL REFERENCES warehouse.dim_date,
            verb               TEXT NOT NULL,
            occurred_at        TIMESTAMPTZ NOT NULL,
            registration       UUID,
            ingest_seq         BIGINT NOT NULL
        )
        """
    )
    op.execute(
        "COMMENT ON TABLE warehouse.fact_activity IS "
        "'THE ATOMIC RECORD: one row per xAPI statement, every verb "
        "included. Use for behaviour and engagement questions. Assessment "
        "statements appear here AND in fact_assessment by design "
        "(ADR-0006) — the two overlap and must never be summed together.'"
    )

    op.execute(
        """
        CREATE TABLE warehouse.fact_assessment (
            assessment_result_key BIGSERIAL PRIMARY KEY,
            statement_id          UUID NOT NULL UNIQUE,
            student_key    BIGINT NOT NULL REFERENCES warehouse.dim_student,
            course_key     BIGINT NOT NULL REFERENCES warehouse.dim_course,
            activity_key   BIGINT NOT NULL REFERENCES warehouse.dim_activity,
            date_key       INT NOT NULL REFERENCES warehouse.dim_date,
            verb           TEXT NOT NULL,
            scaled_score   NUMERIC(6, 4),
            raw_score      NUMERIC(10, 2),
            success        BOOLEAN,
            completion     BOOLEAN,
            attempt_number INT,
            occurred_at    TIMESTAMPTZ NOT NULL,
            registration   UUID,
            ingest_seq     BIGINT NOT NULL
        )
        """
    )
    op.execute(
        "COMMENT ON TABLE warehouse.fact_assessment IS "
        "'One row per graded outcome (passed, failed, submitted). Use for "
        "outcome and attainment questions. These statements ALSO appear in "
        "fact_activity by design (ADR-0006) — the two overlap and must "
        "never be summed together.'"
    )

    for fact in FACTS:
        op.execute(f"CREATE INDEX {fact}_student_idx ON warehouse.{fact} (student_key)")
        # The ETL pages raw by ingest_seq; queries page the warehouse the
        # same way when they need "what arrived since".
        op.execute(
            f"CREATE INDEX {fact}_ingest_seq_idx ON warehouse.{fact} (ingest_seq)"
        )
        op.execute(f"CREATE INDEX {fact}_date_idx ON warehouse.{fact} (date_key)")


def downgrade() -> None:
    """Drop the facts, then the dimensions they reference."""
    for fact in FACTS:
        op.execute(f"DROP TABLE IF EXISTS warehouse.{fact}")
    for dimension in reversed(DIMENSIONS):
        op.execute(f"DROP TABLE IF EXISTS warehouse.{dimension}")
