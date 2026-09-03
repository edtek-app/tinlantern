"""Tests for seed derivation (ADR-0003).

The properties here are what make a cohort reproducible. If any of them
breaks, an M3 evaluation stops being comparable across regenerations and
the M7 demo cohort's narrative students change identity between runs.
"""

import subprocess
import sys
from pathlib import Path

import pytest

from data.generator.rng import STREAMS, derive_seed, stream_rng

pytestmark = pytest.mark.m0

ROOT = Path(__file__).resolve().parents[1]


def test_derivation_is_deterministic() -> None:
    assert derive_seed(7, "timestamps", 42, "analytics-101") == derive_seed(
        7, "timestamps", 42, "analytics-101"
    )


def test_seeds_survive_a_fresh_process() -> None:
    """The salted-hash() trap, checked rather than assumed.

    Python salts string hashing per process, so a hash()-derived seed would
    differ between runs of the same command. blake2b does not. This runs a
    real subprocess with hash randomisation explicitly enabled, because an
    in-process assertion could never catch the bug.
    """
    script = (
        "from data.generator.rng import derive_seed; "
        "print(derive_seed(20260301, 'timestamps', 17, 'analytics-101'))"
    )
    runs = {
        subprocess.run(
            [sys.executable, "-c", script],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=60,
            env={"PYTHONHASHSEED": "random", "PATH": "/usr/bin:/bin"},
            check=True,
        ).stdout.strip()
        for _ in range(3)
    }
    assert len(runs) == 1, f"seed changed between processes: {runs}"
    assert runs != {""}


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ((7, "timestamps", 1), (7, "timestamps", 2)),  # different learner
        ((7, "timestamps", 1), (8, "timestamps", 1)),  # different root seed
        ((7, "timestamps", 1), (7, "scores", 1)),  # different stream
        ((7, "timestamps", 1, "a"), (7, "timestamps", 1, "b")),  # course
    ],
)
def test_distinct_inputs_give_distinct_seeds(left: tuple, right: tuple) -> None:
    assert derive_seed(*left) != derive_seed(*right)


def test_scope_parts_cannot_collide_by_concatenation() -> None:
    """('ab','c') and ('a','bc') must not hash to the same payload."""
    assert derive_seed(7, "timestamps", "ab", "c") != derive_seed(
        7, "timestamps", "a", "bc"
    )


def test_streams_are_independent() -> None:
    """Drawing from one stream must not move another — the whole point."""
    timestamps = stream_rng(7, "timestamps", 1)
    scores_before = stream_rng(7, "scores", 1).random()
    for _ in range(100):
        timestamps.random()
    assert stream_rng(7, "scores", 1).random() == scores_before


def test_learner_streams_are_order_independent() -> None:
    """Adding a learner must not perturb the learners already generated."""
    first_pass = [stream_rng(7, "timestamps", i).random() for i in range(10)]
    second_pass = [stream_rng(7, "timestamps", i).random() for i in range(20)]
    assert second_pass[:10] == first_pass


def test_unknown_stream_is_rejected() -> None:
    """A typo'd stream name would silently create a different stream."""
    with pytest.raises(ValueError, match="unknown stream"):
        derive_seed(7, "timestamp", 1)


def test_nul_in_scope_is_rejected() -> None:
    with pytest.raises(ValueError, match="NUL"):
        derive_seed(7, "timestamps", "course\x00key")


def test_known_streams_cover_the_planned_draws() -> None:
    assert {"archetypes", "timestamps", "scores", "outcomes"} <= STREAMS
