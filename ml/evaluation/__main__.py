"""``python -m ml.evaluation`` — generate the evaluation report.

Invoked by ``make report``. Reads the loaded warehouse and the ground-truth
sidecar, runs the comparison and the window sweep, and writes the markdown
that `evals/reports/` publishes.

Evaluation-only, so this never ships (ADR-0002).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import text

from app.db import engine
from data.generator import load_config
from ml.evaluation.comparison import candidates, run_comparison
from ml.evaluation.labels import load_labels
from ml.evaluation.report import DEFAULT_PATH, collect_provenance, render
from ml.evaluation.sensitivity import sweep_windows, threshold_sweep
from ml.evaluation.split import out_of_fold_scores
from ml.src.features import extract_window_features
from ml.src.model import build_calibrated_gradient_boosting, score

THRESHOLDS = (0.20, 0.25, 0.30, 0.35, 0.40, 0.50, 0.60)


def main(argv: list[str] | None = None) -> int:
    """Generate the report. Non-zero if the warehouse is not loaded."""
    parser = argparse.ArgumentParser(prog="python -m ml.evaluation")
    parser.add_argument("--out", type=Path, default=Path(DEFAULT_PATH))
    parser.add_argument("--config", default="data/generator/cohort.example.yaml")
    args = parser.parse_args(argv)

    config = load_config(args.config)
    labels = load_labels()

    with engine().connect() as connection:
        statements = connection.execute(
            text("SELECT count(*) FROM warehouse.fact_activity")
        ).scalar_one()
        if not statements:
            print(
                "the warehouse is empty; run `make ingest && make etl` first",
                file=sys.stderr,
            )
            return 2

        absolute = extract_window_features(connection, config)
        target = labels["at_risk"].loc[absolute.index]
        archetypes = labels["archetype"].loc[absolute.index]

        comparison = run_comparison(absolute, target, archetypes, candidates())
        windows = sweep_windows(connection, config, labels, candidates())

    def learn(train_x, train_y, test_x):
        model = build_calibrated_gradient_boosting()
        model.fit(train_x, train_y)
        return score(model, test_x)

    sweep = threshold_sweep(
        target, out_of_fold_scores(absolute, target, learn), THRESHOLDS
    )

    provenance = collect_provenance(config.seed, statements, len(absolute))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(comparison, windows, sweep, provenance), "utf-8")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
