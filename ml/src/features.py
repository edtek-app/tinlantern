"""Features for the early-alert risk model.

**Every feature is computed from the first `risk.feature_window_weeks` of
the term and nothing after it.** The outcome being predicted spans the
whole term, so a model given the full record could recompute the label
from observed scores and post a perfect number while predicting nothing
(ADR-0004). The window is what makes the task a prediction.

The window is anchored to **term start**, not to each learner's first
activity, so it closes on the same date for everyone. An early-alert
system answers "who is at risk now, four weeks in" — and a learner whose
history begins in week three is not someone to normalise, they are the
signal.

**Cohort-relative features are fitted, not computed in place.** A "pacing
against the cohort" feature needs a cohort baseline, and computing that
baseline over every learner — including held-out ones — leaks test
information into training. The baseline is therefore a `CohortBaseline`
fitted on the training split and carried as a parameter, so `transform`
never consults the rows it is transforming. A transform that *can* see
the whole frame will eventually be handed it.

Nothing here knows a label exists. Labels live in `ml.evaluation`, which
does not ship.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import pandas as pd
from sqlalchemy import Connection, text

from data.generator.config import CohortConfig

#: The learner identifier. Not a feature — the join key.
KEY = "learner_identifier"

#: Features derived only from a learner's own record inside the window.
#:
#: Two carry an explicit ``_in_window`` suffix because the ground-truth
#: sidecar has full-term twins of the same quantity — `longest_gap_days`
#: and `term_mean_score`. A shared name between a feature and a label is
#: how an accidental join becomes leakage, so the scope is in the name.
ABSOLUTE_FEATURES: tuple[str, ...] = (
    "events",
    "sessions",
    "active_days",
    "days_since_last_activity",
    "days_to_first_activity",
    "longest_gap_days_in_window",
    "graded_items",
    "mean_score_in_window",
    "failures",
    "max_attempts",
)

#: Features expressing a learner against their cohort. Fitted, not free.
RELATIVE_FEATURES: tuple[str, ...] = (
    "events_vs_cohort",
    "active_days_vs_cohort",
    "graded_items_vs_cohort",
    "score_vs_cohort",
)

FEATURE_COLUMNS: tuple[str, ...] = ABSOLUTE_FEATURES + RELATIVE_FEATURES


def window_close(config: CohortConfig) -> datetime:
    """The instant after which no statement may inform a feature.

    Term start plus ``risk.feature_window_weeks``. Configured rather than
    hard-coded because "how early is early" is a product decision
    (ADR-0004).
    """
    from data.generator.calendar import term_bounds

    start, _ = term_bounds(config.term)
    return start + timedelta(weeks=config.risk.feature_window_weeks)


_ACTIVITY = text(
    """
    SELECT s.learner_identifier,
           count(*)                                        AS events,
           count(*) FILTER (WHERE f.verb = 'initialized')   AS sessions,
           count(DISTINCT f.date_key)                       AS active_days,
           min(f.occurred_at)                               AS first_activity,
           max(f.occurred_at)                               AS last_activity
    FROM warehouse.fact_activity f
    JOIN warehouse.dim_student s USING (student_key)
    WHERE f.occurred_at < :close
    GROUP BY 1
    """
)

_GAPS = text(
    """
    WITH active AS (
        SELECT DISTINCT s.learner_identifier, d.full_date
        FROM warehouse.fact_activity f
        JOIN warehouse.dim_student s USING (student_key)
        JOIN warehouse.dim_date    d USING (date_key)
        WHERE f.occurred_at < :close
    ),
    spans AS (
        SELECT learner_identifier,
               full_date - LAG(full_date) OVER (
                   PARTITION BY learner_identifier ORDER BY full_date
               ) AS gap
        FROM active
    )
    SELECT learner_identifier, COALESCE(MAX(gap), 0) AS longest_gap_days_in_window
    FROM spans GROUP BY 1
    """
)

_ASSESSMENT = text(
    """
    SELECT s.learner_identifier,
           count(*)                                  AS graded_items,
           avg(f.scaled_score)                       AS mean_score_in_window,
           count(*) FILTER (WHERE f.verb = 'failed') AS failures,
           COALESCE(max(f.attempt_number), 0)        AS max_attempts
    FROM warehouse.fact_assessment f
    JOIN warehouse.dim_student s USING (student_key)
    WHERE f.occurred_at < :close
    GROUP BY 1
    """
)

_LEARNERS = text("SELECT learner_identifier FROM warehouse.dim_student ORDER BY 1")


def extract_window_features(
    connection: Connection, config: CohortConfig
) -> pd.DataFrame:
    """Read each learner's in-window record from the warehouse.

    Args:
        connection: An open connection.
        config: The cohort configuration, for the term and window.

    Returns:
        One row per learner in ``dim_student``, indexed by identifier and
        sorted, carrying the absolute features only. A learner with no
        activity in the window still gets a row — an absence is the
        loudest signal there is, not a missing record.
    """
    from data.generator.calendar import term_bounds

    close = window_close(config)
    start, _ = term_bounds(config.term)
    params = {"close": close}

    learners = pd.read_sql(_LEARNERS, connection).set_index(KEY)
    activity = pd.read_sql(_ACTIVITY, connection, params=params).set_index(KEY)
    gaps = pd.read_sql(_GAPS, connection, params=params).set_index(KEY)
    graded = pd.read_sql(_ASSESSMENT, connection, params=params).set_index(KEY)

    frame = learners.join([activity, gaps, graded], how="left")

    # Distances are measured from fixed points, so a learner who never
    # appeared gets the worst possible value rather than a null: silence
    # for the whole window is a fact about them, not missing data.
    window_days = (close - start).days
    frame["days_since_last_activity"] = (
        (close - pd.to_datetime(frame["last_activity"], utc=True))
        .dt.total_seconds()
        .div(86_400)
        .fillna(window_days)
    )
    frame["days_to_first_activity"] = (
        (pd.to_datetime(frame["first_activity"], utc=True) - start)
        .dt.total_seconds()
        .div(86_400)
        .fillna(window_days)
    )
    frame = frame.drop(columns=["first_activity", "last_activity"])

    for column in (
        "events",
        "sessions",
        "active_days",
        "graded_items",
        "failures",
        "max_attempts",
        "longest_gap_days_in_window",
    ):
        frame[column] = frame[column].fillna(0).astype(float)
    # A learner with no graded work has no mean score. Zero would assert
    # they scored nothing; the absence is carried by graded_items.
    frame["mean_score_in_window"] = (
        frame["mean_score_in_window"].astype(float).fillna(0.0)
    )

    return frame[list(ABSOLUTE_FEATURES)].sort_index()


@dataclass(frozen=True, slots=True)
class CohortBaseline:
    """Cohort statistics fitted on one split and applied to another.

    Carried as a parameter rather than recomputed inside ``transform``,
    so a relative feature can never be derived from the rows it describes.
    """

    median_events: float
    median_active_days: float
    median_graded_items: float
    median_score: float

    def transform(self, frame: pd.DataFrame) -> pd.DataFrame:
        """Add cohort-relative features using only the fitted values."""
        out = frame.copy()
        out["events_vs_cohort"] = out["events"] / _safe(self.median_events)
        out["active_days_vs_cohort"] = out["active_days"] / _safe(
            self.median_active_days
        )
        out["graded_items_vs_cohort"] = out["graded_items"] / _safe(
            self.median_graded_items
        )
        out["score_vs_cohort"] = out["mean_score_in_window"] - self.median_score
        return out[list(FEATURE_COLUMNS)]


def _safe(value: float) -> float:
    """Avoid dividing by a cohort median of zero."""
    return value if value else 1.0


def fit_baseline(frame: pd.DataFrame) -> CohortBaseline:
    """Fit cohort statistics from a training split.

    Args:
        frame: Absolute features for the TRAINING learners only. Passing
            the full cohort would leak held-out information into every
            relative feature.

    Returns:
        The fitted baseline.
    """
    return CohortBaseline(
        median_events=float(frame["events"].median()),
        median_active_days=float(frame["active_days"].median()),
        median_graded_items=float(frame["graded_items"].median()),
        median_score=float(frame["mean_score_in_window"].median()),
    )


def build_features(
    connection: Connection, config: CohortConfig, baseline: CohortBaseline | None = None
) -> tuple[pd.DataFrame, CohortBaseline]:
    """Extract features and apply a cohort baseline.

    Args:
        connection: An open connection.
        config: The cohort configuration.
        baseline: A baseline fitted on a training split. When omitted one
            is fitted from this frame — acceptable only when the frame IS
            the training split.

    Returns:
        The feature frame and the baseline used.
    """
    absolute = extract_window_features(connection, config)
    fitted = baseline or fit_baseline(absolute)
    return fitted.transform(absolute), fitted
