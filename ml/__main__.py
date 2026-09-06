"""``python -m ml`` — score the cohort.

Invoked by ``make score``. Trains on the loaded warehouse, writes a risk
score and drivers per learner, and replaces any previous score for the
same window and model.

The training target comes from the evaluation sidecar, supplied here at
the edge: `ml.src` never reads labels itself, so nothing that ships knows
the ground truth exists (ADR-0002).
"""

from __future__ import annotations

import sys

from sqlalchemy import text

from app.db import transaction
from data.generator import load_config
from ml.evaluation.labels import TARGET, load_labels
from ml.src.scoring import run


def main(argv: list[str] | None = None) -> int:
    """Score the cohort. Non-zero if the warehouse is not loaded."""
    import argparse

    parser = argparse.ArgumentParser(prog="python -m ml")
    parser.add_argument("--config", default="data/generator/cohort.example.yaml")
    args = parser.parse_args(argv)

    config = load_config(args.config)
    labels = load_labels()[TARGET]

    with transaction() as connection:
        if not connection.execute(
            text("SELECT count(*) FROM warehouse.fact_activity")
        ).scalar_one():
            print(
                "the warehouse is empty; run `make ingest && make etl` first",
                file=sys.stderr,
            )
            return 2
        print(run(connection, config, labels).summary())
    return 0


if __name__ == "__main__":
    sys.exit(main())
