"""The six behavioural generators (DESIGN.md).

An archetype is a *shape*, not a label. Each one describes how a learner's
engagement moves across the term, how their assessment scores are
distributed, and how strongly their work clusters against deadlines. Risk
then emerges from the behaviour these produce — the M3 model sees the
record, never the archetype (ADR-0004).

Profiles are data rather than functions so they can be compared, printed,
and asserted against. Every defining property in DESIGN.md is pinned by a
test: if `disengaging` stopped declining, that is a silent failure of the
whole dataset, not a cosmetic one.

Nothing here draws an outcome. Whether a learner ends the term at risk is
*measured* from the record they generate, and that derivation lives with
the ground-truth sidecar.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from data.generator.config import ARCHETYPES


@dataclass(frozen=True, slots=True)
class EngagementCurve:
    """Engagement intensity across the term, as a piecewise-linear shape.

    Points are ``(progress, intensity)`` with progress running 0.0 (term
    start) to 1.0 (term end) and intensity in ``[0, 1]``.
    """

    points: tuple[tuple[float, float], ...]

    def __post_init__(self) -> None:
        if len(self.points) < 2:
            raise ValueError("an engagement curve needs at least two points")
        progresses = [progress for progress, _ in self.points]
        if progresses != sorted(progresses) or len(set(progresses)) != len(progresses):
            raise ValueError(f"curve points must strictly increase: {progresses}")
        if progresses[0] != 0.0 or progresses[-1] != 1.0:
            raise ValueError("a curve must span the whole term, 0.0 to 1.0")
        if not all(0.0 <= intensity <= 1.0 for _, intensity in self.points):
            raise ValueError("engagement intensities must fall in [0, 1]")

    def at(self, progress: float) -> float:
        """Interpolate the intensity at a point in the term.

        Args:
            progress: Position in the term, 0.0 to 1.0 inclusive.

        Returns:
            Engagement intensity in ``[0, 1]``.

        Raises:
            ValueError: If progress falls outside ``[0, 1]``.
        """
        if not 0.0 <= progress <= 1.0:
            raise ValueError(f"progress must fall in [0, 1], got {progress}")

        for (left_x, left_y), (right_x, right_y) in zip(
            self.points, self.points[1:], strict=False
        ):
            if left_x <= progress <= right_x:
                span = right_x - left_x
                weight = 0.0 if span == 0 else (progress - left_x) / span
                return left_y + weight * (right_y - left_y)
        return self.points[-1][1]  # pragma: no cover - guarded by __post_init__

    def minimum(self) -> float:
        """Lowest intensity at any declared point."""
        return min(intensity for _, intensity in self.points)

    def maximum(self) -> float:
        """Highest intensity at any declared point."""
        return max(intensity for _, intensity in self.points)


@dataclass(frozen=True, slots=True)
class ScoreProfile:
    """How an archetype's assessment scores are distributed.

    Scores are xAPI *scaled* scores in ``[0, 1]``. ``trend`` shifts the mean
    linearly across the term, so a recovering learner's later work really is
    better rather than merely more frequent.
    """

    mean: float
    spread: float
    trend: float = 0.0

    def __post_init__(self) -> None:
        if not 0.0 <= self.mean <= 1.0:
            raise ValueError(f"score mean must fall in [0, 1], got {self.mean}")
        if self.spread <= 0.0:
            raise ValueError("score spread must be positive")

    def sample(self, rng: random.Random, progress: float) -> float:
        """Draw one scaled score, clamped into ``[0, 1]``.

        Args:
            rng: A ``scores`` stream RNG from ``data.generator.rng``.
            progress: Position in the term, 0.0 to 1.0.

        Returns:
            A scaled score in ``[0, 1]``.
        """
        centre = self.mean + self.trend * progress
        return min(max(rng.gauss(centre, self.spread), 0.0), 1.0)


@dataclass(frozen=True, slots=True)
class ArchetypeProfile:
    """One behavioural generator."""

    name: str
    summary: str
    engagement: EngagementCurve
    score: ScoreProfile
    #: Share of a learner's activity that clusters ahead of deadlines
    #: rather than spreading across the term.
    deadline_affinity: float
    #: Extra pull toward late-night hours, on top of the clock's own skew.
    late_night_bias: float
    #: Tendency to retry an assessment after a poor result.
    attempt_persistence: float

    def __post_init__(self) -> None:
        for field in ("deadline_affinity", "late_night_bias", "attempt_persistence"):
            value = getattr(self, field)
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{field} must fall in [0, 1], got {value}")


#: The six archetypes. Each shape encodes the description in DESIGN.md.
PROFILES: dict[str, ArchetypeProfile] = {
    "thriving": ArchetypeProfile(
        name="thriving",
        summary="steady logins, early submissions, high video completion",
        engagement=EngagementCurve(((0.0, 0.85), (0.5, 0.90), (1.0, 0.88))),
        score=ScoreProfile(mean=0.88, spread=0.07),
        deadline_affinity=0.20,
        late_night_bias=0.05,
        attempt_persistence=0.30,
    ),
    "coasting": ArchetypeProfile(
        name="coasting",
        summary="regular but minimal engagement, middling scores",
        engagement=EngagementCurve(((0.0, 0.45), (0.5, 0.42), (1.0, 0.40))),
        score=ScoreProfile(mean=0.70, spread=0.08),
        deadline_affinity=0.55,
        late_night_bias=0.20,
        attempt_persistence=0.15,
    ),
    "procrastinator": ArchetypeProfile(
        name="procrastinator",
        summary="deadline-clustered bursts, late-night activity, volatile scores",
        engagement=EngagementCurve(((0.0, 0.55), (0.5, 0.55), (1.0, 0.58))),
        score=ScoreProfile(mean=0.72, spread=0.17),
        deadline_affinity=0.90,
        late_night_bias=0.75,
        attempt_persistence=0.35,
    ),
    "disengaging": ArchetypeProfile(
        name="disengaging",
        summary="normal start, then decaying logins and widening gaps",
        engagement=EngagementCurve(
            ((0.0, 0.80), (0.25, 0.62), (0.5, 0.38), (0.75, 0.18), (1.0, 0.06))
        ),
        score=ScoreProfile(mean=0.62, spread=0.13, trend=-0.22),
        deadline_affinity=0.35,
        late_night_bias=0.30,
        attempt_persistence=0.10,
    ),
    "struggling": ArchetypeProfile(
        name="struggling",
        summary="consistent effort, low results, repeated attempts",
        engagement=EngagementCurve(((0.0, 0.72), (0.5, 0.70), (1.0, 0.68))),
        score=ScoreProfile(mean=0.46, spread=0.10, trend=0.04),
        deadline_affinity=0.45,
        late_night_bias=0.35,
        attempt_persistence=0.85,
    ),
    "recovering": ArchetypeProfile(
        name="recovering",
        summary="declines, then inflects upward mid-term",
        engagement=EngagementCurve(
            ((0.0, 0.62), (0.35, 0.26), (0.55, 0.38), (0.75, 0.62), (1.0, 0.76))
        ),
        score=ScoreProfile(mean=0.52, spread=0.12, trend=0.28),
        deadline_affinity=0.50,
        late_night_bias=0.30,
        attempt_persistence=0.60,
    ),
}


def profile(archetype: str) -> ArchetypeProfile:
    """Return the profile for an archetype name.

    Args:
        archetype: One of ``ARCHETYPES``.

    Returns:
        That archetype's behavioural profile.

    Raises:
        KeyError: If the archetype is unknown.
    """
    try:
        return PROFILES[archetype]
    except KeyError:
        raise KeyError(
            f"unknown archetype {archetype!r}; known: {sorted(PROFILES)}"
        ) from None


def _check_profiles_cover_archetypes() -> None:
    """Fail at import if the profile table and the config list diverge."""
    missing = set(ARCHETYPES) - set(PROFILES)
    extra = set(PROFILES) - set(ARCHETYPES)
    if missing or extra:
        raise RuntimeError(
            f"archetype profiles out of step with config: "
            f"missing={sorted(missing)} extra={sorted(extra)}"
        )


_check_profiles_cover_archetypes()
