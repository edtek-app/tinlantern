"""create warehouse.risk_score

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-06

Per-learner risk scores and their drivers, written by the scoring job and
read by M5's dashboard.

**Not append-only, unlike `raw`.** `raw.statements` is the source of truth
and is protected by a trigger; this table is derived and rebuildable from
the warehouse plus the model code, exactly like the facts in `0003`.
Re-scoring replaces rather than accumulates: a learner has one current
score per window and model, not a history of every time the job ran.
"""

from __future__ import annotations

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Create the score table, keyed so a re-score replaces in place."""
    op.execute(
        """
        CREATE TABLE warehouse.risk_score (
            risk_score_key BIGSERIAL PRIMARY KEY,
            student_key    BIGINT NOT NULL REFERENCES warehouse.dim_student,
            window_close   TIMESTAMPTZ NOT NULL,
            model_version  TEXT NOT NULL,
            risk           NUMERIC(6, 4) NOT NULL,
            alerted        BOOLEAN NOT NULL,
            drivers        JSONB NOT NULL,
            scored_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (student_key, window_close, model_version)
        )
        """
    )
    op.execute(
        "COMMENT ON TABLE warehouse.risk_score IS "
        "'Current risk score per learner, per feature window, per model. "
        "Derived and rebuildable — NOT append-only, unlike raw.statements: "
        "re-scoring replaces in place. model_version identifies the CODE "
        "that produced the score (git commit plus a dirty marker), not a "
        "semantic version somebody has to remember to bump.'"
    )
    op.execute(
        "COMMENT ON COLUMN warehouse.risk_score.drivers IS "
        "'Per-learner feature contributions by ablation to the cohort "
        "median. Contributions do NOT sum to the score and interact; the "
        "payload carries that caveat so a dashboard cannot render them as "
        "an additive decomposition.'"
    )
    op.execute(
        "CREATE INDEX risk_score_window_idx "
        "ON warehouse.risk_score (window_close, risk DESC)"
    )


def downgrade() -> None:
    """Drop the score table. The warehouse it reads from is untouched."""
    op.execute("DROP TABLE IF EXISTS warehouse.risk_score")
