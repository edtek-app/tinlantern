"""Tests for the xAPI ingestion endpoint.

These exercise the endpoint over HTTP against a real database, because the
guarantees under test are transactional: a refused batch must leave no
rows, and the rejection rows must survive the rollback that discards them.
Neither property is observable through a mocked storage layer.

Unlike the storage tests, these commit, and `raw.*` is append-only so
there is no teardown. Two consequences:

* Every assertion is scoped to the ids the test created. A table-wide
  count would silently become an assertion about every test that ever ran.
* A development database accumulates rows across runs. `make db-reset`
  rebuilds the raw tables empty; CI starts fresh each time.
"""

import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.main import app
from app.raw import RejectionReason
from app.xapi import Statement

pytestmark = pytest.mark.m1

ROOT = Path(__file__).resolve().parents[1]
COHORT = ROOT / "data" / "output" / "statements.ndjson"


@pytest.fixture
def client(engine) -> TestClient:
    """A client for the app. Depends on `engine` so a missing database
    fails loudly here too, rather than surfacing as a confusing 500."""
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


def stored(engine, ids: list[str]) -> int:
    """How many of these ids are held in raw.statements."""
    with engine.connect() as connection:
        return connection.execute(
            text(
                "SELECT count(*) FROM raw.statements "
                "WHERE statement_id = ANY(CAST(:ids AS uuid[]))"
            ),
            {"ids": ids},
        ).scalar_one()


def rejection_rows(engine, statement_id: str) -> list[dict]:
    with engine.connect() as connection:
        result = connection.execute(
            text(
                "SELECT reason, detail, payload FROM raw.rejections "
                "WHERE statement_id = :i"
            ),
            {"i": statement_id},
        )
        return [dict(row._mapping) for row in result]


# --------------------------------------------------------------------------
# Happy paths
# --------------------------------------------------------------------------


def test_health_does_not_need_the_database(client) -> None:
    body = client.get("/health").json()
    # The name of this test is the assertion. Exact-payload equality was
    # over-specification: it broke when /health gained the provider
    # fields, reporting a schema addition as a database-independence
    # failure.
    assert body["status"] == "ok"


def test_a_single_statement_is_stored(client, engine) -> None:
    payload = statement()
    response = client.post("/xapi/statements", json=payload)
    assert response.status_code == 200
    assert response.json() == [payload["id"]]
    assert stored(engine, [payload["id"]]) == 1


def test_a_batch_is_stored_and_ids_returned_in_order(client, engine) -> None:
    batch = [statement() for _ in range(5)]
    response = client.post("/xapi/statements", json=batch)
    assert response.status_code == 200
    assert response.json() == [s["id"] for s in batch]
    assert stored(engine, [s["id"] for s in batch]) == 5


def test_reposting_the_same_statement_is_idempotent(client, engine) -> None:
    """The retry path: same response, still one row."""
    payload = statement()
    first = client.post("/xapi/statements", json=payload)
    second = client.post("/xapi/statements", json=payload)
    assert first.json() == second.json()
    assert second.status_code == 200
    assert stored(engine, [payload["id"]]) == 1


def test_an_empty_batch_is_accepted(client) -> None:
    assert client.post("/xapi/statements", json=[]).json() == []


# --------------------------------------------------------------------------
# Atomicity — asserted, not assumed
# --------------------------------------------------------------------------


def test_one_bad_statement_rejects_the_whole_batch(client, engine) -> None:
    good = [statement() for _ in range(4)]
    bad = statement(sneaky="unmodelled property")
    response = client.post("/xapi/statements", json=[*good[:2], bad, *good[2:]])

    assert response.status_code == 400
    assert stored(engine, [s["id"] for s in good]) == 0, "a fragment was stored"


def test_a_failed_ten_thousand_batch_stores_nothing(client, engine) -> None:
    """Atomicity at the scale the gate cares about.

    A partial write here would be the worst possible failure: thousands of
    rows landed, no marker saying which, and a client with no way to build
    a clean retry.
    """
    if COHORT.is_file():
        batch = []
        with COHORT.open(encoding="utf-8") as handle:
            for line in handle:
                batch.append(json.loads(line))
                if len(batch) == 10_000:
                    break
        # Give them fresh ids so this test is independent of what is held.
        for entry in batch:
            entry["id"] = str(uuid4())
    else:
        batch = [statement() for _ in range(10_000)]

    assert len(batch) == 10_000
    batch[7_777]["verb"] = {"id": "http://adlnet.gov/expapi/verbs/interacted"}

    response = client.post("/xapi/statements", json=batch)
    assert response.status_code == 400
    assert response.json()["detail"]["rejections"][0]["index"] == 7_777
    assert stored(engine, [s["id"] for s in batch]) == 0


def test_a_conflicting_batch_stores_nothing(client, engine) -> None:
    held = statement()
    assert client.post("/xapi/statements", json=held).status_code == 200

    impostor = statement(
        id=held["id"], verb={"id": "http://adlnet.gov/expapi/verbs/completed"}
    )
    fresh = [statement() for _ in range(3)]
    response = client.post("/xapi/statements", json=[*fresh, impostor])

    assert response.status_code == 409
    assert stored(engine, [s["id"] for s in fresh]) == 0


# --------------------------------------------------------------------------
# Rejections are logged, not dropped
# --------------------------------------------------------------------------


def test_an_invalid_statement_is_recorded_with_its_payload(client, engine) -> None:
    """M1's criterion, at the endpoint: refused is not the same as lost."""
    bad = statement(sneaky="unmodelled property")
    assert client.post("/xapi/statements", json=bad).status_code == 400

    rows = rejection_rows(engine, bad["id"])
    assert len(rows) == 1
    assert rows[0]["reason"] == RejectionReason.INVALID_STATEMENT
    assert "sneaky" in rows[0]["detail"]
    assert rows[0]["payload"] == bad, "the payload was not kept verbatim"


def test_a_rejection_names_its_position_in_the_batch(client, engine) -> None:
    """The client must not have to re-derive which statement poisoned it."""
    good = [statement() for _ in range(3)]
    bad = statement(sneaky="x")
    response = client.post("/xapi/statements", json=[*good, bad])

    body = response.json()["detail"]
    assert body["rejections"][0]["index"] == 3
    assert body["rejections"][0]["statementId"] == bad["id"]
    assert "batch index 3" in rejection_rows(engine, bad["id"])[0]["detail"]


def test_every_bad_statement_in_a_batch_is_reported(client) -> None:
    """One redeploy per error is a bad loop; report them all at once."""
    batch = [statement(), statement(sneaky="x"), statement(), statement(also="y")]
    body = client.post("/xapi/statements", json=batch).json()["detail"]
    assert [r["index"] for r in body["rejections"]] == [1, 3]


def test_a_conflict_is_recorded_after_the_rollback(client, engine) -> None:
    """The evidence must outlive the transaction that was discarded."""
    held = statement()
    client.post("/xapi/statements", json=held)
    impostor = statement(
        id=held["id"], verb={"id": "http://adlnet.gov/expapi/verbs/completed"}
    )
    assert client.post("/xapi/statements", json=impostor).status_code == 409

    rows = rejection_rows(engine, held["id"])
    assert [row["reason"] for row in rows] == [RejectionReason.ID_CONFLICT]
    assert rows[0]["payload"] == impostor


def test_a_statement_without_an_id_is_refused_and_recorded(client, engine) -> None:
    """Deviation from spec: an assigned id could not be idempotent."""
    payload = statement()
    del payload["id"]
    response = client.post("/xapi/statements", json=payload)

    assert response.status_code == 400
    rejection = response.json()["detail"]["rejections"][0]
    assert "no id" in rejection["detail"]
    assert "idempotent" in rejection["detail"]

    with engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM raw.rejections "
                    "WHERE statement_id IS NULL AND detail LIKE '%no id%'"
                )
            ).scalar_one()
            >= 1
        )


# --------------------------------------------------------------------------
# The 409 body must be diagnosable
# --------------------------------------------------------------------------


def test_a_conflict_response_names_the_offending_statement(client) -> None:
    """A bare 409 on a large batch tells the client nothing actionable."""
    held = statement()
    client.post("/xapi/statements", json=held)
    impostor = statement(
        id=held["id"], verb={"id": "http://adlnet.gov/expapi/verbs/completed"}
    )
    batch = [statement(), statement(), impostor]

    body = client.post("/xapi/statements", json=batch).json()["detail"]
    assert body["error"] == RejectionReason.ID_CONFLICT
    assert body["conflicts"] == [{"index": 2, "statementId": held["id"]}]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"actor": {"mbox": "mailto:a@b.co"}},
        "not an object",
        [{"nonsense": True}],
    ],
)
def test_malformed_input_is_refused(client, payload) -> None:
    assert client.post("/xapi/statements", json=payload).status_code == 400


def test_returned_ids_are_valid_uuids(client) -> None:
    batch = [statement() for _ in range(3)]
    for identifier in client.post("/xapi/statements", json=batch).json():
        UUID(identifier)


def test_a_stored_statement_can_be_read_back_as_a_statement(client, engine) -> None:
    payload = statement()
    client.post("/xapi/statements", json=payload)
    with engine.connect() as connection:
        held = connection.execute(
            text("SELECT payload FROM raw.statements WHERE statement_id = :i"),
            {"i": payload["id"]},
        ).scalar_one()
    assert Statement.model_validate(held) == Statement.model_validate(payload)
