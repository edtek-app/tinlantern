"""The one seam through which TinLantern talks to a language model.

Every provider call in this repository goes through here (ADR-0001). The
rule is not tidiness: a second call site is a second place where a
credential, a model id, a prompt, or an ungrounded answer can enter the
system without passing the checks this milestone builds.

Three providers, chosen by ``LLM_PROVIDER``:

``stub``
    Deterministic canned responses, no credentials. This is the provider
    CI runs. A test that needs an API key is a test CI cannot run.
``anthropic``
    The real model, for local development.
``bedrock``
    Recognised, not built. Nothing cloud-side exists before M6 and this
    refuses rather than pretending otherwise.

``LLM_PROVIDER`` has **no default**. Falling back to ``stub`` would let a
misconfigured deployment answer an advisor with synthetic text that reads
like analysis; falling back to ``anthropic`` would make CI reach for a
credential. Neither failure announces itself, so an unset variable is an
error instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from app.config import provider_choice

#: Prefixed to every stub response. A canned answer that escapes into a
#: report, a screenshot, or an eval artifact must declare what it is on
#: sight — provenance you have to go and check is provenance nobody checks.
SYNTHETIC_MARKER = "[SYNTHETIC — TinLantern stub provider; no model was called]"

#: The providers this codebase knows by name. `bedrock` is listed because
#: it is planned (`.env.example`), so asking for it gets "not until M6"
#: rather than the misleading "unknown provider".
PROVIDERS: tuple[str, ...] = ("stub", "anthropic", "bedrock")


class LLMError(RuntimeError):
    """Base for every failure originating in the LLM layer."""


class UnknownProvider(LLMError):
    """``LLM_PROVIDER`` names something this codebase does not implement."""


class ProviderNotAvailable(LLMError):
    """A known provider that cannot be used in this milestone."""


class UnregisteredPrompt(LLMError):
    """The stub was asked something it has no canned response for."""


class MalformedResponse(LLMError):
    """The model's structured response could not be parsed.

    Carries the raw text, the stop reason, and the prompt that produced
    it. A bare ``JSONDecodeError`` with a character offset cost 21 API
    calls to diagnose once — the same defect as an eval report saying
    only "could not be verified".
    """

    def __init__(
        self, detail: str, *, raw: str, stop_reason: str | None, prompt: str
    ) -> None:
        super().__init__(detail)
        self.raw = raw
        self.stop_reason = stop_reason
        self.prompt = prompt


class ModelRefused(LLMError):
    """The model declined the request.

    A refusal arrives as a successful HTTP response whose content is
    empty, so it must be checked explicitly — otherwise it surfaces as a
    confidently blank summary rather than as a failure.
    """


@dataclass(frozen=True, slots=True)
class Completion:
    """One model response, with enough provenance to audit it later.

    Attributes:
        text: The response body.
        provider: Which provider produced it.
        model: The model identifier the provider reported.
        synthetic: True when no model was called. Carried as a field, not
            inferred from the provider name, so a consumer writing an
            eval artifact can gate on it mechanically.
    """

    text: str
    provider: str
    model: str
    synthetic: bool


@runtime_checkable
class Provider(Protocol):
    """What every provider implements. Deliberately one method wide."""

    name: str

    def complete(self, *, system: str, prompt: str) -> Completion:
        """Return a completion for ``prompt`` under the ``system`` stance."""
        ...

    def complete_json(
        self, *, system: str, prompt: str, schema: dict
    ) -> tuple[dict, Completion]:
        """Return a completion constrained to ``schema``, parsed.

        The grounded features do not want prose from the model — they
        want claims each carrying the fact they rest on, so that code can
        check them (ADR-0008). Both halves come back: the parsed payload
        to verify, and the raw completion for provenance.
        """
        ...


_UNSET = (
    "LLM_PROVIDER is not set. It has no default on purpose: defaulting to "
    "'stub' would let a deployment answer an advisor with synthetic text, "
    "and defaulting to 'anthropic' would make CI reach for a credential. "
    f"Set one of {list(PROVIDERS)} — see .env.example."
)

_BEDROCK = (
    "provider 'bedrock' is planned but not built. Nothing cloud-side "
    "exists before M6 (CLAUDE.md's local-first rule), so this refuses "
    "rather than failing later against absent infrastructure. Use "
    "'anthropic' locally or 'stub' in tests."
)


def is_transport_failure(error: BaseException) -> bool:
    """Whether a failure was the provider being unreachable or overloaded.

    ADR-0008 keeps SDK exceptions unwrapped, so their typed hierarchy
    survives for whoever decides on retries. But that hierarchy lives in
    the vendor package, and nothing outside `app/llm/` may import it —
    a caller mapping a connection error to a 503 would otherwise have to
    breach the boundary to do it.

    So the classification happens here, behind the seam, and callers ask
    rather than import. The SDK is imported lazily: this must stay
    callable in a process that never configured a real provider.

    Args:
        error: The exception to classify.

    Returns:
        True for connection failures, timeouts, rate limits and 5xx —
        the retryable ones. False for everything else, including a 400,
        which is a bug in the request rather than a fault in the link.
    """
    try:
        import anthropic
    except ImportError:  # pragma: no cover - the SDK is a dependency
        return False

    if isinstance(error, anthropic.APIConnectionError | anthropic.APITimeoutError):
        return True
    if isinstance(error, anthropic.RateLimitError):
        return True
    if isinstance(error, anthropic.APIStatusError):
        return error.status_code >= 500
    return False


def build_client(provider: str | None = None) -> Provider:
    """Construct the provider named by ``LLM_PROVIDER``.

    Args:
        provider: Overrides the environment. For tests and for the eval
            harness, which selects its provider explicitly.

    Returns:
        A ready provider. Construction never performs a network call, so
        an unreachable or unconfigured backend surfaces at the point of
        use rather than at import.

    Raises:
        UnknownProvider: If the name is not one of ``PROVIDERS``.
        ProviderNotAvailable: If the provider is known but not yet built.
    """
    # An explicit argument wins (tests, the eval harness). Otherwise
    # DEMO_MODE overrides LLM_PROVIDER, deliberately and loudly
    # (app/config.py): a demo that can spend money or exercise a
    # credential is a liability, and a safety property that defers to
    # the environment is not a safety property.
    name = provider if provider is not None else provider_choice().provider
    if not name:
        raise UnknownProvider(_UNSET)

    if name == "stub":
        from app.llm.providers.stub import StubProvider

        return StubProvider()

    if name == "anthropic":
        from app.llm.providers.anthropic import AnthropicProvider

        return AnthropicProvider()

    if name == "bedrock":
        raise ProviderNotAvailable(_BEDROCK)

    raise UnknownProvider(
        f"unknown LLM_PROVIDER {name!r}. Known providers: {list(PROVIDERS)}."
    )
