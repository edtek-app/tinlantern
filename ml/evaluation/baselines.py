"""The bars a real model must clear.

Two, and only the second is meaningful.

**Majority class** predicts nobody at risk. It is a formality: 69%
accuracy, zero recall, useless to an advisor.

**The failures rule** thresholds on a single feature — the count of failed
assessments inside the window — which alone reaches AUC 0.923 on this
cohort. That is THE bar. If a fourteen-feature model does not clearly beat
counting failures, the report says so plainly and names the one-feature
rule as the honest baseline. A model that ties a single threshold is a
finding about the problem, and a more interesting one than a marginal win.
"""

from __future__ import annotations

import pandas as pd


def majority_class_scores(absolute: pd.DataFrame) -> pd.Series:
    """Predict nobody at risk. A formality, reported for completeness."""
    return pd.Series(0.0, index=absolute.index, name="risk")


def failures_rule_scores(absolute: pd.DataFrame) -> pd.Series:
    """Rank purely by failed assessments in the window, scaled to [0, 1].

    No fitting and no training data: this is a rule, not a model, which is
    what makes it a fair floor for one.
    """
    # Counts cannot be negative, but the rule promises a score in [0, 1]
    # and should keep that promise regardless of what it is handed.
    failures = absolute["failures"].astype(float).clip(lower=0)
    ceiling = failures.max()
    scores = failures / ceiling if ceiling else failures
    return scores.clip(0.0, 1.0).rename("risk")
