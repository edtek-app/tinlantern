"""Incremental load: `raw.statements` → the warehouse star schema.

Pages `raw` by `ingest_seq` above a stored high-water mark (ADR-0005), so
a run picks up exactly what arrived since the last one. Re-running over
the same statements stores nothing: `statement_id` is UNIQUE on both
facts, so idempotency is enforced by the schema rather than trusted to
the bookkeeping here.

**The course comes from `context.contextActivities.parent`, never from
parsing the activity IRI.** Parsing would hard-code our own generator's
URL convention into the ETL, and the premise of this platform is
consuming *any* xAPI emitter — whose IRIs will look nothing like ours.

**Insert, then correct.** `attempt_number` is recomputed after each batch
for the learner-activity pairs it touched, ordered by `occurred_at`.
Deriving it at insert time would assume arrival order matches event
order; ADR-0005 is explicit that `ingest_seq` and `occurred_at` are
unrelated, so a late-arriving earlier attempt would silently mis-number
everything after it. The warehouse is derived and correctable precisely
so this is available.

A statement that cannot be modelled — no course context, no resolvable
dimension — is recorded in `warehouse.etl_rejections` and skipped, rather
than halting a 192k load over one row. A data-quality check fails the run
if that table is not empty.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Connection, text

from pipeline.warehouse.schema import GRADED_VERBS

#: Identifies this job's watermark row in warehouse.etl_state.
JOB_NAME = "raw_to_warehouse"

#: Statements read from raw per page. Bounded so a large backlog does not
#: materialise in memory at once.
DEFAULT_PAGE_SIZE = 5_000


class UnmodellableStatement(Exception):
    """A statement the warehouse cannot place. Recorded, not fatal."""


@dataclass(frozen=True, slots=True)
class LoadReport:
    """What one ETL run did."""

    read: int = 0
    activity_rows: int = 0
    assessment_rows: int = 0
    rejected: int = 0
    watermark: int = 0

    def summary(self) -> str:
        return (
            f"read        {self.read:,} statements\n"
            f"activity    {self.activity_rows:,} rows\n"
            f"assessment  {self.assessment_rows:,} rows\n"
            f"rejected    {self.rejected:,}\n"
            f"watermark   {self.watermark:,}"
        )


# --------------------------------------------------------------------- read


def watermark(connection: Connection) -> int:
    """Return this job's high-water mark, or 0 if it has never run."""
    return (
        connection.execute(
            text("SELECT last_ingest_seq FROM warehouse.etl_state WHERE job_name = :j"),
            {"j": JOB_NAME},
        ).scalar_one_or_none()
        or 0
    )


def _set_watermark(connection: Connection, value: int, seen: int) -> None:
    connection.execute(
        text(
            """
            INSERT INTO warehouse.etl_state
                (job_name, last_ingest_seq, last_run_at, statements_seen)
            VALUES (:j, :v, now(), :seen)
            ON CONFLICT (job_name) DO UPDATE SET
                last_ingest_seq = EXCLUDED.last_ingest_seq,
                last_run_at     = EXCLUDED.last_run_at,
                statements_seen =
                    warehouse.etl_state.statements_seen + EXCLUDED.statements_seen
            """
        ),
        {"j": JOB_NAME, "v": value, "seen": seen},
    )


# ---------------------------------------------------------------- dimensions


#: Surrogate keys resolved so far this run, keyed by (table, natural key).
#: A cohort has a few hundred distinct dimension values against hundreds of
#: thousands of statements, so without this the ETL issues millions of
#: round trips to answer the same few questions. Scoped to one run: a fresh
#: cache each time means a dimension changed between runs is re-read.
DimensionCache = dict[tuple[str, str], int]


def _dimension_key(
    connection: Connection,
    table: str,
    key_column: str,
    natural_column: str,
    natural_value: str,
    extra: dict[str, Any] | None = None,
    cache: DimensionCache | None = None,
) -> int:
    """Get-or-create a dimension row, returning its surrogate key.

    Conformed: the same learner seen in two courses resolves to one row.
    """
    if cache is not None and (table, natural_value) in cache:
        return cache[(table, natural_value)]
    extra = extra or {}
    columns = [natural_column, *extra]
    placeholders = ", ".join(f":{c}" for c in columns)
    connection.execute(
        text(
            f"INSERT INTO warehouse.{table} ({', '.join(columns)}) "
            f"VALUES ({placeholders}) "
            f"ON CONFLICT ({natural_column}) DO NOTHING"
        ),
        {natural_column: natural_value, **extra},
    )
    key = connection.execute(
        text(f"SELECT {key_column} FROM warehouse.{table} WHERE {natural_column} = :v"),
        {"v": natural_value},
    ).scalar_one()
    if cache is not None:
        cache[(table, natural_value)] = key
    return key


def _course_activity(payload: dict[str, Any]) -> dict[str, Any]:
    """Return the parent course activity from a statement's context.

    The xAPI-native location. Parsing the object IRI would bake our own
    generator's URL shape into the ETL, and any other emitter's IRIs will
    differ.
    """
    parents = (
        payload.get("context", {}).get("contextActivities", {}).get("parent") or []
    )
    if parents:
        return parents[0]
    # A statement *about* the course carries no parent; it is its own course.
    target = payload.get("object", {})
    definition = target.get("definition") or {}
    if (definition.get("type") or "").endswith("/course"):
        return target
    raise UnmodellableStatement(
        "no course context: statement has no contextActivities.parent and "
        "its object is not a course"
    )


def _name_of(activity: dict[str, Any]) -> str | None:
    names = (activity.get("definition") or {}).get("name") or {}
    return next(iter(names.values()), None) if names else None


def _slug_of(course: dict[str, Any]) -> str:
    """A short human label for the course dimension.

    Prefers the last IRI segment, which is conventional and harmless as a
    *label*; unlike course resolution, nothing depends on its structure.
    """
    return course["id"].rstrip("/").rsplit("/", 1)[-1]


def _resolve_keys(
    connection: Connection,
    payload: dict[str, Any],
    cache: DimensionCache | None = None,
) -> dict[str, int]:
    """Conform every dimension this statement needs, returning their keys."""
    actor = payload.get("actor") or {}
    account = actor.get("account") or {}
    if not account.get("name"):
        raise UnmodellableStatement(
            "actor has no account identifier; TinLantern models learners by "
            "opaque account only (ADR-0002)"
        )

    course = _course_activity(payload)
    course_key = _dimension_key(
        connection,
        "dim_course",
        "course_key",
        "course_iri",
        course["id"],
        {"course_slug": _slug_of(course), "title": _name_of(course)},
        cache=cache,
    )

    target = payload.get("object") or {}
    definition = target.get("definition") or {}
    activity_key = _dimension_key(
        connection,
        "dim_activity",
        "activity_key",
        "activity_iri",
        target["id"],
        {
            "activity_type": definition.get("type"),
            "name": _name_of(target),
            "course_key": course_key,
        },
        cache=cache,
    )

    student_key = _dimension_key(
        connection,
        "dim_student",
        "student_key",
        "learner_identifier",
        account["name"],
        {"account_home_page": account.get("homePage", "")},
        cache=cache,
    )

    timestamp = payload.get("timestamp")
    if not timestamp:
        raise UnmodellableStatement("statement has no timestamp")
    day = str(timestamp)[:10]
    if cache is not None and ("dim_date", day) in cache:
        date_key = cache[("dim_date", day)]
    else:
        date_key = connection.execute(
            text(
                "SELECT date_key FROM warehouse.dim_date "
                "WHERE full_date = CAST(:t AS timestamptz)::date"
            ),
            {"t": timestamp},
        ).scalar_one_or_none()
        if cache is not None and date_key is not None:
            cache[("dim_date", day)] = date_key
    if date_key is None:
        raise UnmodellableStatement(f"no dim_date row for {timestamp}")

    return {
        "student_key": student_key,
        "course_key": course_key,
        "activity_key": activity_key,
        "date_key": date_key,
    }


# ------------------------------------------------------------------- write


_INSERT_ACTIVITY = text(
    """
    INSERT INTO warehouse.fact_activity
        (statement_id, student_key, course_key, activity_key, date_key,
         verb, occurred_at, registration, ingest_seq)
    VALUES (:statement_id, :student_key, :course_key, :activity_key,
            :date_key, :verb, CAST(:occurred_at AS timestamptz),
            CAST(:registration AS uuid), :ingest_seq)
    ON CONFLICT (statement_id) DO NOTHING
    """
)

_INSERT_ASSESSMENT = text(
    """
    INSERT INTO warehouse.fact_assessment
        (statement_id, student_key, course_key, activity_key, date_key,
         verb, scaled_score, raw_score, success, completion,
         occurred_at, registration, ingest_seq)
    VALUES (:statement_id, :student_key, :course_key, :activity_key,
            :date_key, :verb, :scaled_score, :raw_score, :success,
            :completion, CAST(:occurred_at AS timestamptz),
            CAST(:registration AS uuid), :ingest_seq)
    ON CONFLICT (statement_id) DO NOTHING
    """
)

_RECORD_REJECTION = text(
    """
    INSERT INTO warehouse.etl_rejections
        (statement_id, ingest_seq, reason, detail, payload)
    VALUES (:statement_id, :ingest_seq, :reason, :detail,
            CAST(:payload AS jsonb))
    """
)

#: Recompute attempt ordering for the learner-activity pairs a batch
#: touched. Ordered by occurred_at, never by arrival: ingest_seq and event
#: time are unrelated (ADR-0005), so a late-arriving earlier attempt must
#: renumber the ones that followed it.
_RENUMBER_ATTEMPTS = text(
    """
    WITH ranked AS (
        SELECT assessment_result_key,
               row_number() OVER (
                   PARTITION BY student_key, activity_key
                   ORDER BY occurred_at, assessment_result_key
               ) AS position
        FROM warehouse.fact_assessment
        WHERE (student_key, activity_key) IN (
            SELECT student_key, activity_key FROM warehouse.fact_assessment
            WHERE statement_id = ANY(CAST(:ids AS uuid[]))
        )
    )
    UPDATE warehouse.fact_assessment f
    SET attempt_number = ranked.position
    FROM ranked
    WHERE f.assessment_result_key = ranked.assessment_result_key
      AND f.attempt_number IS DISTINCT FROM ranked.position
    """
)


def _verb_name(payload: dict[str, Any]) -> str:
    """The verb's short display name, falling back to its IRI's last segment."""
    verb = payload.get("verb") or {}
    display = verb.get("display") or {}
    if display:
        return next(iter(display.values()))
    return str(verb.get("id", "")).rstrip("/").rsplit("/", 1)[-1]


def load_batch(
    connection: Connection,
    rows: list[tuple[int, dict[str, Any]]],
    cache: DimensionCache | None = None,
) -> LoadReport:
    """Load one page of (ingest_seq, payload) pairs into the warehouse."""
    activity = 0
    assessment = 0
    rejected = 0
    graded_ids: list[str] = []
    highest = 0

    for ingest_seq, payload in rows:
        highest = max(highest, ingest_seq)
        statement_id = payload.get("id")
        try:
            keys = _resolve_keys(connection, payload, cache)
        except UnmodellableStatement as error:
            connection.execute(
                _RECORD_REJECTION,
                {
                    "statement_id": statement_id,
                    "ingest_seq": ingest_seq,
                    "reason": "unmodellable",
                    "detail": str(error),
                    "payload": json.dumps(payload),
                },
            )
            rejected += 1
            continue

        verb = _verb_name(payload)
        context = payload.get("context") or {}
        common = {
            "statement_id": statement_id,
            "verb": verb,
            "occurred_at": payload["timestamp"],
            "registration": context.get("registration"),
            "ingest_seq": ingest_seq,
            **keys,
        }
        activity += connection.execute(_INSERT_ACTIVITY, common).rowcount

        if verb in GRADED_VERBS:
            result = payload.get("result") or {}
            score = result.get("score") or {}
            assessment += connection.execute(
                _INSERT_ASSESSMENT,
                {
                    **common,
                    "scaled_score": score.get("scaled"),
                    "raw_score": score.get("raw"),
                    "success": result.get("success"),
                    "completion": result.get("completion"),
                },
            ).rowcount
            graded_ids.append(str(statement_id))

    if graded_ids:
        connection.execute(_RENUMBER_ATTEMPTS, {"ids": graded_ids})

    return LoadReport(
        read=len(rows),
        activity_rows=activity,
        assessment_rows=assessment,
        rejected=rejected,
        watermark=highest,
    )


def run(connection: Connection, page_size: int = DEFAULT_PAGE_SIZE) -> LoadReport:
    """Load everything in `raw` above this job's watermark.

    Args:
        connection: An open connection inside a transaction.
        page_size: Statements read per page.

    Returns:
        Totals for the run and the new watermark.
    """
    mark = watermark(connection)
    totals = LoadReport(watermark=mark)
    cache: DimensionCache = {}

    while True:
        rows = [
            (row.ingest_seq, row.payload)
            for row in connection.execute(
                text(
                    "SELECT ingest_seq, payload FROM raw.statements "
                    "WHERE ingest_seq > :mark ORDER BY ingest_seq LIMIT :n"
                ),
                {"mark": totals.watermark, "n": page_size},
            )
        ]
        if not rows:
            break

        page = load_batch(connection, rows, cache)
        totals = LoadReport(
            read=totals.read + page.read,
            activity_rows=totals.activity_rows + page.activity_rows,
            assessment_rows=totals.assessment_rows + page.assessment_rows,
            rejected=totals.rejected + page.rejected,
            watermark=max(totals.watermark, page.watermark),
        )

    if totals.read:
        _set_watermark(connection, totals.watermark, totals.read)
    return totals
