"""Tests for the warehouse star schema.

Written against a live database: foreign keys, unique constraints and the
generated date dimension are all properties of Postgres, not of Python.

**State discipline.** The warehouse starts empty, which makes it easy to
write a test that silently assumes it stays that way — and the ETL will
fill it shortly. Every test here creates the rows it needs and scopes its
assertions to them; none asserts a table-wide count.
"""

from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from pipeline.warehouse import DIMENSIONS, FACTS, TABLES, qualified
from pipeline.warehouse.schema import GRADED_VERBS

pytestmark = pytest.mark.m2

TERM_START = date(2026, 1, 12)


def insert_student(connection, identifier: str | None = None) -> int:
    identifier = identifier or f"s-{uuid4().hex[:8]}"
    return connection.execute(
        text(
            "INSERT INTO warehouse.dim_student "
            "(learner_identifier, account_home_page) "
            "VALUES (:i, 'https://tinlantern.example/xapi') "
            "RETURNING student_key"
        ),
        {"i": identifier},
    ).scalar_one()


def insert_course(connection, slug: str | None = None) -> int:
    slug = slug or f"c-{uuid4().hex[:8]}"
    return connection.execute(
        text(
            "INSERT INTO warehouse.dim_course (course_iri, course_slug, title) "
            "VALUES (:iri, :slug, 'A Course') RETURNING course_key"
        ),
        {"iri": f"https://tinlantern.example/xapi/course/{slug}", "slug": slug},
    ).scalar_one()


def insert_activity(connection, course_key: int) -> int:
    return connection.execute(
        text(
            "INSERT INTO warehouse.dim_activity "
            "(activity_iri, activity_type, name, course_key, module_index) "
            "VALUES (:iri, 'module', 'Module 1', :c, 1) RETURNING activity_key"
        ),
        {"iri": f"https://tinlantern.example/xapi/a/{uuid4()}", "c": course_key},
    ).scalar_one()


def date_key(connection, day: date = TERM_START) -> int:
    return connection.execute(
        text("SELECT date_key FROM warehouse.dim_date WHERE full_date = :d"),
        {"d": day},
    ).scalar_one()


def insert_fact(connection, table: str, statement_id=None, **overrides) -> int:
    """Insert a fact with freshly created dimension rows unless given."""
    course = overrides.get("course_key") or insert_course(connection)
    values = {
        "statement_id": statement_id or uuid4(),
        "student_key": overrides.get("student_key") or insert_student(connection),
        "course_key": course,
        "activity_key": overrides.get("activity_key")
        or insert_activity(connection, course),
        "date_key": overrides.get("date_key") or date_key(connection),
        "verb": overrides.get("verb", "experienced"),
        "occurred_at": "2026-01-12T10:00:00+00:00",
        "ingest_seq": overrides.get("ingest_seq", 1),
    }
    key = "activity_event_key" if table == "fact_activity" else "assessment_result_key"
    return connection.execute(
        text(
            f"INSERT INTO warehouse.{table} "
            "(statement_id, student_key, course_key, activity_key, date_key, "
            " verb, occurred_at, ingest_seq) "
            "VALUES (:statement_id, :student_key, :course_key, :activity_key, "
            " :date_key, :verb, :occurred_at, :ingest_seq) "
            f"RETURNING {key}"
        ),
        values,
    ).scalar_one()


# --------------------------------------------------------------------------
# The migration created what the metadata describes
# --------------------------------------------------------------------------


@pytest.mark.parametrize("table", TABLES, ids=lambda t: t.name)
def test_every_declared_table_exists(connection, table) -> None:
    """pipeline.warehouse and migration 0003 must not drift apart."""
    assert (
        connection.execute(
            text("SELECT to_regclass(:name)"), {"name": qualified(table)}
        ).scalar_one()
        is not None
    )


@pytest.mark.parametrize("table", TABLES, ids=lambda t: t.name)
def test_declared_columns_exist(connection, table) -> None:
    columns = {
        row[0]
        for row in connection.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'warehouse' AND table_name = :t"
            ),
            {"t": table.name},
        )
    }
    expected = {table.key, *table.required, *(c for c, _ in table.references)}
    assert expected <= columns, f"{table.name} missing {expected - columns}"


@pytest.mark.parametrize("table", TABLES, ids=lambda t: t.name)
def test_grain_is_enforced_by_a_unique_constraint(connection, table) -> None:
    """Grain declared in metadata must be enforced by the database.

    This is what makes the ETL safely re-runnable: a second pass over the
    same statements is stopped by the schema, not by the loader's
    bookkeeping.
    """
    if table.grain is None:
        pytest.fail(f"{table.name} declares no grain")
    unique = connection.execute(
        text(
            """
            SELECT count(*) FROM pg_index i
            JOIN pg_class c ON c.oid = i.indrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            JOIN pg_attribute a ON a.attrelid = c.oid
                               AND a.attnum = ANY(i.indkey)
            WHERE n.nspname = 'warehouse' AND c.relname = :t
              AND a.attname = :col AND i.indisunique
            """
        ),
        {"t": table.name, "col": table.grain},
    ).scalar_one()
    assert unique >= 1, f"{table.name}.{table.grain} is not unique"


# --------------------------------------------------------------------------
# Referential integrity
# --------------------------------------------------------------------------


@pytest.mark.parametrize("fact", FACTS, ids=lambda t: t.name)
def test_a_fact_row_resolves_to_every_dimension(connection, fact) -> None:
    course = insert_course(connection)
    student = insert_student(connection)
    activity = insert_activity(connection, course)
    key = insert_fact(
        connection,
        fact.name,
        course_key=course,
        student_key=student,
        activity_key=activity,
    )

    joined = connection.execute(
        text(
            f"SELECT s.learner_identifier, c.course_slug, a.activity_iri, "
            f"d.full_date FROM warehouse.{fact.name} f "
            "JOIN warehouse.dim_student  s USING (student_key) "
            "JOIN warehouse.dim_course   c USING (course_key) "
            "JOIN warehouse.dim_activity a USING (activity_key) "
            "JOIN warehouse.dim_date     d USING (date_key) "
            f"WHERE f.{fact.key} = :k"
        ),
        {"k": key},
    ).one()
    assert all(value is not None for value in joined)


@pytest.mark.parametrize("fact", FACTS, ids=lambda t: t.name)
def test_an_orphan_fact_is_refused(connection, fact) -> None:
    """A fact pointing at no dimension row must not be storable."""
    with pytest.raises(IntegrityError):
        insert_fact(connection, fact.name, student_key=-1)


def test_an_activity_must_belong_to_a_course(connection) -> None:
    with pytest.raises(IntegrityError):
        insert_activity(connection, course_key=-1)


@pytest.mark.parametrize("fact", FACTS, ids=lambda t: t.name)
def test_the_same_statement_cannot_be_loaded_twice(connection, fact) -> None:
    """The ETL's idempotency guarantee, enforced by the schema."""
    statement_id = uuid4()
    insert_fact(connection, fact.name, statement_id=statement_id)
    with pytest.raises(IntegrityError):
        insert_fact(connection, fact.name, statement_id=statement_id)


# --------------------------------------------------------------------------
# The two facts overlap on purpose
# --------------------------------------------------------------------------


def test_one_statement_may_appear_in_both_facts(connection) -> None:
    """The overlap ADR-0006 chose: an assessment event is also activity.

    fact_activity is the atomic record and holds every statement;
    fact_assessment holds graded outcomes. Summing across both
    double-counts, which is why both tables carry a comment saying so.
    """
    statement_id = uuid4()
    course = insert_course(connection)
    student = insert_student(connection)
    activity = insert_activity(connection, course)
    shared = {
        "course_key": course,
        "student_key": student,
        "activity_key": activity,
        "verb": "passed",
    }
    insert_fact(connection, "fact_activity", statement_id=statement_id, **shared)
    insert_fact(connection, "fact_assessment", statement_id=statement_id, **shared)

    for table in ("fact_activity", "fact_assessment"):
        assert (
            connection.execute(
                text(f"SELECT count(*) FROM warehouse.{table} WHERE statement_id = :i"),
                {"i": statement_id},
            ).scalar_one()
            == 1
        )


@pytest.mark.parametrize("fact", FACTS, ids=lambda t: t.name)
def test_the_overlap_is_documented_where_an_analyst_looks(connection, fact) -> None:
    """A comment on the table itself, not only in an ADR nobody opens."""
    comment = connection.execute(
        text("SELECT obj_description(to_regclass(:t), 'pg_class')"),
        {"t": qualified(fact)},
    ).scalar_one()
    assert comment, f"{fact.name} has no table comment"
    assert "ADR-0006" in comment
    assert "never be summed" in comment


def test_graded_verbs_are_the_ones_that_produce_assessment_rows() -> None:
    assert {"passed", "failed", "submitted"} == GRADED_VERBS


# --------------------------------------------------------------------------
# dim_date
# --------------------------------------------------------------------------


def test_dim_date_covers_the_cohort_term(connection) -> None:
    covered = connection.execute(
        text(
            "SELECT count(*) FROM warehouse.dim_date "
            "WHERE full_date BETWEEN DATE '2026-01-12' AND DATE '2026-05-01'"
        )
    ).scalar_one()
    assert covered == 110


def test_date_attributes_are_correct(connection) -> None:
    """8 March 2026 is a Sunday — the DST date the generator already pins."""
    row = connection.execute(
        text(
            "SELECT date_key, year, quarter, month, day, day_of_week, "
            "day_name, is_weekend FROM warehouse.dim_date "
            "WHERE full_date = DATE '2026-03-08'"
        )
    ).one()
    assert row.date_key == 20260308
    assert (row.year, row.quarter, row.month, row.day) == (2026, 1, 3, 8)
    assert row.day_of_week == 7
    assert row.day_name == "Sunday"
    assert row.is_weekend is True


def test_weekdays_are_not_flagged_as_weekend(connection) -> None:
    row = connection.execute(
        text(
            "SELECT day_name, is_weekend FROM warehouse.dim_date "
            "WHERE full_date = DATE '2026-03-04'"
        )
    ).one()
    assert row.day_name == "Wednesday"
    assert row.is_weekend is False


def test_date_key_is_a_readable_yyyymmdd(connection) -> None:
    """A key someone can read in a query result without a join."""
    assert date_key(connection, date(2026, 1, 12)) == 20260112


# --------------------------------------------------------------------------
# Documentation drift
# --------------------------------------------------------------------------


def test_architecture_diagram_names_every_table() -> None:
    """The diagram is the model's public face; it must not go stale."""
    from pathlib import Path

    architecture = (
        Path(__file__).resolve().parents[1] / "docs" / "ARCHITECTURE.md"
    ).read_text(encoding="utf-8")
    for table in TABLES:
        assert table.name in architecture, f"{table.name} missing from ARCHITECTURE.md"


def test_dimensions_and_facts_are_disjoint_sets() -> None:
    assert not {t.name for t in DIMENSIONS} & {t.name for t in FACTS}
    assert len(TABLES) == len(DIMENSIONS) + len(FACTS)
