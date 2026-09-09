"""Capture real model responses once, so the demo replays them.

Development tooling. Needs a real provider and is never run at request
time or in the gate.

**The demo replays real output rather than serving invented text.** A
demo whose answers were written by hand would be a mock-up of the
product; these are what the model actually said, captured and pinned.
Each entry stores the model and the date it was recorded, so a
partially re-recorded set stays accurate about every entry instead of
inheriting one date from whichever run happened last.

Run:  DEMO_MODE= LLM_PROVIDER=anthropic python -m tools.record_demo_responses
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from app.db import transaction
from app.llm.client import build_client
from app.llm.prompt_library import load_prompt
from app.llm.providers.stub import request_digest
from app.llm.qa import ANSWER_SCHEMA, PLAN_SCHEMA, answer_prompt, plan_prompt
from app.llm.query import run_generated_query

DEFAULT_PATH = Path("app/llm/providers/canned/demo_qa.json")

#: The questions a demo actually gets asked. Deliberately a mix: three
#: it can answer and one it must refuse, because a demo that only shows
#: successes teaches nothing about how the system behaves at its edge —
#: and refusing well is the strongest result M4 measured.
DEMO_QUESTIONS: tuple[str, ...] = (
    "How many learners are currently flagged as at risk?",
    "What is the average scaled score across all graded work?",
    "Which verb appears most often in the activity records?",
    "What is each learner's final grade for the course?",
)


def record() -> dict[str, dict[str, str]]:
    """Ask each demo question for real and keep both calls' responses."""
    provider = build_client()
    model = getattr(provider, "model", provider.name)
    today = datetime.now(UTC).date().isoformat()
    system = load_prompt("grounding")
    captured: dict[str, dict[str, str]] = {}

    def keep(prompt: str, body: str) -> None:
        captured[request_digest(system, prompt)] = {
            "body": body,
            "model": model,
            "recorded_on": today,
        }

    with transaction() as connection:
        for question in DEMO_QUESTIONS:
            plan_text = plan_prompt(question)
            plan, _ = provider.complete_json(
                system=system, prompt=plan_text, schema=PLAN_SCHEMA
            )
            keep(plan_text, json.dumps(plan))
            print(f"  planned: {question[:52]}")

            if not plan.get("answerable"):
                continue

            result = run_generated_query(connection, plan["sql"])
            answer_text = answer_prompt(question, result)
            payload, _ = provider.complete_json(
                system=system, prompt=answer_text, schema=ANSWER_SCHEMA
            )
            keep(answer_text, json.dumps(payload))
            print(f"  answered: {question[:52]}")

    return captured


def main() -> int:
    """Record and write. Refuses to run against the stub."""
    provider = build_client()
    if provider.name == "stub":
        print(
            "refusing to record against the stub. The point of these "
            "responses is that a real model wrote them; recording the "
            "stub's own output would produce a demo of the demo. Unset "
            "DEMO_MODE and set LLM_PROVIDER=anthropic."
        )
        return 2

    captured = record()
    DEFAULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_PATH.write_text(
        json.dumps(captured, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"wrote {len(captured)} responses to {DEFAULT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
