"""Evaluation-only code. Never shipped.

Reads the ground-truth sidecar, which must not reach the warehouse or the
deployed runtime (ADR-0002). Kept out of `pyproject.toml`'s packages list
so the packaging guard prevents it shipping, rather than trusting that
nothing imports it.
"""

from ml.evaluation.baselines import failures_rule_scores, majority_class_scores
from ml.evaluation.comparison import (
    DECISION_METRICS,
    Candidate,
    Comparison,
    candidates,
    decide,
    run_comparison,
)
from ml.evaluation.labels import LABEL_COLUMNS, TARGET, load_labels, with_labels
from ml.evaluation.metrics import (
    AUC_CAVEAT,
    MIN_REPORTABLE_N,
    ArchetypeRecall,
    Report,
    calibration_bins,
    evaluate,
    per_archetype_recall,
)
from ml.evaluation.split import N_SPLITS, SEED, fold_membership, out_of_fold_scores

__all__ = [
    "AUC_CAVEAT",
    "DECISION_METRICS",
    "LABEL_COLUMNS",
    "MIN_REPORTABLE_N",
    "N_SPLITS",
    "SEED",
    "TARGET",
    "ArchetypeRecall",
    "Candidate",
    "Comparison",
    "Report",
    "calibration_bins",
    "candidates",
    "decide",
    "evaluate",
    "failures_rule_scores",
    "fold_membership",
    "load_labels",
    "majority_class_scores",
    "out_of_fold_scores",
    "run_comparison",
    "per_archetype_recall",
    "with_labels",
]
