"""Connection management and connectivity testing API routes."""

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Query, Request, Response, status
from fastapi.responses import JSONResponse

from app.schemas.connection_test import ConnectionTestRequest, ConnectionTestResult, ConnectorType
from app.schemas.connections import (
    ApiErrorResponse,
    ConnectionListResponse,
    ConnectionResponse,
    ConnectionStatus,
    CreateConnectionRequest,
    SavedConnectionTestRequest,
    UpdateConnectionRequest,
)
from app.services.connection_service import ConnectionService
from app.services.connection_test_service import ConnectionTestService

router = APIRouter(prefix="/api/v1/connections", tags=["Connections"])


def get_connection_test_service(request: Request) -> ConnectionTestService:
    """Resolve ConnectionTestService from FastAPI application state."""
    return request.app.state.connection_test_service


def get_connection_service(request: Request) -> ConnectionService:
    """Resolve ConnectionService from FastAPI application state."""
    return request.app.state.connection_service


@router.post(
    "/test",
    response_model=ConnectionTestResult,
    response_model_exclude_none=True,
    summary="Validate and dispatch a transient connector test",
    description=(
        "Validates connector-specific configuration and optional transient credentials, "
        "then dispatches the request to the connector tester registry.\n\n"
        "**Supported connector types:** `PostgreSQL`, `MySQL`, `MongoDB`, `REST API`, "
        "`CSV`, `JSON`.\n\n"
        "Transient credentials and transient file contents are used strictly for the active "
        "test request and are never persisted or logged."
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
    """Validate and dispatch a stateless connection test request."""
    result, http_status = await service.test_connection(payload)
    return JSONResponse(
        status_code=http_status,
        content=result.model_dump(exclude_none=True),
    )


@router.post(
    "",
    response_model=ConnectionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a persistent connection",
    description=(
        "Validates non-secret configuration and optional credentials, encrypts credentials with "
        "AES-256-GCM before storage, and persists the connection atomically.\n\n"
        "**Deployment note:** These routes assume deployment behind an authenticated access "
        "control boundary; UUIDs alone do not provide authorization."
    ),
    responses={
        status.HTTP_201_CREATED: {"model": ConnectionResponse},
        status.HTTP_409_CONFLICT: {"model": ApiErrorResponse},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ApiErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ApiErrorResponse},
    },
)
async def create_connection(
    payload: CreateConnectionRequest,
    service: ConnectionService = Depends(get_connection_service),  # noqa: B008
) -> ConnectionResponse:
    """Create a persistent connection and encrypt credentials when supplied."""
    return await service.create_connection(payload)


@router.get(
    "",
    response_model=ConnectionListResponse,
    summary="List persistent connections",
    responses={
        status.HTTP_200_OK: {"model": ConnectionListResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ApiErrorResponse},
    },
)
async def list_connections(
    status_filter: Annotated[ConnectionStatus | None, Query(alias="status")] = None,
    type_filter: Annotated[ConnectorType | None, Query(alias="type")] = None,
    q: str | None = Query(default=None, max_length=120),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    service: ConnectionService = Depends(get_connection_service),  # noqa: B008
) -> ConnectionListResponse:
    """List saved non-secret connection records with optional filters."""
    return await service.list_connections(
        status_filter=status_filter,
        type_filter=type_filter,
        search_query=q,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{connection_id}",
    response_model=ConnectionResponse,
    summary="Get one persistent connection",
    responses={
        status.HTTP_200_OK: {"model": ConnectionResponse},
        status.HTTP_404_NOT_FOUND: {"model": ApiErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ApiErrorResponse},
    },
)
async def get_connection(
    connection_id: str,
    service: ConnectionService = Depends(get_connection_service),  # noqa: B008
) -> ConnectionResponse:
    """Return one safe, non-secret connection record by ID."""
    return await service.get_connection(connection_id)


@router.put(
    "/{connection_id}",
    response_model=ConnectionResponse,
    summary="Update a persistent connection",
    responses={
        status.HTTP_200_OK: {"model": ConnectionResponse},
        status.HTTP_404_NOT_FOUND: {"model": ApiErrorResponse},
        status.HTTP_409_CONFLICT: {"model": ApiErrorResponse},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ApiErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ApiErrorResponse},
    },
)
async def update_connection(
    connection_id: str,
    payload: UpdateConnectionRequest,
    service: ConnectionService = Depends(get_connection_service),  # noqa: B008
) -> ConnectionResponse:
    """Update connection metadata/configuration and keep, replace, or clear credentials."""
    return await service.update_connection(connection_id, payload)


@router.delete(
    "/{connection_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a persistent connection",
    responses={
        status.HTTP_204_NO_CONTENT: {"description": "Connection and credentials deleted."},
        status.HTTP_404_NOT_FOUND: {"model": ApiErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ApiErrorResponse},
    },
)
async def delete_connection(
    connection_id: str,
    service: ConnectionService = Depends(get_connection_service),  # noqa: B008
) -> Response:
    """Atomically delete a connection record and its encrypted credential vault entry."""
    await service.delete_connection(connection_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{connection_id}/test",
    response_model=ConnectionTestResult,
    response_model_exclude_none=True,
    summary="Test a saved connection using backend-managed credentials",
    description=(
        "Decrypts stored credentials inside backend memory, executes the connector test through "
        "the existing ConnectionTestService and tester registry, and persists test status.\n\n"
        "Credential payloads are rejected on this endpoint; CSV/JSON connections accept only "
        "transient `fileContent`."
    ),
    responses={
        status.HTTP_200_OK: {"model": ConnectionTestResult},
        status.HTTP_404_NOT_FOUND: {"model": ConnectionTestResult},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ConnectionTestResult},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ConnectionTestResult},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ConnectionTestResult},
        status.HTTP_504_GATEWAY_TIMEOUT: {"model": ConnectionTestResult},
    },
)
async def test_saved_connection(
    connection_id: str,
    payload: SavedConnectionTestRequest | None = Body(default=None),  # noqa: B008
    service: ConnectionService = Depends(get_connection_service),  # noqa: B008
) -> JSONResponse:
    """Test a persisted connection by ID without exposing decrypted credentials."""
    resolved_payload = payload or SavedConnectionTestRequest()
    result, http_status = await service.test_saved_connection(connection_id, resolved_payload)
    return JSONResponse(
        status_code=http_status,
        content=result.model_dump(exclude_none=True),
    )
