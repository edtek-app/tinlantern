"""``python -m ml`` — score the cohort.

Invoked by ``make score``. Trains on the loaded warehouse, writes a risk
score and drivers per learner, and replaces any previous score for the
same window and model.

The training target comes from the evaluation sidecar, supplied here at
the edge: `ml.src` never reads labels itself, so nothing that ships knows
the ground truth exists (ADR-0002).

**Advisor summaries are generated here too**, after scoring, because a
summary is keyed to the score it describes and a page view must never
call a language model. M6's deployment story is therefore "scoring
writes summaries", not "the web tier calls an LLM per request".
Summaries are skipped when no provider is configured — scoring must
stay runnable without credentials.
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
    parser.add_argument(
        "--skip-summaries",
        action="store_true",
        help="score without generating advisor summaries",
    )
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
        scoring = run(connection, config, labels)
        print(scoring.summary())

        if not args.skip_summaries:
            written, failed = generate_summaries(connection)
            print(
                f"summaries   {written} written"
                + (f", {failed} skipped" if failed else "")
            )
    return 0


def generate_summaries(connection) -> tuple[int, int]:
    """Write an advisor summary for every scored learner.

    Returns:
        How many were written, and how many were skipped because no
        provider is configured. Scoring must stay runnable without
        credentials, so an unset LLM_PROVIDER skips rather than fails —
        the scores are the point and the summaries are an enhancement.
    """
    from sqlalchemy import text as sql

    from app.dashboard.queries import latest_model_version, learner_detail
    from app.dashboard.summaries import generate
    from app.llm.client import LLMError, build_client

    try:
        provider = build_client()
    except LLMError as unset:
        print(f"summaries   skipped: {unset}", file=sys.stderr)
        return 0, 0

    version = latest_model_version(connection)
    identifiers = connection.execute(
        sql(
            "SELECT DISTINCT s.learner_identifier FROM warehouse.risk_score rs "
            "JOIN warehouse.dim_student s USING (student_key) "
            "WHERE rs.model_version = :version ORDER BY 1"
        ),
        {"version": version},
    ).scalars()

    written = failed = 0
    for identifier in identifiers:
        detail = learner_detail(connection, identifier)
        if detail is None:
            failed += 1
            continue
        generate(connection, detail, provider)
        written += 1
    return written, failed


if __name__ == "__main__":
    sys.exit(main())
