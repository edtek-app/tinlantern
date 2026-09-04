"""Shared pytest configuration.

Two mechanisms live here, both replacing discipline that kept failing.

**Milestone markers.** Every test must carry one, so it is attributable to
a ``make gate-mN`` run. An unmarked test would silently escape every gate.

**An isolated database.** The suite runs against its own database, dropped
and recreated once per session. Four separate times a test assumed state
it had not created — a table-wide count, a reused cohort id, a page-count
assumption — and each surfaced late, twice as `make gate-m2` and
`make lint test` disagreeing about the same suite. Starting every session
from a known-empty database makes state a deterministic function of the
tests that ran, which is what makes those two commands agree. It also
stops the suite writing into a developer's working data.

Storage tests **fail rather than skip** when no database is reachable: a
green suite that silently skipped them would be lying about what it
verified. A red gate is honest; a green one that skipped is not.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url

MILESTONE_MARKERS = frozenset({"m0", "m1", "m2", "m3", "m4", "m5", "demo"})

ROOT = Path(__file__).resolve().parents[1]

_UNSET = (
    "TEST_DATABASE_URL is not set.\n"
    "The suite runs against its own database, which it DROPS and recreates.\n"
    "It is never derived from DATABASE_URL: deriving a name is how a "
    "'_test' suffix ends up appended to something unexpected.\n"
    "Set it explicitly, e.g.\n"
    "  TEST_DATABASE_URL=postgresql+psycopg://tinlantern:tinlantern"
    "@localhost:5432/tinlantern_test\n"
    "or run through `make`, which supplies it."
)

_NOT_A_TEST_NAME = (
    "refusing to drop database {name!r}: the test database's name must end "
    "in '_test'. This guard exists because the fixture is destructive."
)

_SAME_AS_APP = (
    "refusing to drop database {name!r}: TEST_DATABASE_URL and DATABASE_URL "
    "name the same database. The suite would destroy working data."
)

_NO_DATABASE = (
    "no reachable database server at {url}.\n"
    "Storage tests fail rather than skip on purpose — a green suite that "
    "silently skipped them would be lying about what it verified.\n"
    "Run `make setup` (or `docker compose up -d --wait db`) and try again."
)


def checked_test_database_url(test_url: str | None, app_url: str | None) -> str:
    """Validate the test database URL before anything destructive happens.

    Three independent guards, all checked before any DROP:

    1. ``TEST_DATABASE_URL`` must be set explicitly. It is never computed
       from ``DATABASE_URL`` — derivation is the mechanism by which a
       suffix gets appended to the wrong thing.
    2. The database name must end in ``_test``.
    3. It must not name the same database as ``DATABASE_URL``.

    Args:
        test_url: The value of ``TEST_DATABASE_URL``.
        app_url: The value of ``DATABASE_URL``, if any.

    Returns:
        The validated URL.

    Raises:
        pytest.UsageError: If any guard rejects, with what to fix.
    """
    if not test_url:
        raise pytest.UsageError(_UNSET)

    name = make_url(test_url).database or ""
    if not name.endswith("_test"):
        raise pytest.UsageError(_NOT_A_TEST_NAME.format(name=name))

    if app_url and name == (make_url(app_url).database or ""):
        raise pytest.UsageError(_SAME_AS_APP.format(name=name))

    return test_url


@pytest.fixture(scope="session", autouse=True)
def test_database() -> str:
    """Create a fresh database for this session and point the app at it.

    Autouse, so nothing can reach the application's engine before the
    redirect is in place.
    """
    url = checked_test_database_url(
        os.environ.get("TEST_DATABASE_URL"), os.environ.get("DATABASE_URL")
    )
    name = make_url(url).database

    maintenance = create_engine(
        make_url(url).set(database="postgres"), isolation_level="AUTOCOMMIT"
    )
    try:
        with maintenance.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
            connection.execute(text(f'CREATE DATABASE "{name}"'))
    except pytest.UsageError:
        raise
    except Exception as error:
        raise pytest.UsageError(_NO_DATABASE.format(url=url)) from error
    finally:
        maintenance.dispose()

    # Alembic owns all DDL (ADR-0001), so the test database is built the
    # same way every other environment is.
    subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd=ROOT,
        env={**os.environ, "DATABASE_URL": url},
        check=True,
        capture_output=True,
    )

    # Redirect the application itself, so an endpoint under test cannot
    # write to the developer's database.
    os.environ["DATABASE_URL"] = url
    from app import db

    db.engine.cache_clear()
    return url


@pytest.fixture(scope="session")
def engine(test_database: str) -> Engine:
    """A live engine against the isolated test database."""
    built = create_engine(test_database, pool_pre_ping=True)
    with built.connect() as connection:
        connection.execute(text("SELECT 1"))
    return built


@pytest.fixture
def connection(engine: Engine):
    """A connection whose work is rolled back when the test ends.

    Still worth having on top of the fresh database: it keeps one test's
    rows out of the next test's view within a session.
    """
    with engine.connect() as open_connection:
        transaction = open_connection.begin()
        try:
            yield open_connection
        finally:
            transaction.rollback()


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    unmarked = [
        item.nodeid
        for item in items
        if MILESTONE_MARKERS.isdisjoint(m.name for m in item.iter_markers())
    ]
    if unmarked:
        raise pytest.UsageError(
            f"tests are missing a milestone marker ({sorted(MILESTONE_MARKERS)}): "
            + ", ".join(unmarked)
        )
