"""Q&A: the database boundary, the schema description, and citations.

Tests that touch the database use the `connection` fixture and insert
the rows they assert on, so none assumes state it did not create. The
warehouse tables start empty in the test database, so a count assertion
here counts only what this file inserted.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import Connection, text

from app.llm.client import Completion, ModelRefused
from app.llm.prompt_library import load_prompt
from app.llm.providers.stub import StubProvider, canned
from app.llm.qa import (
    ANSWER_SCHEMA,
    PLAN_SCHEMA,
    Answer,
    answer_prompt,
    ask,
    plan_prompt,
    verify_claims,
)
from app.llm.query import (
    READONLY_ROLE,
    QueryResult,
    UnsafeStatement,
    execute_readonly,
    run_generated_query,
    statement_problem,
)
from app.llm.schema_context import TABLES, describe, drift

pytestmark = pytest.mark.m4

QUESTION = "How many learners are alerted?"
SQL = "SELECT count(*) AS alerted_learners FROM warehouse.risk_score"


def seed_students(connection: Connection, count: int) -> None:
    """Insert learners this test can then count."""
    for index in range(count):
        connection.execute(
            text(
                "INSERT INTO warehouse.dim_student (learner_identifier, "
                "account_home_page) VALUES (:id, 'https://example.invalid')"
            ),
            {"id": f"s-{index:05d}"},
        )


# --------------------------------------------------------------------------
# The database boundary — two layers, each proved with the other absent
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO warehouse.dim_course (course_iri, title) VALUES ('a', 'b')",
        "UPDATE warehouse.dim_course SET title = 'x'",
        "DROP TABLE warehouse.dim_course",
    ],
)
def test_the_readonly_role_alone_refuses_writes(
    connection: Connection, statement: str
) -> None:
    """Layer 1 with layer 3 absent: privileges, in a writable transaction.

    The transaction is deliberately NOT read-only here. Two layers that
    have only ever been tested together are one layer with extra steps,
    so each is exercised with the other switched off.
    """
    connection.execute(text(f"SET LOCAL ROLE {READONLY_ROLE}"))

    with pytest.raises(Exception) as raised:
        connection.execute(text(statement))

    assert "permission denied" in str(raised.value) or "must be owner" in str(
        raised.value
    )


def test_the_readonly_transaction_alone_refuses_writes(
    connection: Connection,
) -> None:
    """Layer 3 with layer 1 absent: still the owning role, read-only."""
    connection.execute(text("SET LOCAL transaction_read_only = on"))

    with pytest.raises(Exception, match="read-only transaction"):
        connection.execute(
            text(
                "INSERT INTO warehouse.dim_course (course_iri, title) VALUES ('a','b')"
            )
        )


def test_the_role_cannot_read_raw_statements(connection: Connection) -> None:
    """Generated SQL has no business reading verbatim xAPI payloads.

    `raw` holds the statements as received; the question layer is
    described against the star schema and nothing else.
    """
    connection.execute(text(f"SET LOCAL ROLE {READONLY_ROLE}"))

    with pytest.raises(Exception, match="permission denied for schema raw"):
        connection.execute(text("SELECT count(*) FROM raw.statements"))


def test_the_boundary_holds_with_the_python_guard_bypassed(
    connection: Connection,
) -> None:
    """The point of the arrangement, asserted directly.

    `execute_readonly` is called with a write statement — the fast-fail
    check in `run_generated_query` is not in the way. If the guard were
    ever the only thing standing between a model and the data, this is
    the test that would fail.
    """
    with pytest.raises(Exception) as raised:
        execute_readonly(
            connection,
            "INSERT INTO warehouse.dim_course (course_iri, title) VALUES ('a','b')",
        )

    assert "read-only transaction" in str(raised.value) or "permission denied" in str(
        raised.value
    )


def test_a_query_still_runs_under_both_protections(connection: Connection) -> None:
    """The boundary must not be so tight that nothing works."""
    seed_students(connection, 3)

    result = execute_readonly(
        connection, "SELECT count(*) AS learners FROM warehouse.dim_student"
    )

    assert result.rows == ({"learners": 3},)
    assert result.sql.startswith("SELECT"), "the executed SQL is retained"


def test_the_protections_end_with_the_transaction(engine) -> None:
    """`SET LOCAL` reverts on commit, so a later write is unaffected.

    This is the property that makes it safe to apply the boundary
    per-query rather than per-connection: if either setting survived the
    transaction, a pooled connection that once answered a question would
    silently refuse writes for whatever used it next.

    It needs two real transactions, so it takes its own connection from
    the engine instead of the `connection` fixture — that fixture gives
    each test exactly one transaction and rolls it back, which cannot
    express "and then the next one".
    """
    with engine.connect() as own:
        first = own.begin()
        execute_readonly(own, "SELECT 1 AS one")
        assert own.execute(text("SHOW transaction_read_only")).scalar() == "on"
        # COMMIT, not rollback. A rollback would revert a session-level
        # SET as well, so it cannot tell SET LOCAL from SET — the first
        # version of this test rolled back and passed with the boundary
        # deliberately broken.
        first.commit()

        second = own.begin()
        try:
            assert own.execute(text("SHOW transaction_read_only")).scalar() == "off"
            assert own.execute(text("SELECT current_user")).scalar() != READONLY_ROLE

            # The real proof: a write the previous transaction would have
            # refused now succeeds.
            seed_students(own, 1)
            assert (
                own.execute(text("SELECT count(*) FROM warehouse.dim_student")).scalar()
                == 1
            )
        finally:
            second.rollback()


# --------------------------------------------------------------------------
# The fast-fail check — a convenience, explicitly not the boundary
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "statement",
    [
        "DELETE FROM warehouse.dim_course",
        "UPDATE warehouse.risk_score SET risk = 0",
        "DROP TABLE warehouse.dim_student",
        "SELECT 1; DROP TABLE warehouse.dim_student",
        "I cannot write that query.",
        "",
    ],
)
def test_the_fast_check_rejects_non_queries(statement: str) -> None:
    assert statement_problem(statement) is not None


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT count(*) FROM warehouse.dim_student",
        "  select 1 as one;  ",
        "WITH recent AS (SELECT 1 AS n) SELECT n FROM recent",
    ],
)
def test_the_fast_check_passes_queries(statement: str) -> None:
    assert statement_problem(statement) is None


def test_run_generated_query_rejects_before_touching_the_database(
    connection: Connection,
) -> None:
    with pytest.raises(UnsafeStatement, match="DELETE"):
        run_generated_query(connection, "DELETE FROM warehouse.dim_course")


# --------------------------------------------------------------------------
# The schema description
# --------------------------------------------------------------------------


def test_the_description_matches_the_live_schema(connection: Connection) -> None:
    """Drift in either direction, but undescribed is the one that bites.

    A column the database has and the description omits is what turns
    into silently wrong SQL — the model cannot use what it was not told
    about, and a model that guesses writes plausible nonsense. A
    described column that is gone fails loudly the first time it is
    used, which is the safer failure.
    """
    assert drift(connection) == ()


def test_the_description_carries_the_fact_overlap(connection: Connection) -> None:
    """ADR-0006 named this exact case as a revisit trigger.

    It anticipated "M4's generated SQL is observed summing across both
    facts" and said that would mean the table comments were not doing
    their job. Introspection cannot convey the overlap; a curated
    description read before the SQL is written is the earlier place to
    say it, which is why the description is curated at all.
    """
    text_form = describe()

    assert "OVERLAP BY DESIGN" in text_form
    assert "double counts" in text_form
    assert "fact_activity" in text_form and "fact_assessment" in text_form


def test_bookkeeping_tables_are_excluded_not_omitted() -> None:
    """Named as off-limits, so a model does not discover them.

    Silently leaving `etl_rejections` out of a column list is not the
    same as telling the model not to query it: the first invites a
    guess, the second forecloses it.
    """
    excluded = {table.name for table in TABLES if not table.queryable}
    assert excluded == {"etl_state", "etl_rejections"}

    text_form = describe()
    assert "Do not query these" in text_form
    for name in excluded:
        assert name in text_form
        assert f"### warehouse.{name}" not in text_form


# --------------------------------------------------------------------------
# Citations
# --------------------------------------------------------------------------


def a_result(rows: tuple[dict, ...], truncated: bool = False) -> QueryResult:
    columns = tuple(rows[0]) if rows else ()
    return QueryResult(sql=SQL, columns=columns, rows=rows, truncated=truncated)


def test_a_claim_citing_a_row_that_was_not_returned_is_rejected() -> None:
    result = a_result(({"alerted_learners": 38},))
    payload = {"claims": [{"text": "38 learners alerted.", "source": "row:7"}]}

    problems = verify_claims(payload, result)

    assert any("row:7" in problem for problem in problems)
    assert any("not a row this query returned" in problem for problem in problems)


def test_a_figure_absent_from_the_cited_row_is_rejected() -> None:
    result = a_result(({"alerted_learners": 38},))
    payload = {"claims": [{"text": "38 of 400 learners alerted.", "source": "row:0"}]}

    problems = verify_claims(payload, result)

    assert any("400" in problem for problem in problems)


def test_a_claim_must_ground_in_the_row_it_names_not_any_row() -> None:
    """Grounding against the whole result set would miss the real error.

    A claim citing one learner while quoting another's score reads
    perfectly and is wrong. Checking against the cited row specifically
    is what makes the citation mean something.
    """
    result = a_result(
        ({"learner": "s-00001", "risk": 0.82}, {"learner": "s-00002", "risk": 0.11})
    )
    payload = {"claims": [{"text": "Learner s-00001 scored 0.11.", "source": "row:0"}]}

    problems = verify_claims(payload, result)

    assert any("0.11" in problem for problem in problems), (
        "0.11 is in the result set but not in the cited row — either the "
        "check is grounding against all rows, or the citation is decorative"
    )


def test_a_number_the_question_supplied_is_admitted() -> None:
    """Asked about "above 0.5", an answer may repeat 0.5.

    That figure came from the person asking, not from a row. Rejecting
    it treated the user's own words as a fabrication and refused a
    correct answer in the first real-provider run.
    """
    result = a_result(({"learners_above": 74},))
    payload = {"claims": [{"text": "74 learners score above 0.5.", "source": "row:0"}]}

    assert verify_claims(payload, result, "How many score above 0.5?") == ()
    assert verify_claims(payload, result, "How many are at risk?") != ()


def test_digits_in_a_column_name_are_not_read_as_figures() -> None:
    """A model citing `learners_above_0_5` is naming its column.

    Row VALUES were scrubbed and row KEYS were not, so a column name the
    query itself produced was read as an assertion of 5 and 0.
    """
    result = a_result(({"learners_above_0_5": 74},))
    payload = {"claims": [{"text": "learners_above_0_5 is 74.", "source": "row:0"}]}

    assert verify_claims(payload, result) == ()


def timestamp_row() -> QueryResult:
    """A row whose timestamps are the ones the second eval run tripped on."""
    from datetime import UTC, datetime

    return a_result(
        (
            {
                "window_close": datetime(2026, 2, 9, 5, 0, tzinfo=UTC),
                "scored_at": datetime(2026, 9, 6, 16, 36, 5, 923395, tzinfo=UTC),
                "learners": 74,
            },
        )
    )


def cite(text_value: str) -> dict:
    return {"claims": [{"text": text_value, "source": "row:0"}]}


def test_a_time_of_day_the_scrub_does_not_recognise_is_still_admitted() -> None:
    """Isolates the QUANTITIES half of the timestamp fix.

    "5:00" has no leading zero, so it matches none of the rendered forms
    the scrub removes. Only admitting the hour and minute as quantities
    lets it through — revert that and this fails while the scrub-based
    test still passes.
    """
    payload = cite("74 learners; the window closed at 5:00 UTC.")

    assert verify_claims(payload, timestamp_row()) == ()


def test_a_rendering_finer_than_its_components_is_still_scrubbed() -> None:
    """Isolates the SCRUB half.

    "16:36:05.923395+00:00" carries a microsecond, which is not among the
    components admitted as quantities — 5.923395 leaks as a fabrication
    unless the rendered form is removed first. Revert the scrub to
    isoformat() alone and this fails while the "5:00" test still passes.

    The two halves look redundant on the common case and are not: each
    covers a rendering the other misses.
    """
    payload = cite("74 learners, scored at 2026-09-06 16:36:05.923395+00:00.")

    assert verify_claims(payload, timestamp_row()) == ()


def test_a_timestamp_a_row_did_not_contain_is_still_rejected() -> None:
    """The other direction, so neither half is a blanket amnesty.

    Admitting the hour and minute must not admit ANY two small numbers,
    and the scrub must not strip anything merely time-shaped: a time the
    row does not hold stays a fabrication.
    """
    payload = cite("74 learners; the window closed at 19:45 UTC.")

    problems = verify_claims(payload, timestamp_row())

    assert any("19" in problem for problem in problems), (
        "19:45 is not this row's timestamp — either the scrub is removing "
        "any time-shaped text, or the hour is being admitted unconditionally"
    )


def test_a_thousands_separator_survives_the_row_check() -> None:
    result = a_result(({"events": 192431},))
    payload = {"claims": [{"text": "There are 192,431 events.", "source": "row:0"}]}

    assert verify_claims(payload, result) == ()


def test_identifiers_in_a_row_are_not_read_as_figures() -> None:
    result = a_result(({"learner": "s-00417", "risk": 0.82},))
    payload = {"claims": [{"text": "Learner s-00417 scored 0.82.", "source": "row:0"}]}

    assert verify_claims(payload, result) == ()


# --------------------------------------------------------------------------
# End to end, through the stub
# --------------------------------------------------------------------------


def stub_for(question: str, connection: Connection, plan: dict, answer: dict | None):
    """A stub registered for both calls of one question."""
    system = load_prompt("grounding")
    responses = dict(canned(system, plan_prompt(question), json.dumps(plan)))
    if answer is not None:
        result = run_generated_query(connection, plan["sql"])
        responses.update(
            canned(system, answer_prompt(question, result), json.dumps(answer))
        )
    return StubProvider(responses)


def test_a_grounded_question_is_answered_with_its_citations(
    connection: Connection,
) -> None:
    seed_students(connection, 4)
    question = "How many learners are there?"
    sql = "SELECT count(*) AS learners FROM warehouse.dim_student"
    plan = {"answerable": True, "sql": sql, "reason": ""}
    answer = {"claims": [{"text": "There are 4 learners.", "source": "row:0"}]}

    result = ask(question, connection, stub_for(question, connection, plan, answer))

    assert result.answered is True
    assert "4 learners" in result.text
    assert result.citations == {"row:0": {"learners": 4}}
    assert result.query is not None
    assert result.query.sql == sql, "the executed SQL travels with the answer"


def test_an_unanswerable_question_is_refused_with_what_is_missing(
    connection: Connection,
) -> None:
    """Refusal is the required outcome, not a low-confidence answer.

    The warehouse holds no attendance, so a query returning *something*
    would produce real numbers answering a different question — which an
    advisor cannot detect.
    """
    question = "What is each learner's attendance rate?"
    plan = {
        "answerable": False,
        "sql": "",
        "reason": "The warehouse holds no attendance data.",
    }

    result = ask(question, connection, stub_for(question, connection, plan, None))

    assert result.answered is False
    assert "attendance" in result.refusal_reason
    assert result.query is None


def test_an_unverifiable_answer_is_withheld_rather_than_caveated(
    connection: Connection,
) -> None:
    """No fallback here, unlike the summariser, and deliberately so.

    A summary has a curated fact set code can always render; an
    arbitrary question does not. Showing an unverified answer with a
    caveat is the failure this milestone exists to prevent, wearing a
    disclaimer.
    """
    seed_students(connection, 4)
    question = "How many learners are there?"
    sql = "SELECT count(*) AS learners FROM warehouse.dim_student"
    plan = {"answerable": True, "sql": sql, "reason": ""}
    answer = {"claims": [{"text": "There are 91 learners.", "source": "row:0"}]}

    result = ask(question, connection, stub_for(question, connection, plan, answer))

    assert result.answered is False
    assert "withheld" in result.text
    assert any("91" in problem for problem in result.problems)
    assert result.query is not None, "the query is kept so the failure is auditable"


def test_a_refusing_model_produces_a_refusal_not_an_exception(
    connection: Connection,
) -> None:
    class Refusing:
        name = "refusing"

        def complete(self, *, system: str, prompt: str) -> Completion:
            raise ModelRefused("declined")

        def complete_json(
            self, *, system: str, prompt: str, schema: dict
        ) -> tuple[dict, Completion]:
            raise ModelRefused("declined")

    result = ask(QUESTION, connection, Refusing())

    assert isinstance(result, Answer)
    assert result.answered is False
    assert "declined" in result.refusal_reason


def test_the_answer_prompt_marks_a_truncated_result() -> None:
    """A truncated slice must not be described as the whole."""
    result = a_result(({"learner": "s-00001"},), truncated=True)

    assert "TRUNCATED" in answer_prompt("who?", result)


def test_both_schemas_forbid_unlisted_fields() -> None:
    assert PLAN_SCHEMA["additionalProperties"] is False
    assert ANSWER_SCHEMA["additionalProperties"] is False
    assert ANSWER_SCHEMA["properties"]["claims"]["items"]["required"] == [
        "text",
        "source",
    ]
