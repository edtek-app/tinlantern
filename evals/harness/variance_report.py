"""Rendering the variance measurement.

Its own artifact, not a section of the run report: it answers a
different question (how much does this move) and is regenerated far less
often than a single run.

**Runs 1-4 are excluded and the artifact says why.** They are not one
regime with noise in it — they are four different instruments. Three
reference queries changed between them and the grounding check changed
twice more, so a question's outcome in run 1 and run 4 was decided by
different oracles. Pooling them would produce a variance estimate of the
harness's development history rather than of the model.
"""

from __future__ import annotations

from evals.harness.report import SELF_AUTHORSHIP_WARNING, Provenance
from evals.harness.variance import QuestionVariance, VarianceSummary

DEFAULT_PATH = "evals/reports/m4-llm-variance.md"

EXCLUDED_RUNS = """\
> **Why the first four runs are not in this measurement.** They are not
> one regime with noise in it; they are four different instruments. The
> grounding check changed twice across them and three reference queries
> were corrected, so the same question was graded by a different oracle
> in run 1 than in run 4. Pooling them would measure this harness's
> development history rather than the model's variability. Measurement
> starts from the first run under the current code."""


def render(
    variances: tuple[QuestionVariance, ...],
    summary: VarianceSummary,
    provenance: Provenance,
) -> str:
    """The variance artifact."""
    out = [
        "# M4 — LLM layer run-to-run variance",
        "",
        provenance.render(),
        "",
        SELF_AUTHORSHIP_WARNING,
        "",
        EXCLUDED_RUNS,
        "",
        "## Hypothesis, recorded before measuring",
        "",
        "The variance is in query SHAPE — which columns the model selects "
        "— and not in the answer. The figure a question asks for is "
        "stable; what moves is how much else comes back beside it, and "
        "that reaches verification only because the grounding check is "
        "sensitive to the columns a row contains.",
        "",
        "A question raised during planning — whether `highest-risk` is "
        'underspecified, since "the highest risk score" might admit '
        "either a single scalar or a top-N reading — was checked and "
        "**rejected on the question's wording**: it asks for a score, and "
        "the score is unambiguous. `LIMIT 1` versus top-N is the model "
        "choosing how much context to return alongside an unambiguous "
        "scalar, which is the coupling itself rather than an ambiguity in "
        "the question. Recorded so a reader knows it was considered.",
        "",
        "## Result",
        "",
        f"- **{summary.range_text}** across {summary.runs} runs.",
        f"- Outcome stable (same verdict every run): "
        f"**{summary.outcome_stable} of {summary.total}**.",
        f"- SQL stable (identical query every run): "
        f"**{summary.sql_stable} of {summary.total}**.",
        f"- Malformed responses: **{summary.malformed}** across "
        f"{summary.runs * summary.total} question-runs. Its own outcome "
        "category — a structured response that would not parse is neither "
        "a refusal nor a verification failure. Not retried, so this is a "
        "failure rate. A zero here is meaningful rather than reassuring: "
        "the fault is known to exist, having aborted an earlier attempt "
        "at this very measurement.",
        "",
        "SQL stability is reported for every question, including ones "
        "that never fail. A question passing five times from five "
        "different queries is passing by luck; only this distinguishes it "
        "from one whose query is settled, and a pass rate alone would "
        "show both as a row of ticks.",
        "",
        "## Per question",
        "",
        "| id | passed | outcome stable | distinct queries |",
        "|---|---|---|---|",
    ]
    for item in variances:
        out.append(
            f"| `{item.question.id}` | {item.passes}/{item.runs} | "
            f"{'yes' if item.stable_outcome else '**no**'} | "
            f"{len(item.distinct_queries)} |"
        )
    out.append("")

    varying = [item for item in variances if not item.stable_sql]
    out += ["## Questions whose query changed between runs", ""]
    if not varying:
        out.append("None — every question produced an identical statement each run.")
    for item in varying:
        out.append(f"### `{item.question.id}` — {item.question.question}")
        out.append("")
        out.append(
            f"Passed {item.passes} of {item.runs} runs, from "
            f"{len(item.distinct_queries)} distinct queries."
        )
        for index, statement in enumerate(item.distinct_queries):
            out.append("")
            out.append(f"Query {index + 1}:")
            out.append(f"```sql\n{statement}\n```")
        for run_index, problems in sorted(item.problems.items()):
            out.append("")
            out.append(f"Run {run_index + 1} objections:")
            out.extend(f"- {problem}" for problem in problems)
        out.append("")

    return "\n".join(out)
