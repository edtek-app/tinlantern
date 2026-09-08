"""`make evals` — the golden set against the configured provider.

Exits non-zero when a question fails, so a bad run cannot be mistaken
for a good one by a script. Exits non-zero on a synthetic run too: the
report is refused rather than written, and that refusal is the whole
point of the target.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from app.db import transaction
from app.llm.client import build_client
from evals.harness.golden import load
from evals.harness.report import (
    SyntheticRunRefused,
    collect_provenance,
    render,
    write,
)
from evals.harness.runner import measure, run_all
from evals.harness.variance import collect, run_repeatedly, summarise
from evals.harness.variance_report import DEFAULT_PATH as VARIANCE_PATH
from evals.harness.variance_report import render as render_variance

_STUB_REFUSAL = (
    "refusing to run against the stub provider. Canned responses measure "
    "this harness, not the model, and M4 requires a committed eval run "
    "against a real one. The gate already runs the golden set against the "
    "stub.\nSet LLM_PROVIDER=anthropic (see .env.example)."
)


def variance(runs: int) -> int:
    """Run the golden set ``runs`` times and write the variance report.

    A separate artifact and a separate command: it answers "how much
    does this move", which a single run cannot, and it costs `runs`
    times as much to produce.
    """
    questions = load()
    provider = build_client()
    model = getattr(provider, "model", provider.name)

    if provider.name == "stub":
        print(_STUB_REFUSAL, file=sys.stderr)
        return 2

    with transaction() as connection:
        by_run = run_repeatedly(questions, connection, provider, runs)

    variances = collect(by_run)
    summary = summarise(variances, runs)
    provenance = collect_provenance(
        provider.name, model, len(questions), ignore=(VARIANCE_PATH,)
    )
    Path(VARIANCE_PATH).write_text(
        render_variance(variances, summary, provenance), encoding="utf-8"
    )

    print(f"{summary.range_text} across {runs} runs")
    print(
        f"outcome stable {summary.outcome_stable}/{summary.total}, "
        f"SQL stable {summary.sql_stable}/{summary.total}"
    )
    print(f"wrote {VARIANCE_PATH}")
    return 0


def main() -> int:
    """Run the golden set and write the report."""
    questions = load()
    provider = build_client()
    model = getattr(provider, "model", provider.name)

    # Refuse up front, not after the run. With the stub every question
    # would raise UnregisteredPrompt — correct, but an opaque traceback
    # where the real answer is "this target needs a real provider".
    # `write` refuses a synthetic run too; that stays as the mechanical
    # backstop for any other path into it.
    if provider.name == "stub":
        print(_STUB_REFUSAL, file=sys.stderr)
        return 2

    with transaction() as connection:
        outcomes = run_all(questions, connection, provider)

    metrics = measure(outcomes)
    provenance = collect_provenance(provider.name, model, len(questions))
    text = render(outcomes, metrics, provenance)

    print(
        f"{metrics.passed}/{metrics.total} passed  "
        f"(refusals {metrics.refusals_correct}/{metrics.refusals_expected}, "
        f"answers {metrics.answers_correct}/{metrics.answers_expected})"
    )

    try:
        path = write(text, metrics)
    except SyntheticRunRefused as refused:
        print(refused, file=sys.stderr)
        return 2

    print(f"wrote {path}")
    return 0 if metrics.passed == metrics.total else 1


if __name__ == "__main__":
    os.environ.setdefault("LLM_PROVIDER", "anthropic")
    if "--runs" in sys.argv:
        raise SystemExit(variance(int(sys.argv[sys.argv.index("--runs") + 1])))
    raise SystemExit(main())
