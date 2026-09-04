"""Tests for append-only raw statement storage.

These need a real database and **fail rather than skip** without one — see
`tests/conftest.py`. Storage behaviour cannot be verified against a mock:
the append-only guarantee, the conflict detection, and the sequence
watermark are all properties of Postgres, not of our Python.

Every test runs inside a transaction that is rolled back, so they leave no
rows behind — which matters more than usual here, since append-only means
a stray row cannot simply be deleted afterwards.
"""

import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError

from app.raw import (
    RejectionReason,
    StoreStatus,
    canonical,
    high_water_mark,
    record_rejection,
    store_statement,
    store_statements,
    wire_payload,
)
from app.xapi import Statement

pytestmark = pytest.mark.m1

ROOT = Path(__file__).resolve().parents[1]
COHORT = ROOT / "data" / "output" / "statements.ndjson"


def statement(**overrides) -> Statement:
    """A minimal valid statement with a fresh id."""
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
    return Statement.model_validate(payload)


def rows(connection) -> int:
    return connection.execute(text("SELECT count(*) FROM raw.statements")).scalar_one()


def rejections(connection) -> list[dict]:
    result = connection.execute(
        text("SELECT statement_id, reason, detail FROM raw.rejections ORDER BY 1")
    )
    return [dict(row._mapping) for row in result]


# --------------------------------------------------------------------------
# Round trip
# --------------------------------------------------------------------------


def test_a_stored_statement_round_trips(connection) -> None:
    """What comes back out must still satisfy the contract it went in under."""
    original = statement()
    outcome = store_statement(connection, original)
    assert outcome.status is StoreStatus.STORED
    assert outcome.ingest_seq is not None

    held = connection.execute(
        text("SELECT payload FROM raw.statements WHERE statement_id = :i"),
        {"i": original.id},
    ).scalar_one()
    assert Statement.model_validate(held) == original


def test_received_at_is_set_by_the_database(connection) -> None:
    stored = statement()
    store_statement(connection, stored)
    received = connection.execute(
        text("SELECT received_at FROM raw.statements WHERE statement_id = :i"),
        {"i": stored.id},
    ).scalar_one()
    assert received is not None and received.tzinfo is not None


def test_storing_without_an_id_is_refused(connection) -> None:
    """The caller assigns an id; storage will not invent one silently."""
    anonymous = statement()
    anonymous = anonymous.model_copy(update={"id": None})
    with pytest.raises(ValueError, match="without an id"):
        store_statement(connection, anonymous)


# --------------------------------------------------------------------------
# Idempotency, and the conflict that must not be swallowed
# --------------------------------------------------------------------------


def test_storing_the_same_statement_twice_leaves_one_row(connection) -> None:
    original = statement()
    before = rows(connection)
    assert store_statement(connection, original).status is StoreStatus.STORED
    assert store_statement(connection, original).status is StoreStatus.DUPLICATE
    assert rows(connection) == before + 1


def test_a_reordered_payload_is_a_retry_not_a_conflict(connection) -> None:
    """A well-behaved client must not get a spurious 409 for key order.

    JSON objects are unordered. A client that serialises the same statement
    with its keys in a different order is retrying, and treating that as a
    conflict would punish correct behaviour.
    """
    original = statement()
    store_statement(connection, original)

    payload = wire_payload(original)
    shuffled = dict(reversed(list(payload.items())))
    assert list(shuffled) != list(payload)
    assert canonical(shuffled) == canonical(payload)

    outcome = store_statement(connection, Statement.model_validate(shuffled))
    assert outcome.status is StoreStatus.DUPLICATE
    assert rejections(connection) == []


def test_same_id_different_content_is_a_conflict(connection) -> None:
    """A different event wearing a taken name must not overwrite or vanish."""
    original = statement()
    store_statement(connection, original)

    impostor = statement(
        id=str(original.id),
        verb={"id": "http://adlnet.gov/expapi/verbs/completed"},
    )
    outcome = store_statement(connection, impostor)
    assert outcome.status is StoreStatus.CONFLICT

    held = connection.execute(
        text("SELECT payload FROM raw.statements WHERE statement_id = :i"),
        {"i": original.id},
    ).scalar_one()
    assert Statement.model_validate(held) == original, "the original was overwritten"


def test_a_conflict_is_logged_not_dropped(connection) -> None:
    """M1's criterion: invalid statements are logged, never silently lost."""
    original = statement()
    store_statement(connection, original)
    impostor = statement(
        id=str(original.id),
        verb={"id": "http://adlnet.gov/expapi/verbs/completed"},
    )
    store_statement(connection, impostor)

    logged = rejections(connection)
    assert len(logged) == 1
    assert logged[0]["statement_id"] == original.id
    assert logged[0]["reason"] == RejectionReason.ID_CONFLICT
    assert str(original.id) in logged[0]["detail"]


def test_the_rejected_payload_is_kept_verbatim(connection) -> None:
    """The refused content must be recoverable, not just its id."""
    original = statement()
    store_statement(connection, original)
    impostor = statement(
        id=str(original.id),
        verb={"id": "http://adlnet.gov/expapi/verbs/completed"},
    )
    store_statement(connection, impostor)

    stored_payload = connection.execute(
        text("SELECT payload FROM raw.rejections WHERE statement_id = :i"),
        {"i": original.id},
    ).scalar_one()
    assert canonical(stored_payload) == canonical(wire_payload(impostor))


def test_a_rejection_carries_enough_to_diagnose(connection) -> None:
    """A rejection table nobody can act on fails the same way a log line does.

    Everything needed to understand and replay the refusal must come back
    from one row: which statement, why, exactly what was sent, and when.
    """
    original = statement()
    store_statement(connection, original)
    impostor = statement(
        id=str(original.id),
        verb={"id": "http://adlnet.gov/expapi/verbs/completed"},
    )
    store_statement(connection, impostor)

    row = (
        connection.execute(
            text(
                "SELECT rejection_id, statement_id, payload, reason, detail, "
                "received_at FROM raw.rejections WHERE statement_id = :i"
            ),
            {"i": original.id},
        )
        .one()
        ._mapping
    )

    assert row["rejection_id"] > 0
    assert row["statement_id"] == original.id
    assert row["reason"] == RejectionReason.ID_CONFLICT
    assert row["detail"] and str(original.id) in row["detail"]
    assert row["received_at"] is not None and row["received_at"].tzinfo is not None
    # The offending payload must be replayable, not merely summarised.
    assert Statement.model_validate(row["payload"]) == impostor


def test_rejections_accept_a_payload_with_no_id(connection) -> None:
    """A malformed payload may have no id at all; the column is nullable."""
    rejection_id = record_rejection(
        connection,
        {"not": "a statement"},
        RejectionReason.INVALID_STATEMENT,
        "missing actor",
    )
    assert rejection_id > 0


# --------------------------------------------------------------------------
# Append-only, enforced by the database
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "operation",
    [
        "UPDATE raw.statements SET payload = '{}'::jsonb",
        "DELETE FROM raw.statements",
        "TRUNCATE raw.statements",
    ],
)
def test_mutating_stored_statements_is_refused(connection, operation: str) -> None:
    """Append-only is a database guarantee, not a convention (ADR-0005)."""
    store_statement(connection, statement())
    with pytest.raises(DatabaseError, match="append-only"):
        connection.execute(text(operation))


@pytest.mark.parametrize(
    "operation",
    ["UPDATE raw.rejections SET reason = 'x'", "DELETE FROM raw.rejections"],
)
def test_mutating_rejections_is_refused(connection, operation: str) -> None:
    """The rejection log is evidence; it must not be editable either."""
    record_rejection(connection, {"a": 1}, RejectionReason.INVALID_STATEMENT, "test")
    with pytest.raises(DatabaseError, match="append-only"):
        connection.execute(text(operation))


def test_an_update_matching_no_rows_still_fails(connection) -> None:
    """Statement-level trigger: a silent no-op must not look like success."""
    with pytest.raises(DatabaseError, match="append-only"):
        connection.execute(
            text(
                "UPDATE raw.statements SET payload = '{}'::jsonb "
                "WHERE statement_id = :i"
            ),
            {"i": UUID(int=0)},
        )


# --------------------------------------------------------------------------
# The watermark M2 will page by
# --------------------------------------------------------------------------


def test_ingest_seq_increases_with_each_stored_statement(connection) -> None:
    sequences = [store_statement(connection, statement()).ingest_seq for _ in range(5)]
    assert sequences == sorted(sequences)
    assert len(set(sequences)) == 5


def test_high_water_mark_tracks_the_latest_row(connection) -> None:
    before = high_water_mark(connection)
    outcome = store_statement(connection, statement())
    assert high_water_mark(connection) == outcome.ingest_seq > before


def test_a_duplicate_does_not_advance_the_watermark(connection) -> None:
    """M2 must not be handed a new marker for a row that was not written."""
    original = statement()
    store_statement(connection, original)
    mark = high_water_mark(connection)
    assert store_statement(connection, original).status is StoreStatus.DUPLICATE
    assert high_water_mark(connection) == mark


# --------------------------------------------------------------------------
# Batch load — M1's >=10k criterion, against real generated data
# --------------------------------------------------------------------------


def test_batch_load_of_ten_thousand_statements(connection) -> None:
    """The gate's volume criterion, using the generator's own output.

    Falls back to synthesised statements when no cohort has been generated,
    so a fresh checkout still exercises the volume path.
    """
    target = 10_000
    if COHORT.is_file():
        statements = []
        with COHORT.open(encoding="utf-8") as handle:
            for line in handle:
                statements.append(Statement.model_validate(json.loads(line)))
                if len(statements) == target:
                    break
    else:
        statements = [statement() for _ in range(target)]

    assert len(statements) == target
    before = rows(connection)
    outcomes = store_statements(connection, statements)

    assert len(outcomes) == target
    assert all(o.status is StoreStatus.STORED for o in outcomes)
    assert rows(connection) == before + target
    assert len({o.ingest_seq for o in outcomes}) == target


def test_a_batch_reports_one_outcome_per_statement_in_order(connection) -> None:
    """Partial success must be legible: which of these landed, and which not."""
    first, second = statement(), statement()
    store_statement(connection, first)
    impostor = statement(
        id=str(first.id), verb={"id": "http://adlnet.gov/expapi/verbs/completed"}
    )

    outcomes = store_statements(connection, [second, impostor, first])
    assert [o.status for o in outcomes] == [
        StoreStatus.STORED,
        StoreStatus.CONFLICT,
        StoreStatus.DUPLICATE,
    ]
    assert [o.statement_id for o in outcomes] == [second.id, first.id, first.id]
