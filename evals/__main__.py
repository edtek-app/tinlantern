"""`make evals` — the golden set against the configured provider.

Exits non-zero when a question fails, so a bad run cannot be mistaken
for a good one by a script. Exits non-zero on a synthetic run too: the
report is refused rather than written, and that refusal is the whole
point of the target.
"""

from __future__ import annotations

import os
import sys

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
        print(
            "refusing to run `make evals` against the stub provider. Canned "
            "responses measure this harness, not the model, and M4 requires "
            "a committed eval run against a real one. The gate already runs "
            "the golden set against the stub.\n"
            "Set LLM_PROVIDER=anthropic (see .env.example).",
            file=sys.stderr,
        )
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
    raise SystemExit(main())
