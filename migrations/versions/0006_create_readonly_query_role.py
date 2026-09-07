"""create the read-only role that generated SQL runs as

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-07

M4 lets a language model write SQL and then runs it. The boundary that
stops a generated statement from writing has to be the **database**, not
our reading of the statement: a parser-based guard is a convenience that
is wrong in ways nobody predicts, and it is the classic way this goes
wrong.

**`NOLOGIN`, reached by `SET ROLE`, deliberately.** A login role would
need a password, which would mean either a secret in the repository or a
second credential to distribute — and the enforcement would be identical.
The application connects as itself and drops into this role for the
duration of a generated query, so the privilege boundary exists with no
new secret anywhere.

The role is granted SELECT on `warehouse` only. `raw` is not readable:
generated SQL has no business reading verbatim statement payloads, and
the star schema is what the question layer is described against.
"""

from __future__ import annotations

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | None = None
depends_on: str | None = None

ROLE = "tinlantern_readonly"


def upgrade() -> None:
    """Create the role, grant it reads on the warehouse, nothing else."""
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{ROLE}') THEN
                CREATE ROLE {ROLE} NOLOGIN;
            END IF;
        END
        $$
        """
    )

    op.execute(f"GRANT USAGE ON SCHEMA warehouse TO {ROLE}")
    op.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA warehouse TO {ROLE}")

    # Tables created after this migration are covered too. Without this,
    # a warehouse table added at M5 would be invisible to Q&A and the
    # failure would look like a bad question rather than a missing grant.
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA warehouse GRANT SELECT ON TABLES TO {ROLE}"
    )

    # So the application can SET ROLE into it. current_user is whoever
    # runs the migration, which is the account the app connects as.
    op.execute(f"GRANT {ROLE} TO CURRENT_USER")

    op.execute(
        f"COMMENT ON ROLE {ROLE} IS "
        "'Generated SQL from the M4 Q&A layer runs as this role. SELECT on "
        "warehouse only — no writes, no raw. NOLOGIN: reached with SET "
        "ROLE, so no second credential exists to leak. This role is the "
        "security boundary; the Python statement check in app/llm/qa.py is "
        "a fast-fail convenience and is NOT.'"
    )


def downgrade() -> None:
    """Drop the role, revoking first so the drop can succeed."""
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA warehouse "
        f"REVOKE SELECT ON TABLES FROM {ROLE}"
    )
    op.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA warehouse FROM {ROLE}")
    op.execute(f"REVOKE USAGE ON SCHEMA warehouse FROM {ROLE}")
    op.execute(f"DROP ROLE IF EXISTS {ROLE}")
