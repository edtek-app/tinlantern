"""Realistic timestamp sampling for learner activity.

Learners do not work uniformly around the clock. Evenings carry most of
the traffic, the small hours carry almost none, and weekends sit below
weekdays. Work also clusters ahead of deadlines. This module supplies both
shapes as seeded primitives; which learner uses which is the archetypes'
business, not the clock's.

**Every candidate instant is drawn in UTC.** Local time is computed only to
*evaluate* how likely that instant is — never to construct one. That makes
a nonexistent wall-clock time impossible by construction rather than
something fold logic has to repair afterwards: converting UTC to local
always lands on a real local time, whereas building 02:30 local on a
spring-forward morning would not. See ADR-0003.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

#: Relative weight of each local hour, 00:00 through 23:00. Evening study
#: dominates; 03:00–05:00 is nearly dead.
# fmt: off
# Six hours per row, so the daily shape is readable as a table.
HOUR_WEIGHTS: tuple[float, ...] = (
    0.15, 0.08, 0.04, 0.02, 0.02, 0.03,  # 00-05  overnight
    0.08, 0.18, 0.35, 0.50, 0.55, 0.55,  # 06-11  morning
    0.50, 0.55, 0.60, 0.60, 0.65, 0.80,  # 12-17  afternoon
    0.90, 1.00, 1.00, 0.95, 0.70, 0.40,  # 18-23  evening peak
)
# fmt: on

#: Relative weight per weekday, Monday (0) through Sunday (6).
WEEKDAY_WEIGHTS: tuple[float, ...] = (1.0, 1.0, 1.0, 0.95, 0.85, 0.55, 0.65)

# Rejection sampling gives up after this many tries and takes the best
# candidate it saw. Bounded work, and still fully determined by the RNG.
_MAX_ATTEMPTS = 64

_UTC = ZoneInfo("UTC")


def activity_weight(moment: datetime, timezone: str) -> float:
    """How likely a learner is to be active at this instant, in ``[0, 1]``.

    Args:
        moment: A timezone-aware instant.
        timezone: IANA name of the course's local timezone.

    Returns:
        The product of the hour and weekday weights.

    Raises:
        ValueError: If ``moment`` is naive.
    """
    if moment.tzinfo is None:
        raise ValueError("activity_weight needs a timezone-aware datetime")
    local = moment.astimezone(ZoneInfo(timezone))
    return HOUR_WEIGHTS[local.hour] * WEEKDAY_WEIGHTS[local.weekday()]


def _accept(
    rng: random.Random, candidates: list[datetime], timezone: str
) -> datetime | None:
    """Accept the most recent candidate with probability equal to its weight."""
    moment = candidates[-1]
    return moment if rng.random() < activity_weight(moment, timezone) else None


def _best(candidates: list[datetime], timezone: str) -> datetime:
    """Fall back to the likeliest candidate seen, so sampling always ends."""
    return max(candidates, key=lambda moment: activity_weight(moment, timezone))


def sample_in_term(
    rng: random.Random,
    start: datetime,
    end: datetime,
    timezone: str,
) -> datetime:
    """Sample one background activity instant, skewed toward realistic hours.

    Args:
        rng: A stream RNG from ``data.generator.rng``.
        start: Term start, timezone-aware.
        end: Term end, timezone-aware and after ``start``.
        timezone: IANA name of the course's local timezone.

    Returns:
        A timezone-aware UTC instant within ``[start, end)``.

    Raises:
        ValueError: If the term bounds are naive or not ordered.
    """
    _check_bounds(start, end)
    span = (end - start).total_seconds()
    candidates: list[datetime] = []

    for _ in range(_MAX_ATTEMPTS):
        candidates.append(start + timedelta(seconds=rng.uniform(0.0, span)))
        accepted = _accept(rng, candidates, timezone)
        if accepted is not None:
            return accepted.astimezone(_UTC)

    return _best(candidates, timezone).astimezone(_UTC)


def sample_before_deadline(
    rng: random.Random,
    due: datetime,
    start: datetime,
    timezone: str,
    *,
    mean_hours_before: float = 14.0,
    window_hours: float = 96.0,
) -> datetime:
    """Sample an instant before a deadline, clustered close to it.

    The offset is drawn from an exponential distribution, so most activity
    lands in the final hours while a tail reaches back days — the shape
    deadline-driven work actually has.

    Args:
        rng: A stream RNG from ``data.generator.rng``.
        due: The deadline, timezone-aware.
        start: Earliest permissible instant (the term start), timezone-aware.
        timezone: IANA name of the course's local timezone.
        mean_hours_before: Mean of the exponential offset, in hours.
        window_hours: Hard cap on how far back the offset may reach.

    Returns:
        A timezone-aware UTC instant in ``[start, due)``.

    Raises:
        ValueError: If the bounds are naive or not ordered, or if the
            distribution parameters are not positive.
    """
    _check_bounds(start, due)
    if mean_hours_before <= 0 or window_hours <= 0:
        raise ValueError("mean_hours_before and window_hours must be positive")

    earliest = max(start, due - timedelta(hours=window_hours))
    span = (due - earliest).total_seconds()
    candidates: list[datetime] = []

    for _ in range(_MAX_ATTEMPTS):
        offset = rng.expovariate(1.0 / mean_hours_before) * 3600.0
        # Clamp into the window rather than redraw, which would thin the
        # tail exactly where deadline pressure is most visible.
        offset = min(offset, span)
        candidates.append(due - timedelta(seconds=offset))
        accepted = _accept(rng, candidates, timezone)
        if accepted is not None:
            return accepted.astimezone(_UTC)

    return _best(candidates, timezone).astimezone(_UTC)


def _check_bounds(start: datetime, end: datetime) -> None:
    """Reject naive or inverted bounds before sampling from them."""
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("sampling bounds must be timezone-aware")
    if end <= start:
        raise ValueError(f"end ({end}) must fall after start ({start})")
