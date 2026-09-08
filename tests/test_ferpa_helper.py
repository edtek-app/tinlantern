"""The FERPA helper is now the single point of failure for the posture.

Four call sites depend on it, so it gets the treatment any other
invariant guard gets: it is shown to FAIL, in both directions, rather
than trusted because it reads correctly.
"""

from __future__ import annotations

import pytest

from tests.ferpa import MIN_SUBJECT_LENGTH, assert_no_identifying_data

pytestmark = pytest.mark.m0


def test_an_empty_subject_fails_rather_than_passing() -> None:
    """The defect the helper exists to remove.

    Four guards scanned serialised output for `@` and none checked the
    output existed. `json.dumps([])` is `"[]"` — no `@` in it — so a
    generator that stopped producing statements left every FERPA guard
    green.
    """
    for empty in ("", "   ", "\n\n", "[]", "{}"):
        with pytest.raises(AssertionError, match="NOTHING WAS SCANNED"):
            assert_no_identifying_data(empty, "test subject")


def test_a_trivially_short_subject_fails() -> None:
    assert_no_identifying_data("x" * MIN_SUBJECT_LENGTH)
    with pytest.raises(AssertionError, match="NOTHING WAS SCANNED"):
        assert_no_identifying_data("x" * (MIN_SUBJECT_LENGTH - 1))


@pytest.mark.parametrize("token", ["someone@example.com", "mbox", "MAILTO:x"])
def test_identifying_data_fails(token: str) -> None:
    """Case-insensitively, since a serialiser may change casing."""
    subject = "x" * MIN_SUBJECT_LENGTH + token

    with pytest.raises(AssertionError, match="IDENTIFYING DATA FOUND"):
        assert_no_identifying_data(subject, "test subject")


def test_the_two_failures_are_distinguishable() -> None:
    """A guard that fires for two reasons must say which.

    "Nothing was scanned" and "an address was found" need opposite
    responses — one is a broken producer, the other is a leak — and a
    shared message would send whoever reads it the wrong way.
    """
    with pytest.raises(AssertionError) as empty:
        assert_no_identifying_data("", "s")
    with pytest.raises(AssertionError) as found:
        assert_no_identifying_data("x" * 40 + "a@b.com", "s")

    assert "NOTHING WAS SCANNED" in str(empty.value)
    assert "IDENTIFYING DATA FOUND" in str(found.value)
    assert "NOTHING WAS SCANNED" not in str(found.value)


def test_clean_output_passes() -> None:
    """The helper must not be so strict that nothing gets through."""
    assert_no_identifying_data(
        '[{"learner": "s-00417", "verb": "experienced"}]', "statements"
    )
