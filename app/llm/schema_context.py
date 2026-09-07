"""The warehouse, described for a model that has to write SQL against it.

**Curated, not introspected.** `information_schema` would give the model
every column name for free and would keep itself current, but it cannot
convey the one thing this schema most needs said: `fact_activity` and
`fact_assessment` overlap on purpose, an assessment statement produces a
row in both, and summing across them double counts. ADR-0006 anticipated
this exact case — it names "M4's generated SQL is observed summing across
both facts" as a revisit trigger, on the grounds that the table comments
might not be doing their job. A description the model reads before it
writes is the earlier place to say it.

The cost of curation is drift, so drift is what the test checks: every
table and every column in `warehouse` must appear here. A column added at
M5 fails the suite until it is described, because the failure mode of a
stale schema description is silently wrong SQL, which reads exactly like
correct SQL.

`etl_state` and `etl_rejections` are described as **excluded**, not
omitted: they are pipeline bookkeeping, and a model that sees a table
called `etl_rejections` in a bare column list will eventually answer a
question about data quality from it.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Connection, text

SCHEMA = "warehouse"


@dataclass(frozen=True, slots=True)
class Table:
    """One table, as the model is told about it."""

    name: str
    grain: str
    columns: tuple[str, ...]
    guidance: str = ""
    queryable: bool = True


#: The overlap, stated where the model reads it rather than only in a
#: COMMENT ON TABLE that generated SQL never sees.
OVERLAP_WARNING = """\
`fact_activity` and `fact_assessment` OVERLAP BY DESIGN. A graded
statement (passed, failed, submitted) produces one row in each. This is
deliberate: `fact_activity` is the atomic record of everything a learner
did, and `fact_assessment` is the graded subset with scores attached.

- "how often / how recently / what gaps" -> `fact_activity`
- "what was scored / passed / attempted" -> `fact_assessment`
- NEVER union or sum across both. It double counts every graded event.

If a question needs both, query them separately and report two figures."""

TABLES: tuple[Table, ...] = (
    Table(
        name="dim_student",
        grain="one row per learner",
        columns=("student_key", "learner_identifier", "account_home_page"),
        guidance=(
            "`learner_identifier` is an opaque account id such as "
            "`s-00417`. There are no names or email addresses anywhere in "
            "this warehouse; a question asking for one cannot be answered."
        ),
    ),
    Table(
        name="dim_course",
        grain="one row per course",
        columns=("course_key", "course_iri", "course_slug", "title"),
    ),
    Table(
        name="dim_activity",
        grain="one row per activity (a module, a quiz, a page)",
        columns=(
            "activity_key",
            "activity_iri",
            "activity_type",
            "name",
            "course_key",
            "module_index",
        ),
    ),
    Table(
        name="dim_date",
        grain="one row per calendar day",
        columns=(
            "date_key",
            "full_date",
            "year",
            "quarter",
            "month",
            "day",
            "iso_week",
            "day_of_week",
            "day_name",
            "is_weekend",
        ),
        guidance=(
            "`date_key` is YYYYMMDD as an integer, not an opaque key, so a "
            "result is readable without joining this table back in."
        ),
    ),
    Table(
        name="fact_activity",
        grain="one row per xAPI statement, every verb",
        columns=(
            "activity_event_key",
            "statement_id",
            "student_key",
            "course_key",
            "activity_key",
            "date_key",
            "verb",
            "occurred_at",
            "registration",
            "ingest_seq",
        ),
        guidance=(
            "Engagement questions live here: counts, recency, gaps, active "
            "days. `occurred_at` is when the event happened. `ingest_seq` "
            "is when we received it and is NOT event time — never order or "
            "filter by it to answer a question about when something "
            "occurred."
        ),
    ),
    Table(
        name="fact_assessment",
        grain="one row per graded statement (passed, failed, submitted)",
        columns=(
            "assessment_result_key",
            "statement_id",
            "student_key",
            "course_key",
            "activity_key",
            "date_key",
            "verb",
            "scaled_score",
            "raw_score",
            "success",
            "completion",
            "attempt_number",
            "occurred_at",
            "registration",
            "ingest_seq",
        ),
        guidance=(
            "`scaled_score` is 0-1. `attempt_number` counts attempts at the "
            "same activity in event-time order, so `attempt_number > 1` "
            "means a retry."
        ),
    ),
    Table(
        name="risk_score",
        grain="one row per learner, per feature window, per model version",
        columns=(
            "risk_score_key",
            "student_key",
            "window_close",
            "model_version",
            "risk",
            "alerted",
            "drivers",
            "scored_at",
        ),
        guidance=(
            "`risk` is a calibrated probability 0-1; `alerted` is whether it "
            "crosses the alert threshold. `drivers` is JSONB and its "
            "contributions do NOT sum to `risk` — they are counterfactual "
            "and they interact. More than one `model_version` may be "
            "present, so a question about current risk must pick one rather "
            "than averaging across them."
        ),
    ),
    Table(
        name="etl_state",
        grain="pipeline bookkeeping",
        columns=("job_name", "last_ingest_seq", "last_run_at", "statements_seen"),
        queryable=False,
    ),
    Table(
        name="etl_rejections",
        grain="pipeline bookkeeping",
        columns=(
            "etl_rejection_id",
            "statement_id",
            "ingest_seq",
            "reason",
            "detail",
            "payload",
            "rejected_at",
        ),
        queryable=False,
    ),
)


def describe() -> str:
    """The schema as the model sees it, queryable tables first."""
    lines = [f"Schema `{SCHEMA}`. Every table below is `{SCHEMA}.<name>`.", ""]

    for table in TABLES:
        if not table.queryable:
            continue
        lines.append(f"### {SCHEMA}.{table.name}")
        lines.append(f"Grain: {table.grain}.")
        lines.append(f"Columns: {', '.join(table.columns)}")
        if table.guidance:
            lines.append(table.guidance)
        lines.append("")

    excluded = [table.name for table in TABLES if not table.queryable]
    lines.append(
        "Do not query these — they are pipeline bookkeeping, not analytics: "
        + ", ".join(f"`{name}`" for name in excluded)
        + "."
    )
    lines.append("")
    lines.append(OVERLAP_WARNING)
    return "\n".join(lines)


def live_columns(connection: Connection) -> dict[str, set[str]]:
    """What the database actually has, for the drift check."""
    rows = connection.execute(
        text(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = :schema"
        ),
        {"schema": SCHEMA},
    )
    live: dict[str, set[str]] = {}
    for table_name, column_name in rows:
        live.setdefault(table_name, set()).add(column_name)
    return live


def drift(connection: Connection) -> tuple[str, ...]:
    """Differences between this description and the live schema.

    Both directions, but the one that matters is **undescribed**: a table
    or column the database has and this file does not mention is what
    turns into silently wrong SQL. A described column that no longer
    exists fails loudly the first time it is used, which is the safer
    failure.
    """
    live = live_columns(connection)
    described = {table.name: set(table.columns) for table in TABLES}

    problems: list[str] = []
    for name, columns in sorted(live.items()):
        if name not in described:
            problems.append(
                f"table {SCHEMA}.{name} exists but is not described. Generated "
                "SQL cannot use what it is not told about, and a model that "
                "guesses at an undescribed table writes plausible wrong SQL."
            )
            continue
        for column in sorted(columns - described[name]):
            problems.append(
                f"column {SCHEMA}.{name}.{column} exists but is not described"
            )

    for name, columns in sorted(described.items()):
        if name not in live:
            problems.append(f"table {SCHEMA}.{name} is described but does not exist")
            continue
        for column in sorted(columns - live[name]):
            problems.append(
                f"column {SCHEMA}.{name}.{column} is described but does not exist"
            )

    return tuple(problems)
