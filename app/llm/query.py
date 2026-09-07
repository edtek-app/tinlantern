"""Running generated SQL without letting it write.

A language model writes the statement and this module runs it. The
boundary that makes that safe is the **database**, in two independent
layers:

1. **Privileges.** The query runs as ``tinlantern_readonly`` (migration
   ``0006``), which holds SELECT on ``warehouse`` and nothing else — no
   writes, and no access to ``raw`` at all. Reached with ``SET LOCAL
   ROLE`` rather than a login role, so no second credential exists to
   leak.
2. **A read-only transaction.** ``SET LOCAL transaction_read_only`` for
   the duration, so a write is refused even if a grant were ever widened
   by accident.

Either layer alone refuses an INSERT, an UPDATE, and a DROP; the tests
prove each one with the other absent, because two layers that have only
ever been tested together are one layer with extra steps.

**The Python check in this module is not the security boundary.** It is
a fast-fail convenience: it turns "the model wrote something that is not
a query" into a clear rejection instead of a database error, and it
catches the case early enough to retry. It is pattern matching over text,
it is wrong in ways nobody predicts, and no database protection may ever
be removed on the grounds that it exists.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Connection, text

#: The role generated SQL runs as. Created by migration 0006.
READONLY_ROLE = "tinlantern_readonly"

#: Rows fetched from a generated query. A question like "list every
#: event" would otherwise pull the whole fact table into a prompt; the
#: answer layer is told when this bites so it can say the result was
#: truncated rather than answer from a slice as though it were the whole.
MAX_ROWS = 200

#: Openings that are not a query. Fast-fail only — see the module note.
_NOT_A_QUERY = (
    "insert",
    "update",
    "delete",
    "drop",
    "alter",
    "create",
    "truncate",
    "grant",
    "revoke",
    "copy",
    "call",
    "do",
    "set",
    "reset",
    "vacuum",
)


class UnsafeStatement(RuntimeError):
    """The generated text was rejected before it reached the database."""


@dataclass(frozen=True, slots=True)
class QueryResult:
    """What a generated query returned, and what produced it.

    The SQL is retained alongside the rows so a dashboard can show what
    was run beside what was cited, and so an eval artifact records how an
    answer was reached rather than only that it was grounded.

    Attributes:
        sql: The statement that ran, verbatim.
        columns: Column names in order.
        rows: The rows, at most ``MAX_ROWS``.
        truncated: True when more rows existed than were fetched.
    """

    sql: str
    columns: tuple[str, ...]
    rows: tuple[dict, ...]
    truncated: bool

    def labelled(self) -> dict[str, dict]:
        """The rows keyed by the label a claim cites them under.

        Positional (``row:0``, ``row:1``) rather than by primary key:
        most of what an advisor asks is an aggregate, and an aggregate
        has no key to cite. A label that only works for row-level
        queries would push the model toward citing nothing on exactly
        the questions people ask most.
        """
        return {f"row:{index}": row for index, row in enumerate(self.rows)}


def statement_problem(sql: str) -> str | None:
    """Reject obvious non-queries early. **Not the security boundary.**

    This exists so a model that returns prose, a write, or two statements
    gets a clear rejection rather than a database error — the difference
    between a retryable mistake and an opaque failure. The database
    refuses writes regardless of what this function concludes, and that
    is what makes the arrangement safe. See the module docstring.

    Args:
        sql: The generated statement.

    Returns:
        A description of the problem, or None if it looks like a query.
    """
    stripped = sql.strip().rstrip(";").strip()
    if not stripped:
        return "the generated statement is empty"

    lowered = stripped.lower()
    if not (lowered.startswith("select") or lowered.startswith("with")):
        first = lowered.split(None, 1)[0] if lowered.split() else ""
        if first in _NOT_A_QUERY:
            return f"{first.upper()} is not a query; only SELECT is run"
        return "the generated statement does not begin with SELECT or WITH"

    if ";" in stripped:
        return (
            "the generated text contains more than one statement; only a "
            "single query is run"
        )

    return None


def execute_readonly(
    connection: Connection, sql: str, max_rows: int = MAX_ROWS
) -> QueryResult:
    """Run a generated query under both database protections.

    ``SET LOCAL`` for both, so they revert when the surrounding
    transaction ends and cannot leak into the caller's later work.

    Args:
        connection: An open connection inside a transaction.
        sql: The statement to run. Passed through unmodified — this
            function does not rewrite generated SQL, because a rewrite
            would mean the statement shown to a user is not the one that
            ran.
        max_rows: Fetch limit.

    Returns:
        The rows, the columns, and the SQL that produced them.

    Raises:
        Any database error, unchanged. A permission error or a read-only
        transaction error is the boundary doing its job, and flattening
        it into a local exception type would hide which layer refused.
    """
    connection.execute(text(f"SET LOCAL ROLE {READONLY_ROLE}"))
    connection.execute(text("SET LOCAL transaction_read_only = on"))

    cursor = connection.execute(text(sql))
    columns = tuple(cursor.keys())
    fetched = cursor.fetchmany(max_rows + 1)
    truncated = len(fetched) > max_rows

    return QueryResult(
        sql=sql,
        columns=columns,
        rows=tuple(dict(zip(columns, row, strict=True)) for row in fetched[:max_rows]),
        truncated=truncated,
    )


def run_generated_query(
    connection: Connection, sql: str, max_rows: int = MAX_ROWS
) -> QueryResult:
    """Check, then run. The check is a convenience; the run is protected.

    Raises:
        UnsafeStatement: If the fast check rejects it. The database would
            also have refused — this only arrives sooner and says why.
    """
    problem = statement_problem(sql)
    if problem is not None:
        raise UnsafeStatement(problem)
    return execute_readonly(connection, sql, max_rows=max_rows)
