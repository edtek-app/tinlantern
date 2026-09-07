"""The real provider, used for local development.

The module is named for the ``LLM_PROVIDER`` value it implements, so the
mapping from configuration to code needs no lookup. ``import anthropic``
below still resolves to the SDK: Python 3 has no implicit relative
imports, so a module cannot shadow a top-level package for itself.

**Constructing this provider makes no network call.** The SDK client is
built on first use, so a missing credential or an unreachable endpoint
surfaces where the call happens rather than at import — which is what
lets the test suite construct it without reaching anything.

SDK exceptions are deliberately **not** wrapped. ``anthropic`` raises a
typed hierarchy that distinguishes retryable failures (429, 5xx,
connection) from permanent ones (400, 404); flattening those into one
local error class would discard exactly the distinction a caller needs to
decide whether to retry. Only a refusal is translated, because a refusal
is not an exception at all — see below.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from typing import Any, ClassVar

from app.llm.client import Completion, ModelRefused

#: Opus 5. Named here rather than at call sites so the model in use is one
#: grep away and one line to change.
DEFAULT_MODEL = "claude-opus-5"

#: Non-streaming responses must finish inside the SDK's HTTP timeout.
#: Advisor summaries and Q&A answers are short; this is headroom, not a
#: target.
MAX_TOKENS = 16_000


@dataclass
class AnthropicProvider:
    """Calls Claude through the official SDK."""

    model: str = DEFAULT_MODEL

    name: ClassVar[str] = "anthropic"

    @cached_property
    def _sdk(self) -> Any:
        """The SDK client, built on first use.

        Credentials are resolved by the SDK itself (``ANTHROPIC_API_KEY``,
        ``ANTHROPIC_AUTH_TOKEN``, or a configured profile), so nothing
        here reads or stores a key.
        """
        import anthropic

        return anthropic.Anthropic()

    def complete(self, *, system: str, prompt: str) -> Completion:
        """Send one request and return its text.

        Adaptive thinking is on: grounding an answer in supplied rows and
        deciding whether the rows actually support it is the kind of work
        that benefits, and on this model it is the default anyway.

        Raises:
            ModelRefused: If the model declined. A refusal is an HTTP 200
                whose content is empty, so without this check it would
                reach an advisor as a blank summary rather than a fault.
        """
        response = self._sdk.messages.create(
            model=self.model,
            max_tokens=MAX_TOKENS,
            thinking={"type": "adaptive"},
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )

        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            raise ModelRefused(
                "the model declined this request "
                f"(category: {getattr(details, 'category', None)}). "
                "Nothing was generated; do not present this as an answer."
            )

        text = "".join(block.text for block in response.content if block.type == "text")
        return Completion(
            text=text,
            provider=self.name,
            model=response.model,
            synthetic=False,
        )
