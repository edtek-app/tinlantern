"""Guards on the Alembic baseline.

Per ADR-0001 Alembic owns all DDL, so migration `0001` creating the `raw`
and `warehouse` schemas is the foundation everything later builds on.

The offline test below renders real migration SQL without connecting to a
database, so the migration is genuinely exercised in CI where no Postgres
runs. The live round-trip is a separate, skipped-by-default test.
"""

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.m0

ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_INI = ROOT / "alembic.ini"
VERSIONS = ROOT / "migrations" / "versions"

# Offline rendering never opens a socket, but Alembic still requires a URL.
OFFLINE_URL = "postgresql+psycopg://tinlantern:tinlantern@localhost:5432/tinlantern"
EXPECTED_SCHEMAS = ("raw", "warehouse")


def _alembic(*args: str, url: str = OFFLINE_URL) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["alembic", *args],
        cwd=ROOT,
        env={**os.environ, "DATABASE_URL": url},
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_alembic_ini_points_at_migrations() -> None:
    assert ALEMBIC_INI.is_file()
    assert "script_location = migrations" in ALEMBIC_INI.read_text(encoding="utf-8")


def test_alembic_ini_holds_no_credentials() -> None:
    """The URL comes from DATABASE_URL; a populated one here would be a leak."""
    for line in ALEMBIC_INI.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("sqlalchemy.url"):
            assert not stripped.split("=", 1)[1].strip(), "credentials in alembic.ini"


def test_migration_history_has_a_single_root() -> None:
    """Two roots would make `upgrade head` ambiguous."""
    result = _alembic("heads")
    assert result.returncode == 0, result.stderr
    assert len(result.stdout.strip().splitlines()) == 1, result.stdout


def test_offline_upgrade_creates_both_schemas() -> None:
    """Render the real SQL for `upgrade head` and inspect it — no database."""
    result = _alembic("upgrade", "head", "--sql")
    assert result.returncode == 0, result.stderr
    sql = result.stdout.lower()
    for schema in EXPECTED_SCHEMAS:
        assert f'create schema if not exists "{schema}"' in sql, sql


def test_downgrade_does_not_cascade() -> None:
    """CASCADE here would silently drop ingested statements on a downgrade.

    Inspects the executed statements only; the docstring in the migration
    discusses CASCADE by name to explain why it is not used.
    """
    baseline = (VERSIONS / "0001_create_raw_and_warehouse_schemas.py").read_text(
        encoding="utf-8"
    )
    executed = [line for line in baseline.splitlines() if "op.execute" in line]
    assert executed, "migration executes no SQL"
    assert any("RESTRICT" in line for line in executed)
    assert not [line for line in executed if "CASCADE" in line]


def _live_database_url() -> str | None:
    """Return a reachable database URL, or None if Postgres is not running."""
    url = os.environ.get("DATABASE_URL", OFFLINE_URL)
    try:
        import psycopg

        with psycopg.connect(
            url.replace("postgresql+psycopg://", "postgresql://"), connect_timeout=2
        ):
            return url
    except Exception:
        return None


@pytest.mark.skipif(
    _live_database_url() is None,
    reason="no local Postgres reachable; run `make setup` to exercise this",
)
def test_upgrade_against_a_live_database_creates_both_schemas() -> None:
    import psycopg

    url = _live_database_url()
    assert url is not None
    result = _alembic("upgrade", "head", url=url)
    assert result.returncode == 0, result.stderr

    with psycopg.connect(
        url.replace("postgresql+psycopg://", "postgresql://"), connect_timeout=5
    ) as conn:
        rows = conn.execute(
            "SELECT schema_name FROM information_schema.schemata"
        ).fetchall()
    found = {row[0] for row in rows}
    assert set(EXPECTED_SCHEMAS) <= found, found
