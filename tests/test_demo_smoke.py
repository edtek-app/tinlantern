"""M5's smoke test: the screens' data, end to end against seeded rows.

The gate clause asks for "smoke test against demo seed". The component
tests assert what renders given a payload; this asserts the API actually
produces payloads of that shape from a real database — the half neither
`tsc` nor jsdom can see.

Seeds through the `committed` fixture, because an endpoint opens its own
connection and cannot see a rolled-back one. Status codes are asserted
before payloads, so a failure cannot be read as a passing shape check.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Connection, text

from tests.test_dashboard_api import seed_activity_dimensions, seed_scores

pytestmark = pytest.mark.m5


@pytest.fixture
def client(test_database: str) -> TestClient:
    from app.main import app

    return TestClient(app)


def seed_demo(connection: Connection) -> None:
    """A cohort small enough to check by eye, real enough to render."""
    seed_scores(connection, (0.05, 0.20, 0.42, 0.91))
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
            """
        )
    )


def test_the_cohort_screen_has_everything_it_renders(
    client: TestClient, committed: Connection
) -> None:
    """Every field the Cohort component reads, present and populated."""
    seed_demo(committed)

    response = client.get("/api/cohort")
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["learners"] == 4
    assert body["alerted"] == 2, "0.42 and 0.91 clear the 0.35 threshold"
    assert len(body["distribution"]) == 4
    assert sum(item["learners"] for item in body["distribution"]) == 4
    assert body["model_version"] and body["window_close"]


def test_the_alerted_count_and_the_alerted_bins_agree(
    client: TestClient, committed: Connection
) -> None:
    """The two figures the screen shows side by side.

    If they ever disagree, one of them is lying to whoever is reading
    both at once — and the component test cannot catch it, because it
    is handed a payload rather than producing one.
    """
    seed_demo(committed)

    body = client.get("/api/cohort").json()
    binned = sum(
        item["learners"]
        for item in body["distribution"]
        if item["lower"] >= body["threshold"]
    )

    assert binned == body["alerted"]


def test_the_trend_screen_has_a_series_to_draw(
    client: TestClient, committed: Connection
) -> None:
    seed_demo(committed)

    response = client.get("/api/engagement")
    assert response.status_code == 200, response.text

    points = response.json()["engagement_trend"]
    assert len(points) == 2, "two ISO weeks were seeded"
    for point in points:
        assert point["active_learners"] == 4
        assert point["week_start"]


def test_an_unseeded_demo_reports_missing_rather_than_empty(
    client: TestClient,
) -> None:
    """The contract the empty state depends on.

    If this ever became a 200 with zeroes, the frontend would render a
    healthy-looking cohort that had never been measured.
    """
    assert client.get("/api/cohort").status_code == 404
    assert client.get("/api/learners/s-00000").status_code == 404
