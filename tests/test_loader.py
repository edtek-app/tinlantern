"""Tests for the cohort loader.

The gate runs a small slice — a few hundred statements — so it stays fast.
The full 192k load is manual evidence, produced by `make ingest` and
quoted in the commit and the audit, not something to make every CI run pay
for.

Like the endpoint tests, these commit and cannot clean up: `raw` is
append-only. Assertions scope to the ids each test created.
"""

import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.main import app
from data.loader import (
    DEFAULT_BATCH_SIZE,
    LoadReport,
    Rejection,
    batched,
    load_statements,
    read_statements,
)

pytestmark = pytest.mark.m1

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def client(engine) -> TestClient:
    return TestClient(app)


def statement(**overrides) -> dict:
    payload = {
        "id": str(uuid4()),
        "actor": {
            "objectType": "Agent",
            "account": {
                "homePage": "https://tinlantern.example/xapi",
                "name": "s-00001",
            },
        },
        "verb": {"id": "http://adlnet.gov/expapi/verbs/experienced"},
        "object": {
            "objectType": "Activity",
            "id": "https://tinlantern.example/xapi/course/a/module/1",
        },
        "timestamp": "2026-03-01T10:00:00+00:00",
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def harness(client, engine):
    """A poster and a row-counter wired to the live app and database."""

    def post(batch: list[dict]) -> tuple[int, object]:
        response = client.post("/xapi/statements", json=batch)
        return response.status_code, response.json()

    def count_stored() -> int:
        with engine.connect() as connection:
            return connection.execute(
                text("SELECT count(*) FROM raw.statements")
            ).scalar_one()

    return post, count_stored


# --------------------------------------------------------------------------
# Batching
# --------------------------------------------------------------------------


def test_batches_are_the_requested_size() -> None:
    sizes = [len(b) for b in batched(range(250), 100)]
    assert sizes == [100, 100, 50]


def test_an_empty_source_yields_no_batches() -> None:
    assert list(batched([], 10)) == []


@pytest.mark.parametrize("size", [0, -1])
def test_a_nonpositive_batch_size_is_refused(size: int) -> None:
    with pytest.raises(ValueError, match="must be positive"):
        list(batched([1, 2], size))


def test_the_default_batch_size_is_a_deliberate_blast_radius() -> None:
    """Batches are atomic, so this number is how much one bad statement costs."""
    assert DEFAULT_BATCH_SIZE == 500


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------


def test_a_clean_load_stores_everything(harness) -> None:
    post, count_stored = harness
    statements = [statement() for _ in range(300)]

    report = load_statements(statements, post, count_stored, batch_size=100)

    assert report.ok
    assert report.sent == 300
    assert report.stored == 300
    assert report.duplicates == 0
    assert report.refused == 0
    assert report.batches == 3
    assert report.per_second > 0


def test_reloading_the_same_cohort_is_a_no_op(harness) -> None:
    """Idempotency is what makes an interrupted load safely restartable.

    A re-run must store nothing and refuse nothing — not produce a pile of
    409s. Every statement is already held with identical content, which is
    a successful retry, not a conflict.
    """
    post, count_stored = harness
    statements = [statement() for _ in range(120)]

    first = load_statements(statements, post, count_stored, batch_size=50)
    second = load_statements(statements, post, count_stored, batch_size=50)

    assert first.stored == 120
    assert second.ok, second.rejections
    assert second.stored == 0
    assert second.duplicates == 120
    assert second.sent == 120


def test_a_partial_reload_stores_only_what_is_missing(harness) -> None:
    """Restarting from the top after an interruption is the intended path."""
    post, count_stored = harness
    statements = [statement() for _ in range(100)]

    load_statements(statements[:60], post, count_stored, batch_size=30)
    resumed = load_statements(statements, post, count_stored, batch_size=30)

    assert resumed.stored == 40
    assert resumed.duplicates == 60
    assert resumed.ok


# --------------------------------------------------------------------------
# A rejection must not be swallowed
# --------------------------------------------------------------------------


def test_a_rejected_batch_is_reported_and_fails_the_load(harness) -> None:
    post, count_stored = harness
    good = [statement() for _ in range(40)]
    poisoned = [*good[:20], statement(sneaky="unmodelled"), *good[20:]]

    report = load_statements(poisoned, post, count_stored, batch_size=41)

    assert not report.ok, "a load with rejections must not report success"
    assert len(report.rejections) == 1
    assert report.rejections[0].status == 400
    assert report.refused == 41, "the whole atomic batch counts as refused"
    assert report.stored == 0


def test_a_rejection_in_one_batch_does_not_stop_the_load(harness) -> None:
    """Report every problem, rather than one restart per bad statement."""
    post, count_stored = harness
    clean = [statement() for _ in range(20)]
    dirty = [statement(sneaky="x") for _ in range(2)]

    report = load_statements(
        [*dirty[:1], *clean[:10], *dirty[1:], *clean[10:]],
        post,
        count_stored,
        batch_size=11,
    )

    assert len(report.rejections) == 2
    assert [r.batch for r in report.rejections] == [1, 2]
    assert not report.ok


def test_the_summary_names_the_failing_batches(harness) -> None:
    """The summary is evidence; it has to say what went wrong."""
    post, count_stored = harness
    report = load_statements([statement(sneaky="x")], post, count_stored, batch_size=10)
    summary = report.summary()
    assert "rejected    1" in summary
    assert "batch 1 -> 400" in summary


def test_a_report_with_no_rejections_is_ok() -> None:
    report = LoadReport(sent=10, stored=10, batches=1, seconds=0.5)
    assert report.ok
    assert report.per_second == 20.0
    assert "sent        10" in report.summary()


def test_a_report_counts_a_refused_batch_from_its_size() -> None:
    rejection = Rejection(batch=1, status=400, detail={"_size": 7})
    report = LoadReport(
        sent=10, stored=3, batches=2, seconds=1.0, rejections=(rejection,)
    )
    assert report.refused == 7
    assert report.duplicates == 0
    assert not report.ok


# --------------------------------------------------------------------------
# Reading the generated file
# --------------------------------------------------------------------------


def test_statements_are_read_one_per_line(tmp_path: Path) -> None:
    path = tmp_path / "statements.ndjson"
    written = [statement() for _ in range(3)]
    path.write_text("".join(json.dumps(s) + "\n" for s in written), encoding="utf-8")
    assert list(read_statements(path)) == written


def test_blank_lines_are_skipped(tmp_path: Path) -> None:
    path = tmp_path / "statements.ndjson"
    path.write_text(json.dumps(statement()) + "\n\n\n", encoding="utf-8")
    assert len(list(read_statements(path))) == 1


def test_the_generated_cohort_loads_a_slice(harness) -> None:
    """A real slice of generator output through the real endpoint.

    The gate keeps this small; `make ingest` runs the full 192k.
    """
    cohort = ROOT / "data" / "output" / "statements.ndjson"
    if not cohort.is_file():
        pytest.skip("no generated cohort; run `make seed`")

    post, count_stored = harness
    statements = []
    for entry in read_statements(cohort):
        entry["id"] = str(uuid4())
        statements.append(entry)
        if len(statements) == 400:
            break

    report = load_statements(statements, post, count_stored, batch_size=100)
    assert report.ok, report.summary()
    assert report.stored == 400


# --------------------------------------------------------------------------
# The command line
# --------------------------------------------------------------------------


def test_cli_parses_the_documented_flags() -> None:
    from data.ingest import build_parser

    args = build_parser().parse_args(
        ["--source", "s.ndjson", "--api", "http://x", "--batch-size", "7"]
    )
    assert args.source == Path("s.ndjson")
    assert args.api == "http://x"
    assert args.batch_size == 7


def test_cli_defaults_match_the_generator_output() -> None:
    from data.ingest import DEFAULT_SOURCE, build_parser

    args = build_parser().parse_args([])
    assert Path("data/output/statements.ndjson") == DEFAULT_SOURCE
    assert args.batch_size == DEFAULT_BATCH_SIZE


def test_cli_exits_non_zero_when_the_cohort_is_missing(tmp_path: Path) -> None:
    from data.ingest import main

    assert main(["--source", str(tmp_path / "absent.ndjson")]) == 2


def test_make_ingest_invokes_the_module() -> None:
    assert "python -m data.ingest" in (ROOT / "Makefile").read_text(encoding="utf-8")
