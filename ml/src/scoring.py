"""The scoring job: risk scores and drivers, written to the warehouse.

Trains on the whole cohort and scores it. **Retraining every run** rather
than loading a persisted artifact: at 120 learners it costs under a
second, and it removes a class of "which model produced this score"
confusion — a run is reproducible from the seed and the code alone.

*The tradeoff, which becomes real at M6:* there is no way to score against
a previous model without checking out the code that produced it. When
scoring moves to a deployed runtime and retraining per invocation stops
being free, persistence is the answer — see ADR-0007.

`model_version` records **code identity**, not a semantic version somebody
must remember to bump: the git commit, plus a marker when the tree is
dirty. A version that has to be maintained by hand becomes a constant
nobody updates.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import datetime

import pandas as pd
from sqlalchemy import Connection, text

from data.generator.config import CohortConfig
from ml.src.drivers import MedianProfile, top_drivers
from ml.src.features import build_features, window_close
from ml.src.model import ALERT_THRESHOLD, build_calibrated_gradient_boosting, score


@dataclass(frozen=True, slots=True)
class ScoringRun:
    """What one scoring run produced."""

    model_version: str
    window_close: datetime
    learners: int
    alerted: int

    def summary(self) -> str:
        share = self.alerted / self.learners if self.learners else 0.0
        return (
            f"model       {self.model_version}\n"
            f"window      closes {self.window_close.date()}\n"
            f"scored      {self.learners} learners\n"
            f"alerted     {self.alerted} ({share:.0%})"
        )


def model_version() -> str:
    """Identify the code that produced a score.

    The git commit, suffixed when the working tree is dirty. Derived
    rather than declared, so it cannot drift into a stale constant.
    """

    def git(*args: str) -> str | None:
        try:
            done = subprocess.run(args, capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            return None
        return done.stdout.strip() if done.returncode == 0 else None

    commit = git("git", "rev-parse", "--short", "HEAD") or "unknown"
    status = git("git", "status", "--porcelain")
    return f"{commit}-dirty" if status else commit


_UPSERT = text(
    """
    INSERT INTO warehouse.risk_score
        (student_key, window_close, model_version, risk, alerted, drivers)
    SELECT s.student_key, :window_close, :model_version, :risk, :alerted,
           CAST(:drivers AS jsonb)
    FROM warehouse.dim_student s
    WHERE s.learner_identifier = :learner
    ON CONFLICT (student_key, window_close, model_version) DO UPDATE SET
        risk = EXCLUDED.risk,
        alerted = EXCLUDED.alerted,
        drivers = EXCLUDED.drivers,
        scored_at = now()
    """
)


def run(
    connection: Connection,
    config: CohortConfig,
    labels: pd.Series,
    threshold: float = ALERT_THRESHOLD,
) -> ScoringRun:
    """Train, score every learner, and write the results.

    Args:
        connection: An open connection inside a transaction.
        config: The cohort configuration.
        labels: The training target, indexed by learner. Supplied by the
            caller because production scoring code never reads the
            ground-truth sidecar itself (ADR-0002).
        threshold: Alert threshold.

    Returns:
        A summary of the run.
    """
    features, _ = build_features(connection, config)
    aligned = labels.loc[features.index]

    model = build_calibrated_gradient_boosting()
    model.fit(features, aligned)

    risks = score(model, features)
    profile = MedianProfile.fit(features)
    payloads = top_drivers(model, features, profile)

    version = model_version()
    closes = window_close(config)
    alerted = 0
    for learner, risk in risks.items():
        flag = bool(risk >= threshold)
        alerted += flag
        connection.execute(
            _UPSERT,
            {
                "learner": learner,
                "window_close": closes,
                "model_version": version,
                "risk": round(float(risk), 4),
                "alerted": flag,
                "drivers": json.dumps(payloads.loc[learner]),
            },
        )

    return ScoringRun(
        model_version=version,
        window_close=closes,
        learners=len(features),
        alerted=alerted,
    )
