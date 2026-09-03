"""Serialising a cohort to disk.

Two artifacts plus a manifest, all newline-delimited JSON:

* ``statements.ndjson`` — the event stream, one xAPI statement per line.
* ``ground_truth.ndjson`` — the evaluation sidecar, one learner per line.
  It must never flow through the ingestion API or the warehouse
  (ADR-0002); it exists for ``evals/`` alone.
* ``manifest.json`` — what this cohort is and what produced it.

**Generation streams per learner.** One learner's statements are built,
written, measured, and discarded before the next learner starts, so a
120-learner cohort's ~160k statements never exist in memory at once. That
also makes the no-regeneration requirement structural: the sidecar is
derived from exactly the objects that were written, not from a second
generation pass. ``truth.derive_cohort_truth`` regenerates everything and
is deliberately not used here; a test asserts each learner's stream is
built exactly once.

Output is byte-identical across runs for a given seed and generator code
(the manifest's timestamp excepted, which is the point of a timestamp):
JSON keys are sorted and separators fixed.
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from data.generator.calendar import build_schedule
from data.generator.config import CohortConfig
from data.generator.course import build_courses
from data.generator.roster import build_roster
from data.generator.stream import learner_statements
from data.generator.truth import derive_ground_truth

STATEMENTS_FILE = "statements.ndjson"
GROUND_TRUTH_FILE = "ground_truth.ndjson"
MANIFEST_FILE = "manifest.json"

# Compact and stable: sorted keys and no incidental whitespace, so two runs
# of the same seed produce identical bytes.
_JSON = {"sort_keys": True, "separators": (",", ":")}


@dataclass(frozen=True, slots=True)
class WriteResult:
    """What a generation run produced."""

    directory: Path
    learners: int
    statements: int
    at_risk: int
    seconds: float

    @property
    def statements_path(self) -> Path:
        return self.directory / STATEMENTS_FILE

    @property
    def ground_truth_path(self) -> Path:
        return self.directory / GROUND_TRUTH_FILE

    @property
    def manifest_path(self) -> Path:
        return self.directory / MANIFEST_FILE

    def bytes_written(self) -> int:
        """Total size of the generated artifacts."""
        return sum(
            path.stat().st_size
            for path in (
                self.statements_path,
                self.ground_truth_path,
                self.manifest_path,
            )
        )


def _git_state() -> dict[str, str | bool | None]:
    """Return the generator's commit and dirty flag.

    Keys are always present. ``None`` means "not a git checkout" or "git
    unavailable" — an explicit null, rather than an absent key that a
    future reader would mistake for an older manifest format.
    """

    def _run(*args: str) -> str | None:
        try:
            result = subprocess.run(
                args, capture_output=True, text=True, timeout=10, check=False
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return result.stdout.strip() if result.returncode == 0 else None

    commit = _run("git", "rev-parse", "HEAD")
    status = _run("git", "status", "--porcelain")
    return {
        "commit": commit,
        # ADR-0003's guarantee is seed AND code, so a dirty tree means the
        # cohort is not reproducible from the commit alone.
        "dirty": None if status is None else bool(status),
    }


def write_cohort(config: CohortConfig, directory: Path) -> WriteResult:
    """Generate a cohort and write it to ``directory``.

    Args:
        config: The cohort configuration.
        directory: Destination, created if absent. Existing artifacts are
            overwritten.

    Returns:
        Counts, paths, and elapsed time for the run.
    """
    started = time.perf_counter()
    directory.mkdir(parents=True, exist_ok=True)

    courses = build_courses(config)
    schedules = build_schedule(config)
    roster = build_roster(config)

    statements_written = 0
    at_risk = 0

    with (
        (directory / STATEMENTS_FILE).open("w", encoding="utf-8") as statements_file,
        (directory / GROUND_TRUTH_FILE).open("w", encoding="utf-8") as truth_file,
    ):
        for learner in roster:
            statements = learner_statements(config, learner, courses, schedules)
            for statement in statements:
                payload = statement.model_dump(
                    by_alias=True, exclude_none=True, mode="json"
                )
                statements_file.write(json.dumps(payload, **_JSON) + "\n")
            statements_written += len(statements)

            # Measured from the statements just written — never regenerated.
            truth = derive_ground_truth(config, learner, courses, schedules, statements)
            truth_file.write(json.dumps(asdict(truth), **_JSON) + "\n")
            at_risk += truth.at_risk

    seconds = time.perf_counter() - started
    result = WriteResult(
        directory=directory,
        learners=len(roster),
        statements=statements_written,
        at_risk=at_risk,
        seconds=seconds,
    )
    _write_manifest(config, result)
    return result


def _write_manifest(config: CohortConfig, result: WriteResult) -> None:
    """Record what this cohort is and what produced it.

    The full resolved configuration is embedded, not just the seed: a
    working ``cohort.yaml`` is gitignored, so the seed alone would not let
    anyone else reproduce a run someone did locally.
    """
    manifest = {
        "generated_at": datetime.now(UTC).isoformat(),
        "generator": _git_state(),
        "seed": config.seed,
        "config": config.model_dump(mode="json"),
        "counts": {
            "learners": result.learners,
            "statements": result.statements,
            "at_risk": result.at_risk,
        },
        "files": {
            "statements": STATEMENTS_FILE,
            "ground_truth": GROUND_TRUTH_FILE,
        },
    }
    result.manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
