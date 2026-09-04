"""Command line entry point: ``python -m data.ingest``.

Invoked by ``make ingest`` from the repository root. Posts a generated
cohort to a running TinLantern API and reports what landed.

Exits non-zero if any batch was rejected. A load that quietly ends with
rejections is the same failure the rejection path exists to prevent, one
level up.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx
from sqlalchemy import text

from app.db import engine
from data.loader import (
    DEFAULT_BATCH_SIZE,
    LoadReport,
    load_statements,
    read_statements,
)

DEFAULT_SOURCE = Path("data/output/statements.ndjson")
DEFAULT_API = "http://127.0.0.1:8000"


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        prog="python -m data.ingest",
        description="Post a generated cohort to a running TinLantern API.",
    )
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--api", default=DEFAULT_API, help="base URL of the API")
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=(
            "statements per request. Batches are atomic, so this is also "
            f"the blast radius of one bad statement (default {DEFAULT_BATCH_SIZE})"
        ),
    )
    parser.add_argument("--limit", type=int, default=None, help="stop after N")
    return parser


def _report_progress(batches: int, sent: int) -> None:
    if batches % 50 == 0:
        print(f"  ... {sent:,} statements in {batches:,} batches", flush=True)


def main(argv: list[str] | None = None) -> int:
    """Load a cohort and report. Non-zero exit if anything was rejected."""
    args = build_parser().parse_args(argv)
    if not args.source.is_file():
        print(f"no cohort at {args.source}; run `make seed` first", file=sys.stderr)
        return 2

    statements = read_statements(args.source)
    if args.limit is not None:
        statements = (s for _, s in zip(range(args.limit), statements, strict=False))

    def count_stored() -> int:
        with engine().connect() as connection:
            return connection.execute(
                text("SELECT count(*) FROM raw.statements")
            ).scalar_one()

    print(f"source  {args.source}\napi     {args.api}")
    with httpx.Client(base_url=args.api, timeout=120.0) as client:

        def post(batch: list[dict]) -> tuple[int, object]:
            response = client.post("/xapi/statements", json=batch)
            try:
                return response.status_code, response.json()
            except ValueError:
                return response.status_code, response.text

        report: LoadReport = load_statements(
            statements,
            post,
            count_stored,
            batch_size=args.batch_size,
            progress=_report_progress,
        )

    print(report.summary())
    if not report.ok:
        print("\nload FAILED: batches were rejected", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
