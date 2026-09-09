"""The LLM-backed endpoints: retry policy, status codes, telemetry.

Every test drives the `stub` provider — nothing here needs a key, and a
test that did would be a test CI cannot run. The provider is injected by
overriding `app.api.insights._provider`, so no environment variable
decides what a test talks to.
"""

from __future__ import annotations

import json
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Connection, text

from app.api import insights
from app.dashboard.queries import learner_detail
from app.dashboard.summaries import generate, read, store
from app.dashboard.telemetry import Outcome, outcome_counts, record
from app.llm.client import Completion, MalformedResponse, ModelRefused
from app.llm.summary import AdvisorSummary, FallbackReason
from tests.test_dashboard_api import seed_scores

pytestmark = pytest.mark.m5


class Malforming:
    """A provider whose structured response never parses."""

    name = "stub"
    model = "stub-canned-v1"

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, *, system: str, prompt: str) -> Completion:
        raise AssertionError("not used")

    def complete_json(self, *, system: str, prompt: str, schema: dict):
        self.calls += 1
        raise MalformedResponse(
            "not valid JSON", raw="{", stop_reason="end_turn", prompt=prompt
        )


class Unreachable:
    """A provider that fails the way a dead network does."""

    name = "stub"
    model = "stub-canned-v1"

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, *, system: str, prompt: str) -> Completion:
        raise AssertionError("not used")

    def complete_json(self, *, system: str, prompt: str, schema: dict):
        import anthropic

        self.calls += 1
        raise anthropic.APIConnectionError(request=None)  # type: ignore[arg-type]


class Exploding:
    """A provider with an ordinary bug in it."""

    name = "stub"
    model = "stub-canned-v1"

    def complete(self, *, system: str, prompt: str) -> Completion:
        raise AssertionError("not used")

    def complete_json(self, *, system: str, prompt: str, schema: dict):
        raise ZeroDivisionError("a bug, not an outage")


@pytest.fixture
def client(test_database: str) -> TestClient:
    from app.main import app

    return TestClient(app, raise_server_exceptions=False)


def use(provider) -> None:
    """Point the endpoints at a provider for one test."""
    insights._provider = lambda: provider  # noqa: SLF001 - deliberate injection


@pytest.fixture(autouse=True)
def restore_provider():
    original = insights._provider
    yield
    insights._provider = original


# --------------------------------------------------------------------------
# The retry policy: once, counted, and never for transport
# --------------------------------------------------------------------------


def test_a_malformed_response_is_retried_exactly_once(
    client: TestClient,
) -> None:
    """Twice would be a policy nobody measured.

    0 malformed in 90 eval question-runs is enough to call it rare and
    far too thin to size against, so one retry absorbs a transient and
    production supplies the real rate.
    """
    provider = Malforming()
    use(provider)

    response = client.post("/api/ask", json={"question": "How many learners?"})

    assert provider.calls == 2, "one attempt plus exactly one retry"
    assert response.status_code == 502


def test_the_retry_is_counted_where_someone_can_see_it(
    client: TestClient, committed: Connection
) -> None:
    """A retry rate nobody can see is the 'looks healthy' pattern.

    Durable, not an in-process counter: a restart would discard the only
    real measurement of how often the layer misbehaves.

    The table is emptied first so the counts are exactly what THIS test
    produced. A table-wide count would pass in isolation and break the
    moment another test wrote a row — the failure mode already recorded
    as a standing rule.
    """
    committed.execute(text("DELETE FROM warehouse.llm_call"))
    use(Malforming())

    client.post("/api/ask", json={"question": "How many learners?"})
    counts = outcome_counts(committed)

    assert counts.get(str(Outcome.RETRIED)) == 1, "the first attempt"
    assert counts.get(str(Outcome.MALFORMED)) == 1, "the second, unretried"


def test_a_transport_failure_is_503_and_is_not_retried(
    client: TestClient,
) -> None:
    """ADR-0008's boundary, at the layer that has to honour it.

    Absorbing a retryable outage here would disguise a broken provider
    as a slow feature — exactly what the telemetry exists to prevent.
    """
    provider = Unreachable()
    use(provider)

    response = client.post("/api/ask", json={"question": "How many learners?"})

    assert response.status_code == 503
    assert provider.calls == 1, "transport failures are not retried"


def test_an_ordinary_bug_is_not_served_as_a_503(client: TestClient) -> None:
    """The broad handler must narrow immediately.

    Catching Exception and calling everything a transport failure would
    turn every bug into a plausible outage message, and nobody would
    look at the code.
    """
    use(Exploding())

    response = client.post("/api/ask", json={"question": "How many learners?"})

    assert response.status_code == 500, (
        "a ZeroDivisionError reached the client as a provider outage — "
        "either is_transport_failure is too generous, or the handler "
        "stopped re-raising what it cannot classify"
    )


def test_a_refusal_is_200_not_an_error(
    client: TestClient, connection: Connection
) -> None:
    """A refusal is the system working, not the caller's mistake.

    4xx would tell someone they asked wrongly when they asked a
    reasonable question the warehouse cannot answer.
    """
    from app.llm.prompt_library import load_prompt
    from app.llm.providers.stub import StubProvider, canned
    from app.llm.qa import plan_prompt

    question = "What are their email addresses?"
    plan = {"answerable": False, "sql": "", "reason": "No contact details."}
    use(
        StubProvider(
            canned(load_prompt("grounding"), plan_prompt(question), json.dumps(plan))
        )
    )

    response = client.post("/api/ask", json={"question": question})

    assert response.status_code == 200
    body = response.json()
    assert body["answered"] is False
    assert "contact details" in body["refusal_reason"]


def test_a_declining_model_is_also_200(client: TestClient) -> None:
    """ModelRefused must not be swallowed by the broad handler.

    It is an Exception, so a broad `except` placed first would classify
    it as unclassifiable and re-raise it as a 500.
    """

    class Declining:
        name = "stub"
        model = "stub-canned-v1"

        def complete(self, *, system: str, prompt: str) -> Completion:
            raise AssertionError("not used")

        def complete_json(self, *, system: str, prompt: str, schema: dict):
            raise ModelRefused("declined")

    use(Declining())

    response = client.post("/api/ask", json={"question": "How many learners?"})

    assert response.status_code == 200
    assert response.json()["answered"] is False


# --------------------------------------------------------------------------
# Telemetry must never take down the request it describes
# --------------------------------------------------------------------------


def test_a_failing_telemetry_write_returns_rather_than_raises(
    committed: Connection, caplog
) -> None:
    """A metrics write that fails an endpoint is worse than no metric.

    But silence is right for ONE transient and wrong for a persistent
    fault: a write that fails every time is a misconfiguration, and it
    must not look like a system with nothing to report. So it is
    swallowed AND logged.
    """
    import logging

    committed.execute(text("ALTER TABLE warehouse.llm_call RENAME TO llm_call_x"))
    try:
        with caplog.at_level(logging.ERROR):
            failure = record(
                operation="qa",
                outcome=Outcome.OK,
                provider="stub",
                model="stub",
                latency_ms=1,
            )
    finally:
        committed.execute(text("ALTER TABLE warehouse.llm_call_x RENAME TO llm_call"))

    assert failure is not None, "the write failed and the failure is reported"
    assert isinstance(failure, Exception), "returned, not raised"
    assert caplog.records, (
        "the failure was swallowed silently — replacing one invisible "
        "failure with another"
    )
    assert "telemetry write failed" in caplog.text


def test_telemetry_survives_a_read_only_transaction(
    committed: Connection,
) -> None:
    """The bug this fix exists for, on the path where it actually broke.

    A generated query sets `transaction_read_only` for the remainder of
    the caller's transaction, so every telemetry write after one was
    refused — and `record` swallows failures, so the row vanished with
    no error. Q&A's OK and REFUSED outcomes were lost; only the paths
    that raise BEFORE the query ran were ever recorded, which is why
    the existing telemetry tests all passed.

    Now it writes in its own transaction, following M1's precedent for
    rejections: the record must outlive the constraints of the thing it
    describes.
    """
    from app.db import transaction as own_transaction
    from app.llm.query import execute_readonly

    committed.execute(text("DELETE FROM warehouse.llm_call"))

    with own_transaction() as connection:
        execute_readonly(connection, "SELECT 1 AS one")
        failure = record(
            operation="qa",
            outcome=Outcome.OK,
            provider="stub",
            model="stub",
            latency_ms=1,
            detail="after a generated query",
        )

    assert failure is None, f"the telemetry write was refused: {failure}"
    assert outcome_counts(committed).get(str(Outcome.OK)) == 1


# --------------------------------------------------------------------------
# Summaries: written by scoring, read by the web tier
# --------------------------------------------------------------------------


def stored_summary(text_value: str, from_model: bool = True) -> AdvisorSummary:
    return AdvisorSummary(
        text=text_value,
        from_model=from_model,
        fallback_reason=None if from_model else FallbackReason.PROVIDER_REFUSED,
        synthetic=True,
        problems=(),
    )


def test_a_missing_summary_is_404_not_an_empty_one(
    client: TestClient, committed: Connection
) -> None:
    """Absence and emptiness read identically on screen; one is true.

    The endpoint does not generate inline — a page view must never call
    a language model, which is what makes M6's deployment story
    "scoring writes summaries".
    """
    seed_scores(committed, (0.82,))

    response = client.get("/api/learners/s-00000/summary")

    assert response.status_code == 404
    assert "make score" in response.json()["detail"], (
        "the 404 is about a missing SCORE, not a missing summary — the "
        "seeded rows are invisible to the endpoint's own connection"
    )


def test_a_stored_summary_is_read_back_with_its_provenance(
    connection: Connection,
) -> None:
    """A template summary and a model summary read alike.

    Only one was written by a model, and a reader cannot tell without
    this, so the flag and the reason are stored beside the text.
    """
    seed_scores(connection, (0.82,))
    detail = learner_detail(connection, "s-00000")
    assert detail is not None

    store(connection, detail, stored_summary("Plain prose.", from_model=False))
    back = read(connection, "s-00000", detail.model_version)

    assert back is not None
    assert back.from_model is False
    assert back.fallback_reason == str(FallbackReason.PROVIDER_REFUSED)
    assert back.window_close == date(2026, 2, 23)


def test_a_summary_cannot_outlive_the_score_it_describes(
    connection: Connection,
) -> None:
    """Keyed exactly like risk_score, so staleness is impossible.

    Re-score under a new model and the key changes with it. A cache
    policy someone has to maintain would eventually serve a summary
    about a score that no longer exists.
    """
    seed_scores(connection, (0.11,), model_version="old")
    old = learner_detail(connection, "s-00000")
    assert old is not None
    store(connection, old, stored_summary("About the old score."))

    seed_scores(connection, (0.82,), model_version="new")
    current = learner_detail(connection, "s-00000")
    assert current is not None
    assert current.model_version == "new"

    assert read(connection, "s-00000", current.model_version) is None, (
        "the old summary was served for the new score — the key is not "
        "tracking model_version"
    )
    assert read(connection, "s-00000", "old") is not None


class Declining:
    """A provider that declines, so the template answers instead."""

    name = "stub"
    model = "stub-canned-v1"

    def complete(self, *, system: str, prompt: str) -> Completion:
        raise ModelRefused("declined")

    def complete_json(self, *, system: str, prompt: str, schema: dict):
        raise ModelRefused("declined")


def test_generating_a_summary_records_the_call(connection: Connection) -> None:
    """Whether the model wrote it or the template did.

    An EMPTY stub would raise UnregisteredPrompt rather than falling
    back — `summarise` deliberately does not catch it, so the raising
    stub stays visible at the layer that uses it. A declining provider
    is the fallback path.
    """
    seed_scores(connection, (0.82,))
    detail = learner_detail(connection, "s-00000")
    assert detail is not None

    connection.execute(text("DELETE FROM warehouse.llm_call"))
    result = generate(connection, detail, Declining())

    assert result.from_model is False, "a declining model falls back"
    counts = outcome_counts(connection)
    assert counts.get(str(Outcome.FALLBACK)) == 1
    assert read(connection, "s-00000", detail.model_version) is not None


# --------------------------------------------------------------------------
# A recorded failure carries what it objected to, not only that it happened
# --------------------------------------------------------------------------


def test_a_fallback_records_what_verification_objected_to(
    connection: Connection,
) -> None:
    """A reason that duplicates the `outcome` column carries nothing.

    `AdvisorSummary.problems` says which claim broke; storing only
    "verification-failed" beside an outcome column already reading
    "fallback" is a diagnostic gap wearing the shape of observability.
    Recovering these once cost 25 live API calls to re-derive what the
    code had already computed.
    """
    from app.dashboard.summaries import _detail

    detailed = _detail(
        AdvisorSummary(
            text="…",
            from_model=False,
            fallback_reason=FallbackReason.VERIFICATION_FAILED,
            synthetic=False,
            problems=("claim 3 contains 41, which traces to no supplied fact",),
        )
    )

    assert detailed is not None
    assert "verification-failed" in detailed, "the category is still useful"
    assert "claim 3 contains 41" in detailed, (
        "the objection is the only thing this column can carry that the "
        "outcome column cannot"
    )


def test_a_withheld_answer_records_its_objections(
    client: TestClient, committed: Connection
) -> None:
    """The same gap on the Q&A path, where the sentence is not a
    duplicate but the objections were still dropped."""
    import json as _json

    from app.llm.prompt_library import load_prompt
    from app.llm.providers.stub import StubProvider, canned
    from app.llm.qa import answer_prompt, plan_prompt
    from app.llm.query import run_generated_query

    committed.execute(text("DELETE FROM warehouse.llm_call"))
    seed_scores(committed, (0.82,))

    question = "How many learners are there?"
    sql = "SELECT count(*) AS learners FROM warehouse.dim_student"
    system = load_prompt("grounding")
    responses = dict(
        canned(
            system,
            plan_prompt(question),
            _json.dumps({"answerable": True, "sql": sql, "reason": ""}),
        )
    )
    result = run_generated_query(committed, sql)
    responses.update(
        canned(
            system,
            answer_prompt(question, result),
            _json.dumps(
                {"claims": [{"text": "There are 91 learners.", "source": "row:0"}]}
            ),
        )
    )
    use(StubProvider(responses))

    response = client.post("/api/ask", json={"question": question})

    assert response.status_code == 200
    assert response.json()["answered"] is False

    rows = committed.execute(
        text(
            "SELECT outcome, detail FROM warehouse.llm_call "
            "WHERE operation = 'qa' ORDER BY llm_call_key DESC"
        )
    ).fetchall()
    print("ROWS:", rows)
    detail = rows[0][1] if rows else None
    assert detail is not None
    assert "91" in detail, (
        "the row records that verification failed but not which figure "
        "broke — the same discard as the summary path"
    )
