"""Health check endpoint."""

from fastapi import APIRouter

router = APIRouter(tags=["Health"])


@router.get(
    "/health",
    summary="API health check",
    description="Returns service liveness status without inspecting credentials or secrets.",
)
async def health_check() -> dict[str, str]:
    """Liveness check endpoint."""
    return {"status": "ok"}
