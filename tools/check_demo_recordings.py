"""Are the demo's recorded responses still true of the loaded cohort?

Run before showing the demo, and at milestone close.

**Staleness is already detected — the problem is when.** A recorded
answer is keyed by the exact request that produced it, and that request
embeds the rows the query returned. Change the cohort and the digest
changes, so the recording is not found and `UnregisteredPrompt` is
raised. That is loud, correct, and arrives mid-demo in front of an
audience. This moves the discovery earlier.

**It needs no reference queries.** The digest covers every column the
query returned, which is strictly stronger than comparing one figure a
hand-written oracle computes — and this repository's hand-written
oracles have been wrong three times in twelve (see evals/README.md), so
not growing that surface to protect a demo is the point.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from app.db import transaction
from app.demo import DEMO_QUESTIONS
from app.llm.prompt_library import load_prompt
from app.llm.providers.stub import Recorded, recorded_responses, request_digest
from app.llm.qa import answer_prompt, plan_prompt
from app.llm.query import run_generated_query


@dataclass(frozen=True, slots=True)
class Staleness:
    """What is wrong with one demo question's recordings."""

    question: str
    problem: str


def check(
    connection, questions: tuple[str, ...] = DEMO_QUESTIONS
) -> tuple[Staleness, ...]:
    """Confirm every recorded response is still reachable.

    Args:
        connection: An open connection to the loaded cohort.
        questions: The demo question set.

    Returns:
        One entry per problem found; empty when the recordings still
        match the data.
    """
    system = load_prompt("grounding")
    recordings: dict[str, Recorded] = recorded_responses()
    problems: list[Staleness] = []

    for question in questions:
        plan_key = request_digest(system, plan_prompt(question))
        planned = recordings.get(plan_key)
        if planned is None:
            problems.append(
                Staleness(
                    question,
                    "no recorded plan. Either the question is new, or a "
                    "prompt file or the schema description changed — both "
                    "alter the request the plan is keyed by.",
                )
            )
            continue

        plan = json.loads(planned.body)
        if not plan.get("answerable"):
            # A refusal never reached a query, so there is nothing that
            # could have gone stale with the data.
            continue

        result = run_generated_query(connection, plan["sql"])
        if request_digest(system, answer_prompt(question, result)) not in recordings:
            problems.append(
                Staleness(
                    question,
                    "the recorded answer no longer matches what the query "
                    f"returns. Current rows: {result.rows}. The recording "
                    "was made against different data, so replaying it would "
                    "state figures that are not on the screen — re-record "
                    "with `python -m tools.record_demo_responses`.",
                )
            )

    return tuple(problems)


def main() -> int:
    """Check, and name what is stale."""
    with transaction() as connection:
        problems = check(connection)

    if not problems:
        print(
            f"demo recordings match the loaded cohort ({len(DEMO_QUESTIONS)} questions)"
        )
        return 0

    print(f"{len(problems)} of {len(DEMO_QUESTIONS)} demo questions are stale:\n")
    for item in problems:
        print(f"  {item.question}\n    {item.problem}\n")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
