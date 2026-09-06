"""Per-learner explanations, by ablation to the cohort median.

For each feature, the learner's value is replaced with the cohort median
and the model re-scored. The drop is that feature's contribution: *"if
this learner's failure count were typical, their risk would fall 0.31."*

Chosen over SHAP because it explains **the number the advisor actually
sees**. The selected model wraps a tree ensemble in Platt scaling, and a
tree explainer would attribute the *pre-calibration* estimator's output —
so the explanation would not correspond to the displayed score. An
attribution that explains a different number than the one on screen is a
worse failure than no attribution.

**Contributions do not sum to the score, and they interact.** Ablating two
features separately does not predict ablating both. That caveat travels
with the payload rather than living only here, so a dashboard cannot
render these as an additive decomposition.

The cohort median is a **fitted parameter**, like `CohortBaseline`: fitted
on training rows and carried. Recomputing it at scoring time would let the
population being scored leak into its own explanation.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from ml.src.features import FEATURE_COLUMNS

#: Attached to every drivers payload. M5 must not render contributions as
#: an additive breakdown of the score, because they are not one.
CONTRIBUTION_CAVEAT = (
    "Contributions are counterfactual: each is the change in risk if that "
    "one feature were typical for the cohort. They do NOT sum to the risk "
    "score and they interact — ablating two features together is not the "
    "sum of ablating each alone. Read them as 'what is unusual about this "
    "learner', not as a decomposition."
)


@dataclass(frozen=True, slots=True)
class MedianProfile:
    """The typical learner, fitted on training rows and carried.

    Never recomputed from the rows being scored: an explanation derived
    from the scored population would leak it into itself.
    """

    values: dict[str, float]

    @classmethod
    def fit(cls, features: pd.DataFrame) -> MedianProfile:
        """Fit from a TRAINING frame."""
        return cls(
            values={
                column: float(features[column].median())
                for column in FEATURE_COLUMNS
                if column in features
            }
        )


def contributions(
    model, features: pd.DataFrame, profile: MedianProfile
) -> pd.DataFrame:
    """Per-learner, per-feature contribution to risk.

    Args:
        model: A fitted model exposing ``predict_proba``. Model-agnostic on
            purpose, so it works through the calibration wrapper.
        features: The learners to explain, indexed by learner.
        profile: The cohort's typical values, fitted on training rows.

    Returns:
        One row per learner, one column per feature: the amount that
        feature raises this learner's risk above what it would be if the
        feature were typical. Positive means it pushes risk up.
    """
    ordered = features[list(FEATURE_COLUMNS)]
    baseline = model.predict_proba(ordered)[:, 1]

    columns = {}
    for column in FEATURE_COLUMNS:
        if column not in profile.values:
            continue
        counterfactual = ordered.copy()
        counterfactual[column] = profile.values[column]
        columns[column] = baseline - model.predict_proba(counterfactual)[:, 1]
    return pd.DataFrame(columns, index=features.index)


def top_drivers(
    model, features: pd.DataFrame, profile: MedianProfile, top: int = 3
) -> pd.Series:
    """The features raising each learner's risk most, as a payload.

    Returns:
        One dict per learner: the ranked drivers and the caveat that stops
        them being read as a decomposition.
    """
    frame = contributions(model, features, profile)

    def payload(row: pd.Series) -> dict:
        ranked = row.sort_values(ascending=False).head(top)
        return {
            "caveat": CONTRIBUTION_CAVEAT,
            "method": "ablation-to-cohort-median",
            "additive": False,
            "drivers": [
                {"feature": name, "contribution": round(float(value), 4)}
                for name, value in ranked.items()
            ],
        }

    return frame.apply(payload, axis=1)
