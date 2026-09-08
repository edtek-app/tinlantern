"""The eval harness: mechanics in the gate, quality only against a model.

**These tests assert that the harness works, not that the model does.**
Everything here runs against the stub, so a threshold on groundedness
here would be satisfied by canned text — the "looks healthy while
broken" pattern. Quality thresholds belong to `make evals`, and M4's
acceptance criteria require a committed real-provider run for exactly
that reason.

What the gate must prove is that the mechanics can DETECT failure, not
merely complete. The seeded-wrong-answer cases below are that proof.

Every test seeds the rows it asserts on. The reference queries are run
against a cohort this file builds, so a broken reference query fails
here as a test rather than later as a model failure.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import Connection, text

from app.llm.prompt_library import load_prompt
from app.llm.providers.stub import StubProvider, canned
from app.llm.qa import answer_prompt, plan_prompt
from app.llm.query import run_generated_query
from evals.harness.golden import (
    BOUNDS,
    PROBABILITY,
    TEXT,
    GoldenQuestion,
    GoldenSetError,
    load,
    parse,
)
from evals.harness.report import (
    Provenance,
    SyntheticRunRefused,
    collect_provenance,
    render,
    write,
)
from evals.harness.runner import (
    grade,
    measure,
    reference_value,
    run_all,
    states_truth,
)

pytestmark = pytest.mark.m4

ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------
# A small real cohort, built by these tests
# --------------------------------------------------------------------------


def seed_cohort(connection: Connection) -> None:
    """Enough warehouse rows for every reference query to mean something.

    Deliberately small and hand-built rather than the generated cohort:
    the numbers below are checkable by eye, so a reference query that
    counts the wrong thing is visible here rather than plausible.
    """
    connection.execute(
        text(
            "INSERT INTO warehouse.dim_course (course_iri, course_slug, title) "
            "VALUES ('urn:c:1', 'c1', 'Course One'), ('urn:c:2', 'c2', 'Course Two')"
        )
    )
    connection.execute(
        text(
            "INSERT INTO warehouse.dim_student (learner_identifier, "
            "account_home_page) VALUES "
            "('s-00001', 'https://x.invalid'), ('s-00002', 'https://x.invalid'), "
            "('s-00003', 'https://x.invalid')"
        )
    )
    connection.execute(
        text(
            "INSERT INTO warehouse.dim_date (date_key, full_date, year, quarter, "
            "month, day, iso_week, day_of_week, day_name, is_weekend) VALUES "
            "(20260201, DATE '2026-02-01', 2026, 1, 2, 1, 5, 7, 'Sunday', true), "
            "(20260202, DATE '2026-02-02', 2026, 1, 2, 2, 6, 1, 'Monday', false) "
            # The date dimension is pre-populated by migration 0003, so
            # these rows may already be there. Conflicting is expected;
            # the fact rows below are what this cohort actually asserts on.
            "ON CONFLICT (date_key) DO NOTHING"
        )
    )
    # Course One has two activities, Course Two has one.
    connection.execute(
        text(
            "INSERT INTO warehouse.dim_activity (activity_iri, activity_type, "
            "name, course_key, module_index) SELECT 'urn:a:' || n, 'module', "
            "'Module ' || n, (SELECT course_key FROM warehouse.dim_course "
            "WHERE course_slug = 'c1'), n FROM generate_series(1, 2) AS n"
        )
    )
    connection.execute(
        text(
            "INSERT INTO warehouse.dim_activity (activity_iri, activity_type, "
            "name, course_key, module_index) VALUES ('urn:a:3', 'module', "
            "'Module 3', (SELECT course_key FROM warehouse.dim_course "
            "WHERE course_slug = 'c2'), 1)"
        )
    )
    connection.execute(
        text(
            """
            INSERT INTO warehouse.fact_activity
                (statement_id, student_key, course_key, activity_key, date_key,
                 verb, occurred_at, ingest_seq)
            SELECT gen_random_uuid(), s.student_key, c.course_key, a.activity_key,
                   20260201, v.verb, TIMESTAMPTZ '2026-02-01 09:00Z', 1
            FROM warehouse.dim_student s
            CROSS JOIN (SELECT course_key FROM warehouse.dim_course
                        WHERE course_slug = 'c1') c
            CROSS JOIN (SELECT activity_key FROM warehouse.dim_activity
                        WHERE activity_iri = 'urn:a:1') a
            CROSS JOIN (VALUES ('experienced'), ('initialized')) AS v(verb)
            """
        )
    )
    connection.execute(
        text(
            """
            INSERT INTO warehouse.fact_assessment
                (statement_id, student_key, course_key, activity_key, date_key,
                 verb, scaled_score, success, completion, attempt_number,
                 occurred_at, ingest_seq)
            SELECT gen_random_uuid(), s.student_key, c.course_key, a.activity_key,
                   20260202, t.verb, t.score, t.verb = 'passed', true,
                   t.attempt, TIMESTAMPTZ '2026-02-02 09:00Z', 1
            FROM warehouse.dim_student s
            CROSS JOIN (SELECT course_key FROM warehouse.dim_course
                        WHERE course_slug = 'c1') c
            CROSS JOIN (SELECT activity_key FROM warehouse.dim_activity
                        WHERE activity_iri = 'urn:a:1') a
            CROSS JOIN (VALUES ('failed', 0.40, 1), ('passed', 0.80, 2))
                       AS t(verb, score, attempt)
            """
        )
    )
    connection.execute(
        text(
            """
            INSERT INTO warehouse.risk_score
                (student_key, window_close, model_version, risk, alerted, drivers)
            SELECT s.student_key, TIMESTAMPTZ '2026-02-23Z', 'test000',
                   r.risk, r.risk > 0.35, '{"additive": false}'::jsonb
            FROM warehouse.dim_student s
            JOIN (VALUES ('s-00001', 0.82), ('s-00002', 0.11), ('s-00003', 0.40))
                 AS r(identifier, risk) ON r.identifier = s.learner_identifier
            """
        )
    )


# --------------------------------------------------------------------------
# The reference queries — a broken one must fail HERE, not as a model failure
# --------------------------------------------------------------------------


@pytest.mark.parametrize("question", [q for q in load() if q.reference_sql])
def test_every_reference_query_returns_a_sane_value(
    connection: Connection, question: GoldenQuestion
) -> None:
    """A wrong reference query would blame the model for our mistake.

    That is the worst outcome this harness can produce, so each query is
    exercised against a cohort built above: it must run, return exactly
    one row and one column, and produce a non-null value of a sensible
    type. Counts must be non-negative; a query that silently returned
    NULL would make every answer look wrong.
    """
    seed_cohort(connection)

    value = reference_value(connection, question.reference_sql)

    assert value is not None, (
        f"{question.id}: the reference query returned NULL against a "
        "populated cohort — either the query is wrong or it filters "
        "everything out, and both would grade every answer as wrong"
    )
    if isinstance(value, int | float):
        assert value >= 0, f"{question.id}: a negative count is not sane"


@pytest.mark.parametrize("question", [q for q in load() if q.reference_sql])
def test_no_reference_value_exceeds_its_population(
    connection: Connection, question: GoldenQuestion
) -> None:
    """The cheapest tell that a query counts the wrong thing.

    A count above the population it counts over cannot be right, and it
    is checkable without a model, without a real provider, and without
    anyone reading the SQL. This exact check would have caught the
    risk_score double count on the day it was written — 76 alerted in a
    cohort of 120 learners — instead of three eval runs later, after it
    had been quoted in two committed reports.

    The oracle in this set has been wrong three times out of twelve. It
    gets a bound for the same reason the model's answers do.
    """
    seed_cohort(connection)
    value = reference_value(connection, question.reference_sql)

    if question.bound == TEXT:
        assert isinstance(value, str), (
            f"{question.id}: bound says text but the reference returned "
            f"{type(value).__name__} — either the bound or the query is wrong"
        )
        return

    numeric = float(value)
    if question.bound == PROBABILITY:
        assert 0.0 <= numeric <= 1.0, (
            f"{question.id}: {numeric} is not a probability. A risk or a "
            "scaled score outside 0-1 means the query is reading a "
            "different column than the question is about"
        )
        return

    population = float(reference_value(connection, BOUNDS[question.bound]))
    assert numeric <= population, (
        f"{question.id}: the reference returned {numeric:g} over a "
        f"population of {population:g} {question.bound}. A count cannot "
        "exceed what it counts — the query is almost certainly counting "
        "rows across a dimension the question does not range over, which "
        "is exactly how alerted-count returned 76 for 120 learners"
    )


def test_every_answerable_question_declares_a_bound() -> None:
    """Omission is how an unchecked oracle gets added next time."""
    with pytest.raises(GoldenSetError, match="bound"):
        parse(
            [
                {
                    "id": "x",
                    "question": "how many?",
                    "disposition": "answered",
                    "reference_sql": "SELECT 1",
                }
            ]
        )


def test_the_risk_score_references_count_learners_not_rows(
    connection: Connection,
) -> None:
    """The specific defect, pinned so it cannot come back.

    risk_score holds one row per learner PER MODEL VERSION (ADR-0007).
    A reference that does not scope to one version counts every learner
    once per scoring run, and the answer looks plausible until someone
    notices it exceeds the cohort.
    """
    seed_cohort(connection)
    # A second scoring run for the same learners and window.
    connection.execute(
        text(
            """
            INSERT INTO warehouse.risk_score
                (student_key, window_close, model_version, risk, alerted,
                 drivers, scored_at)
            SELECT student_key, window_close, 'test001', risk, alerted,
                   drivers, scored_at + interval '1 hour'
            FROM warehouse.risk_score WHERE model_version = 'test000'
            """
        )
    )
    by_id = {question.id: question for question in load()}
    learners = reference_value(connection, BOUNDS["learners"])

    for identifier in ("alerted-count", "risk-above-half"):
        value = reference_value(connection, by_id[identifier].reference_sql)
        assert value <= learners, (
            f"{identifier} returned {value} for {learners} learners after a "
            "second scoring run — the reference is counting rows per model "
            "version rather than learners"
        )
    assert reference_value(connection, by_id["alerted-count"].reference_sql) == 2


def test_the_reference_queries_compute_what_they_claim(
    connection: Connection,
) -> None:
    """Spot-check the arithmetic against a cohort small enough to count.

    Parametrised sanity above proves each query runs and returns
    something; this proves a few of them return the RIGHT something,
    which is the part a type check cannot see.
    """
    seed_cohort(connection)
    by_id = {question.id: question for question in load()}

    def truth(identifier: str):
        return reference_value(connection, by_id[identifier].reference_sql)

    assert truth("learner-count") == 3
    assert truth("course-count") == 2
    assert truth("alerted-count") == 2, "0.82 and 0.40 exceed the 0.35 threshold"
    assert truth("risk-above-half") == 1, "only 0.82 exceeds 0.5"
    assert float(truth("highest-risk")) == pytest.approx(0.82)
    assert truth("retry-count") == 3, "one second attempt per learner"
    assert truth("learners-with-a-failure") == 3
    assert truth("largest-course") == 2, "Course One holds two activities"
    assert truth("active-days") == 1, "all activity rows share one date_key"


# --------------------------------------------------------------------------
# The golden set's own shape
# --------------------------------------------------------------------------


def test_refusals_are_a_substantial_fraction_and_include_hard_ones() -> None:
    """A set of easy refusals measures almost nothing.

    Any system that declines "what is their email address" may still
    confidently average assessment scores into a final grade that does
    not exist. The looks-answerable cases are the ones that separate a
    grounded system from a fluent one, so their presence is pinned.
    """
    questions = load()
    refusals = [q for q in questions if not q.expects_answer]

    assert len(refusals) / len(questions) >= 0.30, (
        "refusals have fallen below a third of the set — either questions "
        "were added without refusal counterparts, or refusals were removed "
        "because they were failing, which is the wrong fix"
    )
    hard = [q for q in refusals if q.kind == "looks-answerable"]
    assert len(hard) >= 3, (
        "too few looks-answerable refusals. Out-of-scope questions are easy "
        "to refuse; the hard case is a column that exists but does not mean "
        "what the question assumes"
    )


def test_an_answerable_question_without_a_reference_is_rejected() -> None:
    """Silently grading on disposition alone is the failure to prevent."""
    with pytest.raises(GoldenSetError, match="reference_sql"):
        parse([{"id": "x", "question": "how many?", "disposition": "answered"}])


def test_a_refusal_must_say_why() -> None:
    with pytest.raises(GoldenSetError, match="why"):
        parse([{"id": "x", "question": "what?", "disposition": "refused"}])


def test_a_refusal_may_not_carry_a_reference_query() -> None:
    with pytest.raises(GoldenSetError, match="must not carry"):
        parse(
            [
                {
                    "id": "x",
                    "question": "what?",
                    "disposition": "refused",
                    "why": "no data",
                    "reference_sql": "SELECT 1",
                }
            ]
        )


def test_duplicate_ids_are_rejected() -> None:
    entry = {
        "id": "x",
        "question": "what?",
        "disposition": "refused",
        "why": "no data",
    }
    with pytest.raises(GoldenSetError, match="duplicate"):
        parse([entry, dict(entry)])


# --------------------------------------------------------------------------
# Grading — the mechanics must DETECT failure, not merely complete
# --------------------------------------------------------------------------


def a_question(**overrides) -> GoldenQuestion:
    fields = {
        "id": "q",
        "question": "How many learners?",
        "disposition": "answered",
        "reference_sql": "SELECT 1",
        "bound": "learners",
    }
    return GoldenQuestion(**{**fields, **overrides})


def an_answer(text_value: str, answered: bool = True):
    from app.llm.qa import Answer

    return Answer(text=text_value, answered=answered, synthetic=True)


def test_a_seeded_wrong_answer_is_caught() -> None:
    """The gate's proof that grading can fail, not only finish.

    The answer here is grounded — it would have cited a returned row —
    and it is wrong, because the query measured something other than
    what was asked. Citations cannot see this; the reference query is
    what does.
    """
    outcome = grade(a_question(), an_answer("There are 91 learners."), truth=3)

    assert outcome.passed is False
    assert any("reference value" in problem for problem in outcome.problems)


def test_answering_a_question_that_must_be_refused_fails() -> None:
    question = a_question(
        disposition="refused", reference_sql=None, why="no attendance data"
    )

    outcome = grade(question, an_answer("Attendance is 41 percent."), truth=None)

    assert outcome.passed is False
    assert any("expected a refusal" in problem for problem in outcome.problems)


def test_refusing_a_question_that_should_be_answered_fails() -> None:
    outcome = grade(a_question(), an_answer("no", answered=False), truth=3)

    assert outcome.passed is False
    assert any("expected an answer" in problem for problem in outcome.problems)


def test_a_correct_answer_passes() -> None:
    outcome = grade(a_question(), an_answer("There are 3 learners."), truth=3)

    assert outcome.passed is True
    assert outcome.problems == ()


def test_a_text_reference_value_is_matched_as_a_substring() -> None:
    assert states_truth("The most common verb is experienced.", "experienced")
    assert not states_truth("The most common verb is initialized.", "experienced")


def test_a_percentage_states_a_proportion() -> None:
    assert states_truth("Risk peaks at 82%.", 0.82)


def test_a_reference_query_returning_many_rows_is_rejected(
    connection: Connection,
) -> None:
    """A wider result would grade every answer wrong, silently."""
    seed_cohort(connection)

    with pytest.raises(ValueError, match="exactly one row"):
        reference_value(
            connection, "SELECT learner_identifier FROM warehouse.dim_student"
        )


# --------------------------------------------------------------------------
# End to end through the stub, and the refusal to write a synthetic report
# --------------------------------------------------------------------------


def stub_for(questions, connection: Connection, answers: dict[str, dict]):
    """Register both calls for each question in a small set."""
    system = load_prompt("grounding")
    responses: dict[str, str] = {}
    for question in questions:
        plan = answers[question.id]["plan"]
        responses.update(
            canned(system, plan_prompt(question.question), json.dumps(plan))
        )
        reply = answers[question.id].get("answer")
        if reply is not None:
            result = run_generated_query(connection, plan["sql"])
            responses.update(
                canned(
                    system,
                    answer_prompt(question.question, result),
                    json.dumps(reply),
                )
            )
    return StubProvider(responses)


def test_the_runner_completes_a_set_and_measures_it(connection: Connection) -> None:
    seed_cohort(connection)
    questions = (
        a_question(
            id="count",
            reference_sql="SELECT count(*) AS n FROM warehouse.dim_student",
        ),
        a_question(
            id="emails",
            question="What are their email addresses?",
            disposition="refused",
            reference_sql=None,
            why="no contact details exist",
        ),
    )
    provider = stub_for(
        questions,
        connection,
        {
            "count": {
                "plan": {
                    "answerable": True,
                    "sql": "SELECT count(*) AS learners FROM warehouse.dim_student",
                    "reason": "",
                },
                "answer": {
                    "claims": [{"text": "There are 3 learners.", "source": "row:0"}]
                },
            },
            "emails": {
                "plan": {
                    "answerable": False,
                    "sql": "",
                    "reason": "No contact details are held.",
                }
            },
        },
    )

    outcomes = run_all(questions, connection, provider)
    metrics = measure(outcomes)

    assert metrics.total == 2
    assert metrics.passed == 2
    assert metrics.refusals_expected == 1 and metrics.refusals_correct == 1
    assert metrics.answers_expected == 1 and metrics.answers_correct == 1
    assert metrics.synthetic is True


def test_a_synthetic_run_refuses_to_write_a_report(tmp_path: Path) -> None:
    """The rule that stops canned text becoming M4's evidence.

    Implemented AND proven: a report in evals/reports/ that had only
    ever seen the stub would satisfy the acceptance criterion while
    measuring nothing, so the refusal is mechanical rather than a note
    in a README.
    """
    outcome = grade(a_question(), an_answer("There are 3 learners."), truth=3)
    metrics = measure((outcome,))
    destination = tmp_path / "m4-llm.md"

    assert metrics.synthetic is True
    with pytest.raises(SyntheticRunRefused, match="stub"):
        write("# report", metrics, path=str(destination))

    assert not destination.exists(), "no file may be produced by a stub run"


def test_a_real_run_writes_the_report(tmp_path: Path) -> None:
    """The other direction, so the refusal is not vacuously always-on."""
    from dataclasses import replace

    from app.llm.qa import Answer

    real = Answer(text="There are 3 learners.", answered=True, synthetic=False)
    outcome = replace(
        grade(a_question(), an_answer("There are 3 learners."), truth=3), answer=real
    )
    metrics = measure((outcome,))
    destination = tmp_path / "m4-llm.md"

    assert metrics.synthetic is False
    written = write("# report", metrics, path=str(destination))

    assert written.exists()


# --------------------------------------------------------------------------
# The report
# --------------------------------------------------------------------------


def test_the_provenance_header_names_the_provider_and_model() -> None:
    """A committed artifact must identify what produced it.

    Without the provider and model on the page, a reader cannot tell a
    real run from a synthetic one that slipped through, and the whole
    point of the criterion is that the distinction is visible.
    """
    header = Provenance(
        generated_at="2026-09-07T00:00:00+00:00",
        commit="abc1234",
        dirty=False,
        provider="anthropic",
        model="claude-opus-5",
        questions=18,
    ).render()

    assert "anthropic" in header
    assert "claude-opus-5" in header
    assert "abc1234" in header
    assert "make evals" in header


def test_the_report_renders_failures_with_the_query_that_ran() -> None:
    """ "What did it run" is the first question about a bad answer."""
    from app.llm.qa import Answer
    from app.llm.query import QueryResult

    sql = "SELECT count(*) AS n FROM warehouse.dim_student"
    answer = Answer(
        text="There are 91 learners.",
        answered=True,
        synthetic=True,
        query=QueryResult(sql=sql, columns=("n",), rows=({"n": 3},), truncated=False),
    )
    outcome = grade(a_question(), answer, truth=3)
    body = render((outcome,), measure((outcome,)), collect_provenance("stub", "s", 1))

    assert "## Failures" in body
    assert sql in body
    assert "reference value" in body


def test_the_report_carries_what_verification_objected_to() -> None:
    """ "Could not be verified" is a rejection nobody can act on.

    The first real-provider run cost seven live API calls to learn why
    eight answers failed, because the artifact recorded only that they
    had. The detail belongs in the report.
    """
    from app.llm.qa import Answer
    from app.llm.query import QueryResult

    answer = Answer(
        text="withheld",
        answered=False,
        refusal_reason="could not be verified",
        synthetic=True,
        query=QueryResult(sql="SELECT 1", columns=(), rows=(), truncated=False),
        problems=("claim 0 contains 192, which is not in the row it cites",),
    )
    outcome = grade(a_question(), answer, truth=3)
    body = render((outcome,), measure((outcome,)), collect_provenance("stub", "s", 1))

    assert "Verification objected to:" in body
    assert "claim 0 contains 192" in body


def test_the_self_authorship_limit_is_in_the_opening_framing() -> None:
    """Beside the score, never in a limitations section at the bottom.

    The questions, the reference queries and the schema description were
    all written by the same author, so the set cannot detect a
    misconception shared across all three. A reader who takes 18/18 away
    must take that with it — the treatment M3's separability warning
    got, and for the same reason: a limitation a reader has to go
    looking for is one they will not find.
    """
    passing = grade(a_question(), an_answer("There are 3 learners."), truth=3)
    body = render((passing,), measure((passing,)), collect_provenance("stub", "s", 1))

    assert "same author" in body
    before_failures = body.split("## Failures")[0]
    assert "same author" in before_failures, (
        "the warning is below the failures section — either it moved, or "
        "the report grew a section between the score and the caveat that "
        "a reader will stop at"
    )
    assert body.index("same author") < body.index("## Every question")


def test_the_report_separates_refusal_and_answer_accuracy() -> None:
    """One combined rate would hide the trade between them."""
    passing = grade(a_question(), an_answer("There are 3 learners."), truth=3)
    body = render((passing,), measure((passing,)), collect_provenance("stub", "s", 1))

    assert "Refusals:" in body
    assert "Answers:" in body
    assert "refuses everything" in body, "the reason must travel with the numbers"


def test_make_evals_refuses_the_stub_before_running(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """The CLI says why, rather than raising on the first question.

    With the stub, every golden question would hit UnregisteredPrompt —
    accurate but opaque, when the real answer is that this target needs
    a real provider. The refusal in `write` stays as the mechanical
    backstop; this one is for the person who typed the command.
    """
    from evals.__main__ import main

    monkeypatch.setenv("LLM_PROVIDER", "stub")

    assert main() == 2
    assert "refusing to run" in capsys.readouterr().err


# --------------------------------------------------------------------------
# Malformed responses — their own outcome, and they must not abort a run
# --------------------------------------------------------------------------


class Malforming:
    """A provider whose structured response will not parse."""

    name = "malforming"

    def complete(self, *, system: str, prompt: str):
        raise AssertionError("not used")

    def complete_json(self, *, system: str, prompt: str, schema: dict):
        from app.llm.client import MalformedResponse

        raise MalformedResponse(
            "not valid JSON",
            raw='{"answerable": true, "sql": "SELECT',
            stop_reason="end_turn",
            prompt=prompt,
        )


def test_a_malformed_response_is_graded_not_raised(connection: Connection) -> None:
    """One bad response costs one question in one run, not the run.

    A variance harness that aborts on the variance it exists to measure
    is the defect, not the transient — this exact fault killed all five
    runs of an earlier attempt at the measurement and produced no data.
    """
    from evals.harness.runner import run_question

    outcome = run_question(a_question(), connection, Malforming())

    assert outcome.malformed is True
    assert outcome.passed is False
    assert outcome.observed == "malformed", (
        "a malformed response is neither a refusal nor a verification "
        "failure, and folding it into either would hide its rate"
    )


def test_a_malformed_response_carries_what_it_was(connection: Connection) -> None:
    """The raw text and stop reason travel with the failure.

    Diagnosing the first occurrence cost 21 API calls to reconstruct
    what a decent error would have said immediately.
    """
    from evals.harness.runner import run_question

    outcome = run_question(a_question(), connection, Malforming())

    joined = " ".join(outcome.problems)
    assert "SELECT" in joined, "the raw text must be recoverable"
    assert "end_turn" in joined, "the stop reason distinguishes truncation"


def test_the_run_continues_past_a_malformed_response(
    connection: Connection,
) -> None:
    """The whole point of grading it rather than raising."""
    from evals.harness.runner import measure, run_all

    questions = (a_question(id="a"), a_question(id="b"))

    outcomes = run_all(questions, connection, Malforming())
    metrics = measure(outcomes)

    assert len(outcomes) == 2, "both questions were asked"
    assert metrics.malformed == 2
    assert metrics.passed == 0


# --------------------------------------------------------------------------
# Variance aggregation — mechanics in the gate, the measurement is `make
# evals-variance` against a real provider
# --------------------------------------------------------------------------


def an_outcome(passed: bool, sql: str | None):
    """One graded result, with the query that produced it."""
    from app.llm.qa import Answer
    from app.llm.query import QueryResult

    query = QueryResult(sql=sql, columns=(), rows=(), truncated=False) if sql else None
    return grade(
        a_question(),
        Answer(
            text="There are 3 learners." if passed else "There are 91 learners.",
            answered=True,
            synthetic=True,
            query=query,
        ),
        truth=3,
    )


def test_variance_reports_each_runs_outcome_not_an_aggregate() -> None:
    """A question passing 3 of 5 must read as 3 of 5.

    The per-run flags are stored rather than derived, so the summary
    cannot drift from the detail table beneath it.
    """
    from evals.harness.variance import collect, summarise

    runs = (
        (an_outcome(True, "A"),),
        (an_outcome(False, "B"),),
        (an_outcome(True, "A"),),
    )
    variances = collect(runs)

    assert variances[0].passed_in == (True, False, True)
    assert variances[0].passes == 2
    assert variances[0].runs == 3
    assert summarise(variances, 3).range_text == "0-1 of 1"


def test_sql_stability_is_reported_even_when_the_outcome_never_varies() -> None:
    """The finding a pass rate would hide entirely.

    A question passing five times from five different queries is passing
    by luck. Without this it appears in the table as an unbroken row of
    ticks, indistinguishable from one whose query is settled.
    """
    from evals.harness.variance import collect

    lucky = collect(
        ((an_outcome(True, "A"),), (an_outcome(True, "B"),), (an_outcome(True, "C"),))
    )[0]
    settled = collect(
        ((an_outcome(True, "A"),), (an_outcome(True, "A"),), (an_outcome(True, "A"),))
    )[0]

    assert lucky.stable_outcome and settled.stable_outcome, "both always pass"
    assert lucky.stable_sql is False, "three distinct queries is not stable"
    assert settled.stable_sql is True
    assert len(lucky.distinct_queries) == 3


def test_the_headline_is_a_range_not_a_point() -> None:
    """A point estimate is what made highest-risk look decided.

    It read as a settled pass or fail in each of four consecutive
    reports while actually flipping every time.
    """
    from evals.harness.variance import collect, summarise

    variances = collect(
        (
            (an_outcome(True, "A"), an_outcome(True, "A")),
            (an_outcome(False, "B"), an_outcome(True, "A")),
        )
    )
    summary = summarise(variances, 2)

    assert summary.worst == 1 and summary.best == 2
    assert summary.range_text == "1-2 of 2"


def test_the_variance_report_states_the_malformed_rate() -> None:
    """Reported even at zero, because the fault is known to exist."""
    from evals.harness.report import collect_provenance
    from evals.harness.variance import collect, summarise
    from evals.harness.variance_report import render as render_variance

    variances = collect(((an_outcome(True, "A"),), (an_outcome(True, "A"),)))
    body = render_variance(
        variances, summarise(variances, 2), collect_provenance("stub", "s", 1)
    )

    assert "Malformed responses:" in body
    assert "failure rate" in body, "it must not read as a retry rate"


def test_the_variance_report_shows_both_queries_not_a_claim_they_differ() -> None:
    """The hypothesis is about query shape, so the SQL is the evidence.

    A failure traced to a column present in one run and absent in
    another has to be checkable on the page.
    """
    from evals.harness.report import collect_provenance
    from evals.harness.variance import collect, summarise
    from evals.harness.variance_report import render as render_variance

    variances = collect(
        (
            (an_outcome(True, "SELECT a FROM t"),),
            (an_outcome(False, "SELECT b FROM t"),),
        )
    )
    body = render_variance(
        variances, summarise(variances, 2), collect_provenance("stub", "s", 1)
    )

    assert "SELECT a FROM t" in body
    assert "SELECT b FROM t" in body
    assert "same author" in body, "the self-authorship limit travels with it"
    assert "four different instruments" in body, "runs 1-4 exclusion must be stated"


def test_the_harness_is_never_in_the_deployment_manifest() -> None:
    """`evals` stays importable from the root and unlisted (ADR-0002)."""
    import tomllib

    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    packages = config["tool"]["setuptools"]["packages"]

    assert not [name for name in packages if name.split(".")[0] == "evals"]
