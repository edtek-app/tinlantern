"""Loading and validating the golden question set.

Validation is strict and happens at load, because every way this file can
be wrong produces a *misleading* result rather than an error: an
answerable question with no reference query is graded on disposition
alone and silently stops catching wrong answers; a refusal case with a
reference query is a contradiction nobody would notice.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

DEFAULT_PATH = Path(__file__).resolve().parents[1] / "golden" / "questions.yaml"

ANSWERED = "answered"
REFUSED = "refused"
DISPOSITIONS = (ANSWERED, REFUSED)


class GoldenSetError(ValueError):
    """The golden file is malformed in a way that would mislead."""


@dataclass(frozen=True, slots=True)
class GoldenQuestion:
    """One graded question.

    Attributes:
        id: Stable identifier, used in the report.
        question: What is asked, verbatim.
        disposition: What the system MUST do — answer, or refuse.
        reference_sql: Hand-written query computing the truth. Required
            for answerable questions, forbidden for refusals.
        kind: For refusals, whether it is out-of-scope or the harder
            looks-answerable kind.
        why: For refusals, why it cannot be answered. Read by a human
            reviewing the report, and by nothing else.
    """

    id: str
    question: str
    disposition: str
    reference_sql: str | None = None
    kind: str | None = None
    why: str | None = None

    @property
    def expects_answer(self) -> bool:
        return self.disposition == ANSWERED


def parse(entries: list[dict]) -> tuple[GoldenQuestion, ...]:
    """Validate raw entries into questions.

    Raises:
        GoldenSetError: On anything that would grade a question wrongly.
    """
    if not entries:
        raise GoldenSetError("the golden set is empty")

    questions: list[GoldenQuestion] = []
    seen: set[str] = set()

    for entry in entries:
        identifier = entry.get("id")
        if not identifier:
            raise GoldenSetError(f"an entry has no id: {entry}")
        if identifier in seen:
            raise GoldenSetError(f"duplicate id {identifier!r}")
        seen.add(identifier)

        disposition = entry.get("disposition")
        if disposition not in DISPOSITIONS:
            raise GoldenSetError(
                f"{identifier}: disposition must be one of {list(DISPOSITIONS)}, "
                f"got {disposition!r}"
            )

        reference = entry.get("reference_sql")
        if disposition == ANSWERED and not reference:
            raise GoldenSetError(
                f"{identifier}: an answerable question needs a reference_sql. "
                "Without one it is graded on disposition alone, which stops "
                "catching an answer that is grounded and wrong — the failure "
                "the reference query exists for."
            )
        if disposition == REFUSED and reference:
            raise GoldenSetError(
                f"{identifier}: a refusal case must not carry a reference_sql; "
                "there is no truth to compare against"
            )
        if disposition == REFUSED and not entry.get("why"):
            raise GoldenSetError(
                f"{identifier}: a refusal case must say why it cannot be "
                "answered, so a reviewer can judge whether the refusal is right"
            )

        questions.append(
            GoldenQuestion(
                id=identifier,
                question=entry["question"],
                disposition=disposition,
                reference_sql=reference.strip() if reference else None,
                kind=entry.get("kind"),
                why=entry.get("why"),
            )
        )

    return tuple(questions)


def load(path: Path = DEFAULT_PATH) -> tuple[GoldenQuestion, ...]:
    """Read and validate the golden set."""
    return parse(yaml.safe_load(path.read_text(encoding="utf-8")))
