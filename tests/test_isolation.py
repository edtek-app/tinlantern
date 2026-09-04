"""Guards on the test suite's own isolation mechanism.

The session fixture DROPS a database. These tests exist because a
destructive fixture that resolved to the wrong target would take a
developer's working data and the generated cohort with it.

Each guard is asserted independently, so a future edit that removes one
fails here rather than being discovered by its consequences.
"""

import os

import pytest
from sqlalchemy.engine import make_url

from tests.conftest import checked_test_database_url

pytestmark = pytest.mark.m2

APP = "postgresql+psycopg://u:p@localhost:5432/tinlantern"
TEST = "postgresql+psycopg://u:p@localhost:5432/tinlantern_test"


# --------------------------------------------------------------------------
# The three guards, each on its own
# --------------------------------------------------------------------------


def test_an_unset_test_url_is_refused() -> None:
    """Never derived from DATABASE_URL. Derivation is the failure class."""
    with pytest.raises(pytest.UsageError, match="TEST_DATABASE_URL is not set"):
        checked_test_database_url(None, APP)
    with pytest.raises(pytest.UsageError):
        checked_test_database_url("", APP)


def test_a_name_not_ending_in_test_is_refused() -> None:
    with pytest.raises(pytest.UsageError, match="must end in '_test'"):
        checked_test_database_url(APP, APP)


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://u:p@localhost:5432/tinlantern",
        "postgresql+psycopg://u:p@localhost:5432/production",
        "postgresql+psycopg://u:p@localhost:5432/test",
        "postgresql+psycopg://u:p@localhost:5432/tinlantern_testing",
    ],
)
def test_only_a_test_suffixed_name_is_accepted(url: str) -> None:
    with pytest.raises(pytest.UsageError):
        checked_test_database_url(url, "postgresql+psycopg://u:p@h:5432/other")


def test_a_name_matching_the_app_database_is_refused() -> None:
    """Even with a _test suffix, it must not be the app's own database."""
    same = "postgresql+psycopg://u:p@localhost:5432/thing_test"
    with pytest.raises(pytest.UsageError, match="same database"):
        checked_test_database_url(same, same)


def test_a_distinct_test_database_is_accepted() -> None:
    assert checked_test_database_url(TEST, APP) == TEST


def test_validation_never_touches_a_database() -> None:
    """The guards decide from the URL alone, before anything connects.

    Pointed at a host that does not exist. If validation opened a
    connection on its way to deciding, this would raise a connection
    error or hang; instead it rejects on the name, immediately.

    Behavioural rather than a source scan — a textual guard would fail
    the day someone wrote "DROP" in a docstring, which is not the
    property being protected.
    """
    unreachable = "postgresql+psycopg://u:p@203.0.113.1:5432/tinlantern"
    with pytest.raises(pytest.UsageError, match="must end in '_test'"):
        checked_test_database_url(unreachable, APP)

    ok = "postgresql+psycopg://u:p@203.0.113.1:5432/nowhere_test"
    assert checked_test_database_url(ok, APP) == ok


# --------------------------------------------------------------------------
# The mechanism is actually in effect
# --------------------------------------------------------------------------


def test_the_suite_runs_against_the_test_database(engine) -> None:
    assert (make_url(str(engine.url)).database or "").endswith("_test")


def test_the_application_engine_points_at_the_test_database() -> None:
    """The redirect that keeps an endpoint under test off the dev database."""
    from app.db import engine as app_engine

    assert (make_url(str(app_engine().url)).database or "").endswith("_test")


def test_the_test_database_is_not_the_configured_one() -> None:
    configured = os.environ.get("TEST_DATABASE_URL", "")
    assert make_url(configured).database != "tinlantern"
