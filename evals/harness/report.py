"""Rendering an eval run, and refusing to write a synthetic one.

**A stub run never becomes a committed artifact.** Canned responses
measure the harness, not the model, and a report in `evals/reports/`
that had only ever seen canned text would satisfy M4's acceptance
criterion while proving nothing — the exact "looks healthy while broken"
failure the criterion exists to prevent. So `write` refuses when the run
was synthetic, and the provenance header names the provider and model so
a committed artifact identifies itself.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from evals.harness.runner import Metrics, Outcome

DEFAULT_PATH = "evals/reports/m4-llm.md"


class SyntheticRunRefused(RuntimeError):
    """A stub run was asked to write a report. It may not."""


@dataclass(frozen=True, slots=True)
class Provenance:
    """What produced these results, including WHICH MODEL."""

    generated_at: str
    commit: str | None
    dirty: bool | None
    provider: str
    model: str
    questions: int

    def render(self) -> str:
        commit = self.commit or "unknown (not a git checkout)"
        dirty = "" if self.dirty is False else "  **working tree dirty**"
        return (
            "> **Provenance.** Generated "
            f"{self.generated_at} from commit `{commit}`{dirty}, against "
            f"provider `{self.provider}` model `{self.model}`, over "
            f"{self.questions} golden questions.\n>\n"
            "> Regenerate with `make evals`. A report generated against the "
            "`stub` provider is refused rather than written: canned "
            "responses measure this harness, not the model."
        )


def collect_provenance(
    provider: str, model: str, questions: int, ignore: tuple[str, ...] = (DEFAULT_PATH,)
) -> Provenance:
    """Capture the context, excluding the report being written."""

    def git(*args: str) -> str | None:
        try:
            done = subprocess.run(args, capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            return None
        return done.stdout.strip() if done.returncode == 0 else None

    status = git("git", "status", "--porcelain")
    if status is not None:
        status = "\n".join(
            line
            for line in status.splitlines()
            if not any(path in line for path in ignore)
        )

    return Provenance(
        generated_at=datetime.now(UTC).isoformat(timespec="seconds"),
        commit=git("git", "rev-parse", "HEAD"),
        dirty=None if status is None else bool(status),
        provider=provider,
        model=model,
        questions=questions,
    )


#: Printed in the OPENING framing of every report, beside the score,
#: never in a limitations section at the bottom. A reader who takes the
#: headline number away must take this with it — the same treatment the
#: separability warning gets in the M3 report, and for the same reason:
#: a limitation a reader has to go looking for is one they will not find.
SELF_AUTHORSHIP_WARNING = """\
> **Read the score with this.** The questions, the reference queries
> that decide whether an answer is right, and the schema description the
> model plans against were **all written by the same author**. This set
> cannot detect a misconception shared between the person who wrote the
> questions and the person who wrote the context the model answers from.
>
> That is a structural limitation of the instrument, not a caveat about
> any one run. It does not weaken with a better score — a perfect run
> under this arrangement and a perfect run under an independent one are
> not the same evidence. What would strengthen it is an independently
> authored question set, or questions drawn from real advisor queries
> (ADR-0008, revisit triggers)."""


def render(
    outcomes: tuple[Outcome, ...], metrics: Metrics, provenance: Provenance
) -> str:
    """The report a reviewer reads."""
    out = ["# M4 — LLM layer evaluation", "", provenance.render(), ""]

    out += [
        "## Results",
        "",
        SELF_AUTHORSHIP_WARNING,
        "",
        f"- **{metrics.passed} of {metrics.total} passed** ({metrics.pass_rate:.0%}).",
        f"- Refusals: {metrics.refusals_correct} of "
        f"{metrics.refusals_expected} correct. A question the warehouse "
        "cannot answer must be refused; answering it produces a real "
        "figure for a different question.",
        f"- Answers: {metrics.answers_correct} of {metrics.answers_expected} "
        "correct, where correct means the answer states the value a "
        "hand-written reference query computes — not merely that every "
        "claim cited a returned row.",
        "",
        "Refusal and answer accuracy are reported separately on purpose. "
        "Combined, they hide the trade: a system that refuses everything "
        "scores perfectly on one and is useless.",
        "",
    ]

    failures = [item for item in outcomes if not item.passed]
    out += ["## Failures", ""]
    if not failures:
        out.append("None.")
    for item in failures:
        out.append(f"### `{item.question.id}` — {item.question.question}")
        out.append("")
        out.append(f"Expected {item.question.disposition}, got {item.observed}.")
        for problem in item.problems:
            out.append(f"- {problem}")
        # What verification actually objected to. Without this the report
        # says only "could not be verified", and learning why costs a
        # live API call per failure — the same shape as a rejection row
        # nobody can act on.
        if item.answer.problems:
            out.append("")
            out.append("Verification objected to:")
            for problem in item.answer.problems:
                out.append(f"- {problem}")
        if item.answer.query is not None:
            out.append("")
            out.append(f"Query run:\n```sql\n{item.answer.query.sql}\n```")
        out.append("")

    out += [
        "## Every question",
        "",
        "| id | expected | observed | passed |",
        "|---|---|---|---|",
    ]
    for item in outcomes:
        mark = "yes" if item.passed else "**no**"
        out.append(
            f"| `{item.question.id}` | {item.question.disposition} | "
            f"{item.observed} | {mark} |"
        )
    out.append("")

    return "\n".join(out)


def write(text: str, metrics: Metrics, path: str = DEFAULT_PATH) -> Path:
    """Write the report, unless the run was synthetic.

    Raises:
        SyntheticRunRefused: If every answer came from the stub. The
            rule that stops a canned run masquerading as a real one is
            mechanical rather than a note in a README.
    """
    if metrics.synthetic:
        raise SyntheticRunRefused(
            "refusing to write an eval report from a synthetic run. Every "
            "answer came from the stub provider, so this measures the "
            "harness and not the model. A committed report is M4's evidence "
            "that the golden set has met a real model; one produced from "
            "canned responses would satisfy the criterion while proving "
            "nothing. Run `make evals` with LLM_PROVIDER=anthropic."
        )
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text, encoding="utf-8")
    return destination
