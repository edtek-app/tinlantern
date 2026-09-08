"""FastAPI application entry point.

Run with ``make run``. The ingestion endpoint is LRS-*style*: it accepts
and validates the xAPI subset this platform models, and deviates from
conformance in two documented ways (see README limitations).
"""

from fastapi import FastAPI

from app.api import dashboard_router, insights_router, statements_router

app = FastAPI(
    title="TinLantern",
    description="AI early-alert learning analytics over xAPI.",
    version="0.1.0",
)
app.include_router(statements_router)
app.include_router(dashboard_router)
app.include_router(insights_router)


@app.get("/health", tags=["ops"])
def health() -> dict[str, str]:
    """Liveness probe. Deliberately does not touch the database."""
    return {"status": "ok"}
