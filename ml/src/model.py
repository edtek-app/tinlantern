"""The risk model: definition and scoring.

Ships. Production scores learners; it does not train them, and it never
sees a label — training and evaluation live in `ml.evaluation`, which is
kept out of the deployment manifest.

Logistic regression on standardised features, chosen as the baseline
because it is interpretable: a per-learner risk score comes with signed
coefficients, which is what "top contributing features per student"
needs. A stronger model has to earn its place against this.
"""

from __future__ import annotations

import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from ml.src.features import FEATURE_COLUMNS

#: Probability at or above which a learner is flagged. Deliberately not
#: 0.5: an early-alert system's cost of a missed learner is far higher
#: than the cost of a conversation that turns out to be unnecessary. The
#: value is argued in the model ADR, alongside the precision it buys.
ALERT_THRESHOLD = 0.35


def build_model() -> Pipeline:
    """A standardised logistic regression.

    Scaling matters here: `events` runs to the hundreds while
    `score_vs_cohort` is a fraction, and unscaled coefficients would be
    unreadable as feature importances.
    """
    return Pipeline(
        [
            ("scale", StandardScaler()),
            (
                "model",
                LogisticRegression(
                    max_iter=1000,
                    class_weight="balanced",
                    random_state=20260301,
                ),
            ),
        ]
    )


def score(model: Pipeline, features: pd.DataFrame) -> pd.Series:
    """Return each learner's risk probability, indexed by learner."""
    ordered = features[list(FEATURE_COLUMNS)]
    return pd.Series(
        model.predict_proba(ordered)[:, 1], index=features.index, name="risk"
    )


def drivers(model: Pipeline, features: pd.DataFrame, top: int = 3) -> pd.DataFrame:
    """Per-learner top contributing features, signed toward risk.

    Contribution is the standardised value times the coefficient, so it
    reads as "this learner is unusual in this way, and that pushes their
    risk up". Interpretability is why logistic regression is the baseline.
    """
    ordered = features[list(FEATURE_COLUMNS)]
    scaled = model.named_steps["scale"].transform(ordered)
    coefficients = model.named_steps["model"].coef_[0]
    contributions = pd.DataFrame(
        scaled * coefficients, index=features.index, columns=list(FEATURE_COLUMNS)
    )
    ranked = contributions.apply(
        lambda row: ", ".join(row.abs().nlargest(top).index), axis=1
    )
    return pd.DataFrame({"top_drivers": ranked})
