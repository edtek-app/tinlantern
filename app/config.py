"""Runtime configuration that other modules must not each re-derive.

Small on purpose. The only thing here is the demo switch, because it
overrides a setting someone else made and an override that different
callers interpret differently is worse than no override at all.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

TRUE = frozenset({"1", "true", "yes", "on"})


def demo_mode() -> bool:
    """Whether this process is serving a public demo."""
    return os.environ.get("DEMO_MODE", "").strip().lower() in TRUE


@dataclass(frozen=True, slots=True)
class ProviderChoice:
    """Which provider won, and why.

    Carried rather than logged only. An override decided inside a
    process and reported nowhere is a safety property nobody can check
    — this is surfaced on `/health` so it is verifiable from outside
    without reading logs someone has to find first.
    """

    provider: str
    requested: str | None
    demo_mode: bool

    @property
    def overridden(self) -> bool:
        return self.requested is not None and self.requested != self.provider

    def explain(self) -> str:
        if not self.provider:
            return (
                "LLM_PROVIDER is not set and DEMO_MODE is off, so no provider "
                "is configured. It has no default on purpose — see "
                "app/llm/client.py."
            )
        if self.overridden:
            return (
                f"DEMO_MODE is on, so the '{self.provider}' provider is in use "
                f"and LLM_PROVIDER={self.requested!r} was overridden. A demo "
                "that can spend money or exercise a credential is a liability, "
                "so the switch does not defer to the environment."
            )
        if self.demo_mode:
            return f"DEMO_MODE is on; the '{self.provider}' provider is in use."
        return f"LLM_PROVIDER={self.provider!r}."


def provider_choice() -> ProviderChoice:
    """Resolve the provider, recording what was asked for.

    **Demo mode wins over `LLM_PROVIDER`.** A publicly reachable demo
    that reaches a paid API is a liability, and a safety property that
    depends on whoever set the environment variables is not one. The
    override is loud rather than silent: the requested value is kept so
    the explanation can name it.
    """
    requested = os.environ.get("LLM_PROVIDER") or None
    if demo_mode():
        return ProviderChoice(provider="stub", requested=requested, demo_mode=True)
    return ProviderChoice(
        provider=requested or "", requested=requested, demo_mode=False
    )
