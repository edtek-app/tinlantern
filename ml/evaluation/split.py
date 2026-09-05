"""Splitting, and out-of-fold prediction.

**Stratified k-fold rather than a single holdout.** At 120 learners a 70/30
split leaves ~36 test rows and about six `disengaging` learners — and
per-archetype recall is the metric that leads the report. One learner
either way would swing it by seventeen points, which is noise presented as
a finding. Out-of-fold prediction gives every learner a score from a model
that never saw them, so the per-archetype numbers rest on all 120.

*Stated limitation:* repeatedly selecting models against a
cross-validation estimate can overfit that estimate. With two models
compared once this is negligible. If model iteration becomes extensive,
that is the trigger for a genuine untouched holdout.

**The cohort baseline is fitted inside each fold**, on the training
portion only. Fitting it once over everything would leak held-out
information into every relative feature.
"""

from __future__ import annotations

from collections.abc import Callable

import pandas as pd
from sklearn.model_selection import StratifiedKFold

from ml.src.features import fit_baseline

#: Folds and seed, fixed so a reported number is reproducible. Recorded in
#: the model ADR alongside why k-fold rather than a holdout.
N_SPLITS = 5
SEED = 20260301

#: Given training features, training labels and test features, return the
#: test rows' risk probabilities.
Learner = Callable[[pd.DataFrame, pd.Series, pd.DataFrame], pd.Series]


def out_of_fold_scores(
    absolute: pd.DataFrame,
    target: pd.Series,
    learner: Learner,
    n_splits: int = N_SPLITS,
    seed: int = SEED,
) -> pd.Series:
    """Score every learner using a model that never saw them.

    Args:
        absolute: Absolute (un-normalised) features, indexed by learner.
        target: The binary outcome, aligned to ``absolute``.
        learner: Fits on a fold's training rows and scores its test rows.
        n_splits: Number of folds.
        seed: Fixed, so the split is reproducible.

    Returns:
        One risk score per learner, in the input's order.
    """
    aligned = target.loc[absolute.index]
    scores = pd.Series(index=absolute.index, dtype=float, name="risk")

    splitter = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=seed)
    for train_rows, test_rows in splitter.split(absolute, aligned):
        train = absolute.iloc[train_rows]
        test = absolute.iloc[test_rows]

        # Fitted per fold, on training rows only. Fitting once over the
        # whole frame would put held-out information into every relative
        # feature of every fold.
        baseline = fit_baseline(train)
        scores.iloc[test_rows] = learner(
            baseline.transform(train),
            aligned.iloc[train_rows],
            baseline.transform(test),
        ).to_numpy()

    if scores.isna().any():
        raise RuntimeError("some learners received no out-of-fold score")
    return scores


def fold_membership(
    absolute: pd.DataFrame,
    target: pd.Series,
    n_splits: int = N_SPLITS,
    seed: int = SEED,
) -> list[tuple[list[str], list[str]]]:
    """Return (train, test) learner identifiers per fold, for inspection."""
    aligned = target.loc[absolute.index]
    splitter = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=seed)
    return [
        (list(absolute.index[train]), list(absolute.index[test]))
        for train, test in splitter.split(absolute, aligned)
    ]
