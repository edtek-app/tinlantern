"""The LLM layer. Nothing outside this package talks to a provider.

Import the pieces from here rather than from the submodules: the flat
surface is what makes the boundary in ADR-0001 easy to keep.
"""

from app.llm.client import (
    PROVIDERS,
    SYNTHETIC_MARKER,
    Completion,
    LLMError,
    MalformedResponse,
    ModelRefused,
    Provider,
    ProviderNotAvailable,
    UnknownProvider,
    UnregisteredPrompt,
    build_client,
    is_transport_failure,
)
from app.llm.prompt_library import UnknownPrompt, load_prompt, prompt_names

__all__ = [
    "PROVIDERS",
    "SYNTHETIC_MARKER",
    "Completion",
    "LLMError",
    "MalformedResponse",
    "ModelRefused",
    "Provider",
    "ProviderNotAvailable",
    "UnknownPrompt",
    "UnknownProvider",
    "UnregisteredPrompt",
    "build_client",
    "is_transport_failure",
    "load_prompt",
    "prompt_names",
]
