"""FastAPI application entry point.

Run with ``make run``. The ingestion endpoint is LRS-*style*: it accepts
and validates the xAPI subset this platform models, and deviates from
conformance in two documented ways (see README limitations).
"""

from fastapi import FastAPI

from app.api import dashboard_router, insights_router, statements_router
from app.config import provider_choice

app = FastAPI(
    title="TinLantern",
    description="AI early-alert learning analytics over xAPI.",
    version="0.1.0",
)
app.include_router(statements_router)
app.include_router(dashboard_router)
app.include_router(insights_router)


@app.get("/health", tags=["ops"])
def health() -> dict[str, str | bool | None]:
    """Liveness probe, and which LLM provider is actually in use.

    The provider is reported here because DEMO_MODE overrides
    LLM_PROVIDER, and an override decided inside a process and reported
    nowhere is a safety property nobody can check. Verifiable from
    outside without reading logs someone has to find first.

    Still does not touch the database.
    """
    choice = provider_choice()
    return {
        "status": "ok",
        "demo_mode": choice.demo_mode,
        # null rather than "" when nothing is configured: an empty
        # string reads as a provider named nothing.
        "llm_provider": choice.provider or None,
        "provider_overridden": choice.overridden,
        "provider_reason": choice.explain(),
    }
