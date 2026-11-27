"""Health and database readiness check endpoints."""

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.db.pool import DatabaseManager, get_database_manager

router = APIRouter(tags=["Health"])


class DatabaseReadinessResponse(BaseModel):
    """Safe database readiness payload (never exposes DSNs or credentials)."""

    status: str
    persistenceEnabled: bool
    detail: str


@router.get(
    "/health",
    summary="API health check",
    description="Returns service liveness status without inspecting credentials or secrets.",
)
async def health_check() -> dict[str, str]:
    """Liveness check endpoint."""
    return {"status": "ok"}


@router.get(
    "/health/db",
    response_model=DatabaseReadinessResponse,
    summary="Database readiness check",
    description=(
        "Reports PostgreSQL pool readiness when DATABASE_URL is configured, or reports "
        "persistence as disabled in stateless mode."
    ),
    responses={
        status.HTTP_200_OK: {
            "model": DatabaseReadinessResponse,
            "description": "Database is ready, or persistence is disabled in stateless mode.",
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": DatabaseReadinessResponse,
            "description": "Database persistence is configured but currently unavailable.",
        },
    },
)
async def database_readiness(
    db_manager: DatabaseManager = Depends(get_database_manager),  # noqa: B008
) -> JSONResponse:
    """Readiness check for PostgreSQL persistence."""
    readiness_status, detail = await db_manager.check_readiness()
    http_status = (
        status.HTTP_503_SERVICE_UNAVAILABLE
        if readiness_status == "unavailable"
        else status.HTTP_200_OK
    )
    payload = DatabaseReadinessResponse(
        status=readiness_status,
        persistenceEnabled=db_manager.is_configured,
        detail=detail,
    )
    return JSONResponse(status_code=http_status, content=payload.model_dump())
