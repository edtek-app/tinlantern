"""Data-quality checks over the warehouse.

Two kinds of check, and every check must declare which it is:

* **ABSOLUTE** — the model guarantees this. One violation is a failure.
* **REPORTED** — informational. Printed, never fails the run.

The distinction is deliberate. A check that fails on 0.1% nulls in a
genuinely optional field trains people to ignore the suite, and a suite
people ignore is worse than no suite. Forcing every check to name its own
category means adding one is a decision rather than a default.

The headline check is **reconciliation**: every statement at or below the
ETL watermark is either in the warehouse or in `etl_rejections`, and none
is in neither. That is the failure mode nothing else would notice —
foreign keys cannot catch a statement that was silently never loaded.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import Connection, text

from app.xapi import VERB_IRIS
from pipeline.warehouse.schema import GRADED_VERBS

#: The verb IRIs whose statements must produce a fact_assessment row.
GRADED_IRIS: tuple[str, ...] = tuple(VERB_IRIS[name] for name in sorted(GRADED_VERBS))


class Severity(StrEnum):
    """Whether a failing check fails the run."""

    ABSOLUTE = "absolute"
    REPORTED = "reported"


@dataclass(frozen=True, slots=True)
class CheckResult:
    """The outcome of one check."""

    name: str
    severity: Severity
    passed: bool
    detail: str

    @property
    def fatal(self) -> bool:
        """Whether this result should fail the run."""
        return self.severity is Severity.ABSOLUTE and not self.passed

    def line(self) -> str:
        mark = "ok  " if self.passed else ("FAIL" if self.fatal else "note")
        return f"  {mark}  {self.name:<34} {self.detail}"


def _watermark(connection: Connection) -> int:
    from pipeline.etl import watermark

    return watermark(connection)


def _scalar(connection: Connection, sql: str, **params) -> int:
    return connection.execute(text(sql), params).scalar_one()


# --------------------------------------------------------------------------
# Reconciliation — the checks that catch a silently dropped statement
# --------------------------------------------------------------------------


def reconcile_activity(connection: Connection) -> CheckResult:
    """Every loaded statement is in fact_activity or was rejected.

    ABSOLUTE. Foreign keys guarantee that what *is* in the warehouse is
    consistent; nothing but this notices a statement that never arrived.
    """
    mark = _watermark(connection)
    eligible = _scalar(
        connection,
        "SELECT count(*) FROM raw.statements WHERE ingest_seq <= :m",
        m=mark,
    )
    loaded = _scalar(
        connection,
        "SELECT count(*) FROM warehouse.fact_activity WHERE ingest_seq <= :m",
        m=mark,
    )
    rejected = _scalar(
        connection,
        "SELECT count(*) FROM warehouse.etl_rejections WHERE ingest_seq <= :m",
        m=mark,
    )
    accounted = loaded + rejected
    return CheckResult(
        name="reconcile_activity",
        severity=Severity.ABSOLUTE,
        passed=accounted == eligible,
        detail=(
            f"{eligible:,} eligible = {loaded:,} loaded + {rejected:,} rejected"
            + ("" if accounted == eligible else f"  MISSING {eligible - accounted:,}")
        ),
    )


def reconcile_assessment(connection: Connection) -> CheckResult:
    """Every graded statement is in fact_assessment or was rejected.

    ABSOLUTE. The activity reconciliation would not notice a bug that
    dropped *only* graded statements, because they would still be present
    in fact_activity — this narrows the same question to the subset.
    """
    mark = _watermark(connection)
    eligible = _scalar(
        connection,
        "SELECT count(*) FROM raw.statements WHERE ingest_seq <= :m "
        "AND payload->'verb'->>'id' = ANY(:iris)",
        m=mark,
        iris=list(GRADED_IRIS),
    )
    loaded = _scalar(
        connection,
        "SELECT count(*) FROM warehouse.fact_assessment WHERE ingest_seq <= :m",
        m=mark,
    )
    rejected = _scalar(
        connection,
        "SELECT count(*) FROM warehouse.etl_rejections WHERE ingest_seq <= :m "
        "AND payload->'verb'->>'id' = ANY(:iris)",
        m=mark,
        iris=list(GRADED_IRIS),
    )
    accounted = loaded + rejected
    return CheckResult(
        name="reconcile_assessment",
        severity=Severity.ABSOLUTE,
        passed=accounted == eligible,
        detail=(
            f"{eligible:,} graded = {loaded:,} loaded + {rejected:,} rejected"
            + ("" if accounted == eligible else f"  MISSING {eligible - accounted:,}")
        ),
    )


def no_outstanding_rejections(connection: Connection) -> CheckResult:
    """warehouse.etl_rejections is empty.

    ABSOLUTE. The ETL deliberately does not fail on historical rejections —
    an ETL that failed forever after one bad statement would be unusable —
    so asserting the warehouse is clean overall is this suite's job. That
    responsibility transfer is recorded in M2's acceptance criteria.
    """
    count = _scalar(connection, "SELECT count(*) FROM warehouse.etl_rejections")
    return CheckResult(
        name="no_outstanding_rejections",
        severity=Severity.ABSOLUTE,
        passed=count == 0,
        detail=f"{count:,} statements could not be modelled",
    )


# --------------------------------------------------------------------------
# Model guarantees
# --------------------------------------------------------------------------


def attempt_number_populated(connection: Connection) -> CheckResult:
    """Every fact_assessment row carries an attempt number.

    ABSOLUTE. The column was added on M3's anticipated need, and a column
    added on anticipation quietly stays null unless something insists.
    """
    missing = _scalar(
        connection,
        "SELECT count(*) FROM warehouse.fact_assessment WHERE attempt_number IS NULL",
    )
    return CheckResult(
        name="attempt_number_populated",
        severity=Severity.ABSOLUTE,
        passed=missing == 0,
        detail=f"{missing:,} rows without an attempt number",
    )


def attempt_numbers_are_contiguous(connection: Connection) -> CheckResult:
    """Attempts per learner-activity run 1..n with no gaps.

    ABSOLUTE. A gap would mean the renumbering missed a row — the exact
    damage a late-arriving statement does if numbering is derived at
    insert time rather than recomputed.
    """
    broken = _scalar(
        connection,
        """
        SELECT count(*) FROM (
            SELECT student_key, activity_key
            FROM warehouse.fact_assessment
            GROUP BY student_key, activity_key
            HAVING max(attempt_number) <> count(*)
                OR min(attempt_number) <> 1
        ) AS bad
        """,
    )
    return CheckResult(
        name="attempt_numbers_are_contiguous",
        severity=Severity.ABSOLUTE,
        passed=broken == 0,
        detail=f"{broken:,} learner-activity pairs numbered with gaps",
    )


def facts_carry_required_fields(connection: Connection) -> CheckResult:
    """No nulls in columns the model requires.

    ABSOLUTE. NOT NULL already enforces most of these; the check exists so
    that dropping a constraint in a future migration is caught rather than
    discovered downstream.
    """
    offenders = _scalar(
        connection,
        """
        SELECT
          (SELECT count(*) FROM warehouse.fact_activity
           WHERE verb IS NULL OR occurred_at IS NULL OR ingest_seq IS NULL)
        + (SELECT count(*) FROM warehouse.fact_assessment
           WHERE verb IS NULL OR occurred_at IS NULL OR ingest_seq IS NULL)
        """,
    )
    return CheckResult(
        name="facts_carry_required_fields",
        severity=Severity.ABSOLUTE,
        passed=offenders == 0,
        detail=f"{offenders:,} fact rows missing a required field",
    )


# --------------------------------------------------------------------------
# Schema defences — that the constraints still exist
# --------------------------------------------------------------------------


def schema_defences_intact(connection: Connection) -> CheckResult:
    """The foreign keys and grain constraints are still in place.

    ABSOLUTE, and deliberately NOT a check of the data. Foreign keys make
    orphan facts unrepresentable, so a check hunting for orphans could
    never fail and would be theatre. What *can* change is the schema: a
    future migration could drop a constraint and every downstream
    guarantee would quietly weaken. This asserts the defences exist.

    Do not "simplify" this into an orphan-row query. That is the version
    that cannot fail.
    """
    foreign_keys = _scalar(
        connection,
        """
        SELECT count(*) FROM information_schema.table_constraints
        WHERE constraint_schema = 'warehouse' AND constraint_type = 'FOREIGN KEY'
          AND table_name IN ('fact_activity', 'fact_assessment', 'dim_activity')
        """,
    )
    grains = _scalar(
        connection,
        """
        SELECT count(*) FROM pg_index i
        JOIN pg_class c ON c.oid = i.indrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = ANY(i.indkey)
        WHERE n.nspname = 'warehouse' AND i.indisunique
          AND c.relname IN ('fact_activity', 'fact_assessment')
          AND a.attname = 'statement_id'
        """,
    )
    # Four dimension references on each fact, plus dim_activity -> dim_course.
    expected_fks = 9
    passed = foreign_keys >= expected_fks and grains == 2
    return CheckResult(
        name="schema_defences_intact",
        severity=Severity.ABSOLUTE,
        passed=passed,
        detail=(
            f"{foreign_keys} foreign keys (expect >={expected_fks}), "
            f"{grains} statement_id unique indexes (expect 2)"
        ),
    )


# --------------------------------------------------------------------------
# Reported only
# --------------------------------------------------------------------------


def optional_score_coverage(connection: Connection) -> CheckResult:
    """How many graded rows carry a score.

    REPORTED. Our generator always scores, but xAPI does not require it and
    another emitter may legitimately omit one. Failing on this would train
    people to ignore the suite.
    """
    total = _scalar(connection, "SELECT count(*) FROM warehouse.fact_assessment")
    scored = _scalar(
        connection,
        "SELECT count(*) FROM warehouse.fact_assessment WHERE scaled_score IS NOT NULL",
    )
    share = (scored / total * 100) if total else 100.0
    return CheckResult(
        name="optional_score_coverage",
        severity=Severity.REPORTED,
        passed=True,
        detail=f"{scored:,}/{total:,} graded rows scored ({share:.1f}%)",
    )


def dimension_row_counts(connection: Connection) -> CheckResult:
    """Row counts across the dimensions.

    REPORTED. Useful context beside a failure; not a pass/fail condition,
    since a correct warehouse can hold any number of learners.
    """
    counts = {
        name: _scalar(connection, f"SELECT count(*) FROM warehouse.{name}")
        for name in ("dim_student", "dim_course", "dim_activity")
    }
    return CheckResult(
        name="dimension_row_counts",
        severity=Severity.REPORTED,
        passed=True,
        detail=", ".join(f"{k.removeprefix('dim_')}={v:,}" for k, v in counts.items()),
    )


#: Every check, in the order they are reported.
CHECKS = (
    reconcile_activity,
    reconcile_assessment,
    no_outstanding_rejections,
    attempt_number_populated,
    attempt_numbers_are_contiguous,
    facts_carry_required_fields,
    schema_defences_intact,
    optional_score_coverage,
    dimension_row_counts,
)


def run_checks(connection: Connection) -> tuple[CheckResult, ...]:
    """Run every check and return its result."""
    return tuple(check(connection) for check in CHECKS)


def failures(results: tuple[CheckResult, ...]) -> tuple[CheckResult, ...]:
    """The results that should fail the run."""
    return tuple(result for result in results if result.fatal)
