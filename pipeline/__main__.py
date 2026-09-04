"""``python -m pipeline`` — run the raw-to-warehouse ETL.

Invoked by ``make etl``. Re-running is safe: the load pages above a stored
watermark, and the warehouse's UNIQUE grain stops any duplicate the
watermark logic might let through.

Exits non-zero if *this run* could not model something. A run that ended
quietly with rejections would be the silent loss the rejection path exists
to prevent, one layer further in.

Historical rejections are reported but do not fail the run: whether the
warehouse as a whole is clean is the data-quality suite's question. An ETL
that failed forever after one bad statement would be unusable.
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import text

from app.db import transaction
from pipeline.etl import DEFAULT_PAGE_SIZE, run


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m pipeline",
        description="Load raw xAPI statements into the warehouse star schema.",
    )
    parser.add_argument("--page-size", type=int, default=DEFAULT_PAGE_SIZE)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the ETL and report. Non-zero if anything could not be modelled."""
    args = build_parser().parse_args(argv)
    with transaction() as connection:
        report = run(connection, page_size=args.page_size)
        outstanding = connection.execute(
            text("SELECT count(*) FROM warehouse.etl_rejections")
        ).scalar_one()

    print(report.summary())
    if outstanding:
        # Informational. Whether the warehouse as a whole is clean is the
        # data-quality suite's question, not this run's.
        print(f"note        {outstanding:,} rejections on record (all runs)")

    if report.rejected:
        print(
            f"\nETL FAILED: {report.rejected} statements could not be "
            "modelled in this run; see warehouse.etl_rejections",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
