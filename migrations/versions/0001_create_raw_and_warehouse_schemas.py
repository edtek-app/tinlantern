"""create raw and warehouse schemas

Revision ID: 0001
Revises:
Create Date: 2026-09-02

The baseline migration. Per ADR-0001 the database is a single Postgres
instance with one database and two schemas: ``raw`` holds append-only
ingested xAPI statements, ``warehouse`` holds the star schema.

Alembic owns all DDL including this initial schema creation; there is no
docker-entrypoint-initdb.d path, because initdb hooks do not run on managed
Postgres (M6) and drift on already-provisioned local volumes.
"""

from __future__ import annotations

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None

SCHEMAS: tuple[str, ...] = ("raw", "warehouse")


def upgrade() -> None:
    """Create the raw and warehouse schemas."""
    for schema in SCHEMAS:
        op.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')


def downgrade() -> None:
    """Drop the schemas, but only if they are empty.

    RESTRICT rather than CASCADE: a downgrade must never silently destroy
    ingested statements or warehouse tables. If the schemas still hold
    objects, this fails loudly and the operator decides.
    """
    for schema in reversed(SCHEMAS):
        op.execute(f'DROP SCHEMA IF EXISTS "{schema}" RESTRICT')
