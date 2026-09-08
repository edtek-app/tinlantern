"""Checking that a figure in generated text came from somewhere real.

Shared by the summariser and by Q&A, which ground against different
things — a curated fact set in one case, returned query rows in the
other — but need the same answer to the same question: *does this number
trace to something we supplied?*

Kept in one place because the alternative is two implementations that
drift, and the one that drifts is the one nobody is looking at.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Sequence
from datetime import date, datetime
from decimal import Decimal

#: Matches a figure as a human writes one, thousands separators included.
#: The grouped alternative must come FIRST: without it "192,431" matches
#: as 192 and 431, and a correct answer is rejected twice over. That is
#: not hypothetical — it caused four of eight failures in the first
#: real-provider eval run, because canned test data never had a number
#: big enough to need a comma.
_NUMBER = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")


def numbers_in(text: str) -> list[float]:
    """Every numeric literal in a piece of text.

    "192,431" is one number, not two. An answer written for a human uses
    separators, and a check that splits on them measures formatting.
    """
    return [float(match.replace(",", "")) for match in _NUMBER.findall(text)]


def traces_to(value: float, allowed: Iterable[float]) -> bool:
    """Whether a number in generated text matches something supplied.

    Percentages count: "82%" of a supplied 0.82 is the same fact in the
    register a reader expects, not an invention. Rejecting it would push
    the model toward less useful prose to satisfy a rule that was
    measuring formatting.
    """
    for permitted in allowed:
        if math.isclose(value, permitted, abs_tol=0.005):
            return True
        if math.isclose(value, permitted * 100, abs_tol=0.5):
            return True
    return False


def ungrounded(
    text: str, allowed: Iterable[float], scrub: Sequence[str] = ()
) -> tuple[float, ...]:
    """Numbers in ``text`` that trace to nothing supplied.

    Args:
        text: Generated text.
        allowed: Quantities that were supplied.
        scrub: Literals whose digits are not quantities — an opaque
            account id, a git short SHA, an IRI. Only the exact supplied
            strings are removed, so a *different* identifier stays
            visible and the exemption cannot smuggle a figure through.

    Returns:
        The numbers that trace to nothing, in the order they appear.
    """
    permitted = tuple(allowed)
    # Longest first: removing "05:00" before "2026-02-09 05:00" would
    # leave a fragment that no longer matches the longer form.
    for literal in sorted(scrub, key=len, reverse=True):
        if literal:
            text = text.replace(literal, " ")
    return tuple(value for value in numbers_in(text) if not traces_to(value, permitted))


def quantities_in(value: object) -> tuple[float, ...]:
    """The numbers a database value legitimately contributes.

    A date contributes its parts, so an answer may say "March 2026"
    about a row holding 2026-03-14. **A timestamp contributes its time
    of day too**: a row holding 2026-02-09 05:00 permits an answer to
    write "05:00", and admitting only the date read the 5 and the 0 as
    fabrications. A string contributes nothing — it is scrubbed instead,
    because an identifier's digits are not a quantity.
    """
    if isinstance(value, bool) or value is None:
        return ()
    if isinstance(value, int | float | Decimal):
        return (float(value),)
    if isinstance(value, datetime):
        return (
            float(value.year),
            float(value.month),
            float(value.day),
            float(value.hour),
            float(value.minute),
            float(value.second),
        )
    if isinstance(value, date):
        return (float(value.year), float(value.month), float(value.day))
    return ()


def literals_in(value: object) -> tuple[str, ...]:
    """The strings whose digits must not be read as quantities.

    A timestamp yields several renderings, not just ``isoformat()``.
    Nobody writes ``2026-02-09T05:00:00+00:00`` in a sentence; a model
    writes ``2026-02-09 05:00 UTC``, and scrubbing only the ISO form
    matched nothing and let the clock digits through. The forms are
    listed longest-first so a longer one is removed before a shorter one
    can consume part of it.
    """
    if isinstance(value, str):
        return (value,)
    if isinstance(value, datetime):
        return (
            value.isoformat(),
            value.isoformat(sep=" "),
            value.strftime("%Y-%m-%d %H:%M:%S"),
            value.strftime("%Y-%m-%dT%H:%M:%S"),
            value.strftime("%Y-%m-%d %H:%M"),
            value.strftime("%H:%M:%S"),
            value.strftime("%H:%M"),
            value.date().isoformat(),
        )
    if isinstance(value, date):
        return (value.isoformat(),)
    return ()


def row_grounding(row: dict) -> tuple[tuple[float, ...], tuple[str, ...]]:
    """What a single returned row permits a claim to say.

    Returns:
        The quantities it contributes and the literals to scrub.
    """
    quantities: list[float] = []
    # Column names are supplied text too, and they carry digits: a model
    # citing `learners_above_0_5` is naming the column it was handed, not
    # asserting 5 and 0. Scrubbing only the values missed that.
    literals: list[str] = list(row.keys())
    for value in row.values():
        quantities.extend(quantities_in(value))
        literals.extend(literals_in(value))
    return tuple(quantities), tuple(literals)
