"""Tests for the six behavioural generators.

Each archetype's defining property from DESIGN.md is asserted here. That is
the point of the file: without these, the archetypes are six names and a
table of numbers, and a regression that flattened `disengaging` into a
straight line would pass every other test in the suite while quietly
destroying the dataset the M3 model is supposed to learn from.
"""

import pytest

from data.generator.archetypes import (
    PROFILES,
    ArchetypeProfile,
    EngagementCurve,
    ScoreProfile,
    profile,
)
from data.generator.config import ARCHETYPES
from data.generator.rng import stream_rng

pytestmark = pytest.mark.m0

TERM = [index / 20 for index in range(21)]


def curve_of(name: str) -> EngagementCurve:
    return PROFILES[name].engagement


def scores(name: str, *, progress: float, count: int = 3_000) -> list[float]:
    rng = stream_rng(11, "scores", name)
    return [PROFILES[name].score.sample(rng, progress) for _ in range(count)]


def mean(values: list[float]) -> float:
    return sum(values) / len(values)


# --------------------------------------------------------------------------
# Table integrity
# --------------------------------------------------------------------------


def test_every_configured_archetype_has_a_profile() -> None:
    assert set(PROFILES) == set(ARCHETYPES)


def test_profile_lookup_rejects_unknown_names() -> None:
    with pytest.raises(KeyError, match="unknown archetype"):
        profile("overachiever")


def test_every_profile_has_a_summary() -> None:
    assert all(PROFILES[name].summary for name in PROFILES)


def test_curves_stay_in_range_across_the_whole_term() -> None:
    for name in PROFILES:
        assert all(0.0 <= curve_of(name).at(t) <= 1.0 for t in TERM)


# --------------------------------------------------------------------------
# Defining properties, one archetype at a time
# --------------------------------------------------------------------------


def test_thriving_stays_high_and_steady() -> None:
    curve = curve_of("thriving")
    assert curve.minimum() >= 0.80
    assert curve.maximum() - curve.minimum() <= 0.15


def test_coasting_is_regular_but_minimal() -> None:
    """Low engagement throughout, and middling scores — not failing ones."""
    curve = curve_of("coasting")
    assert curve.maximum() <= 0.50
    assert curve.maximum() - curve.minimum() <= 0.15
    assert 0.6 <= mean(scores("coasting", progress=0.5)) <= 0.8


def test_procrastinator_clusters_at_deadlines_and_is_volatile() -> None:
    procrastinator = PROFILES["procrastinator"]
    assert procrastinator.deadline_affinity == max(
        p.deadline_affinity for p in PROFILES.values()
    )
    assert procrastinator.late_night_bias == max(
        p.late_night_bias for p in PROFILES.values()
    )
    assert procrastinator.score.spread == max(p.score.spread for p in PROFILES.values())


def test_disengaging_decays_across_the_term() -> None:
    """The classic early-alert target: normal start, widening gaps."""
    curve = curve_of("disengaging")
    values = [curve.at(t) for t in TERM]
    assert values == sorted(values, reverse=True), "engagement must not rise"
    assert curve.at(1.0) < curve.at(0.0) / 4


def test_struggling_sustains_effort_but_scores_lowest() -> None:
    curve = curve_of("struggling")
    assert curve.minimum() >= 0.60, "effort is consistent, not absent"
    lowest = min(PROFILES, key=lambda name: PROFILES[name].score.mean)
    assert lowest == "struggling"
    assert PROFILES["struggling"].attempt_persistence == max(
        p.attempt_persistence for p in PROFILES.values()
    )


def test_recovering_dips_then_inflects_upward() -> None:
    """Tests that an alert can clear — the case a static model gets wrong."""
    curve = curve_of("recovering")
    trough = min(TERM, key=curve.at)
    assert 0.2 < trough < 0.7, f"trough should be mid-term, got {trough}"
    assert curve.at(trough) < curve.at(0.0)
    assert curve.at(1.0) > curve.at(0.0)


def test_only_recovering_and_struggling_improve_over_the_term() -> None:
    improving = {name for name in PROFILES if PROFILES[name].score.trend > 0}
    assert improving == {"recovering", "struggling"}


def test_scores_improve_for_recovering_learners() -> None:
    """The trend has to show up in drawn scores, not just in the table."""
    early = mean(scores("recovering", progress=0.0))
    late = mean(scores("recovering", progress=1.0))
    assert late - early > 0.15, f"early={early:.3f} late={late:.3f}"


def test_scores_decline_for_disengaging_learners() -> None:
    early = mean(scores("disengaging", progress=0.0))
    late = mean(scores("disengaging", progress=1.0))
    assert early - late > 0.10, f"early={early:.3f} late={late:.3f}"


# --------------------------------------------------------------------------
# EngagementCurve
# --------------------------------------------------------------------------


def test_curve_interpolates_between_points() -> None:
    curve = EngagementCurve(((0.0, 0.0), (1.0, 1.0)))
    assert curve.at(0.25) == pytest.approx(0.25)
    assert curve.at(0.0) == 0.0
    assert curve.at(1.0) == 1.0


@pytest.mark.parametrize(
    "points",
    [
        (((0.0, 0.5),)),  # single point
        (((0.0, 0.5), (0.5, 0.5))),  # does not reach term end
        (((0.2, 0.5), (1.0, 0.5))),  # does not start at term start
        (((0.0, 0.5), (0.5, 0.5), (0.3, 0.5), (1.0, 0.5))),  # unsorted
        (((0.0, 0.5), (0.5, 0.5), (0.5, 0.4), (1.0, 0.5))),  # duplicate progress
        (((0.0, 1.5), (1.0, 0.5))),  # intensity out of range
    ],
)
def test_malformed_curves_are_rejected(points: tuple) -> None:
    with pytest.raises(ValueError):
        EngagementCurve(points)


@pytest.mark.parametrize("progress", [-0.01, 1.01, 2.0])
def test_curve_rejects_progress_outside_the_term(progress: float) -> None:
    with pytest.raises(ValueError, match=r"\[0, 1\]"):
        curve_of("thriving").at(progress)


# --------------------------------------------------------------------------
# ScoreProfile
# --------------------------------------------------------------------------


def test_scores_are_clamped_into_range() -> None:
    """A wide spread must not produce a scaled score outside [0, 1]."""
    wide = ScoreProfile(mean=0.5, spread=0.9)
    rng = stream_rng(11, "scores", "clamp")
    assert all(0.0 <= wide.sample(rng, 0.5) <= 1.0 for _ in range(2_000))


@pytest.mark.parametrize(
    "kwargs",
    [
        {"mean": 1.4, "spread": 0.1},
        {"mean": -0.1, "spread": 0.1},
        {"mean": 0.5, "spread": 0.0},
        {"mean": 0.5, "spread": -0.2},
    ],
)
def test_malformed_score_profiles_are_rejected(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        ScoreProfile(**kwargs)


def test_score_sampling_is_reproducible() -> None:
    assert scores("thriving", progress=0.5, count=50) == scores(
        "thriving", progress=0.5, count=50
    )


# --------------------------------------------------------------------------
# ArchetypeProfile
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field", ["deadline_affinity", "late_night_bias", "attempt_persistence"]
)
def test_profile_weights_must_be_fractions(field: str) -> None:
    kwargs = {
        "name": "test",
        "summary": "test",
        "engagement": EngagementCurve(((0.0, 0.5), (1.0, 0.5))),
        "score": ScoreProfile(mean=0.5, spread=0.1),
        "deadline_affinity": 0.5,
        "late_night_bias": 0.5,
        "attempt_persistence": 0.5,
        field: 1.5,
    }
    with pytest.raises(ValueError, match=field):
        ArchetypeProfile(**kwargs)


def test_no_archetype_returns_an_outcome() -> None:
    """Outcome is measured from the record, never assigned here (ADR-0004)."""
    for name in PROFILES:
        assert not hasattr(PROFILES[name], "outcome")
        assert not hasattr(PROFILES[name], "at_risk")
