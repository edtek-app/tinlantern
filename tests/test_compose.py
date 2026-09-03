"""Guards on the local Postgres stack.

`make setup` promises one command that installs deps, starts Postgres, and
migrates it. These tests pin the parts of that promise that can be checked
without a running database, so they hold in CI.
"""

from pathlib import Path
from urllib.parse import urlparse

import pytest
import yaml

pytestmark = pytest.mark.m0

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "docker-compose.yml"
ENV_EXAMPLE = ROOT / ".env.example"
MAKEFILE = ROOT / "Makefile"


def _compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def _db_service() -> dict:
    return _compose()["services"]["db"]


def _example_database_url() -> str:
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip()
    raise AssertionError("DATABASE_URL is not defined in .env.example")


def test_compose_file_parses() -> None:
    assert COMPOSE.is_file()
    assert _compose()["services"]


def test_db_service_named_db_on_postgres_16() -> None:
    """`make setup` runs `docker compose up -d --wait db`; the name matters."""
    assert _db_service()["image"] == "postgres:16"


def test_db_service_declares_a_healthcheck() -> None:
    """`up --wait` blocks on this; without it setup races the migration."""
    healthcheck = _db_service().get("healthcheck", {})
    assert healthcheck.get("test"), "db service has no healthcheck"


def test_db_data_is_on_a_named_volume() -> None:
    """An anonymous volume would silently lose the database on recreate."""
    service = _db_service()
    mounts = [m.split(":", 1)[0] for m in service.get("volumes", [])]
    assert "pgdata" in mounts
    assert "pgdata" in (_compose().get("volumes") or {})


def test_compose_credentials_match_env_example() -> None:
    """Three files repeat these credentials; keep them from drifting apart."""
    env = _db_service()["environment"]
    url = urlparse(_example_database_url())
    assert url.username == env["POSTGRES_USER"]
    assert url.password == env["POSTGRES_PASSWORD"]
    assert (url.path or "").lstrip("/") == env["POSTGRES_DB"]
    assert url.port == 5432


def test_database_url_uses_the_psycopg3_driver() -> None:
    """A bare postgresql:// URL resolves to psycopg2, which is not installed."""
    assert _example_database_url().startswith("postgresql+psycopg://")
    makefile = MAKEFILE.read_text(encoding="utf-8")
    assert "postgresql+psycopg://" in makefile


def test_no_database_init_script_path() -> None:
    """ADR-0001: Alembic owns all DDL. An initdb hook would fork that.

    Checks the parsed mounts, not the file text, so the comment explaining
    this decision does not trip the assertion.
    """
    mounts = _db_service().get("volumes", [])
    assert not [m for m in mounts if "docker-entrypoint-initdb.d" in m]
    assert not (ROOT / "docker" / "initdb").exists()


def test_setup_migrates_after_starting_the_database() -> None:
    makefile = MAKEFILE.read_text(encoding="utf-8")
    assert "docker compose up -d --wait db" in makefile
    assert "alembic upgrade head" in makefile
