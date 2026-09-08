"""The provider CI runs: canned responses, no credentials, no network.

**It raises on anything unregistered.** A generic fallback would be one
line and would quietly wreck the suite: a test asserting "the summary
mentions the risk score" would pass against a response written for some
other interaction entirely, and the assertion would be measuring nothing.
The same shape of defect has already been caught twice in this repository
(a driver fixture whose two groups were identical, a burst-length bound
set to exactly the buggy value) — both passed while proving nothing.
Registering every stubbed interaction deliberately is the cost of not
having a third.

**Responses are keyed by the exact request text**, not by a feature name.
So editing a prompt invalidates its canned response and the stub starts
raising. That is the intended behaviour, not a rough edge: a changed
prompt is a changed interaction, and silently reusing the old answer
would let a prompt regression sail through the gate.

Responses carry a visible synthetic marker (`SYNTHETIC_MARKER`) because
eval artifacts get pasted into reports and screenshots, and a canned
answer whose provenance is ambiguous is worse than no answer.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import ClassVar

from app.llm.client import (
    SYNTHETIC_MARKER,
    Completion,
    MalformedResponse,
    UnregisteredPrompt,
)

#: Reported as the model identifier, so a `Completion` from the stub is
#: distinguishable from a real one by machine as well as by eye.
STUB_MODEL = "stub-canned-v1"


def request_digest(system: str, prompt: str) -> str:
    """Key a canned response by the exact request that produces it.

    blake2b rather than the builtin ``hash``: the builtin is salted per
    process, so a registry keyed with it would resolve in one run and
    raise in the next (ADR-0003 makes the same point for the generator).

    Args:
        system: The system stance.
        prompt: The user prompt.

    Returns:
        A stable 32-character digest.
    """
    digest = hashlib.blake2b(digest_size=16)
    digest.update(system.encode("utf-8"))
    digest.update(b"\x00")  # so ("ab", "c") and ("a", "bc") differ
    digest.update(prompt.encode("utf-8"))
    return digest.hexdigest()


def canned(system: str, prompt: str, response: str) -> dict[str, str]:
    """Build one registry entry, so callers never write a digest by hand."""
    return {request_digest(system, prompt): response}


#: Canned responses for interactions that exist in the repository. Empty
#: at the commit that introduces the client — the summariser and the Q&A
#: layer each register their own as they land, which is what keeps this
#: list an inventory of real interactions rather than a pile of fixtures.
CANNED_RESPONSES: dict[str, str] = {}


_UNREGISTERED = (
    "the stub has no canned response for request {digest}.\n"
    "Two explanations, and they need different fixes:\n"
    "  * this interaction is new — add its response to CANNED_RESPONSES "
    "(or pass one to StubProvider) using canned(system, prompt, ...);\n"
    "  * a prompt was edited — the stub keys on exact request text, so an "
    "edit deliberately invalidates the old response rather than reusing "
    "an answer written for different wording.\n"
    "The stub never falls back to a generic response: that would let a "
    "test pass against something never written for it.\n"
    "System prompt begins: {system!r}\nUser prompt begins: {prompt!r}"
)


@dataclass(frozen=True, slots=True)
class StubProvider:
    """A provider that answers only what it was told to answer."""

    responses: Mapping[str, str] = field(default_factory=lambda: CANNED_RESPONSES)

    name: ClassVar[str] = "stub"

    def complete(self, *, system: str, prompt: str) -> Completion:
        """Return the canned response for this exact request.

        Raises:
            UnregisteredPrompt: If nothing is registered, with both
                explanations and the digest to register under.
        """
        digest = request_digest(system, prompt)
        try:
            body = self.responses[digest]
        except KeyError:
            raise UnregisteredPrompt(
                _UNREGISTERED.format(
                    digest=digest, system=system[:80], prompt=prompt[:80]
                )
            ) from None

        return Completion(
            text=f"{SYNTHETIC_MARKER}\n{body}",
            provider=self.name,
            model=STUB_MODEL,
            synthetic=True,
        )

    def complete_json(
        self, *, system: str, prompt: str, schema: dict
    ) -> tuple[dict, Completion]:
        """Parse the canned response as JSON.

        The schema is accepted and ignored: a canned response is written
        by hand against the same schema the real provider is constrained
        to, so validating it here would only re-check the fixture. What
        the caller actually needs proved — that the payload's claims rest
        on supplied facts — is verification's job, not the provider's.
        """
        completion = self.complete(system=system, prompt=prompt)
        body = completion.text.removeprefix(SYNTHETIC_MARKER).strip()
        try:
            return json.loads(body), completion
        except json.JSONDecodeError as broken:
            # Same error type as the real provider, so a caller handling
            # a malformed response is exercised by the stub rather than
            # only in production.
            raise MalformedResponse(
                f"the canned response is not valid JSON ({broken})",
                raw=body,
                stop_reason=None,
                prompt=prompt,
            ) from broken
