"""The LLM client: provider selection, the stub's contract, the boundary.

Every test here creates its own state. Nothing reads the database, and
the two tests that touch environment variables set them through
`monkeypatch`, which restores them — so none of these assume state they
did not create.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from app.llm import (
    PROVIDERS,
    SYNTHETIC_MARKER,
    ProviderNotAvailable,
    UnknownProvider,
    UnregisteredPrompt,
    build_client,
    load_prompt,
    prompt_names,
)
from app.llm.prompt_library import UnknownPrompt
from app.llm.providers.anthropic import AnthropicProvider
from app.llm.providers.stub import STUB_MODEL, StubProvider, canned

pytestmark = pytest.mark.m4

ROOT = Path(__file__).resolve().parents[1]

SYSTEM = "You answer only from the supplied rows."
PROMPT = "How many learners are alerted?"
BODY = "38 of 120 learners are above the alert threshold."


def a_stub() -> StubProvider:
    """A stub carrying exactly one registered interaction."""
    return StubProvider(canned(SYSTEM, PROMPT, BODY))


# --------------------------------------------------------------------------
# The stub's contract
# --------------------------------------------------------------------------


def test_the_stub_needs_no_credentials_and_repeats_itself_exactly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The CI provider: no key, byte-identical output across calls.

    Both credential variables are cleared, so a stub that quietly reached
    for the real SDK would fail here rather than in CI.
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)

    provider = a_stub()
    first = provider.complete(system=SYSTEM, prompt=PROMPT)
    second = provider.complete(system=SYSTEM, prompt=PROMPT)

    assert first == second
    assert BODY in first.text
    assert first.model == STUB_MODEL


def test_the_stub_raises_rather_than_inventing_a_generic_answer() -> None:
    """An unregistered request is an error, never a bland response.

    This is the whole reason the stub is written this way: a fallback
    would let an assertion about a summary pass against a response
    written for some other interaction, which is a test that measures
    nothing while looking green.
    """
    with pytest.raises(UnregisteredPrompt) as raised:
        a_stub().complete(system=SYSTEM, prompt="something nobody registered")

    message = str(raised.value)
    assert "canned" in message, "the error must say how to register a response"
    assert "edited" in message, (
        "the error must offer both explanations — a genuinely new "
        "interaction and an edited prompt need different fixes, and the "
        "digest alone does not distinguish them"
    )


def test_editing_a_prompt_invalidates_its_canned_response() -> None:
    """Keying on exact request text is a feature, not an inconvenience.

    A reworded prompt is a different interaction. Reusing the old canned
    answer would let a prompt regression pass the gate, so the stub
    refuses instead.
    """
    provider = a_stub()
    edited = PROMPT + " Answer in one sentence."

    with pytest.raises(UnregisteredPrompt):
        provider.complete(system=SYSTEM, prompt=edited)

    # And the system stance is part of the key too, not just the prompt.
    with pytest.raises(UnregisteredPrompt):
        provider.complete(system=SYSTEM + " Be terse.", prompt=PROMPT)


def test_stub_output_is_identifiably_synthetic() -> None:
    """Machine-readable and visible, because artifacts get pasted around.

    A canned answer that reaches a report or a screenshot must announce
    itself; provenance nobody can see is provenance nobody checks.
    """
    completion = a_stub().complete(system=SYSTEM, prompt=PROMPT)

    assert completion.synthetic is True
    assert completion.text.startswith(SYNTHETIC_MARKER)
    assert "SYNTHETIC" in completion.text


def test_the_stub_parses_a_canned_json_response() -> None:
    """Structured requests come back parsed, with the completion beside them.

    The schema is accepted and ignored by the stub: a canned response is
    written by hand against the same schema the real provider is
    constrained to, so validating it here would only re-check the
    fixture. The caller still gets the completion, because provenance
    has to survive the parse.
    """
    provider = StubProvider(canned(SYSTEM, PROMPT, '{"alerted": 38}'))

    payload, completion = provider.complete_json(
        system=SYSTEM, prompt=PROMPT, schema={"type": "object"}
    )

    assert payload == {"alerted": 38}
    assert completion.synthetic is True
    assert completion.text.startswith(SYNTHETIC_MARKER)


def test_a_structured_request_raises_when_unregistered_too() -> None:
    """The raising contract is not weaker on the JSON path."""
    with pytest.raises(UnregisteredPrompt):
        StubProvider({}).complete_json(
            system=SYSTEM, prompt=PROMPT, schema={"type": "object"}
        )


# --------------------------------------------------------------------------
# Provider selection
# --------------------------------------------------------------------------


def test_the_provider_comes_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "stub")
    assert build_client().name == "stub"


def test_an_unset_provider_is_an_error_not_a_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Neither possible default is safe, so there is none.

    Defaulting to `stub` would let a deployment answer an advisor with
    synthetic text; defaulting to `anthropic` would make CI reach for a
    credential. Both fail silently, so an unset variable fails loudly.
    """
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    with pytest.raises(UnknownProvider, match="no default"):
        build_client()


def test_an_unknown_provider_names_the_ones_that_exist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    with pytest.raises(UnknownProvider) as raised:
        build_client()
    for known in PROVIDERS:
        assert known in str(raised.value)


def test_bedrock_is_recognised_but_refused_until_m6() -> None:
    """A planned provider gets 'not yet', not 'unknown'.

    `.env.example` documents `bedrock`, so someone will set it. "Unknown
    provider" would send them looking for a typo; the local-first rule is
    the actual reason.
    """
    with pytest.raises(ProviderNotAvailable, match="M6"):
        build_client("bedrock")


def test_constructing_the_real_provider_does_not_call_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Construction is inert; the SDK client is built on first use.

    Proven by pointing the SDK at an unroutable address: if construction
    connected, this would hang or raise. It returns, so the network is
    not touched until `complete` is called.
    """
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-a-real-key")
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")

    provider = build_client()

    assert isinstance(provider, AnthropicProvider)
    assert provider.name == "anthropic"
    assert provider.model == "claude-opus-5"


# --------------------------------------------------------------------------
# The boundary (ADR-0001: no provider calls outside app/llm/)
# --------------------------------------------------------------------------

_IMPORT_PROBE = """
import sys
import app.main, app.api, app.raw.storage, app.xapi
import pipeline, pipeline.etl, pipeline.quality
import ml.src.features, ml.src.model, ml.src.scoring
roots = {name.split(".")[0] for name in sys.modules}
print(",".join(sorted(roots & {"anthropic", "boto3"})))
"""


def test_no_shipped_module_outside_the_llm_layer_imports_the_sdk() -> None:
    """The boundary asserted by what actually gets imported.

    A fresh interpreter imports every shipped entry point except
    `app.llm`, then reports whether the provider SDK ended up in
    `sys.modules`. This watches the real import graph rather than
    scanning source text — a text scan matches its own docstring and
    misses a transitive import, which is neither direction anyone wants.
    """
    finished = subprocess.run(
        [sys.executable, "-c", _IMPORT_PROBE],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    leaked = [name for name in finished.stdout.strip().split(",") if name]

    assert not leaked, (
        f"{leaked} was imported by a module outside app/llm/. Either a "
        "provider call has escaped the LLM layer (ADR-0001), or something "
        "imports app.llm.providers at module scope when it should build a "
        "client at the point of use."
    )


# --------------------------------------------------------------------------
# Prompts
# --------------------------------------------------------------------------


def test_every_named_prompt_loads_and_carries_text() -> None:
    names = prompt_names()
    assert names, "no prompt files found; the loader would be unreachable"
    for name in names:
        assert load_prompt(name).strip(), f"prompt {name!r} is empty"


def test_an_unknown_prompt_names_the_ones_that_exist() -> None:
    with pytest.raises(UnknownPrompt) as raised:
        load_prompt("no-such-prompt")
    assert "grounding" in str(raised.value)


def test_the_grounding_prompt_is_loaded_from_a_file_not_a_literal() -> None:
    """The file on disk is what reaches the model.

    Written to the file, read back through the loader: if a call site
    ever inlined the text instead, the two would stop agreeing.
    """
    path = ROOT / "app" / "llm" / "prompts" / "grounding.md"
    assert path.is_file()
    assert load_prompt("grounding") == path.read_text(encoding="utf-8").rstrip()
