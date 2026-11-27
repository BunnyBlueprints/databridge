"""Connection testing API routes."""

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse

from app.schemas.connection_test import ConnectionTestRequest, ConnectionTestResult
from app.services.connection_test_service import ConnectionTestService

router = APIRouter(prefix="/api/v1/connections", tags=["Connections"])


def get_connection_test_service(request: Request) -> ConnectionTestService:
    """Resolve ConnectionTestService from FastAPI application state."""
    return request.app.state.connection_test_service


@router.post(
    "/test",
    response_model=ConnectionTestResult,
    response_model_exclude_none=True,
    summary="Validate and dispatch a connector test",
    description=(
        "Validates connector-specific configuration and optional transient credentials, "
        "then dispatches the request to the connector tester registry.\n\n"
        "**Supported connector types:** `PostgreSQL`, `MySQL`, `MongoDB`, `REST API`, "
        "`CSV`, `JSON`.\n\n"
        "**Current status:** `PostgreSQL`, `MySQL`, and `MongoDB` execute read-only "
        "connectivity verification (`SELECT 1` / `admin` `ping`). `REST API`, `CSV`, and "
        "`JSON` testers are not implemented yet and return `501 Not Implemented` "
        "(`TESTER_NOT_IMPLEMENTED`). Transient credentials are never persisted or logged."
    ),
    responses={
        status.HTTP_200_OK: {
            "model": ConnectionTestResult,
            "description": "Connection test executed (connected or target-level failure).",
        },
        status.HTTP_400_BAD_REQUEST: {
            "model": ConnectionTestResult,
            "description": "Malformed JSON payload.",
        },
        status.HTTP_413_CONTENT_TOO_LARGE: {
            "model": ConnectionTestResult,
            "description": "Request body exceeds configured maximum payload size.",
        },
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "model": ConnectionTestResult,
            "description": "Schema validation failure or unsupported connector type.",
        },
        status.HTTP_500_INTERNAL_SERVER_ERROR: {
            "model": ConnectionTestResult,
            "description": "Sanitized internal server error.",
        },
        status.HTTP_501_NOT_IMPLEMENTED: {
            "model": ConnectionTestResult,
            "description": "Connector type is valid, but its tester is not implemented yet.",
        },
        status.HTTP_504_GATEWAY_TIMEOUT: {
            "model": ConnectionTestResult,
            "description": "Connection test exceeded server-side timeout.",
        },
    },
)
async def test_connection(
    payload: ConnectionTestRequest,
    service: ConnectionTestService = Depends(get_connection_test_service),  # noqa: B008
) -> JSONResponse:
    """Validate and dispatch a connection test request."""
    result, http_status = await service.test_connection(payload)
    return JSONResponse(
        status_code=http_status,
        content=result.model_dump(exclude_none=True),
    )
