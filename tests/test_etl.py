"""Tests for the raw → warehouse ETL.

**State discipline.** Both `raw` and the warehouse are non-empty in any
real environment — a full cohort is loaded. Every test here inserts its own
statements with fresh ids and scopes assertions to them; none counts
warehouse rows globally, and none assumes the cohort is or is not present.

Because the ETL pages by a stored watermark, tests drive `load_batch`
directly with the rows they created rather than calling `run`, except
where the watermark itself is under test.
"""

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text

from pipeline.etl import (
    JOB_NAME,
    UnmodellableStatement,
    _course_activity,
    load_batch,
    run,
    watermark,
)

pytestmark = pytest.mark.m2

COURSE_IRI = "https://elsewhere.invalid/lms/offering/17"
BASE = datetime(2026, 2, 2, 9, 0, tzinfo=UTC)


def statement(
    verb: str = "experienced",
    *,
    course_iri: str = COURSE_IRI,
    object_iri: str | None = None,
    learner: str | None = None,
    at: datetime | None = None,
    result: dict | None = None,
    with_context: bool = True,
) -> dict:
    """A statement whose IRIs deliberately do not follow our conventions."""
    payload = {
        "id": str(uuid4()),
        "actor": {
            "objectType": "Agent",
            "account": {
                "homePage": "https://elsewhere.invalid",
                "name": learner or f"s-{uuid4().hex[:8]}",
            },
        },
        "verb": {
            "id": f"http://adlnet.gov/expapi/verbs/{verb}",
            "display": {"en": verb},
        },
        "object": {
            "objectType": "Activity",
            "id": object_iri or f"https://elsewhere.invalid/thing/{uuid4()}",
            "definition": {"name": {"en": "An Item"}},
        },
        "timestamp": (at or BASE).isoformat(),
    }
    if with_context:
        payload["context"] = {
            "registration": str(uuid4()),
            "contextActivities": {
                "parent": [
                    {
                        "objectType": "Activity",
                        "id": course_iri,
                        "definition": {
                            "name": {"en": "Somebody Else's Course"},
                            "type": "http://adlnet.gov/expapi/activities/course",
                        },
                    }
                ]
            },
        }
    if result is not None:
        payload["result"] = result
    return payload


def graded(scaled: float, verb: str = "passed", **kwargs) -> dict:
    return statement(
        verb,
        result={
            "score": {"scaled": scaled, "raw": scaled * 100, "min": 0, "max": 100},
            "success": verb == "passed",
            "completion": True,
        },
        **kwargs,
    )


def seq() -> int:
    """A synthetic ingest_seq well above anything a real load would use."""
    return 10_000_000 + int(uuid4().int % 1_000_000)


def load(connection, *statements) -> None:
    load_batch(connection, [(seq(), s) for s in statements])


def activity_rows(connection, ids: list[str]) -> int:
    return connection.execute(
        text(
            "SELECT count(*) FROM warehouse.fact_activity "
            "WHERE statement_id = ANY(CAST(:ids AS uuid[]))"
        ),
        {"ids": ids},
    ).scalar_one()


# --------------------------------------------------------------------------
# Loading into both facts
# --------------------------------------------------------------------------


def test_a_statement_lands_in_fact_activity(connection) -> None:
    payload = statement()
    load(connection, payload)
    assert activity_rows(connection, [payload["id"]]) == 1


def test_a_graded_statement_lands_in_both_facts(connection) -> None:
    """The overlap ADR-0006 chose, exercised by the loader."""
    payload = graded(0.82)
    load(connection, payload)
    for table in ("fact_activity", "fact_assessment"):
        assert (
            connection.execute(
                text(f"SELECT count(*) FROM warehouse.{table} WHERE statement_id = :i"),
                {"i": payload["id"]},
            ).scalar_one()
            == 1
        )


def test_an_ungraded_statement_stays_out_of_fact_assessment(connection) -> None:
    payload = statement("played")
    load(connection, payload)
    assert (
        connection.execute(
            text(
                "SELECT count(*) FROM warehouse.fact_assessment WHERE statement_id = :i"
            ),
            {"i": payload["id"]},
        ).scalar_one()
        == 0
    )


def test_scores_are_carried_into_the_fact(connection) -> None:
    payload = graded(0.73, "failed")
    load(connection, payload)
    row = connection.execute(
        text(
            "SELECT verb, scaled_score, raw_score, success, completion "
            "FROM warehouse.fact_assessment WHERE statement_id = :i"
        ),
        {"i": payload["id"]},
    ).one()
    assert row.verb == "failed"
    assert float(row.scaled_score) == pytest.approx(0.73)
    assert float(row.raw_score) == pytest.approx(73.0)
    assert row.success is False and row.completion is True


# --------------------------------------------------------------------------
# The course comes from context, never from the IRI
# --------------------------------------------------------------------------


def test_course_resolves_from_context_with_foreign_iri_shapes(connection) -> None:
    """Pins the property against a future "helpful" IRI-parsing optimisation.

    These IRIs follow no TinLantern convention — no /course/ segment, no
    predictable nesting. Only `context.contextActivities.parent` identifies
    the course, which is the xAPI-native answer and the only one that works
    for an emitter that is not ours.
    """
    course_iri = "urn:x-acme:offering:2026:BIO-140"
    payload = statement(
        course_iri=course_iri,
        object_iri="urn:x-acme:asset:9f3c1b",
    )
    load(connection, payload)

    row = connection.execute(
        text(
            "SELECT c.course_iri, a.activity_iri FROM warehouse.fact_activity f "
            "JOIN warehouse.dim_course   c USING (course_key) "
            "JOIN warehouse.dim_activity a USING (activity_key) "
            "WHERE f.statement_id = :i"
        ),
        {"i": payload["id"]},
    ).one()
    assert row.course_iri == course_iri
    assert row.activity_iri == "urn:x-acme:asset:9f3c1b"


def test_a_statement_about_a_course_is_its_own_course(connection) -> None:
    """`registered` targets the course itself and carries no parent."""
    course_iri = f"urn:x-acme:offering:{uuid4()}"
    payload = statement("registered", with_context=False)
    payload["object"] = {
        "objectType": "Activity",
        "id": course_iri,
        "definition": {
            "name": {"en": "A Course"},
            "type": "http://adlnet.gov/expapi/activities/course",
        },
    }
    load(connection, payload)
    assert (
        connection.execute(
            text(
                "SELECT c.course_iri FROM warehouse.fact_activity f "
                "JOIN warehouse.dim_course c USING (course_key) "
                "WHERE f.statement_id = :i"
            ),
            {"i": payload["id"]},
        ).scalar_one()
        == course_iri
    )


def test_course_activity_raises_without_context_or_a_course_object() -> None:
    with pytest.raises(UnmodellableStatement, match="no course context"):
        _course_activity(statement(with_context=False))


# --------------------------------------------------------------------------
# Conformed dimensions
# --------------------------------------------------------------------------


def test_one_learner_across_two_courses_is_one_dimension_row(connection) -> None:
    learner = f"s-{uuid4().hex[:8]}"
    load(
        connection,
        statement(learner=learner, course_iri=f"urn:c:{uuid4()}"),
        statement(learner=learner, course_iri=f"urn:c:{uuid4()}"),
    )
    assert (
        connection.execute(
            text(
                "SELECT count(*) FROM warehouse.dim_student "
                "WHERE learner_identifier = :i"
            ),
            {"i": learner},
        ).scalar_one()
        == 1
    )


def test_an_activity_is_linked_to_its_course(connection) -> None:
    course_iri = f"urn:c:{uuid4()}"
    object_iri = f"urn:a:{uuid4()}"
    load(connection, statement(course_iri=course_iri, object_iri=object_iri))
    assert (
        connection.execute(
            text(
                "SELECT c.course_iri FROM warehouse.dim_activity a "
                "JOIN warehouse.dim_course c USING (course_key) "
                "WHERE a.activity_iri = :i"
            ),
            {"i": object_iri},
        ).scalar_one()
        == course_iri
    )


# --------------------------------------------------------------------------
# Unmodellable statements are recorded, not dropped, and not fatal
# --------------------------------------------------------------------------


def test_a_statement_without_course_context_is_recorded(connection) -> None:
    payload = statement(with_context=False)
    report = load_batch(connection, [(seq(), payload)])

    assert report.rejected == 1
    assert activity_rows(connection, [payload["id"]]) == 0
    row = connection.execute(
        text(
            "SELECT reason, detail, payload FROM warehouse.etl_rejections "
            "WHERE statement_id = :i"
        ),
        {"i": payload["id"]},
    ).one()
    assert row.reason == "unmodellable"
    assert "no course context" in row.detail
    assert row.payload == payload, "the payload must be recoverable verbatim"


def test_one_bad_statement_does_not_stop_the_batch(connection) -> None:
    """A 192k load must not halt over a single unmodellable row."""
    good = [statement() for _ in range(3)]
    report = load_batch(
        connection,
        [(seq(), s) for s in [good[0], statement(with_context=False), *good[1:]]],
    )
    assert report.rejected == 1
    assert report.activity_rows == 3
    assert activity_rows(connection, [s["id"] for s in good]) == 3


def test_an_actor_without_an_account_is_recorded(connection) -> None:
    payload = statement()
    payload["actor"] = {"objectType": "Agent", "name": "Nobody"}
    report = load_batch(connection, [(seq(), payload)])
    assert report.rejected == 1


# --------------------------------------------------------------------------
# Idempotency and incrementality
# --------------------------------------------------------------------------


def test_loading_the_same_statements_twice_adds_nothing(connection) -> None:
    """M2's headline criterion, at the loader."""
    payloads = [statement() for _ in range(4)]
    rows = [(seq(), s) for s in payloads]

    first = load_batch(connection, rows)
    second = load_batch(connection, rows)

    assert first.activity_rows == 4
    assert second.activity_rows == 0, "a second pass inserted rows"
    assert activity_rows(connection, [s["id"] for s in payloads]) == 4


def test_a_run_loads_only_what_is_above_the_watermark(connection) -> None:
    """Incrementality, driven through the real raw table and watermark."""
    connection.execute(
        text(
            "INSERT INTO warehouse.etl_state (job_name, last_ingest_seq) "
            "VALUES (:j, (SELECT COALESCE(MAX(ingest_seq), 0) FROM raw.statements)) "
            "ON CONFLICT (job_name) DO UPDATE "
            "SET last_ingest_seq = EXCLUDED.last_ingest_seq"
        ),
        {"j": JOB_NAME},
    )
    caught_up = run(connection)
    assert caught_up.read == 0, "nothing should be pending"

    fresh = statement()
    connection.execute(
        text(
            "INSERT INTO raw.statements (statement_id, payload) "
            "VALUES (:i, CAST(:p AS jsonb))"
        ),
        {"i": fresh["id"], "p": json.dumps(fresh)},
    )

    incremental = run(connection)
    assert incremental.read == 1
    assert activity_rows(connection, [fresh["id"]]) == 1
    assert watermark(connection) > caught_up.watermark


# --------------------------------------------------------------------------
# attempt_number follows event time, not arrival
# --------------------------------------------------------------------------


def test_attempts_are_numbered_by_event_time(connection) -> None:
    learner = f"s-{uuid4().hex[:8]}"
    item = f"urn:a:{uuid4()}"
    first, second = (
        graded(0.3, "failed", learner=learner, object_iri=item, at=BASE),
        graded(
            0.8, "passed", learner=learner, object_iri=item, at=BASE + timedelta(days=2)
        ),
    )
    load(connection, first, second)

    numbering = dict(
        connection.execute(
            text(
                "SELECT statement_id::text, attempt_number "
                "FROM warehouse.fact_assessment "
                "WHERE statement_id = ANY(CAST(:ids AS uuid[]))"
            ),
            {"ids": [first["id"], second["id"]]},
        ).all()
    )
    assert numbering[first["id"]] == 1
    assert numbering[second["id"]] == 2


def test_a_late_arriving_earlier_attempt_renumbers_the_rest(connection) -> None:
    """The property insert-time numbering would get wrong.

    Arrival order and event order are unrelated (ADR-0005). Here the
    EARLIEST attempt arrives last, in a separate ETL run. Final numbering
    must follow occurred_at, so the late arrival becomes attempt 1 and the
    rows already loaded shift up.
    """
    learner = f"s-{uuid4().hex[:8]}"
    item = f"urn:a:{uuid4()}"
    middle = graded(
        0.5, "failed", learner=learner, object_iri=item, at=BASE + timedelta(days=5)
    )
    latest = graded(
        0.9, "passed", learner=learner, object_iri=item, at=BASE + timedelta(days=9)
    )
    earliest = graded(0.2, "failed", learner=learner, object_iri=item, at=BASE)

    load(connection, middle, latest)  # first run: two attempts
    load(connection, earliest)  # second run: an older one shows up

    numbering = dict(
        connection.execute(
            text(
                "SELECT statement_id::text, attempt_number "
                "FROM warehouse.fact_assessment "
                "WHERE statement_id = ANY(CAST(:ids AS uuid[]))"
            ),
            {"ids": [earliest["id"], middle["id"], latest["id"]]},
        ).all()
    )
    assert numbering[earliest["id"]] == 1, "the late arrival is the first attempt"
    assert numbering[middle["id"]] == 2, "already-loaded rows must renumber"
    assert numbering[latest["id"]] == 3


def test_attempts_are_numbered_per_learner_and_activity(connection) -> None:
    """Two learners on the same item each start at attempt 1."""
    item = f"urn:a:{uuid4()}"
    one = graded(0.4, "failed", learner=f"s-{uuid4().hex[:8]}", object_iri=item)
    two = graded(0.4, "failed", learner=f"s-{uuid4().hex[:8]}", object_iri=item)
    load(connection, one, two)

    numbers = (
        connection.execute(
            text(
                "SELECT attempt_number FROM warehouse.fact_assessment "
                "WHERE statement_id = ANY(CAST(:ids AS uuid[]))"
            ),
            {"ids": [one["id"], two["id"]]},
        )
        .scalars()
        .all()
    )
    assert numbers == [1, 1]


def test_every_loaded_assessment_has_an_attempt_number(connection) -> None:
    """A column added on anticipation must not quietly stay null."""
    payloads = [
        graded(0.6, learner="s-fixed-1", object_iri="urn:a:fixed", at=BASE),
        graded(
            0.7,
            learner="s-fixed-1",
            object_iri="urn:a:fixed",
            at=BASE + timedelta(days=1),
        ),
    ]
    load(connection, *payloads)
    assert (
        connection.execute(
            text(
                "SELECT count(*) FROM warehouse.fact_assessment "
                "WHERE statement_id = ANY(CAST(:ids AS uuid[])) "
                "AND attempt_number IS NULL"
            ),
            {"ids": [p["id"] for p in payloads]},
        ).scalar_one()
        == 0
    )


# --------------------------------------------------------------------------
# Sample analytical queries (gate criterion)
# --------------------------------------------------------------------------


def test_engagement_by_week_returns_expected_counts(connection) -> None:
    """The shape M3's recency and pacing features will be built on."""
    learner = f"s-{uuid4().hex[:8]}"
    load(
        connection,
        statement(learner=learner, at=BASE),
        statement(learner=learner, at=BASE + timedelta(days=1)),
        statement(learner=learner, at=BASE + timedelta(days=8)),
    )
    weeks = connection.execute(
        text(
            "SELECT d.iso_week, count(*) AS events "
            "FROM warehouse.fact_activity f "
            "JOIN warehouse.dim_student s USING (student_key) "
            "JOIN warehouse.dim_date    d USING (date_key) "
            "WHERE s.learner_identifier = :i GROUP BY 1 ORDER BY 1"
        ),
        {"i": learner},
    ).all()
    assert [row.events for row in weeks] == [2, 1]


def test_mean_score_per_learner_returns_expected_value(connection) -> None:
    learner = f"s-{uuid4().hex[:8]}"
    load(
        connection,
        graded(0.4, "failed", learner=learner, object_iri=f"urn:a:{uuid4()}"),
        graded(0.8, learner=learner, object_iri=f"urn:a:{uuid4()}"),
    )
    mean = connection.execute(
        text(
            "SELECT avg(f.scaled_score) FROM warehouse.fact_assessment f "
            "JOIN warehouse.dim_student s USING (student_key) "
            "WHERE s.learner_identifier = :i"
        ),
        {"i": learner},
    ).scalar_one()
    assert float(mean) == pytest.approx(0.6)


def test_activity_and_assessment_must_not_be_summed(connection) -> None:
    """Documents the double-count the overlap creates, as executable proof."""
    learner = f"s-{uuid4().hex[:8]}"
    load(connection, graded(0.9, learner=learner))

    counts = connection.execute(
        text(
            "SELECT (SELECT count(*) FROM warehouse.fact_activity f "
            "        JOIN warehouse.dim_student s USING (student_key) "
            "        WHERE s.learner_identifier = :i) AS activity, "
            "       (SELECT count(*) FROM warehouse.fact_assessment f "
            "        JOIN warehouse.dim_student s USING (student_key) "
            "        WHERE s.learner_identifier = :i) AS assessment"
        ),
        {"i": learner},
    ).one()
    assert counts.activity == 1
    assert counts.assessment == 1
    # One event, counted once in each table. Summing gives 2 — hence the
    # table comments and ADR-0006.


# --------------------------------------------------------------------------
# The dimension cache's lifecycle
# --------------------------------------------------------------------------


def test_the_cache_is_used_within_a_run(connection) -> None:
    """Proves the fast path exists: a cached key is returned without a read."""
    from pipeline.etl import _dimension_key

    course_iri = f"urn:c:{uuid4()}"
    cache = {("dim_course", course_iri): -999}
    assert (
        _dimension_key(
            connection,
            "dim_course",
            "course_key",
            "course_iri",
            course_iri,
            {"course_slug": "x", "title": "x"},
            cache=cache,
        )
        == -999
    )
    # Nothing was written, because the cache short-circuited before the insert.
    assert (
        connection.execute(
            text("SELECT count(*) FROM warehouse.dim_course WHERE course_iri = :i"),
            {"i": course_iri},
        ).scalar_one()
        == 0
    )


def test_each_run_starts_with_a_fresh_cache(connection, monkeypatch) -> None:
    """The invalidation property, pinned.

    The cache is correct only because its lifetime is one run: a dimension
    changed between runs must be re-read, not served stale. That is a
    lifecycle choice a future edit could silently break by hoisting the
    cache to module scope, and nothing else would fail if it did.

    Asserts that the two runs share no cache object. Deliberately NOT that
    load_batch was called a fixed number of times — a run pages, and how
    many pages it needs depends on how much happens to be pending, which
    this test does not control.
    """
    import pipeline.etl as etl

    seen: list[int] = []
    original = etl.load_batch

    def spy(conn, rows, cache=None):
        seen.append(id(cache))
        return original(conn, rows, cache)

    monkeypatch.setattr(etl, "load_batch", spy)

    def one_run() -> set[int]:
        fresh = statement()
        connection.execute(
            text(
                "INSERT INTO raw.statements (statement_id, payload) "
                "VALUES (:i, CAST(:p AS jsonb))"
            ),
            {"i": fresh["id"], "p": json.dumps(fresh)},
        )
        seen.clear()
        etl.run(connection)
        return set(seen)

    first, second = one_run(), one_run()

    assert first, "the first run loaded nothing"
    assert second, "the second run loaded nothing"
    assert len(first) == 1, "one run must use exactly one cache across its pages"
    assert len(second) == 1
    assert not (first & second), "runs shared a cache; a stale key would survive"
