"""``python -m pipeline.dq`` — run the warehouse data-quality checks.

Invoked by ``make dq``. Deliberately separate from the ETL: coupling them
would make a data-quality failure look like a broken load, and would give
the ETL's exit code two meanings. The documented sequence is
``make etl && make dq``, and the gate runs both.

Exits non-zero if any ABSOLUTE check fails. REPORTED checks are printed
and never fail the run.
"""

from __future__ import annotations

import argparse
import sys

from app.db import transaction
from pipeline.quality import failures, run_checks


def build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        prog="python -m pipeline.dq",
        description="Run data-quality checks over the warehouse.",
    )


def main(argv: list[str] | None = None) -> int:
    """Run every check and report. Non-zero if an absolute check failed."""
    build_parser().parse_args(argv)
    with transaction() as connection:
        results = run_checks(connection)

    print("data quality")
    for result in results:
        print(result.line())

    broken = failures(results)
    if broken:
        names = ", ".join(result.name for result in broken)
        print(f"\nDATA QUALITY FAILED: {names}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
