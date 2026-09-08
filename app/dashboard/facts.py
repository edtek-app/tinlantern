"""Building a learner's fact set from the warehouse.

M4's summariser takes a `LearnerFacts` and deliberately not a
connection, so its behaviour is testable without a database. This is the
piece it left to its caller.

The fact set's composition is the curation decision recorded in
ADR-0008; nothing is added here that is not in that table.
"""

from __future__ import annotations

from sqlalchemy import Connection, text

from app.dashboard.queries import LearnerDetail, latest_model_version
from app.llm.summary import DriverFact, LearnerFacts

_COHORT = text(
    """
    WITH current AS (
        SELECT DISTINCT ON (rs.student_key) rs.alerted
        FROM warehouse.risk_score rs
        WHERE rs.model_version = :model_version
        ORDER BY rs.student_key, rs.window_close DESC, rs.scored_at DESC
    )
    SELECT count(*) AS scored, count(*) FILTER (WHERE alerted) AS alerted
    FROM current
    """
)


def cohort_counts(connection: Connection, model_version: str) -> tuple[int, int]:
    """Learners scored and learners alerted, for one model version.

    Scoped and deduplicated per learner for the reason ADR-0007 records:
    an unscoped count returns rows, and returned 76 for a cohort of 120.
    """
    row = connection.execute(_COHORT, {"model_version": model_version}).one()
    return row.scored, row.alerted


def facts_for(connection: Connection, detail: LearnerDetail) -> LearnerFacts:
    """Turn a drill-down row into the fact set a summary may use.

    Args:
        connection: An open connection, for the cohort context.
        detail: The learner's current score and drivers.

    Returns:
        Exactly the facts ADR-0008 curates — no cohort median, because
        the median the contributions were ablated against is not
        persisted and a recomputed one could differ from the one those
        numbers describe.
    """
    scored, alerted = cohort_counts(connection, detail.model_version)
    return LearnerFacts(
        learner=detail.learner_identifier,
        risk=detail.risk,
        alerted=detail.alerted,
        threshold=detail.threshold,
        window_close=detail.window_close,
        model_version=detail.model_version,
        drivers=tuple(
            DriverFact(feature=driver.feature, contribution=driver.contribution)
            for driver in detail.drivers
        ),
        cohort_size=scored,
        cohort_alerted=alerted,
        caveat=detail.caveat,
    )


def current_model_version(connection: Connection) -> str | None:
    """Re-exported so callers need one import for the scoping rule."""
    return latest_model_version(connection)
