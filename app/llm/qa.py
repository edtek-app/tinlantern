"""Natural-language questions answered from warehouse rows, with citations.

Two model calls with a database query between them:

1. **Plan.** The model is given the curated schema description and either
   writes one SELECT or says the question cannot be answered from this
   warehouse, and why.
2. **Run.** The query executes under both database protections
   (`app.llm.query`), and its rows are labelled `row:0`, `row:1`, ...
3. **Answer.** The model is given those labelled rows and returns claims,
   each citing the row it rests on. Every claim is checked against the
   row it names before anything is rendered.

**There is no fallback answer here, unlike the summariser.** A summary
has a curated fact set that code can always turn into grounded prose; an
arbitrary question does not, so the honest outcome when a question cannot
be answered — or when an answer fails verification — is a refusal that
says what is missing. Guessing at an unanswerable question is the failure
this milestone exists to prevent, and a template that guessed politely
would be the same failure with better manners.

The executed SQL is retained on the result beside the cited rows, so a
dashboard can show what was run, and an eval artifact records how an
answer was reached rather than only that it was grounded.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import Connection

from app.llm.client import ModelRefused, Provider
from app.llm.grounding import numbers_in, row_grounding, ungrounded
from app.llm.prompt_library import load_prompt
from app.llm.query import QueryResult, UnsafeStatement, run_generated_query
from app.llm.schema_context import describe

#: What the planning call must return.
PLAN_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "answerable": {"type": "boolean"},
        "sql": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["answerable", "sql", "reason"],
    "additionalProperties": False,
}

#: What the answering call must return. Same claim shape as the
#: summariser, so the citation machinery is one mechanism rather than
#: two — here `source` is a row label instead of a fact key.
ANSWER_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "source": {"type": "string"},
                },
                "required": ["text", "source"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["claims"],
    "additionalProperties": False,
}


@dataclass(frozen=True, slots=True)
class Answer:
    """An answer, or a refusal, with everything needed to audit it.

    Attributes:
        text: What a reader sees — the answer, or why there is none.
        answered: False for every refusal, whatever caused it.
        refusal_reason: Why, when there is no answer.
        query: The executed query and its rows, when one ran. Retained
            even on a verification failure, because "what did it run"
            is the first question anyone asks about a bad answer.
        citations: Only the rows an accepted claim cited, keyed by label.
        problems: What verification objected to.
        synthetic: True when no model was called (stub provider).
    """

    text: str
    answered: bool
    refusal_reason: str | None = None
    query: QueryResult | None = None
    citations: dict[str, dict] = field(default_factory=dict)
    problems: tuple[str, ...] = ()
    synthetic: bool = False


def plan_prompt(question: str) -> str:
    """The planning request: instructions, schema, then the question."""
    return (
        f"{load_prompt('nl_to_sql')}\n\n"
        f"## Schema\n\n{describe()}\n\n"
        f"## Question\n\n{question}"
    )


def answer_prompt(question: str, result: QueryResult) -> str:
    """The answering request: instructions, labelled rows, the question."""
    lines = [load_prompt("answer_from_rows"), "", "## Rows", ""]
    if not result.rows:
        lines.append("The query returned no rows.")
    for label, row in result.labelled().items():
        rendered = ", ".join(f"{key}={value!r}" for key, value in row.items())
        lines.append(f"- `{label}`: {rendered}")
    if result.truncated:
        lines.append("")
        lines.append(
            "These rows are TRUNCATED — more matched than were returned. Do "
            "not describe them as the complete set or total them."
        )
    lines.extend(["", "## Question", "", question])
    return "\n".join(lines)


def verify_claims(
    payload: dict, result: QueryResult, question: str = ""
) -> tuple[str, ...]:
    """Check every claim against the row it cites.

    A claim's figures must come from **the row it named**, not from any
    row that happened to be returned. Grounding against the whole result
    set would let a claim cite one learner and quote another's score,
    which is the kind of error that reads perfectly.

    **Numbers the question itself supplied are admitted.** Asked "how
    many learners have a risk score above 0.5", an answer repeats 0.5 —
    a figure that came from the person asking, not from a row. Rejecting
    it treated the user's own words as a fabrication. This does widen
    what counts as grounded, and ADR-0008 records that.
    """
    asked = tuple(numbers_in(question))
    labelled = result.labelled()
    problems: list[str] = []

    claims = payload.get("claims") or []
    if not claims:
        problems.append("no claims returned")

    for index, claim in enumerate(claims):
        source = claim.get("source", "")
        text = claim.get("text", "")

        row = labelled.get(source)
        if row is None:
            problems.append(
                f"claim {index} cites {source!r}, which is not a row this "
                f"query returned. Available: {sorted(labelled)}"
            )
            continue

        quantities, literals = row_grounding(row)
        for value in ungrounded(text, quantities + asked, scrub=literals):
            problems.append(
                f"claim {index} contains {value:g}, which is not in the row "
                f"it cites ({source}) — either the model computed something "
                "the query did not return, or it cited the wrong row"
            )

    return tuple(problems)


def render(payload: dict) -> str:
    """Arrange verified claims into the answer a reader sees."""
    return " ".join(claim["text"].strip() for claim in payload["claims"])


def _refuse(reason: str, query: QueryResult | None = None, **extra) -> Answer:
    return Answer(
        text=f"I can't answer that from this data. {reason}",
        answered=False,
        refusal_reason=reason,
        query=query,
        **extra,
    )


def ask(question: str, connection: Connection, provider: Provider) -> Answer:
    """Answer a question from the warehouse, or refuse and say why.

    Args:
        question: The natural-language question.
        connection: An open connection inside a transaction. The
            generated query runs under `SET LOCAL` protections that
            revert when it ends.
        provider: Where to send both model calls.

    Returns:
        An answer whose every claim cites a returned row, or a refusal.
    """
    try:
        plan, planning = provider.complete_json(
            system=load_prompt("grounding"),
            prompt=plan_prompt(question),
            schema=PLAN_SCHEMA,
        )
    except ModelRefused:
        return _refuse("The model declined to plan a query for this question.")

    synthetic = planning.synthetic

    if not plan.get("answerable"):
        return _refuse(
            plan.get("reason") or "The warehouse does not hold what this needs.",
            synthetic=synthetic,
        )

    try:
        result = run_generated_query(connection, plan["sql"])
    except UnsafeStatement as rejected:
        return _refuse(
            f"The generated statement was rejected before it ran: {rejected}",
            synthetic=synthetic,
        )

    try:
        payload, answering = provider.complete_json(
            system=load_prompt("grounding"),
            prompt=answer_prompt(question, result),
            schema=ANSWER_SCHEMA,
        )
    except ModelRefused:
        return _refuse(
            "The model declined to answer from these rows.",
            query=result,
            synthetic=synthetic,
        )

    problems = verify_claims(payload, result, question)
    if problems:
        return _refuse(
            "The answer could not be verified against the rows the query "
            "returned, so it is withheld rather than shown with a caveat.",
            query=result,
            problems=problems,
            synthetic=answering.synthetic,
        )

    cited = {claim["source"] for claim in payload["claims"]}
    labelled = result.labelled()

    return Answer(
        text=render(payload),
        answered=True,
        query=result,
        citations={label: labelled[label] for label in sorted(cited)},
        synthetic=answering.synthetic,
    )
