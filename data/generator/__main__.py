"""Command line entry point: ``python -m data.generator``.

Invoked by ``make seed`` from the repository root. The generator is
development tooling and is never installed (see ``data/__init__.py``), so
it runs as a module from the working tree rather than as a console script.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from data.generator.config import load_config
from data.generator.writer import write_cohort

#: Preferred configuration, then the committed default. A working
#: ``cohort.yaml`` is gitignored, so a fresh checkout falls back to the
#: example and ``make seed`` works with no setup.
CONFIG_CANDIDATES = (
    Path("data/generator/cohort.yaml"),
    Path("data/generator/cohort.example.yaml"),
)

DEFAULT_OUTPUT = Path("data/output")


def _default_config() -> Path:
    for candidate in CONFIG_CANDIDATES:
        if candidate.is_file():
            return candidate
    raise SystemExit(
        "no cohort configuration found; expected one of: "
        + ", ".join(str(path) for path in CONFIG_CANDIDATES)
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        prog="python -m data.generator",
        description="Generate a synthetic xAPI cohort and its ground truth.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="cohort YAML (default: cohort.yaml, else cohort.example.yaml)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"output directory (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="override the configured seed, for searching cohorts",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Generate a cohort and report what was written."""
    args = build_parser().parse_args(argv)
    config_path = args.config or _default_config()
    config = load_config(config_path)
    if args.seed is not None:
        config = config.model_copy(update={"seed": args.seed})

    print(f"config  {config_path}  (seed {config.seed})")
    result = write_cohort(config, args.out)

    megabytes = result.bytes_written() / 1_048_576
    at_risk_share = result.at_risk / result.learners if result.learners else 0.0
    print(
        f"wrote   {result.statements:,} statements for {result.learners} learners\n"
        f"        {result.at_risk} at risk ({at_risk_share:.0%})\n"
        f"        {megabytes:.1f} MB in {result.seconds:.1f}s -> {result.directory}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
