"""The generated TypeScript types, and the generator's refusal to guess.

The whole argument for a hand-written generator over `openapi-typescript`
is that it RAISES on a construct it cannot express, where a general tool
emits `any`. `any` is a type that checks nothing while looking like it
does, so the raise is the property under test here — not the sixty lines.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel

from tools.generate_api_types import (
    DEFAULT_PATH,
    DYNAMIC_FIELDS,
    UnsupportedSchema,
    generate,
    render,
)

pytestmark = pytest.mark.m5

ROOT = Path(__file__).resolve().parents[1]


def test_the_committed_types_match_the_models() -> None:
    """Drift fails the gate, the same shape as the warehouse guard.

    A hand-maintained parallel definition with a "keep in sync" comment
    is a convention; regenerating and comparing is a mechanism.
    """
    committed = (ROOT / DEFAULT_PATH).read_text(encoding="utf-8")

    assert committed == generate(), (
        "the committed TypeScript no longer matches the Pydantic models. "
        "Regenerate with `python -m tools.generate_api_types` — do not "
        "edit the generated file, it will not survive the gate."
    )


def test_the_generated_file_contains_no_any() -> None:
    """Belt and braces: the argument is that `any` never appears.

    Checked as a token, so a field legitimately named `company` or a
    word like `many` does not trip it.
    """
    import re

    text = (ROOT / DEFAULT_PATH).read_text(encoding="utf-8")

    assert not re.search(r"\bany\b", text), (
        "`any` reached the generated types. Either the generator stopped "
        "raising on a construct it cannot express, or someone edited the "
        "file by hand — both mean the compiler silently stopped checking."
    )


def test_the_generator_raises_rather_than_emitting_any() -> None:
    """The property the whole design decision rests on.

    A general tool meets an unexpressible construct and emits `any`.
    This one must fail the build instead, so an untypeable field is a
    decision someone makes rather than one that happens to them.
    """

    class Unhandled(BaseModel):
        anything: dict  # unconstrained value type, not exempt

    with pytest.raises(UnsupportedSchema, match="unconstrained properties"):
        render(Unhandled)


def test_an_ambiguous_union_raises_too() -> None:
    class Ambiguous(BaseModel):
        either: int | str

    with pytest.raises(UnsupportedSchema, match="union"):
        render(Ambiguous)


def test_the_dynamic_exemption_emits_unknown_not_any() -> None:
    """`unknown` forces the consumer to narrow; `any` lets them skip it.

    A cited row's columns genuinely vary between runs — ADR-0008's
    coupling finding — so no fixed interface describes one. That is a
    real exemption, and it must still produce a type that makes the
    caller prove what it holds.
    """
    text = generate()

    assert "Record<string, unknown>" in text
    assert set(DYNAMIC_FIELDS) == {"Answer.citations"}, (
        "the dynamic exemption has grown. Every entry is a place the "
        "compiler stops helping, so each one needs its own reason"
    )


def test_optional_fields_are_nullable_not_absent() -> None:
    """`fallback_reason` is null when a model wrote the summary.

    Typed `string | null` rather than optional: the field is always
    present, and a client checking `if (x.fallback_reason)` should not
    have to also handle it being missing.
    """
    text = generate()

    assert "fallback_reason: string | null;" in text
