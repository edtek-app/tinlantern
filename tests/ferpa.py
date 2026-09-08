"""The FERPA scan, with its precondition built in.

Four tests scanned serialised output for email-shaped data and none of
them checked the output was non-empty. `json.dumps([])` is `"[]"`, which
contains no `@` — so a generator that stopped producing statements would
have left every one of those guards passing while the thing they guard
went unexercised. An absence assertion is satisfied by an absent
subject.

The precondition and the scan live together here so a fifth call site
cannot be written without one. That is the mechanism; "remember to check
the subject is non-empty" is the convention it replaces.
"""

from __future__ import annotations

#: Shorter than this and the subject is not a serialised cohort artifact
#: — it is `""`, `"[]"`, `"{}"`, or an error string. The exact value is
#: not load-bearing; being non-trivial is.
MIN_SUBJECT_LENGTH = 32

FORBIDDEN = ("@", "mbox", "mailto")


def assert_no_identifying_data(text: str, subject: str = "output") -> None:
    """Fail if `text` is empty, or if it carries email-shaped data.

    Args:
        text: The serialised artifact to scan.
        subject: What produced it, named in the failure message.

    Raises:
        AssertionError: With a message distinguishing the two reasons a
            FERPA guard can fire. "Nothing was scanned" and "an address
            was found" need opposite responses, and a shared message
            would send whoever reads it the wrong way.
    """
    stripped = text.strip()
    assert len(stripped) >= MIN_SUBJECT_LENGTH, (
        f"the {subject} scanned for identifying data is empty or trivial "
        f"({len(stripped)} characters). NOTHING WAS SCANNED — this is not "
        "a passing FERPA check, it is an absent subject, and the guard "
        "would stay silent for exactly as long as the producer stayed "
        "broken."
    )

    for token in FORBIDDEN:
        assert token not in text.lower(), (
            f"IDENTIFYING DATA FOUND in {subject}: {token!r}. Learners are "
            "opaque account identifiers everywhere (ADR-0002); an address "
            "in a screenshot, a log, or a demo cannot be un-seen."
        )
