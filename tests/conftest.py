"""Shared pytest configuration.

Enforces ruling #6: every test must carry a milestone marker so it is
attributable to a ``make gate-mN`` run. An unmarked test would silently
escape every gate.

Also provides the database fixture. It **fails rather than skips** when no
database is reachable: from M1 the gate exercises real storage, and a
suite that skipped those tests on missing infrastructure would report
green while testing nothing. A red gate is honest; a green one that
skipped is not.
"""

import os

import pytest
from sqlalchemy import Engine, create_engine, text

MILESTONE_MARKERS = frozenset({"m0", "m1", "m2", "m3", "m4", "m5", "demo"})

_LOCAL_DEFAULT = "postgresql+psycopg://tinlantern:tinlantern@localhost:5432/tinlantern"

_NO_DATABASE = (
    "no reachable database at {url}.\n"
    "Storage tests fail rather than skip on purpose — a green suite that "
    "silently skipped them would be lying about what it verified.\n"
    "Run `make setup` (or `docker compose up -d --wait db && make migrate`) "
    "and try again."
)


@pytest.fixture(scope="session")
def engine() -> Engine:
    """A live database engine, or a loud failure explaining how to get one."""
    url = os.environ.get("DATABASE_URL", _LOCAL_DEFAULT)
    built = create_engine(url, pool_pre_ping=True)
    try:
        with built.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as error:
        raise pytest.UsageError(_NO_DATABASE.format(url=url)) from error
    return built


@pytest.fixture
def connection(engine: Engine):
    """A connection whose work is rolled back when the test ends.

    Storage tests write real rows; the rollback keeps them from leaking
    into the next test or into a developer's local database.
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
