"""Window sensitivity: the same comparison at several window widths.

**This is a measurement, not a search.** The production window is four
weeks by product decision (ADR-0004): an early-alert system answers "who
is at risk now, four weeks in". The curve says what that choice costs, and
2 weeks is included precisely so the curve looks in both directions — one
that only looked toward more data would be an argument for waiting rather
than a measurement of the trade.

Picking the window that scores best would be tuning on the evaluation set
with extra steps.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
from sqlalchemy import Connection

from data.generator.config import CohortConfig
from ml.evaluation.comparison import Candidate, run_comparison
from ml.evaluation.metrics import Report
from ml.src.features import extract_window_features

#: Widths measured. 2 is deliberately EARLIER than production, so the
#: curve shows the cost of moving earlier and not only the gain of waiting.
WINDOW_WEEKS: tuple[int, ...] = (2, 4, 6, 8)

#: The window actually used. A product decision, not a result.
PRODUCTION_WINDOW_WEEKS = 4


@dataclass(frozen=True, slots=True)
class WindowResult:
    """One window width's measured outcome."""

    weeks: int
    reports: tuple[Report, ...]
    is_production: bool


def sweep_windows(
    connection: Connection,
    config: CohortConfig,
    labels: pd.DataFrame,
    candidates: tuple[Candidate, ...],
    widths: tuple[int, ...] = WINDOW_WEEKS,
) -> tuple[WindowResult, ...]:
    """Measure the same models at each window width.

    Args:
        connection: An open connection to a loaded warehouse.
        config: The cohort configuration.
        labels: The ground-truth frame, indexed by learner.
        candidates: The models to compare.
        widths: Window widths in weeks.

    Returns:
        One result per width, in the order given.
    """
    results = []
    for weeks in widths:
        windowed = config.model_copy(deep=True)
        windowed.risk.feature_window_weeks = weeks
        absolute = extract_window_features(connection, windowed)
        target = labels["at_risk"].loc[absolute.index]
        archetypes = labels["archetype"].loc[absolute.index]
        comparison = run_comparison(absolute, target, archetypes, candidates)
        results.append(
            WindowResult(
                weeks=weeks,
                reports=comparison.reports,
                is_production=weeks == PRODUCTION_WINDOW_WEEKS,
            )
        )
    return tuple(results)


def threshold_sweep(
    truth: pd.Series, scores: pd.Series, thresholds: tuple[float, ...]
) -> tuple[tuple[float, float, float, int, int], ...]:
    """(threshold, precision, recall, missed, false alarms) per threshold.

    Evidence for the alert threshold's cost, not a curve to maximise. The
    threshold is argued from operational reasoning first; this shows what
    that reasoning costs.
    """
    rows = []
    actual = truth.astype(bool)
    for threshold in thresholds:
        flagged = scores >= threshold
        true_positive = int((flagged & actual).sum())
        false_positive = int((flagged & ~actual).sum())
        missed = int((~flagged & actual).sum())
        precision = (
            true_positive / (true_positive + false_positive) if flagged.any() else 0.0
        )
        recall = true_positive / int(actual.sum()) if actual.any() else 0.0
        rows.append((threshold, precision, recall, missed, false_positive))
    return tuple(rows)
