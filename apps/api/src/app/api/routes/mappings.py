"""Persistent field mapping CRUD API routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status

from app.schemas.connections import ApiErrorResponse
from app.schemas.mappings import (
    MappingCreate,
    MappingListResponse,
    MappingResponse,
    MappingStatus,
    MappingUpdate,
    MappingValidationResponse,
)
from app.services.mappings import MappingService

router = APIRouter(prefix="/api/v1/mappings", tags=["Mappings"])


def get_mapping_service(request: Request) -> MappingService:
    """Resolve MappingService from FastAPI application state."""
    return request.app.state.mapping_service


@router.post(
    "",
    response_model=MappingResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a field mapping between discovered source and destination objects",
    responses={
        status.HTTP_201_CREATED: {"model": MappingResponse},
        status.HTTP_404_NOT_FOUND: {"model": ApiErrorResponse},
        status.HTTP_409_CONFLICT: {"model": ApiErrorResponse},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ApiErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ApiErrorResponse},
    },
)
async def create_mapping(
    payload: MappingCreate,
    service: MappingService = Depends(get_mapping_service),  # noqa: B008
) -> MappingResponse:
    """Create a persistent mapping and its ordered field rules."""
    return await service.create_mapping(payload)


@router.post(
    "/validate",
    response_model=MappingValidationResponse,
    status_code=status.HTTP_200_OK,
    summary="Validate an unsaved field mapping definition without persisting",
    responses={
        status.HTTP_200_OK: {"model": MappingValidationResponse},
        status.HTTP_404_NOT_FOUND: {"model": ApiErrorResponse},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ApiErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ApiErrorResponse},
    },
)
async def validate_mapping_definition_preview(
    payload: MappingCreate,
    service: MappingService = Depends(get_mapping_service),  # noqa: B008
) -> MappingValidationResponse:
    """Validate a supplied mapping definition against latest discovered schemas."""
    return await service.validate_mapping_definition_preview(payload)


@router.get(
    "",
    response_model=MappingListResponse,
    summary="List saved field mappings",
    responses={
        status.HTTP_200_OK: {"model": MappingListResponse},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ApiErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ApiErrorResponse},
    },
)
async def list_mappings(
    source_connection_id: Annotated[str | None, Query(alias="sourceConnectionId")] = None,
    destination_connection_id: Annotated[str | None, Query(alias="destinationConnectionId")] = None,
    status_filter: Annotated[MappingStatus | None, Query(alias="status")] = None,
    q: Annotated[str | None, Query(max_length=120)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    service: MappingService = Depends(get_mapping_service),  # noqa: B008
) -> MappingListResponse:
    """List paginated field mappings with optional connection, status, and name filters."""
    return await service.list_mappings(
        source_connection_id=source_connection_id,
        destination_connection_id=destination_connection_id,
        status_filter=status_filter,
        search_query=q,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{mapping_id}",
    response_model=MappingResponse,
    summary="Get one saved field mapping with ordered rules",
    responses={
        status.HTTP_200_OK: {"model": MappingResponse},
        status.HTTP_404_NOT_FOUND: {"model": ApiErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ApiErrorResponse},
    },
)
async def get_mapping(
    mapping_id: str,
    service: MappingService = Depends(get_mapping_service),  # noqa: B008
) -> MappingResponse:
    """Retrieve one field mapping and its ordered rules by UUID."""
    return await service.get_mapping(mapping_id)


@router.put(
    "/{mapping_id}",
    response_model=MappingResponse,
    summary="Update a mapping and/or replace ordered rules atomically",
    responses={
        status.HTTP_200_OK: {"model": MappingResponse},
        status.HTTP_404_NOT_FOUND: {"model": ApiErrorResponse},
        status.HTTP_409_CONFLICT: {"model": ApiErrorResponse},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ApiErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ApiErrorResponse},
    },
)
async def update_mapping(
    mapping_id: str,
    payload: MappingUpdate,
    service: MappingService = Depends(get_mapping_service),  # noqa: B008
) -> MappingResponse:
    """Update mapping metadata or object bindings and atomically replace ordered rules."""
    return await service.update_mapping(mapping_id, payload)


@router.post(
    "/{mapping_id}/validate",
    response_model=MappingValidationResponse,
    status_code=status.HTTP_200_OK,
    summary="Validate a saved mapping against latest discovered schemas and persist outcome",
    responses={
        status.HTTP_200_OK: {"model": MappingValidationResponse},
        status.HTTP_404_NOT_FOUND: {"model": ApiErrorResponse},
        status.HTTP_409_CONFLICT: {"model": ApiErrorResponse},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ApiErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ApiErrorResponse},
    },
)
async def validate_saved_mapping(
    mapping_id: str,
    service: MappingService = Depends(get_mapping_service),  # noqa: B008
) -> MappingValidationResponse:
    """Evaluate a saved mapping against latest schemas and atomically persist its outcome."""
    return await service.validate_mapping(mapping_id)


@router.delete(
    "/{mapping_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a mapping configuration",
    responses={
        status.HTTP_204_NO_CONTENT: {"description": "Mapping deleted."},
        status.HTTP_404_NOT_FOUND: {"model": ApiErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ApiErrorResponse},
    },
)
async def delete_mapping(
    mapping_id: str,
    service: MappingService = Depends(get_mapping_service),  # noqa: B008
) -> Response:
    """Delete a saved mapping and cascade-delete its rules."""
    await service.delete_mapping(mapping_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
