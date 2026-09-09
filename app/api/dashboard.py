"""HTTP surface for the dashboard.

Thin on purpose: the numbers come from `app.dashboard.queries`, tested
without a client, and this module turns them into JSON and status codes.

Naming is load-bearing here. The trend endpoint, its field names, and
the heading the UI renders all say **engagement**, never risk — only one
`window_close` exists, so a risk trend would be a point or a line drawn
through repeated scoring runs. A test asserts no risk-shaped key appears
in the trend response, because label discipline that depends on nobody
renaming a field is not discipline.
"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, HTTPException

from app.api import schemas
from app.dashboard.queries import (
    DEFAULT_LIMIT,
    cohort_overview,
    engagement_trend,
    learner_detail,
    rank_learners,
)
from app.db import transaction

router = APIRouter(prefix="/api", tags=["dashboard"])

_NOT_SCORED = (
    "no cohort has been scored yet. Run `make score` — this is an empty "
    "warehouse, not a cohort of zero learners, and the difference matters "
    "to anything rendering it."
)


@router.get("/cohort", response_model=schemas.CohortOverview)
def cohort() -> dict:
    """The current risk distribution and alert count.

    Bins are computed here rather than in the frontend: their middle
    edge IS the alert threshold, so a client choosing its own edges
    would draw a chart that disagreed with the flag on the same learner.
    """
    with transaction() as connection:
        overview = cohort_overview(connection)
    if overview is None:
        raise HTTPException(status_code=404, detail=_NOT_SCORED)
    return asdict(overview)


@router.get("/engagement", response_model=schemas.EngagementTrend)
def engagement() -> dict:
    """Active learners per week. **Engagement over time, not risk.**"""
    with transaction() as connection:
        points = engagement_trend(connection)
    return {"engagement_trend": [asdict(point) for point in points]}


@router.get("/learners", response_model=schemas.LearnerRanking)
def learners(limit: int = DEFAULT_LIMIT) -> dict:
    """Learners by risk, highest first.

    The way in to a drill-down. M5's criterion said "student
    drill-down" and assumed a route to one that did not exist: the
    detail endpoint needs an identifier a user has no way to obtain
    from a distribution chart.

    Bounded by default, with the cohort total returned alongside, so a
    caller can say "top 50 of 120" instead of implying it is showing
    everyone.
    """
    with transaction() as connection:
        ranking = rank_learners(connection, limit=limit)
    if ranking is None:
        raise HTTPException(status_code=404, detail=_NOT_SCORED)
    return asdict(ranking)


@router.get("/learners/{identifier}", response_model=schemas.LearnerDetail)
def learner(identifier: str) -> dict:
    """One learner's score and drivers.

    404 when the learner has no score. An unscored learner is not a
    learner at risk zero, and an empty shell would let a dashboard
    render one as the other.
    """
    with transaction() as connection:
        detail = learner_detail(connection, identifier)
    if detail is None:
        raise HTTPException(
            status_code=404,
            detail=f"no current score for learner {identifier!r}.",
        )
    return asdict(detail)
