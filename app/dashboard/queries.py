"""Warehouse reads behind the dashboard.

Separate from the HTTP layer so the shape of an answer can be tested
without a client, and so a second consumer at M6 gets the same numbers
rather than its own reimplementation.

**Everything touching `risk_score` scopes to one `model_version`.** That
table accumulates a row per learner per scoring run (ADR-0007), so an
unscoped count returns rows rather than learners — it returned 76 for a
120-learner cohort and cost three eval runs before anyone noticed the
number exceeded the population. The scoping is here, once, rather than
in each caller.

**The trend is ENGAGEMENT over time, never risk over time.** Only one
`window_close` exists, so a risk trend would be a single point, or a
line drawn through repeated scoring runs of the same window — the same
double count wearing a chart. Engagement is real, is what a director
actually asks about ("is the cohort tailing off?"), and is named that
way in every field so it cannot be read as the other thing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import Connection, text

from ml.src.model import ALERT_THRESHOLD


#: Bin edges as fractions of the alert threshold and of the span above
#: it. Derived rather than written alongside `ALERT_THRESHOLD`, so
#: moving the threshold moves the bins and "alerted" keeps meaning one
#: thing across the API, the model, and the UI.
def bin_edges(threshold: float = ALERT_THRESHOLD) -> tuple[float, ...]:
    """The distribution's boundaries, derived from the alert threshold.

    Four bins: comfortably below, approaching, alerted, and high. The
    third edge IS the threshold, so the alerted bin is exactly the set
    of learners the model flags — a chart whose bins disagreed with the
    flag would make the two tell different stories about one learner.
    """
    return (0.0, threshold / 2, threshold, (1.0 + threshold) / 2, 1.0)


#: Bins are labelled by their EDGES, not by words.
#:
#: They were "low / approaching / alerted / high", and the bin named
#: "alerted" held 2 learners directly beneath a headline reading "38
#: flagged" — because `alerted` and `high` are BOTH above the threshold
#: and 2 + 36 = 38. Any pair of words invites a reader to match one
#: against the headline and find a number that disagrees; a range
#: cannot, because it describes the interval rather than naming a
#: state. It is also the most honest description of what these are:
#: intervals derived from `ALERT_THRESHOLD`.
#:
#: Which bins are alerted is carried by colour instead — the status
#: hue, the same one the headline uses.
def bin_labels(threshold: float = ALERT_THRESHOLD) -> tuple[str, ...]:
    """Each bin named by the interval it covers."""
    edges = bin_edges(threshold)
    return tuple(
        f"{edges[index]:.2f}–{edges[index + 1]:.2f}" for index in range(len(edges) - 1)
    )


#: How many bins there are. The names come from `bin_labels`.
BIN_COUNT = 4


@dataclass(frozen=True, slots=True)
class Bin:
    """One bar of the risk distribution."""

    label: str
    lower: float
    upper: float
    learners: int


@dataclass(frozen=True, slots=True)
class CohortOverview:
    """What a program director sees first."""

    model_version: str
    window_close: date
    threshold: float
    learners: int
    alerted: int
    distribution: tuple[Bin, ...]


@dataclass(frozen=True, slots=True)
class EngagementPoint:
    """Active learners in one ISO week.

    Named for engagement in every field. This is NOT a risk trend and
    the type says so, because a chart labelled one way and read another
    is the misreading a single-window risk line would invite.
    """

    week_start: date
    active_learners: int
    events: int


@dataclass(frozen=True, slots=True)
class Driver:
    """One contribution to a learner's risk."""

    feature: str
    contribution: float


@dataclass(frozen=True, slots=True)
class LearnerDetail:
    """One learner's current score and what moves it."""

    learner_identifier: str
    risk: float
    alerted: bool
    threshold: float
    window_close: date
    model_version: str
    drivers: tuple[Driver, ...]
    caveat: str
    additive: bool


#: `scored_at` is the semantic answer to "which run is newest", but it
#: defaults to `now()`, which is TRANSACTION time — two scoring runs in
#: one transaction, or two within the same clock tick, tie. An
#: undefined tie-break means the dashboard can silently show an older
#: model's scores. `risk_score_key` is a BIGSERIAL, so it breaks the tie
#: by insertion order, the same way `ingest_seq` marks arrival in `raw`
#: (ADR-0005) — an opaque marker, never read as time.
_LATEST_VERSION = text(
    """
    SELECT model_version FROM warehouse.risk_score
    GROUP BY model_version
    ORDER BY max(scored_at) DESC, max(risk_score_key) DESC
    LIMIT 1
    """
)

#: One row per learner for the newest model version — the shape every
#: cohort figure is computed from.
_CURRENT_SCORES = """
    SELECT DISTINCT ON (rs.student_key)
           rs.student_key, rs.risk, rs.alerted, rs.window_close,
           rs.model_version, rs.drivers
    FROM warehouse.risk_score rs
    WHERE rs.model_version = :model_version
    ORDER BY rs.student_key, rs.window_close DESC, rs.scored_at DESC
"""


def latest_model_version(connection: Connection) -> str | None:
    """The most recently scored model version, or None if never scored."""
    return connection.execute(_LATEST_VERSION).scalar()


def cohort_overview(
    connection: Connection, threshold: float = ALERT_THRESHOLD
) -> CohortOverview | None:
    """The cohort's current risk distribution.

    Returns:
        The overview, or None when nothing has been scored — an absent
        cohort is not a cohort of zero learners, and the caller needs to
        tell those apart.
    """
    version = latest_model_version(connection)
    if version is None:
        return None

    rows = connection.execute(
        text(f"SELECT risk, alerted, window_close FROM ({_CURRENT_SCORES}) current"),
        {"model_version": version},
    ).fetchall()
    if not rows:
        return None

    edges = bin_edges(threshold)
    labels = bin_labels(threshold)
    counts = [0] * BIN_COUNT
    for risk, _alerted, _window in rows:
        value = float(risk)
        for index in range(BIN_COUNT):
            upper = edges[index + 1]
            # The last bin is closed at the top so a risk of exactly 1.0
            # lands somewhere rather than being silently dropped.
            if value < upper or index == BIN_COUNT - 1:
                counts[index] += 1
                break

    return CohortOverview(
        model_version=version,
        window_close=rows[0][2].date(),
        threshold=threshold,
        learners=len(rows),
        alerted=sum(1 for _risk, alerted, _window in rows if alerted),
        distribution=tuple(
            Bin(
                label=labels[index],
                lower=edges[index],
                upper=edges[index + 1],
                learners=counts[index],
            )
            for index in range(BIN_COUNT)
        ),
    )


@dataclass(frozen=True, slots=True)
class RankedLearner:
    """One row of the "who should I look at" list."""

    learner_identifier: str
    risk: float
    alerted: bool


@dataclass(frozen=True, slots=True)
class LearnerRanking:
    """The ranked list, and how much of the cohort it covers.

    ``total`` travels with the rows so a caller can say "top 50 of 120"
    rather than implying it is showing everything. A list that silently
    truncates is how a director concludes nobody else needs attention.
    """

    model_version: str
    learners: tuple[RankedLearner, ...]
    total: int


#: Bounded from the start. 120 learners fits in one response today and
#: stops fitting at a cohort size we have not met; a limit added under
#: M6 deployment pressure is a limit designed in a hurry.
DEFAULT_LIMIT = 50


def rank_learners(
    connection: Connection, limit: int = DEFAULT_LIMIT
) -> LearnerRanking | None:
    """Learners by risk, highest first.

    Returns:
        The ranking, or None when nothing has been scored — the same
        distinction between absent and empty the rest of this module
        draws.
    """
    version = latest_model_version(connection)
    if version is None:
        return None

    rows = connection.execute(
        text(
            f"""
            SELECT s.learner_identifier, current.risk, current.alerted
            FROM ({_CURRENT_SCORES}) current
            JOIN warehouse.dim_student s USING (student_key)
            ORDER BY current.risk DESC, s.learner_identifier
            LIMIT :limit
            """
        ),
        {"model_version": version, "limit": limit},
    ).fetchall()

    total = connection.execute(
        text(f"SELECT count(*) FROM ({_CURRENT_SCORES}) current"),
        {"model_version": version},
    ).scalar_one()

    if not total:
        return None

    return LearnerRanking(
        model_version=version,
        learners=tuple(
            RankedLearner(
                learner_identifier=row.learner_identifier,
                risk=float(row.risk),
                alerted=row.alerted,
            )
            for row in rows
        ),
        total=total,
    )


_ENGAGEMENT = text(
    """
    SELECT min(d.full_date) AS week_start,
           count(DISTINCT f.student_key) AS active_learners,
           count(*) AS events
    FROM warehouse.fact_activity f
    JOIN warehouse.dim_date d USING (date_key)
    GROUP BY d.year, d.iso_week
    ORDER BY 1
    """
)


def engagement_trend(connection: Connection) -> tuple[EngagementPoint, ...]:
    """Active learners and events per ISO week.

    Engagement, not risk. Every field says so.
    """
    return tuple(
        EngagementPoint(
            week_start=row.week_start,
            active_learners=row.active_learners,
            events=row.events,
        )
        for row in connection.execute(_ENGAGEMENT)
    )


def learner_detail(
    connection: Connection, identifier: str, threshold: float = ALERT_THRESHOLD
) -> LearnerDetail | None:
    """One learner's current score and drivers.

    Returns:
        The detail, or None when the learner has no score. An unscored
        learner is not a learner with a risk of zero, and returning an
        empty shell would let a dashboard render one as the other.
    """
    version = latest_model_version(connection)
    if version is None:
        return None

    row = connection.execute(
        text(
            f"""
            SELECT s.learner_identifier, current.risk, current.alerted,
                   current.window_close, current.model_version, current.drivers
            FROM ({_CURRENT_SCORES}) current
            JOIN warehouse.dim_student s USING (student_key)
            WHERE s.learner_identifier = :identifier
            """
        ),
        {"model_version": version, "identifier": identifier},
    ).fetchone()
    if row is None:
        return None

    payload = row.drivers or {}
    return LearnerDetail(
        learner_identifier=row.learner_identifier,
        risk=float(row.risk),
        alerted=row.alerted,
        threshold=threshold,
        window_close=row.window_close.date(),
        model_version=row.model_version,
        drivers=tuple(
            Driver(feature=item["feature"], contribution=item["contribution"])
            for item in payload.get("drivers", [])
        ),
        # Carried from the stored payload rather than restated here: the
        # caveat is a fact about how the contributions were computed, and
        # a copy in this module would drift from the one in the payload.
        caveat=payload.get("caveat", ""),
        additive=bool(payload.get("additive", False)),
    )
