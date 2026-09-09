"""Demo mode: the stub wins, and the replayed text says what it is.

Nothing here reaches a network. That is the property under test.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.config import provider_choice
from app.llm.client import build_client
from app.llm.prompt_library import load_prompt
from app.llm.providers.stub import (
    RECORDED_PATH,
    Recorded,
    StubProvider,
    recorded_responses,
    request_digest,
)

pytestmark = pytest.mark.m5


@pytest.fixture
def client(test_database: str) -> TestClient:
    from app.main import app

    return TestClient(app)


# --------------------------------------------------------------------------
# The safety property: demo mode overrides an explicit provider
# --------------------------------------------------------------------------


def test_demo_mode_overrides_an_explicit_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A demo that can spend money or exercise a key is a liability.

    A safety property that defers to whoever set the environment is not
    a safety property, so DEMO_MODE wins over LLM_PROVIDER rather than
    yielding to it.
    """
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")

    assert build_client().name == "stub"
    assert provider_choice().provider == "stub"


def test_the_override_says_which_setting_won_and_why(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Loud, not silent. An ignored explicit setting must be explicable."""
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")

    choice = provider_choice()

    assert choice.overridden is True
    assert "anthropic" in choice.explain(), "the ignored setting must be named"
    assert "DEMO_MODE" in choice.explain()


def test_without_demo_mode_the_environment_decides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The override must not be permanent, or it is not an override."""
    monkeypatch.delenv("DEMO_MODE", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")

    choice = provider_choice()

    assert choice.provider == "anthropic"
    assert choice.overridden is False


@pytest.mark.parametrize("value", ["", "false", "no", "0", "off"])
def test_demo_mode_is_off_unless_explicitly_on(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """A misread value must fail toward the real provider being honoured,
    never toward a demo silently serving recordings in production."""
    monkeypatch.setenv("DEMO_MODE", value)
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")

    assert provider_choice().provider == "anthropic"


def test_health_reports_the_provider_actually_in_use(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Checkable from outside the process.

    An override decided internally and reported only in logs is a
    safety property nobody can verify without finding the logs first.
    """
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")

    body = client.get("/health").json()

    assert body["status"] == "ok"
    assert body["demo_mode"] is True
    assert body["llm_provider"] == "stub"
    assert body["provider_overridden"] is True
    assert "anthropic" in body["provider_reason"]


# --------------------------------------------------------------------------
# Recorded responses say what they are
# --------------------------------------------------------------------------


def a_recording() -> Recorded:
    return Recorded(
        body='{"answerable": true, "sql": "SELECT 1", "reason": ""}',
        model="claude-opus-5",
        recorded_on="2026-09-08",
    )


def test_a_replayed_response_names_the_model_and_the_date() -> None:
    """Both facts, because either alone misleads.

    "No model was called" is true of the replay and wrong about the
    text — a reader would discount prose a real model wrote as
    machine-generated filler, which UNDERSTATES its authority rather
    than overstating it.
    """
    recording = a_recording()
    provider = StubProvider({}, {request_digest("sys", "q"): recording})

    completion = provider.complete(system="sys", prompt="q")

    assert "RECORDED" in completion.text
    assert "claude-opus-5" in completion.text
    assert "2026-09-08" in completion.text
    assert "replayed without calling a model" in completion.text


def test_the_two_markers_are_distinguishable() -> None:
    """A hand-written fixture and a recorded response are not the same
    claim, and a reader must be able to tell which they are looking at."""
    from app.llm.providers.stub import SYNTHETIC_MARKER, canned

    recorded = StubProvider({}, {request_digest("sys", "q"): a_recording()})
    handwritten = StubProvider(canned("sys", "q", "some canned text"))

    replayed = recorded.complete(system="sys", prompt="q").text
    invented = handwritten.complete(system="sys", prompt="q").text

    assert replayed.startswith("[RECORDED")
    assert invented.startswith(SYNTHETIC_MARKER)
    assert "RECORDED" not in invented
    assert SYNTHETIC_MARKER not in replayed


def test_a_replayed_completion_reports_the_model_that_wrote_it() -> None:
    """Not the stub replaying it.

    Someone reconciling a demo screenshot needs to know whose output
    they are reading.
    """
    provider = StubProvider({}, {request_digest("sys", "q"): a_recording()})

    completion = provider.complete(system="sys", prompt="q")

    assert completion.model == "claude-opus-5"
    assert completion.synthetic is True, "no model was called to serve this"


def test_the_marker_does_not_leak_into_parsed_json() -> None:
    provider = StubProvider({}, {request_digest("sys", "q"): a_recording()})

    payload, _ = provider.complete_json(system="sys", prompt="q", schema={})

    assert payload["sql"] == "SELECT 1"


def test_provenance_is_stored_per_response_not_per_set() -> None:
    """A partially re-recorded set stays accurate about every entry.

    One date for the whole file would make every untouched response
    claim the date of whichever run happened last.
    """
    raw = json.loads(RECORDED_PATH.read_text(encoding="utf-8"))

    assert raw, "the recorded set is empty; the demo would answer nothing"
    for digest, entry in raw.items():
        assert entry["model"], f"{digest} has no model"
        assert entry["recorded_on"], f"{digest} has no recording date"


def test_every_recorded_response_is_valid_json() -> None:
    """A response that will not parse is a demo that raises on stage."""
    for digest, recording in recorded_responses().items():
        parsed = json.loads(recording.body)
        assert isinstance(parsed, dict), f"{digest} is not an object"


# --------------------------------------------------------------------------
# Recordings go stale when the cohort changes, and that must be found
# BEFORE the demo rather than during it
# --------------------------------------------------------------------------


def test_the_staleness_check_reports_a_changed_cohort(
    connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The scenario M7 creates: recordings made against another cohort.

    A recording is keyed by the request that produced it, and that
    request embeds the rows the query returned. So changed data means
    the recording is simply not found — correct, loud, and otherwise
    discovered mid-demo. The checker moves that discovery earlier and
    names which question went stale.

    Needs no reference query: the digest covers every returned column,
    which is stronger than comparing one figure a hand-written oracle
    computes — and this repository's oracles have been wrong three times
    in twelve.
    """
    from tools.check_demo_recordings import check

    question = "How many learners are in this test?"
    monkeypatch.setattr("app.llm.qa.plan_prompt", lambda q: f"PLAN:{q}", raising=True)
    monkeypatch.setattr(
        "tools.check_demo_recordings.plan_prompt", lambda q: f"PLAN:{q}"
    )

    system = load_prompt("grounding")
    plan_body = json.dumps(
        {
            "answerable": True,
            "sql": "SELECT count(*) AS learners FROM warehouse.dim_student",
            "reason": "",
        }
    )
    recordings = {
        request_digest(system, f"PLAN:{question}"): Recorded(
            body=plan_body, model="claude-opus-5", recorded_on="2026-09-08"
        )
    }
    monkeypatch.setattr(
        "tools.check_demo_recordings.recorded_responses", lambda: recordings
    )

    problems = check(connection, (question,))

    assert len(problems) == 1
    assert problems[0].question == question
    assert "no longer matches" in problems[0].problem
    assert "re-record" in problems[0].problem, "it must say how to fix it"


def test_a_missing_plan_recording_is_reported_separately(
    connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A new question and a changed prompt need different responses.

    One means "record it"; the other means a prompt file or the schema
    description moved underneath every recording at once.
    """
    from tools.check_demo_recordings import check

    monkeypatch.setattr("tools.check_demo_recordings.recorded_responses", lambda: {})

    problems = check(connection, ("A question nobody recorded",))

    assert len(problems) == 1
    assert "no recorded plan" in problems[0].problem


def test_a_refusal_cannot_go_stale(connection, monkeypatch: pytest.MonkeyPatch) -> None:
    """It never reached a query, so no data underlies it."""
    from tools.check_demo_recordings import check

    question = "What are their email addresses?"
    monkeypatch.setattr(
        "tools.check_demo_recordings.plan_prompt", lambda q: f"PLAN:{q}"
    )
    system = load_prompt("grounding")
    recordings = {
        request_digest(system, f"PLAN:{question}"): Recorded(
            body=json.dumps(
                {"answerable": False, "sql": "", "reason": "No contact details."}
            ),
            model="claude-opus-5",
            recorded_on="2026-09-08",
        )
    }
    monkeypatch.setattr(
        "tools.check_demo_recordings.recorded_responses", lambda: recordings
    )

    assert check(connection, (question,)) == ()
