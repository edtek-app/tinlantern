"""Ground-truth labels, for evaluation only.

Reads `data/output/ground_truth.ndjson` — the sidecar the generator emits
alongside the statements. It must never reach the warehouse or the
deployed runtime (ADR-0002), so this module lives outside the packaging
manifest and the packaging guard asserts it stays there.

Features come from the warehouse and labels come from here; they meet in
`ml/` and nowhere else. `pipeline/` has no business knowing labels exist.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

KEY = "learner_identifier"

#: Everything the sidecar carries. NONE of it may become a feature —
#: `archetype` least of all: it is the generative shape the behaviour was
#: drawn from, so using it would be reading the answer (ADR-0004).
LABEL_COLUMNS: tuple[str, ...] = (
    "archetype",
    "at_risk",
    "failed_term",
    "collapsed",
    "term_mean_score",
    "longest_gap_days",
    "scheduled_items",
    "completed_items",
)

DEFAULT_SIDECAR = Path("data/output/ground_truth.ndjson")

#: The column a model predicts.
TARGET = "at_risk"


def load_labels(path: Path = DEFAULT_SIDECAR) -> pd.DataFrame:
    """Load the ground-truth sidecar, indexed by learner.

    Args:
        path: The sidecar file.

    Returns:
        One row per learner, sorted by identifier.

    Raises:
        FileNotFoundError: If no cohort has been generated.
    """
    if not path.is_file():
        raise FileNotFoundError(
            f"no ground truth at {path}; run `make seed` to generate a cohort"
        )
    rows = [json.loads(line) for line in path.read_text("utf-8").splitlines() if line]
    frame = pd.DataFrame(rows).set_index(KEY).sort_index()
    return frame[[c for c in LABEL_COLUMNS if c in frame.columns]]


def with_labels(features: pd.DataFrame, labels: pd.DataFrame) -> pd.DataFrame:
    """Join features to labels on the learner identifier.

    Args:
        features: The feature frame, indexed by learner.
        labels: The sidecar frame, indexed by learner.

    Returns:
        Features plus the target column only. The other sidecar fields are
        deliberately not attached — they are measurements of the outcome,
        and a stray join would put the answer beside the question.

    Raises:
        ValueError: If a learner has features but no label.
    """
    missing = features.index.difference(labels.index)
    if len(missing):
        raise ValueError(
            f"{len(missing)} learners have features but no ground truth: "
            f"{list(missing[:5])}"
        )
    return features.join(labels[[TARGET]], how="inner")
