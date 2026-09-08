"""Tests for cohort serialisation.

These run against a deliberately tiny cohort. The point is the *shape* of
the artifacts and the properties of the writing, not the volume — a
full-size run takes ~25 seconds and belongs in `make seed`, not the gate.
"""

import json
from pathlib import Path

import pytest

from app.xapi import Statement
from data.generator import writer
from data.generator.config import CohortConfig, load_config
from data.generator.truth import GroundTruth
from data.generator.writer import (
    GROUND_TRUTH_FILE,
    MANIFEST_FILE,
    STATEMENTS_FILE,
    write_cohort,
)
from tests.ferpa import assert_no_identifying_data

pytestmark = pytest.mark.m0

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "data" / "generator" / "cohort.example.yaml"


@pytest.fixture
def config() -> CohortConfig:
    """A small cohort: four learners over a short, sparse term."""
    base = load_config(EXAMPLE)
    small = base.model_copy(deep=True)
    small.learners = 4
    small.activity.sessions_per_week = 1.0
    small.activity.max_events_per_session = 3
    small.courses = base.courses[:1]
    small.courses[0].modules = 3
    return small


@pytest.fixture
def written(config: CohortConfig, tmp_path: Path):
    return write_cohort(config, tmp_path / "out")


def lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


# --------------------------------------------------------------------------
# Artifacts exist and round-trip
# --------------------------------------------------------------------------


def test_all_three_artifacts_are_written(written) -> None:
    assert written.statements_path.is_file()
    assert written.ground_truth_path.is_file()
    assert written.manifest_path.is_file()


def test_every_written_line_revalidates_as_a_statement(written) -> None:
    """Round trip through the actual file, not the in-memory objects.

    Serialisation is where a contract violation would first become
    someone else's problem, so the check reads the bytes back.
    """
    rows = lines(written.statements_path)
    assert len(rows) == written.statements
    for row in rows:
        Statement.model_validate(json.loads(row))


def test_ground_truth_has_one_row_per_learner(written, config) -> None:
    rows = lines(written.ground_truth_path)
    assert len(rows) == config.learners == written.learners
    identifiers = {json.loads(row)["learner_identifier"] for row in rows}
    assert len(identifiers) == config.learners


def test_ground_truth_rows_carry_every_measurement(written) -> None:
    row = json.loads(lines(written.ground_truth_path)[0])
    assert set(row) == set(GroundTruth.__dataclass_fields__)


def test_sidecar_carries_no_statement_data(written) -> None:
    """The sidecar is labels. A second copy of the stream would be a leak."""
    rows = lines(written.ground_truth_path)
    assert rows, (
        "the sidecar is empty, so the loop below never runs and this test "
        "asserts nothing — an absent subject, not a clean one"
    )
    for row in rows:
        parsed = json.loads(row)
        assert "verb" not in parsed
        assert "statements" not in parsed
        assert "timestamp" not in parsed


def test_no_email_shaped_data_in_any_artifact(written) -> None:
    for path in (written.statements_path, written.ground_truth_path):
        assert_no_identifying_data(path.read_text(encoding="utf-8"), path.name)


# --------------------------------------------------------------------------
# The sidecar is derived, not regenerated
# --------------------------------------------------------------------------


def test_each_learner_stream_is_generated_exactly_once(
    config: CohortConfig, tmp_path: Path, monkeypatch
) -> None:
    """The mechanism, not a docstring warning.

    The sidecar must be measured from the very statements that were
    written. Calling `derive_cohort_truth` here — or deriving truth in a
    second pass — would double generation time and, worse, could silently
    diverge from the emitted file if anything about generation changed
    between the passes. A call count proves the property; a source scan
    for a function name would only prove it isn't mentioned.
    """
    calls: list[str] = []
    original = writer.learner_statements

    def counted(cfg, learner, courses, schedules=None):
        calls.append(learner.identifier)
        return original(cfg, learner, courses, schedules)

    monkeypatch.setattr(writer, "learner_statements", counted)
    write_cohort(config, tmp_path / "out")

    assert len(calls) == config.learners
    assert len(set(calls)) == config.learners, f"regenerated: {calls}"


def test_reported_counts_match_the_files(written) -> None:
    assert written.statements == len(lines(written.statements_path))
    assert written.learners == len(lines(written.ground_truth_path))
    at_risk = sum(
        1 for row in lines(written.ground_truth_path) if json.loads(row)["at_risk"]
    )
    assert written.at_risk == at_risk


# --------------------------------------------------------------------------
# Determinism
# --------------------------------------------------------------------------


def test_two_runs_produce_byte_identical_data(
    config: CohortConfig, tmp_path: Path
) -> None:
    """Same seed, same code, same bytes — ADR-0003's guarantee, on disk."""
    first = write_cohort(config, tmp_path / "first")
    second = write_cohort(config, tmp_path / "second")
    for name in (STATEMENTS_FILE, GROUND_TRUTH_FILE):
        assert (first.directory / name).read_bytes() == (
            second.directory / name
        ).read_bytes(), name


def test_manifest_differs_only_by_timestamp(
    config: CohortConfig, tmp_path: Path
) -> None:
    """The manifest is deliberately not byte-stable; that is what it records."""
    first = json.loads(
        (write_cohort(config, tmp_path / "a").manifest_path).read_text("utf-8")
    )
    second = json.loads(
        (write_cohort(config, tmp_path / "b").manifest_path).read_text("utf-8")
    )
    assert first.pop("generated_at") is not None
    assert second.pop("generated_at") is not None
    assert first == second


def test_a_different_seed_changes_the_output(
    config: CohortConfig, tmp_path: Path
) -> None:
    other = config.model_copy(update={"seed": config.seed + 1})
    first = write_cohort(config, tmp_path / "first")
    second = write_cohort(other, tmp_path / "second")
    assert first.statements_path.read_bytes() != second.statements_path.read_bytes()


# --------------------------------------------------------------------------
# Manifest
# --------------------------------------------------------------------------


def test_manifest_embeds_the_full_config(written, config: CohortConfig) -> None:
    """cohort.yaml is gitignored, so the seed alone would not reproduce a run."""
    manifest = json.loads(written.manifest_path.read_text("utf-8"))
    assert manifest["seed"] == config.seed
    assert manifest["config"]["risk"]["pass_threshold"] == config.risk.pass_threshold
    assert manifest["config"]["activity"]["submission_diligence"] == (
        config.activity.submission_diligence
    )
    assert manifest["config"]["archetype_mix"] == config.archetype_mix


def test_manifest_records_generator_state_with_explicit_nulls(written) -> None:
    """A missing key would read as an old format; null reads as unknown."""
    generator = json.loads(written.manifest_path.read_text("utf-8"))["generator"]
    assert set(generator) == {"commit", "dirty"}
    assert "commit" in generator and "dirty" in generator


def test_manifest_counts_match_the_result(written) -> None:
    counts = json.loads(written.manifest_path.read_text("utf-8"))["counts"]
    assert counts["learners"] == written.learners
    assert counts["statements"] == written.statements
    assert counts["at_risk"] == written.at_risk


def test_output_directory_is_created_if_absent(
    config: CohortConfig, tmp_path: Path
) -> None:
    target = tmp_path / "deep" / "nested" / "out"
    assert not target.exists()
    assert write_cohort(config, target).statements_path.is_file()


def test_rerunning_overwrites_rather_than_appends(
    config: CohortConfig, tmp_path: Path
) -> None:
    target = tmp_path / "out"
    first = write_cohort(config, target)
    second = write_cohort(config, target)
    assert len(lines(second.statements_path)) == first.statements


# --------------------------------------------------------------------------
# The command line
# --------------------------------------------------------------------------


def test_cli_parses_the_documented_flags() -> None:
    from data.generator.__main__ import build_parser

    args = build_parser().parse_args(
        ["--config", "c.yaml", "--out", "somewhere", "--seed", "7"]
    )
    assert args.config == Path("c.yaml")
    assert args.out == Path("somewhere")
    assert args.seed == 7


def test_cli_defaults_to_the_output_directory() -> None:
    from data.generator.__main__ import DEFAULT_OUTPUT, build_parser

    args = build_parser().parse_args([])
    assert args.out == DEFAULT_OUTPUT
    assert args.seed is None


def test_make_seed_invokes_the_module() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "python -m data.generator" in makefile


def test_generated_output_is_gitignored() -> None:
    """The generated cohort must never be committable (ADR-0002)."""
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "data/output/" in ignored


def test_artifact_filenames_are_stable() -> None:
    """Downstream milestones will hardcode these; changing one is a break."""
    assert STATEMENTS_FILE == "statements.ndjson"
    assert GROUND_TRUTH_FILE == "ground_truth.ndjson"
    assert MANIFEST_FILE == "manifest.json"
