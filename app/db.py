"""Database engine and session wiring.

The URL comes from ``DATABASE_URL`` so no credentials live in the
repository (see ``.env.example``). The ``+psycopg`` scheme is required:
a bare ``postgresql://`` URL resolves to psycopg2, which this project
does not install.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Connection, Engine, create_engine


def database_url() -> str:
    """Return the configured database URL.

    Raises:
        RuntimeError: If ``DATABASE_URL`` is unset, rather than silently
            falling back to a default that might point somewhere else.
    """
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Copy .env.example to .env and export "
            "it, or run through `make`, which supplies the local default."
        )
    return url


@lru_cache(maxsize=1)
def engine() -> Engine:
    """Return the process-wide engine, created on first use."""
    return create_engine(database_url(), pool_pre_ping=True, future=True)


@contextmanager
def transaction() -> Iterator[Connection]:
    """Run a unit of work in one transaction, committing on success."""
    with engine().begin() as connection:
        yield connection
