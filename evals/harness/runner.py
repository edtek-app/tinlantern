"""Running the golden set and grading what comes back.

Grading is two independent questions, and both must pass:

**Did it do the right thing?** A question marked `refused` that gets
answered is a failure, not a lower score, and so is the inverse. A system
that answers everything is fluent, not grounded, and the disposition
check is what tells them apart.

**Is the answer right?** Citations prove a figure came from a returned
row. They do not prove the query measured what was asked — ADR-0008's
addendum names that gap explicitly. The reference query closes it: a
hand-written statement computes the truth and the answer must state it.

The reference query is the harness's own, never the model's. A wrong
reference query would blame the model for the harness's mistake, which
is the worst outcome available here, so each one is separately tested
against a loaded cohort.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Connection, text

from app.llm.client import MalformedResponse, Provider
from app.llm.grounding import numbers_in, traces_to
from app.llm.qa import Answer, ask
from evals.harness.golden import GoldenQuestion


@dataclass(frozen=True, slots=True)
class Outcome:
    """How one golden question went.

    Attributes:
        question: The question that was graded.
        answer: What the system produced.
        truth: The reference value, when there is one.
        passed: Both checks succeeded.
        problems: Why not, when it failed.
    """

    question: GoldenQuestion
    answer: Answer
    truth: object | None
    passed: bool
    problems: tuple[str, ...]
    malformed: bool = False

    @property
    def observed(self) -> str:
        if self.malformed:
            return "malformed"
        return "answered" if self.answer.answered else "refused"


def reference_value(connection: Connection, sql: str) -> object:
    """Compute the truth for one question.

    Returns:
        The single value the reference query produces.

    Raises:
        ValueError: If the query does not return exactly one row and one
            column. The comparison has no meaning otherwise, and a
            reference query that silently returns a wider result would
            grade every answer as wrong.
    """
    rows = connection.execute(text(sql)).fetchall()
    if len(rows) != 1 or len(rows[0]) != 1:
        raise ValueError(
            f"a reference query must return exactly one row and one column; "
            f"got {len(rows)} rows of {len(rows[0]) if rows else 0} columns"
        )
    return rows[0][0]


def states_truth(answer_text: str, truth: object) -> bool:
    """Whether the answer actually states the reference value.

    Numbers are compared numerically — an answer saying "82%" of a
    reference 0.82 is stating it. Text is compared case-insensitively
    as a substring, which is how a verb or a course name appears in a
    sentence.
    """
    if truth is None:
        return False
    if isinstance(truth, str):
        return truth.lower() in answer_text.lower()
    reference = (float(truth),)
    return any(traces_to(number, reference) for number in numbers_in(answer_text))


def grade(
    question: GoldenQuestion,
    answer: Answer,
    truth: object | None,
) -> Outcome:
    """Apply both checks to one result."""
    problems: list[str] = []

    if question.expects_answer and not answer.answered:
        problems.append(
            "expected an answer and got a refusal. Either the schema "
            f"description does not make the path obvious, or: "
            f"{answer.refusal_reason}"
        )
    elif not question.expects_answer and answer.answered:
        problems.append(
            "expected a refusal and got an answer. This question cannot be "
            "answered from the warehouse, so whatever figure was produced "
            "measures something other than what was asked"
        )

    if (
        question.expects_answer
        and answer.answered
        and not states_truth(answer.text, truth)
    ):
        problems.append(
            f"the answer does not state the reference value ({truth!r}). "
            "Every claim cited a returned row, so the query ran and the "
            "figures are real — they answer a different question than "
            "the one asked"
        )

    return Outcome(
        question=question,
        answer=answer,
        truth=truth,
        passed=not problems,
        problems=tuple(problems),
    )


def run_question(
    question: GoldenQuestion, connection: Connection, provider: Provider
) -> Outcome:
    """Ask one golden question and grade the result.

    A malformed response is graded as its own outcome rather than
    raised. It is a real property of the system being measured — not a
    refusal and not a verification failure — and a harness that aborted
    on it would let one bad response in one run destroy the whole
    measurement. One bad response costs one question in one run.

    Its rate is reported. It is NOT retried: a retry rate and a failure
    rate are different measurements, and retry policy belongs to the
    caller (ADR-0008), not to the thing measuring the caller's system.
    """
    truth = (
        reference_value(connection, question.reference_sql)
        if question.reference_sql
        else None
    )
    try:
        answer = ask(question.question, connection, provider)
    except MalformedResponse as broken:
        return Outcome(
            question=question,
            answer=Answer(
                text="",
                answered=False,
                refusal_reason="malformed response",
                problems=(str(broken),),
            ),
            truth=truth,
            passed=False,
            problems=(
                f"the model's structured response could not be parsed: "
                f"{broken}. stop_reason={broken.stop_reason!r}. Raw text: "
                f"{broken.raw[:300]!r}",
            ),
            malformed=True,
        )
    return grade(question, answer, truth)


def run_all(
    questions: tuple[GoldenQuestion, ...],
    connection: Connection,
    provider: Provider,
) -> tuple[Outcome, ...]:
    """Run the whole set."""
    return tuple(run_question(item, connection, provider) for item in questions)


@dataclass(frozen=True, slots=True)
class Metrics:
    """What the run measured.

    Refusal accuracy is reported separately from answer accuracy rather
    than folded into one rate. A single number hides the trade between
    them: a system that refuses everything scores perfectly on refusals
    and is useless, and the combined figure would look merely mediocre.
    """

    total: int
    passed: int
    refusals_expected: int
    refusals_correct: int
    answers_expected: int
    answers_correct: int
    malformed: int
    synthetic: bool

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total if self.total else 0.0


def measure(outcomes: tuple[Outcome, ...]) -> Metrics:
    """Summarise a run."""
    refusals = [item for item in outcomes if not item.question.expects_answer]
    answers = [item for item in outcomes if item.question.expects_answer]
    return Metrics(
        total=len(outcomes),
        passed=sum(1 for item in outcomes if item.passed),
        refusals_expected=len(refusals),
        refusals_correct=sum(1 for item in refusals if item.passed),
        answers_expected=len(answers),
        answers_correct=sum(1 for item in answers if item.passed),
        malformed=sum(1 for item in outcomes if item.malformed),
        synthetic=all(item.answer.synthetic for item in outcomes) if outcomes else True,
    )
