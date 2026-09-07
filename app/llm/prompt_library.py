"""Prompts live in files, and are loaded by name.

A prompt is the part of an LLM feature most worth reviewing and least
readable as a Python string literal: escaped newlines, concatenated
fragments, and indentation that belongs to the code rather than the text.
Under `app/llm/prompts/` a prompt change arrives in a diff that reads as
prose, which is the point of putting it in version control at all.

The directory holds the text; this module is the only way to reach it, so
a prompt cannot be assembled ad hoc at a call site.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

PROMPT_DIR = Path(__file__).resolve().parent / "prompts"

SUFFIX = ".md"


class UnknownPrompt(LookupError):
    """No prompt file exists for that name."""


def prompt_names() -> tuple[str, ...]:
    """Every prompt available, sorted."""
    return tuple(sorted(path.stem for path in PROMPT_DIR.glob(f"*{SUFFIX}")))


@cache
def load_prompt(name: str) -> str:
    """Return the text of a named prompt.

    Args:
        name: The file stem, e.g. ``"grounding"``.

    Returns:
        The prompt text, trailing whitespace stripped so an editor's
        final newline cannot change what the model receives — and, since
        the stub keys on exact prompt text, cannot invalidate a canned
        response for a reason nobody made.

    Raises:
        UnknownPrompt: If no such file exists, naming what does.
    """
    path = PROMPT_DIR / f"{name}{SUFFIX}"
    if not path.is_file():
        raise UnknownPrompt(
            f"no prompt named {name!r} in {PROMPT_DIR}. Available: "
            f"{list(prompt_names())}."
        )
    return path.read_text(encoding="utf-8").rstrip()
