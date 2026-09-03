"""Tests for the seeded clock.

The skew and clustering assertions run over a large fixed-seed sample and
compare counts. Because the RNG is seeded, these are deterministic checks
on a known sample — not probabilistic ones that could flake in CI.
"""

from collections import Counter
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from data.generator.calendar import build_schedule, term_bounds
from data.generator.clock import (
    HOUR_WEIGHTS,
    WEEKDAY_WEIGHTS,
    activity_weight,
    sample_before_deadline,
    sample_in_term,
)
from data.generator.config import CohortConfig, load_config
from data.generator.rng import stream_rng

pytestmark = pytest.mark.m0

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "data" / "generator" / "cohort.example.yaml"

SAMPLE_SIZE = 4_000
DST_GAP_DAY = "2026-03-08"


@pytest.fixture
def config() -> CohortConfig:
    return load_config(EXAMPLE)


def sample_term(config: CohortConfig, *, learner: int = 1, count: int = SAMPLE_SIZE):
    start, end = term_bounds(config.term)
    rng = stream_rng(config.seed, "timestamps", learner)
    return [sample_in_term(rng, start, end, config.term.timezone) for _ in range(count)]


# --------------------------------------------------------------------------
# Shape of the output
# --------------------------------------------------------------------------


def test_samples_are_utc_aware_and_inside_the_term(config: CohortConfig) -> None:
    start, end = term_bounds(config.term)
    for moment in sample_term(config, count=200):
        assert moment.tzinfo is not None
        assert str(moment.tzinfo) == "UTC"
        assert start <= moment < end


def test_weights_are_well_formed() -> None:
    assert len(HOUR_WEIGHTS) == 24
    assert len(WEEKDAY_WEIGHTS) == 7
    assert all(0.0 <= w <= 1.0 for w in HOUR_WEIGHTS + WEEKDAY_WEIGHTS)


def test_activity_weight_rejects_naive_datetimes(config: CohortConfig) -> None:
    from datetime import datetime

    with pytest.raises(ValueError, match="timezone-aware"):
        activity_weight(datetime(2026, 3, 1, 12, 0), config.term.timezone)


# --------------------------------------------------------------------------
# Skew — the behaviour that makes the data realistic
# --------------------------------------------------------------------------


def test_evening_activity_dominates_the_small_hours(config: CohortConfig) -> None:
    tz = ZoneInfo(config.term.timezone)
    hours = Counter(m.astimezone(tz).hour for m in sample_term(config))
    evening = sum(hours[h] for h in range(18, 23))
    small_hours = sum(hours[h] for h in range(2, 6))
    assert evening > 5 * small_hours, f"evening={evening} small_hours={small_hours}"


def test_weekends_are_quieter_than_weekdays(config: CohortConfig) -> None:
    tz = ZoneInfo(config.term.timezone)
    days = Counter(m.astimezone(tz).weekday() for m in sample_term(config))
    weekday_mean = sum(days[d] for d in range(5)) / 5
    weekend_mean = sum(days[d] for d in (5, 6)) / 2
    assert weekend_mean < weekday_mean


def test_every_hour_remains_reachable(config: CohortConfig) -> None:
    """Skew, not censorship: a 03:00 session is rare, never impossible."""
    tz = ZoneInfo(config.term.timezone)
    hours = {m.astimezone(tz).hour for m in sample_term(config)}
    assert len(hours) == 24


# --------------------------------------------------------------------------
# Deadline clustering
# --------------------------------------------------------------------------


def test_activity_clusters_before_a_deadline(config: CohortConfig) -> None:
    start, _ = term_bounds(config.term)
    due = build_schedule(config)[0].deadlines()[4].due
    rng = stream_rng(config.seed, "timestamps", 2)
    samples = [
        sample_before_deadline(rng, due, start, config.term.timezone)
        for _ in range(SAMPLE_SIZE)
    ]

    assert all(start <= m < due for m in samples)
    final_day = sum(1 for m in samples if due - m <= timedelta(hours=24))
    earlier = sum(
        1 for m in samples if timedelta(hours=48) < due - m <= timedelta(hours=72)
    )
    assert final_day > earlier, f"final_day={final_day} earlier={earlier}"


def test_deadline_sampling_respects_the_term_start(config: CohortConfig) -> None:
    """A deadline early in the term must not pull samples before it begins."""
    start, _ = term_bounds(config.term)
    due = start + timedelta(hours=6)
    rng = stream_rng(config.seed, "timestamps", 3)
    assert all(
        start <= sample_before_deadline(rng, due, start, config.term.timezone) < due
        for _ in range(500)
    )


def test_invalid_distribution_parameters_are_rejected(config: CohortConfig) -> None:
    start, end = term_bounds(config.term)
    rng = stream_rng(config.seed, "timestamps", 4)
    with pytest.raises(ValueError, match="must be positive"):
        sample_before_deadline(
            rng, end, start, config.term.timezone, mean_hours_before=0
        )


# --------------------------------------------------------------------------
# Daylight saving
# --------------------------------------------------------------------------


def test_no_sample_lands_in_the_nonexistent_local_hour(config: CohortConfig) -> None:
    """Sampling in UTC makes the spring-forward gap unreachable by design."""
    tz = ZoneInfo(config.term.timezone)
    for moment in sample_term(config):
        local = moment.astimezone(tz)
        if str(local.date()) == DST_GAP_DAY:
            assert local.hour != 2


# --------------------------------------------------------------------------
# Reproducibility
# --------------------------------------------------------------------------


def test_same_seed_reproduces_the_same_timestamps(config: CohortConfig) -> None:
    assert sample_term(config, count=100) == sample_term(config, count=100)


def test_different_seed_produces_different_timestamps(config: CohortConfig) -> None:
    """Unlike the structure, the clock genuinely depends on the seed."""
    other = config.model_copy(update={"seed": config.seed + 1})
    assert sample_term(config, count=100) != sample_term(other, count=100)


def test_learners_have_independent_timelines(config: CohortConfig) -> None:
    assert sample_term(config, learner=1, count=100) != sample_term(
        config, learner=2, count=100
    )


def test_bounds_must_be_aware_and_ordered(config: CohortConfig) -> None:
    start, end = term_bounds(config.term)
    rng = stream_rng(config.seed, "timestamps", 5)
    with pytest.raises(ValueError, match="must fall after"):
        sample_in_term(rng, end, start, config.term.timezone)
    with pytest.raises(ValueError, match="timezone-aware"):
        sample_in_term(
            rng,
            start.replace(tzinfo=None),
            end.replace(tzinfo=None),
            config.term.timezone,
        )
