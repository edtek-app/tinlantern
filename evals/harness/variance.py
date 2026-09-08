"""Measuring how much the golden set's result moves between runs.

A single run is an anecdote. `highest-risk` failed, passed, failed and
passed across the first four, which is enough to know the instrument is
noisy and not enough to say how noisy — and a clean run is the easiest
place to stop looking.

**The hypothesis, recorded before measuring:** the variance is in query
SHAPE — which columns the model selects — and not in the answer. The
figure the question asks for is stable; what moves is how much else
comes back beside it, and that reaches verification only because the
grounding check is sensitive to the columns a row contains. Selecting
`scored_at` introduces a timestamp; selecting a descriptive column
introduces figures the answer may quote.

So the SQL is kept per run, not just pass/fail. The hypothesis is about
queries, so the evidence has to be the queries: a failure traced to a
column present in one run and absent in another needs both runs' SQL on
the page, not an assertion that they differed.

**Stability is reported for every question, including ones that never
fail.** If fifteen of eighteen produce identical SQL every time and
three vary, that distribution is the finding — a pass rate alone would
show eighteen passes and hide it completely.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import Connection

from app.llm.client import Provider
from evals.harness.golden import GoldenQuestion
from evals.harness.runner import Outcome, run_question


@dataclass(frozen=True, slots=True)
class QuestionVariance:
    """How one question behaved across N runs.

    Attributes:
        question: The question measured.
        passed_in: One flag per run, in run order. Stored rather than
            derived — a per-run outcome reconstructed from aggregates is
            how a summary quietly stops matching its own detail table.
        runs: How many runs it was asked in.
        sql: The statement each run produced, in run order. None where
            the question was refused before a query was planned.
        problems: Verification objections, keyed by run index, so a
            failure can be read beside the SQL that caused it.
    """

    question: GoldenQuestion
    passed_in: tuple[bool, ...]
    sql: tuple[str | None, ...]
    malformed_in: tuple[bool, ...] = ()
    problems: dict[int, tuple[str, ...]] = field(default_factory=dict)

    @property
    def malformed(self) -> int:
        """How many runs produced an unparseable response."""
        return sum(self.malformed_in)

    @property
    def runs(self) -> int:
        return len(self.passed_in)

    @property
    def passes(self) -> int:
        return sum(self.passed_in)

    @property
    def stable_outcome(self) -> bool:
        """Whether it landed the same way every run."""
        return self.passes in (0, self.runs)

    @property
    def distinct_queries(self) -> tuple[str, ...]:
        """The distinct statements it produced, in first-seen order."""
        seen: list[str] = []
        for statement in self.sql:
            if statement is not None and statement not in seen:
                seen.append(statement)
        return tuple(seen)

    @property
    def stable_sql(self) -> bool:
        """Whether every run produced the same query.

        Reported even when the outcome never varies: a question that
        passes five times from five different queries is passing by
        luck, and only this distinguishes it from one that passes
        because its query is settled.
        """
        return len(self.distinct_queries) <= 1


def collect(
    outcomes_by_run: tuple[tuple[Outcome, ...], ...],
) -> tuple[QuestionVariance, ...]:
    """Turn N runs of outcomes into per-question variance.

    Args:
        outcomes_by_run: One tuple of outcomes per run, each in the same
            question order.

    Returns:
        One record per question.
    """
    if not outcomes_by_run:
        return ()

    per_question: list[QuestionVariance] = []
    for index in range(len(outcomes_by_run[0])):
        across = [run[index] for run in outcomes_by_run]
        per_question.append(
            QuestionVariance(
                question=across[0].question,
                passed_in=tuple(outcome.passed for outcome in across),
                malformed_in=tuple(outcome.malformed for outcome in across),
                sql=tuple(
                    outcome.answer.query.sql if outcome.answer.query else None
                    for outcome in across
                ),
                problems={
                    run_index: outcome.answer.problems
                    for run_index, outcome in enumerate(across)
                    if outcome.answer.problems
                },
            )
        )
    return tuple(per_question)


def run_repeatedly(
    questions: tuple[GoldenQuestion, ...],
    connection: Connection,
    provider: Provider,
    runs: int,
) -> tuple[tuple[Outcome, ...], ...]:
    """Ask the whole set ``runs`` times.

    Each run is independent — no state carries between them, and the
    questions are asked in the same order every time so the collected
    outcomes line up by index.
    """
    return tuple(
        tuple(run_question(question, connection, provider) for question in questions)
        for _ in range(runs)
    )


@dataclass(frozen=True, slots=True)
class VarianceSummary:
    """The distribution, not a point estimate."""

    runs: int
    total: int
    best: int
    worst: int
    outcome_stable: int
    sql_stable: int
    malformed: int

    @property
    def range_text(self) -> str:
        if self.best == self.worst:
            return f"{self.best}/{self.total} every run"
        return f"{self.worst}-{self.best} of {self.total}"


def summarise(variances: tuple[QuestionVariance, ...], runs: int) -> VarianceSummary:
    """Reduce to a range and two stability counts.

    The headline is a RANGE, never a point. A single run's score is what
    made `highest-risk` look decided four times in a row while flipping
    every time.
    """
    totals = [
        sum(1 for item in variances if item.passed_in[run_index])
        for run_index in range(runs)
    ]
    return VarianceSummary(
        runs=runs,
        total=len(variances),
        best=max(totals) if totals else 0,
        worst=min(totals) if totals else 0,
        outcome_stable=sum(1 for item in variances if item.stable_outcome),
        sql_stable=sum(1 for item in variances if item.stable_sql),
        malformed=sum(item.malformed for item in variances),
    )
