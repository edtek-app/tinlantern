"""The dashboard read API: scoping, bins, labelling, and absence.

Every test seeds the rows it asserts on, and assertions scope to those
rows rather than to table totals.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Connection, text

from app.dashboard.queries import (
    BIN_COUNT,
    bin_edges,
    cohort_overview,
    engagement_trend,
    latest_model_version,
    learner_detail,
    rank_learners,
)
from ml.src.drivers import CONTRIBUTION_CAVEAT
from ml.src.model import ALERT_THRESHOLD

pytestmark = pytest.mark.m5

DRIVERS = {
    "caveat": CONTRIBUTION_CAVEAT,
    "method": "ablation-to-cohort-median",
    "additive": False,
    "drivers": [
        {"feature": "failures", "contribution": 0.31},
        {"feature": "mean_score_in_window", "contribution": 0.18},
    ],
}


def seed_activity_dimensions(connection: Connection) -> None:
    """A course and activity for fact_activity's NOT NULL keys."""
    connection.execute(
        text(
            "INSERT INTO warehouse.dim_course (course_iri, course_slug, title) "
            "VALUES ('urn:c:1', 'c1', 'Course One') "
            "ON CONFLICT (course_iri) DO NOTHING"
        )
    )
    connection.execute(
        text(
            "INSERT INTO warehouse.dim_activity (activity_iri, activity_type, "
            "name, course_key, module_index) SELECT 'urn:a:1', 'module', "
            "'Module 1', course_key, 1 FROM warehouse.dim_course "
            "WHERE course_slug = 'c1' ON CONFLICT (activity_iri) DO NOTHING"
        )
    )


def seed_scores(
    connection: Connection,
    risks: tuple[float, ...],
    model_version: str = "test000",
) -> None:
    """Learners with the given risks, scored under one model version."""
    for index in range(len(risks)):
        connection.execute(
            text(
                "INSERT INTO warehouse.dim_student (learner_identifier, "
                "account_home_page) VALUES (:id, 'https://x.invalid') "
                "ON CONFLICT (learner_identifier) DO NOTHING"
            ),
            {"id": f"s-{index:05d}"},
        )
    connection.execute(
        text(
            """
            INSERT INTO warehouse.risk_score
                (student_key, window_close, model_version, risk, alerted,
                 drivers)
            SELECT s.student_key, TIMESTAMPTZ '2026-02-23Z', :version,
                   r.risk, r.risk >= :threshold, CAST(:drivers AS jsonb)
            FROM warehouse.dim_student s
            JOIN (SELECT * FROM unnest(CAST(:ids AS text[]),
                                       CAST(:risks AS numeric[]))
                  AS t(identifier, risk)) r
              ON r.identifier = s.learner_identifier
            """
        ),
        {
            "version": model_version,
            "threshold": ALERT_THRESHOLD,
            "drivers": json.dumps(DRIVERS),
            "ids": [f"s-{index:05d}" for index in range(len(risks))],
            "risks": list(risks),
        },
    )


@pytest.fixture
def client(test_database: str) -> TestClient:
    from app.main import app

    return TestClient(app)


# --------------------------------------------------------------------------
# Scoping — the defect that cost three eval runs, pinned at the API
# --------------------------------------------------------------------------


def test_the_cohort_counts_learners_not_rows(connection: Connection) -> None:
    """risk_score holds a row per learner PER MODEL VERSION (ADR-0007).

    An unscoped count returns rows: it reported 76 for a 120-learner
    cohort and survived two committed eval reports before anyone noticed
    it exceeded the population. A dashboard is where that error becomes
    a number a director acts on, so the scoping is pinned here too.
    """
    seed_scores(connection, (0.82, 0.11, 0.40), model_version="test000")
    seed_scores(connection, (0.82, 0.11, 0.40), model_version="test001")

    overview = cohort_overview(connection)

    assert overview is not None
    assert overview.learners == 3, (
        "six rows for three learners — either the query is unscoped, or "
        "it is not deduplicating per learner within a version"
    )
    assert overview.model_version == "test001", "the newest scoring run"
    assert sum(item.learners for item in overview.distribution) == 3


def test_a_learner_gets_their_newest_score_only(connection: Connection) -> None:
    seed_scores(connection, (0.11,), model_version="test000")
    seed_scores(connection, (0.82,), model_version="test001")

    detail = learner_detail(connection, "s-00000")

    assert detail is not None
    assert detail.risk == pytest.approx(0.82)
    assert detail.model_version == "test001"


# --------------------------------------------------------------------------
# The ranked list — the way in to a drill-down
# --------------------------------------------------------------------------


def test_learners_are_ranked_by_risk_highest_first(
    connection: Connection,
) -> None:
    """A director's question is "who should I look at", not "list them"."""
    seed_scores(connection, (0.11, 0.91, 0.42))

    ranking = rank_learners(connection)

    assert ranking is not None
    assert [round(item.risk, 2) for item in ranking.learners] == [0.91, 0.42, 0.11]
    assert ranking.total == 3


def test_the_ranking_counts_learners_not_rows(connection: Connection) -> None:
    """The double count, pinned again at a third endpoint.

    risk_score holds a row per learner PER MODEL VERSION (ADR-0007). It
    returned 76 for a 120-learner cohort once and survived two committed
    eval reports; each new consumer gets its own guard rather than
    trusting that the last one covered it.
    """
    seed_scores(connection, (0.11, 0.91), model_version="old")
    seed_scores(connection, (0.11, 0.91), model_version="new")

    ranking = rank_learners(connection)

    assert ranking is not None
    assert ranking.total == 2, "four rows, two learners"
    assert len(ranking.learners) == 2
    assert ranking.model_version == "new"


def test_the_total_travels_with_a_truncated_list(
    connection: Connection,
) -> None:
    """ "Top 3 of 5" is honest; three rows alone implies there are three.

    A list that silently truncates is how a director concludes nobody
    else needs attention.
    """
    seed_scores(connection, (0.9, 0.8, 0.7, 0.6, 0.5))

    ranking = rank_learners(connection, limit=3)

    assert ranking is not None
    assert len(ranking.learners) == 3
    assert ranking.total == 5


def test_an_unscored_cohort_has_no_ranking(connection: Connection) -> None:
    assert rank_learners(connection) is None


def test_the_ranking_endpoint_reports_missing_rather_than_empty(
    client: TestClient,
) -> None:
    response = client.get("/api/learners")

    assert response.status_code == 404
    assert "make score" in response.json()["detail"]


# --------------------------------------------------------------------------
# Bins derived from the threshold, not written beside it
# --------------------------------------------------------------------------


def test_the_newest_version_is_decided_deterministically(
    connection: Connection,
) -> None:
    """`scored_at` defaults to now(), which is TRANSACTION time.

    Two scoring runs inside one transaction — or two within a clock tick
    — carry an identical timestamp, and an undefined tie-break lets the
    dashboard silently show an older model's scores while looking
    current. The BIGSERIAL breaks it by insertion order, the same role
    `ingest_seq` plays in `raw` (ADR-0005).

    Found by this test failing: the first version of the query picked
    the OLDER run.
    """
    seed_scores(connection, (0.11,), model_version="older")
    seed_scores(connection, (0.82,), model_version="newer")

    same_instant = connection.execute(
        text("SELECT count(DISTINCT scored_at) FROM warehouse.risk_score")
    ).scalar()

    assert same_instant == 1, "the premise: both runs share one timestamp"
    assert latest_model_version(connection) == "newer"


def test_the_alerted_bin_edge_is_the_alert_threshold() -> None:
    """A chart with its own edges tells a different story than the flag.

    The third edge IS the threshold, so the alerted bin is exactly the
    set of learners the model flags. Derived, so moving the threshold
    moves the chart rather than leaving them disagreeing.
    """
    assert bin_edges()[2] == ALERT_THRESHOLD
    assert bin_edges(0.6)[2] == 0.6
    assert bin_edges(0.6) != bin_edges(0.35), "edges must follow the threshold"
    assert len(bin_edges()) == BIN_COUNT + 1


def test_no_bin_label_competes_with_the_headline_stat() -> None:
    """Bins are named by their edges so they cannot contradict the stat.

    They were words — and the bin named "alerted" held 2 learners
    directly beneath a headline reading "38 flagged", because `alerted`
    and `high` are BOTH above the threshold. A reader scanning the chart
    plausibly took "2" as the alerted count and found it disagreeing
    with the stat row.

    Any pair of words reintroduces this: one of them will match the
    headline's vocabulary and carry a number that is only part of it. A
    range describes the interval instead of naming a state, so there is
    nothing for a reader to match. Which bins are alerted is carried by
    colour.
    """
    from app.dashboard.queries import bin_labels

    # The words the headline stat uses about the cohort.
    headline = {"alerted", "flagged", "risk", "at-risk", "high", "low"}

    for label in bin_labels():
        words = set(label.replace("–", " ").replace("-", " ").lower().split())
        assert not (words & headline), (
            f"bin label {label!r} borrows the headline's vocabulary. A "
            "reader will match it against 'N flagged' and find a number "
            "that is only part of that total."
        )
        assert any(character.isdigit() for character in label), (
            f"bin label {label!r} is not an interval"
        )


def test_bin_labels_follow_the_threshold() -> None:
    """Renaming by edges keeps them derived, not written down twice."""
    from app.dashboard.queries import bin_labels

    assert bin_labels(0.35)[2].startswith("0.35")
    assert bin_labels(0.60)[2].startswith("0.60")


def test_every_learner_lands_in_exactly_one_bin(connection: Connection) -> None:
    """Including a risk of exactly 1.0, which an open top bin drops."""
    seed_scores(connection, (0.0, 0.1, 0.5, 0.9, 1.0))

    overview = cohort_overview(connection)

    assert overview is not None
    assert sum(item.learners for item in overview.distribution) == 5


def test_the_alerted_count_matches_the_alerted_bins(
    connection: Connection,
) -> None:
    """The two figures on the same screen must agree.

    `alerted` is read from the stored flag; the bins are computed from
    the risk. If they ever disagree, one of them is lying to a director
    looking at both at once.
    """
    seed_scores(connection, (0.05, 0.20, 0.40, 0.95))

    overview = cohort_overview(connection)

    assert overview is not None
    binned = sum(
        item.learners
        for item in overview.distribution
        if item.lower >= overview.threshold
    )
    assert binned == overview.alerted


# --------------------------------------------------------------------------
# Engagement, never risk — label discipline asserted mechanically
# --------------------------------------------------------------------------


def test_the_trend_response_carries_no_risk_shaped_key(
    client: TestClient, committed: Connection
) -> None:
    """Only one window_close exists, so there is no risk trend to draw.

    A single-window risk line would be a point, or a line through
    repeated scoring runs of the same window — the double count wearing
    a chart. Engagement is real and is what a director actually asks.
    The discipline is mechanical because a field renamed in six months
    is how a chart labelled one way starts being read as the other.
    """
    seed_scores(committed, (0.82,))
    seed_activity_dimensions(committed)
    committed.execute(
        text(
            """
            INSERT INTO warehouse.fact_activity
                (statement_id, student_key, course_key, activity_key,
                 date_key, verb, occurred_at, ingest_seq)
            SELECT gen_random_uuid(), s.student_key, c.course_key,
                   a.activity_key, 20260202, 'experienced',
                   TIMESTAMPTZ '2026-02-02Z', 1
            FROM warehouse.dim_student s
            CROSS JOIN (SELECT course_key FROM warehouse.dim_course
                        WHERE course_slug = 'c1') c
            CROSS JOIN (SELECT activity_key FROM warehouse.dim_activity
                        WHERE activity_iri = 'urn:a:1') a
            WHERE s.learner_identifier = 's-00000'
            """
        )
    )

    response = client.get("/api/engagement")

    assert response.status_code == 200
    body = response.json()
    assert body["engagement_trend"], (
        "the trend is empty, so this test would pass against a response "
        "that never contained a series at all"
    )

    serialised = json.dumps(body).lower()
    assert "risk" not in serialised, (
        "a risk-shaped key reached the engagement response — either a "
        "field was renamed, or a risk series was added to an endpoint "
        "whose whole purpose is that no such series exists"
    )


def test_engagement_is_weekly_active_learners(connection: Connection) -> None:
    seed_scores(connection, (0.5,))
    seed_activity_dimensions(connection)
    connection.execute(
        text(
            """
            INSERT INTO warehouse.fact_activity
                (statement_id, student_key, course_key, activity_key,
                 date_key, verb, occurred_at, ingest_seq)
            SELECT gen_random_uuid(), s.student_key, c.course_key,
                   a.activity_key, d.date_key, 'experienced', d.full_date, 1
            FROM warehouse.dim_student s
            CROSS JOIN (SELECT course_key FROM warehouse.dim_course
                        WHERE course_slug = 'c1') c
            CROSS JOIN (SELECT activity_key FROM warehouse.dim_activity
                        WHERE activity_iri = 'urn:a:1') a
            CROSS JOIN (SELECT date_key, full_date FROM warehouse.dim_date
                        WHERE date_key IN (20260202, 20260209)) d
            WHERE s.learner_identifier = 's-00000'
            """
        )
    )

    points = engagement_trend(connection)

    assert len(points) == 2, "two ISO weeks"
    assert all(point.active_learners == 1 for point in points)
    assert all(point.events == 1 for point in points)


# --------------------------------------------------------------------------
# Absence is not zero
# --------------------------------------------------------------------------


def test_an_unscored_cohort_is_404_not_an_empty_chart(
    client: TestClient,
) -> None:
    """An empty warehouse is not a cohort of zero learners.

    Rendering it as a chart of zeroes would tell a director the cohort
    is fine when nothing has been measured.
    """
    response = client.get("/api/cohort")

    assert response.status_code == 404
    assert "make score" in response.json()["detail"]


def test_an_unscored_learner_is_404_not_a_risk_of_zero(
    client: TestClient, connection: Connection
) -> None:
    seed_scores(connection, (0.82,))

    response = client.get("/api/learners/s-99999")

    assert response.status_code == 404


# --------------------------------------------------------------------------
# The drill-down's contract
# --------------------------------------------------------------------------


def test_the_drilldown_carries_the_non_additivity_caveat(
    connection: Connection,
) -> None:
    """A dashboard must not render contributions as a decomposition.

    The caveat travels from the stored payload rather than being
    restated here, so it cannot drift from the one the scoring job
    wrote.
    """
    seed_scores(connection, (0.82,))

    detail = learner_detail(connection, "s-00000")

    assert detail is not None
    assert detail.additive is False
    assert detail.caveat == CONTRIBUTION_CAVEAT
    assert [driver.feature for driver in detail.drivers] == [
        "failures",
        "mean_score_in_window",
    ]


def test_no_endpoint_exposes_anything_identifying(
    client: TestClient, committed: Connection
) -> None:
    """FERPA posture, extended from storage to the HTTP surface.

    Learners are opaque account identifiers everywhere (ADR-0002). The
    API is the first place that could leak a person, so the same
    mechanical check applies here.
    """
    seed_scores(committed, (0.82,))

    responses = [
        client.get("/api/cohort"),
        client.get("/api/engagement"),
        client.get("/api/learners/s-00000"),
    ]
    assert [item.status_code for item in responses] == [200, 200, 200], (
        "an endpoint returned an error, so this would be scanning error "
        "messages rather than payloads — which is how this test passed "
        "vacuously before the `committed` fixture existed"
    )

    for body in [item.text for item in responses]:
        lowered = body.lower()
        assert "@" not in body
        assert "mbox" not in lowered
        assert "mailto" not in lowered


def test_latest_model_version_is_none_before_any_scoring(
    connection: Connection,
) -> None:
    assert latest_model_version(connection) is None
