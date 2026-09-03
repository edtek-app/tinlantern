"""Ground truth for evaluation: what actually happened to each learner.

The outcome is **measured from the generated record**, never assigned
alongside the archetype (ADR-0004). A learner stamped at-risk whose
behaviour was then generated to match would hand M3 a target written into
its own features.

Two signals, both from `RiskSpec`:

* **Score.** The term mean over every *scheduled* item, with missing work
  counted as zero — gradebook semantics. Averaging only over submitted
  work would let a learner who vanished in week five post a respectable
  mean from three early assessments.
* **Collapse.** The longest gap between sessions, measured across all of a
  learner's courses. Gaps come from ``emit.session_windows`` and nowhere
  else, so the definition of a gap lives in one place rather than being
  re-derived from the statement stream.

The underlying measurements are kept alongside the booleans so `evals/`
can analyse near-threshold cases instead of only counting fires.

This file is the sidecar. It must never flow through the ingestion API or
the warehouse — ADR-0002, enforced by the leakage guard from M3 onward.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.xapi import VERB_IRIS, Statement
from data.generator.calendar import CourseSchedule, term_bounds
from data.generator.config import CohortConfig
from data.generator.course import CourseStructure, build_courses
from data.generator.emit import session_windows
from data.generator.roster import Learner, build_roster
from data.generator.stream import learner_statements

#: Verbs whose results count toward a learner's term mean.
GRADED_VERBS: frozenset[str] = frozenset({"passed", "failed", "submitted"})

_SECONDS_PER_DAY = 86_400.0


@dataclass(frozen=True, slots=True)
class GroundTruth:
    """One learner's archetype and measured outcome.

    Attributes:
        learner_identifier: The learner's opaque account name.
        archetype: The behavioural generator they were assigned.
        term_mean_score: Mean scaled score over all scheduled items,
            missing work counted as zero.
        scheduled_items: How many items were due across all courses.
        completed_items: How many the learner actually turned in.
        longest_gap_days: Longest stretch with no session, in days.
        longest_gap_courses: Course keys bounding that gap.
        failed_term: Whether the mean fell below ``pass_threshold``.
        collapsed: Whether the longest gap reached ``collapse_gap_days``.
        at_risk: Either signal firing.
    """

    learner_identifier: str
    archetype: str
    term_mean_score: float
    scheduled_items: int
    completed_items: int
    longest_gap_days: float
    longest_gap_courses: tuple[str, ...]
    failed_term: bool
    collapsed: bool
    at_risk: bool


def _final_scores(statements: tuple[Statement, ...]) -> dict[str, float]:
    """Last graded result per activity — a retake replaces its predecessor.

    Statements arrive in chronological order, so later results overwrite
    earlier ones, which is what a gradebook does with a resit.
    """
    graded = {VERB_IRIS[name] for name in GRADED_VERBS}
    scores: dict[str, float] = {}
    for statement in statements:
        if statement.verb.id not in graded:
            continue
        if statement.result is None or statement.result.score is None:
            continue
        scaled = statement.result.score.scaled
        if scaled is not None:
            scores[statement.object_.id] = scaled
    return scores


def _longest_gap(
    config: CohortConfig, learner: Learner, courses: tuple[CourseStructure, ...]
) -> tuple[float, tuple[str, ...]]:
    """Longest stretch with no session, across every course, in days.

    Sessions come from ``session_windows`` so the gap definition is not
    duplicated here. The stretch from the last session to the end of term
    counts: a learner who stops in week five has their disengagement
    entirely in that trailing gap, and ignoring it would make the clearest
    collapse in the dataset invisible. The leading gap counts too, for a
    learner who never really starts.
    """
    start, end = term_bounds(config.term)
    openings: list[tuple[datetime, str]] = sorted(
        (window.opened, course.key)
        for course in courses
        for window in session_windows(config, learner, course)
    )

    if not openings:
        return (end - start).total_seconds() / _SECONDS_PER_DAY, tuple(
            course.key for course in courses
        )

    longest = 0.0
    bounding: tuple[str, ...] = ()

    leading = (openings[0][0] - start).total_seconds()
    if leading > longest:
        longest, bounding = leading, (openings[0][1],)

    for (earlier, before), (later, after) in zip(openings, openings[1:], strict=False):
        span = (later - earlier).total_seconds()
        if span > longest:
            longest = span
            bounding = (before,) if before == after else (before, after)

    trailing = (end - openings[-1][0]).total_seconds()
    if trailing > longest:
        longest, bounding = trailing, (openings[-1][1],)

    return longest / _SECONDS_PER_DAY, bounding


def derive_ground_truth(
    config: CohortConfig,
    learner: Learner,
    courses: tuple[CourseStructure, ...],
    schedules: tuple[CourseSchedule, ...],
    statements: tuple[Statement, ...],
) -> GroundTruth:
    """Measure one learner's outcome from the record they generated.

    Args:
        config: The cohort configuration, carrying the risk thresholds.
        learner: The learner being measured.
        courses: Every course structure in the cohort.
        schedules: Their schedules, in the same order.
        statements: That learner's complete statement stream.

    Returns:
        The learner's ground truth, measurements and verdicts together.
    """
    scheduled = sum(len(schedule.deadlines()) for schedule in schedules)
    scores = _final_scores(statements)
    # Missing work scores zero: the mean divides by everything that was
    # DUE, not by everything that was handed in.
    mean = sum(scores.values()) / scheduled if scheduled else 0.0

    gap_days, gap_courses = _longest_gap(config, learner, courses)
    failed = mean < config.risk.pass_threshold
    collapsed = gap_days >= config.risk.collapse_gap_days

    return GroundTruth(
        learner_identifier=learner.identifier,
        archetype=learner.archetype,
        term_mean_score=round(mean, 4),
        scheduled_items=scheduled,
        completed_items=len(scores),
        longest_gap_days=round(gap_days, 3),
        longest_gap_courses=gap_courses,
        failed_term=failed,
        collapsed=collapsed,
        at_risk=failed or collapsed,
    )


def derive_cohort_truth(config: CohortConfig) -> tuple[GroundTruth, ...]:
    """Measure every learner in a cohort.

    Regenerates each learner's statements, so this is the expensive path;
    the writer reuses the streams it already has instead.
    """
    from data.generator.calendar import build_schedule

    courses = build_courses(config)
    schedules = build_schedule(config)
    return tuple(
        derive_ground_truth(
            config,
            learner,
            courses,
            schedules,
            learner_statements(config, learner, courses, schedules),
        )
        for learner in build_roster(config)
    )


def at_risk_rate(truths: tuple[GroundTruth, ...]) -> float:
    """Share of a cohort measured at risk.

    An output to inspect, never a target to hit (ADR-0004).
    """
    return sum(1 for truth in truths if truth.at_risk) / len(truths) if truths else 0.0
