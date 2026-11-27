"""Schema discovery and persisted schema/object/field retrieval routes."""

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Query, Request, status

from app.schemas.connections import ApiErrorResponse
from app.schemas.schemas import (
    DiscoverConnectionSchemaRequest,
    DiscoveredSchemaResponse,
    DiscoverSchemaResponse,
    SchemaObjectFieldsResponse,
    SchemaObjectListResponse,
    SchemaObjectType,
)
from app.services.discovery.service import SchemaDiscoveryService

router = APIRouter(prefix="/api/v1/connections", tags=["Schemas"])


def get_schema_discovery_service(request: Request) -> SchemaDiscoveryService:
    """Resolve SchemaDiscoveryService from FastAPI application state."""
    return request.app.state.schema_discovery_service


@router.post(
    "/{connection_id}/discover",
    response_model=DiscoverSchemaResponse,
    summary="Discover and persist schema metadata for a saved connection",
    description=(
        "Executes read-only schema discovery (or transient CSV/JSON inference or manual REST "
        "declaration) using backend-decrypted credentials without returning secrets or storing "
        "raw sample records."
    ),
    responses={
        status.HTTP_200_OK: {"model": DiscoverSchemaResponse},
        status.HTTP_404_NOT_FOUND: {"model": ApiErrorResponse},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ApiErrorResponse},
        status.HTTP_502_BAD_GATEWAY: {"model": ApiErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ApiErrorResponse},
        status.HTTP_504_GATEWAY_TIMEOUT: {"model": ApiErrorResponse},
    },
)
async def discover_connection_schema(
    connection_id: str,
    payload: Annotated[
        DiscoverConnectionSchemaRequest,
        Body(default_factory=DiscoverConnectionSchemaRequest),
    ] = ...,  # type: ignore[assignment]
    service: SchemaDiscoveryService = Depends(get_schema_discovery_service),  # noqa: B008
) -> DiscoverSchemaResponse:
    """Trigger schema discovery and persist the latest normalized snapshot."""
    return await service.discover_connection_schema(connection_id, payload)


@router.get(
    "/{connection_id}/schema",
    response_model=DiscoveredSchemaResponse,
    summary="Get the latest discovered schema snapshot for a connection",
    responses={
        status.HTTP_200_OK: {"model": DiscoveredSchemaResponse},
        status.HTTP_404_NOT_FOUND: {"model": ApiErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ApiErrorResponse},
    },
)
async def get_connection_schema(
    connection_id: str,
    service: SchemaDiscoveryService = Depends(get_schema_discovery_service),  # noqa: B008
) -> DiscoveredSchemaResponse:
    """Return the latest persisted schema snapshot for `connection_id`."""
    return await service.get_latest_schema(connection_id)


@router.get(
    "/{connection_id}/objects",
    response_model=SchemaObjectListResponse,
    summary="List discovered objects for a connection",
    responses={
        status.HTTP_200_OK: {"model": SchemaObjectListResponse},
        status.HTTP_404_NOT_FOUND: {"model": ApiErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ApiErrorResponse},
    },
)
async def list_connection_objects(
    connection_id: str,
    q: Annotated[str | None, Query(max_length=120)] = None,
    object_type: Annotated[SchemaObjectType | None, Query(alias="objectType")] = None,
    object_key: Annotated[str | None, Query(alias="objectKey", max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=250)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    service: SchemaDiscoveryService = Depends(get_schema_discovery_service),  # noqa: B008
) -> SchemaObjectListResponse:
    """List paginated discovered schema objects without hydrating fields."""
    return await service.list_objects(
        connection_id,
        search_query=q,
        object_type=object_type,
        object_key=object_key,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{connection_id}/objects/{object_id}/fields",
    response_model=SchemaObjectFieldsResponse,
    summary="Get ordered fields for a discovered object",
    responses={
        status.HTTP_200_OK: {"model": SchemaObjectFieldsResponse},
        status.HTTP_404_NOT_FOUND: {"model": ApiErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ApiErrorResponse},
    },
)
async def get_object_fields(
    connection_id: str,
    object_id: str,
    q: Annotated[str | None, Query(max_length=120)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
    service: SchemaDiscoveryService = Depends(get_schema_discovery_service),  # noqa: B008
) -> SchemaObjectFieldsResponse:
    """Return ordered fields for one connection-owned schema object."""
    return await service.get_object_fields(
        connection_id,
        object_id,
        field_query=q,
        limit=limit,
        offset=offset,
    )
