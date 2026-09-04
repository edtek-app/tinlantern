"""Evaluation-only code. Never shipped.

Reads the ground-truth sidecar, which must not reach the warehouse or the
deployed runtime (ADR-0002). Kept out of `pyproject.toml`'s packages list
so the packaging guard prevents it shipping, rather than trusting that
nothing imports it.
"""

from ml.evaluation.labels import LABEL_COLUMNS, load_labels, with_labels

__all__ = ["LABEL_COLUMNS", "load_labels", "with_labels"]
