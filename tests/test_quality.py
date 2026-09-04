"""Tests for the warehouse data-quality checks.

Every ABSOLUTE check is mutation-verified: the test introduces the defect
it exists to catch and asserts the check fails. A check that has never
been seen to fail is unverified, and a suite of unverified checks is worse
than none — it reports green while looking at nothing.

**State discipline.** These checks read table-wide state; that is their
job. The *tests* work inside a rolled-back transaction, introduce their
own defect, and assert on that one check's verdict — never on the
warehouse's global condition, which depends on whatever else has run.
"""

import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import text

from pipeline.etl import JOB_NAME, load_batch, run
from pipeline.quality import (
    CHECKS,
    GRADED_IRIS,
    CheckResult,
    Severity,
    attempt_number_populated,
    attempt_numbers_are_contiguous,
    facts_carry_required_fields,
    failures,
    no_outstanding_rejections,
    reconcile_activity,
    reconcile_assessment,
    run_checks,
    schema_defences_intact,
)

pytestmark = pytest.mark.m2

BASE = datetime(2026, 2, 3, 9, 0, tzinfo=UTC)


def statement(verb: str = "experienced", result: dict | None = None) -> dict:
    payload = {
        "id": str(uuid4()),
        "actor": {
            "objectType": "Agent",
            "account": {
                "homePage": "https://elsewhere.invalid",
                "name": f"s-{uuid4().hex[:8]}",
            },
        },
        "verb": {
            "id": f"http://adlnet.gov/expapi/verbs/{verb}",
            "display": {"en": verb},
        },
        "object": {
            "objectType": "Activity",
            "id": f"urn:a:{uuid4()}",
            "definition": {"name": {"en": "An Item"}},
        },
        "timestamp": BASE.isoformat(),
        "context": {
            "contextActivities": {
                "parent": [
                    {
                        "objectType": "Activity",
                        "id": f"urn:c:{uuid4()}",
                        "definition": {
                            "name": {"en": "A Course"},
                            "type": "http://adlnet.gov/expapi/activities/course",
                        },
                    }
                ]
            }
        },
    }
    if result is not None:
        payload["result"] = result
    return payload


def graded() -> dict:
    return statement(
        "passed",
        {
            "score": {"scaled": 0.9, "raw": 90, "min": 0, "max": 100},
            "success": True,
            "completion": True,
        },
    )


def ingest(connection, payload: dict) -> int:
    """Put a statement in raw, returning its ingest_seq."""
    return connection.execute(
        text(
            "INSERT INTO raw.statements (statement_id, payload) "
            "VALUES (:i, CAST(:p AS jsonb)) RETURNING ingest_seq"
        ),
        {"i": payload["id"], "p": json.dumps(payload)},
    ).scalar_one()


def set_watermark(connection, value: int) -> None:
    connection.execute(
        text(
            "INSERT INTO warehouse.etl_state (job_name, last_ingest_seq) "
            "VALUES (:j, :v) ON CONFLICT (job_name) DO UPDATE "
            "SET last_ingest_seq = EXCLUDED.last_ingest_seq"
        ),
        {"j": JOB_NAME, "v": value},
    )


@pytest.fixture
def caught_up(connection):
    """A warehouse with nothing pending, inside this test's transaction.

    Reconciliation is table-wide by nature, so "the check passes" is a
    statement about the whole database — and `raw` legitimately holds
    statements from other suites that were never put through the ETL.
    Rather than assume a clean baseline, these tests create one by running
    the real ETL, which is rolled back with everything else.
    """
    run(connection)
    return connection


# --------------------------------------------------------------------------
# The suite's shape
# --------------------------------------------------------------------------


def test_every_check_declares_a_severity(connection) -> None:
    """Adding a check must be a decision, not a default."""
    for result in run_checks(connection):
        assert isinstance(result.severity, Severity)
        assert result.name and result.detail


def test_only_absolute_failures_are_fatal() -> None:
    reported = CheckResult("x", Severity.REPORTED, passed=False, detail="")
    absolute = CheckResult("y", Severity.ABSOLUTE, passed=False, detail="")
    assert not reported.fatal
    assert absolute.fatal
    assert failures((reported, absolute)) == (absolute,)


def test_a_reported_failure_does_not_fail_the_run() -> None:
    """The whole point of the split: no crying wolf on optional fields."""
    results = (CheckResult("n", Severity.REPORTED, passed=False, detail="nulls"),)
    assert failures(results) == ()


def test_the_suite_runs_every_declared_check(connection) -> None:
    assert len(run_checks(connection)) == len(CHECKS)


# --------------------------------------------------------------------------
# Reconciliation — mutation-verified
# --------------------------------------------------------------------------


def test_reconcile_activity_passes_when_everything_is_loaded(caught_up) -> None:
    payload = statement()
    seq = ingest(caught_up, payload)
    load_batch(caught_up, [(seq, payload)])
    set_watermark(caught_up, seq)
    assert reconcile_activity(caught_up).passed


def test_reconcile_activity_fails_on_a_silently_dropped_statement(
    connection,
) -> None:
    """The defect nothing else could catch.

    A statement sits in raw below the watermark, is in neither fact table
    nor the rejection log. Foreign keys cannot see this — there is no row
    to be inconsistent.
    """
    payload = statement()
    seq = ingest(connection, payload)
    set_watermark(connection, seq)  # claim it was processed; never load it

    result = reconcile_activity(connection)
    assert not result.passed
    assert result.fatal
    assert "MISSING" in result.detail


def test_reconcile_activity_counts_a_rejection_as_accounted_for(
    caught_up,
) -> None:
    """A recorded rejection is not a loss; it must not fail the check."""
    payload = statement()
    payload.pop("context")
    seq = ingest(caught_up, payload)
    load_batch(caught_up, [(seq, payload)])
    set_watermark(caught_up, seq)
    assert reconcile_activity(caught_up).passed


def test_reconcile_assessment_fails_when_only_graded_rows_are_dropped(
    caught_up,
) -> None:
    """The narrower check earns its place here.

    fact_activity still holds the statement, so reconcile_activity is
    satisfied. Only the graded-subset check notices the missing outcome —
    which is exactly the bug the headline check cannot see.
    """
    payload = graded()
    seq = ingest(caught_up, payload)
    load_batch(caught_up, [(seq, payload)])
    set_watermark(caught_up, seq)

    caught_up.execute(
        text("DELETE FROM warehouse.fact_assessment WHERE statement_id = :i"),
        {"i": payload["id"]},
    )

    assert reconcile_activity(caught_up).passed, "activity is intact"
    narrowed = reconcile_assessment(caught_up)
    assert not narrowed.passed
    assert narrowed.fatal


def test_graded_iris_match_the_verbs_that_produce_assessment_rows() -> None:
    assert len(GRADED_IRIS) == 3
    assert all(iri.startswith(("http://", "https://")) for iri in GRADED_IRIS)


# --------------------------------------------------------------------------
# Model guarantees — mutation-verified
# --------------------------------------------------------------------------


def test_outstanding_rejections_fail_the_suite(connection) -> None:
    """The assertion the ETL handed to this suite."""
    payload = statement()
    payload.pop("context")
    seq = ingest(connection, payload)
    load_batch(connection, [(seq, payload)])

    result = no_outstanding_rejections(connection)
    assert not result.passed
    assert result.fatal


def test_attempt_number_populated_fails_on_a_null(connection) -> None:
    payload = graded()
    seq = ingest(connection, payload)
    load_batch(connection, [(seq, payload)])
    assert attempt_number_populated(connection).passed

    connection.execute(
        text(
            "UPDATE warehouse.fact_assessment SET attempt_number = NULL "
            "WHERE statement_id = :i"
        ),
        {"i": payload["id"]},
    )
    assert not attempt_number_populated(connection).passed


def test_contiguity_fails_on_a_gap_in_numbering(connection) -> None:
    """The damage insert-time numbering does under late arrival."""
    payload = graded()
    seq = ingest(connection, payload)
    load_batch(connection, [(seq, payload)])
    assert attempt_numbers_are_contiguous(connection).passed

    connection.execute(
        text(
            "UPDATE warehouse.fact_assessment SET attempt_number = 4 "
            "WHERE statement_id = :i"
        ),
        {"i": payload["id"]},
    )
    result = attempt_numbers_are_contiguous(connection)
    assert not result.passed
    assert result.fatal


def test_required_fields_check_fails_when_a_column_is_emptied(
    connection,
) -> None:
    payload = statement()
    seq = ingest(connection, payload)
    load_batch(connection, [(seq, payload)])
    assert facts_carry_required_fields(connection).passed

    connection.execute(
        text("ALTER TABLE warehouse.fact_activity ALTER COLUMN verb DROP NOT NULL")
    )
    connection.execute(
        text("UPDATE warehouse.fact_activity SET verb = NULL WHERE statement_id = :i"),
        {"i": payload["id"]},
    )
    assert not facts_carry_required_fields(connection).passed


# --------------------------------------------------------------------------
# Schema defences — mutation-verified
# --------------------------------------------------------------------------


def test_schema_defences_pass_on_an_intact_schema(connection) -> None:
    assert schema_defences_intact(connection).passed


def test_schema_defences_fail_when_a_foreign_key_is_dropped(connection) -> None:
    """The failure mode an orphan-row query could never detect.

    Foreign keys make orphans unrepresentable, so hunting for orphan rows
    is theatre. What can actually change is the schema — this proves the
    check notices when it does.
    """
    name = connection.execute(
        text(
            "SELECT constraint_name FROM information_schema.table_constraints "
            "WHERE constraint_schema = 'warehouse' "
            "AND constraint_type = 'FOREIGN KEY' AND table_name = 'fact_activity' "
            "LIMIT 1"
        )
    ).scalar_one()
    connection.execute(
        text(f'ALTER TABLE warehouse.fact_activity DROP CONSTRAINT "{name}"')
    )

    result = schema_defences_intact(connection)
    assert not result.passed
    assert result.fatal


def test_schema_defences_fail_when_the_grain_constraint_is_dropped(
    connection,
) -> None:
    """Losing this would silently make the ETL non-idempotent."""
    name = connection.execute(
        text(
            "SELECT c.conname FROM pg_constraint c "
            "JOIN pg_class t ON t.oid = c.conrelid "
            "JOIN pg_namespace n ON n.oid = t.relnamespace "
            "WHERE n.nspname = 'warehouse' AND t.relname = 'fact_assessment' "
            "AND c.contype = 'u' LIMIT 1"
        )
    ).scalar_one()
    connection.execute(
        text(f'ALTER TABLE warehouse.fact_assessment DROP CONSTRAINT "{name}"')
    )
    assert not schema_defences_intact(connection).passed


# --------------------------------------------------------------------------
# The command line
# --------------------------------------------------------------------------


def test_the_cli_exit_code_agrees_with_its_own_report(capsys) -> None:
    """The real contract: non-zero iff the run reported an ABSOLUTE failure.

    Asserting a fixed exit code would depend on whatever state the database
    happens to be in. Asserting that the code agrees with the report this
    same invocation printed is deterministic, and it is the property that
    matters — an exit code disagreeing with the report is the bug worth
    catching.
    """
    from pipeline.dq import main

    code = main([])
    printed = capsys.readouterr().out

    assert "data quality" in printed
    for check in CHECKS:
        assert check.__name__ in printed, f"{check.__name__} missing from the report"

    reported_failure = any(
        line.strip().startswith("FAIL") for line in printed.splitlines()
    )
    assert code == (1 if reported_failure else 0), (
        f"exit code {code} contradicts the report"
    )


def test_make_dq_invokes_the_module() -> None:
    from pathlib import Path

    makefile = (Path(__file__).resolve().parents[1] / "Makefile").read_text("utf-8")
    assert "python -m pipeline.dq" in makefile


def test_readme_documents_the_sequence() -> None:
    """A stranger looks in the README, not in a module docstring."""
    from pathlib import Path

    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text("utf-8")
    assert "make etl && make dq" in readme
