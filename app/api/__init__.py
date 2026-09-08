"""HTTP surface: the FastAPI routers TinLantern exposes."""

from app.api.dashboard import router as dashboard_router
from app.api.insights import router as insights_router
from app.api.statements import router as statements_router

__all__ = ["dashboard_router", "insights_router", "statements_router"]
