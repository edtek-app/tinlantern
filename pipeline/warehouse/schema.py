"""Names and shapes of the warehouse star schema.

Structural metadata only — no DDL. Alembic owns DDL (ADR-0001); this
describes what those migrations created so the ETL and the data-quality
checks can agree on it without restating table names in three files.
"""

from __future__ import annotations

from dataclasses import dataclass

SCHEMA = "warehouse"


@dataclass(frozen=True, slots=True)
class Table:
    """One warehouse table and the columns other code depends on."""

    name: str
    key: str
    #: Columns that must never be null. Checked by the DQ suite.
    required: tuple[str, ...] = ()
    #: (column, referenced table) pairs. Checked for referential integrity.
    references: tuple[tuple[str, str], ...] = ()
    #: Column whose uniqueness defines the table's grain, when it has one.
    grain: str | None = None


DIM_STUDENT = Table(
    name="dim_student",
    key="student_key",
    required=("learner_identifier", "account_home_page"),
    grain="learner_identifier",
)

DIM_COURSE = Table(
    name="dim_course",
    key="course_key",
    required=("course_iri", "course_slug"),
    grain="course_iri",
)

DIM_ACTIVITY = Table(
    name="dim_activity",
    key="activity_key",
    required=("activity_iri", "course_key"),
    references=(("course_key", "dim_course"),),
    grain="activity_iri",
)

DIM_DATE = Table(
    name="dim_date",
    key="date_key",
    required=("full_date", "year", "iso_week", "day_of_week", "is_weekend"),
    grain="full_date",
)

_FACT_REFERENCES = (
    ("student_key", "dim_student"),
    ("course_key", "dim_course"),
    ("activity_key", "dim_activity"),
    ("date_key", "dim_date"),
)

FACT_ACTIVITY = Table(
    name="fact_activity",
    key="activity_event_key",
    required=("statement_id", "verb", "occurred_at", "ingest_seq"),
    references=_FACT_REFERENCES,
    # One row per statement. The ETL can therefore re-run over the same
    # input and be stopped by the database rather than by its own bookkeeping.
    grain="statement_id",
)

FACT_ASSESSMENT = Table(
    name="fact_assessment",
    key="assessment_result_key",
    required=("statement_id", "verb", "occurred_at", "ingest_seq"),
    references=_FACT_REFERENCES,
    grain="statement_id",
)

DIMENSIONS: tuple[Table, ...] = (DIM_STUDENT, DIM_COURSE, DIM_ACTIVITY, DIM_DATE)
FACTS: tuple[Table, ...] = (FACT_ACTIVITY, FACT_ASSESSMENT)
TABLES: tuple[Table, ...] = DIMENSIONS + FACTS

#: Verbs whose statements produce a fact_assessment row as well as the
#: fact_activity row every statement gets (ADR-0006).
GRADED_VERBS: frozenset[str] = frozenset({"passed", "failed", "submitted"})


def qualified(table: Table | str) -> str:
    """Return the schema-qualified name of a table."""
    return f"{SCHEMA}.{table.name if isinstance(table, Table) else table}"
