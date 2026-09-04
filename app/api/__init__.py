"""HTTP surface: the FastAPI routers TinLantern exposes."""

from app.api.statements import router as statements_router

__all__ = ["statements_router"]
