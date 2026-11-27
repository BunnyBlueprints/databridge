"""Business logic and integrity verification for persistent field mappings."""

import logging
import uuid

from fastapi import status

from app.repositories.connections import ConnectionRepository
from app.repositories.mappings import MappingRepository
from app.repositories.schemas import SchemaRepository
from app.schemas.connections import ConnectionApiError
from app.schemas.mappings import (
    MappingCreate,
    MappingListResponse,
    MappingResponse,
    MappingRuleInput,
    MappingStatus,
    MappingUpdate,
    MappingValidationResponse,
)
from app.schemas.schemas import DiscoveredSchemaResponse, SchemaObjectResponse
from app.services.mapping_validation import validate_mapping_definition

logger = logging.getLogger("databridge.api")


def _parse_mapping_uuid(mapping_id: str) -> str:
    try:
        return str(uuid.UUID(mapping_id))
    except ValueError as exc:
        raise ConnectionApiError(
            error_code="MAPPING_NOT_FOUND",
            message="Mapping not found.",
            http_status=status.HTTP_404_NOT_FOUND,
        ) from exc


def _parse_connection_filter_uuid(raw_id: str | None, label: str) -> str | None:
    if raw_id is None:
        return None
    try:
        return str(uuid.UUID(raw_id))
    except ValueError as exc:
        raise ConnectionApiError(
            error_code="INVALID_MAPPING_REFERENCE",
            message=f"{label} must be a valid connection UUID.",
            http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
        ) from exc


def _validate_rules_against_objects(
    *,
    rules: list[MappingRuleInput],
    source_obj: SchemaObjectResponse,
    destination_obj: SchemaObjectResponse,
) -> None:
    source_paths = {field.fieldPath for field in source_obj.fields}
    dest_paths = {field.fieldPath for field in destination_obj.fields}

    for rule in rules:
        if rule.destinationFieldPath not in dest_paths:
            raise ConnectionApiError(
                error_code="INVALID_MAPPING_RULE",
                message=(
                    f"Destination field '{rule.destinationFieldPath}' does not exist on "
                    f"object '{destination_obj.objectKey}'."
                ),
                http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
            )
        if rule.mappingType in ("direct", "transform"):
            if rule.sourceFieldPath is None or rule.sourceFieldPath not in source_paths:
                raise ConnectionApiError(
                    error_code="INVALID_MAPPING_RULE",
                    message=(
                        f"Source field '{rule.sourceFieldPath}' does not exist on "
                        f"object '{source_obj.objectKey}'."
                    ),
                    http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
                )


class MappingService:
    """Manages persistent field mapping CRUD without accessing connection credentials."""

    def __init__(
        self,
        *,
        connection_repo: ConnectionRepository,
        schema_repo: SchemaRepository,
        mapping_repo: MappingRepository,
    ) -> None:
        self._connection_repo = connection_repo
        self._schema_repo = schema_repo
        self._mapping_repo = mapping_repo

    async def create_mapping(self, payload: MappingCreate) -> MappingResponse:
        """Verify connections, discovered objects, and rule fields, then persist mapping."""
        src_fp, dst_fp, src_obj, dst_obj = await self._resolve_and_verify_bindings(
            source_connection_id=str(payload.sourceConnectionId),
            source_object_key=payload.sourceObjectKey,
            destination_connection_id=str(payload.destinationConnectionId),
            destination_object_key=payload.destinationObjectKey,
            expected_source_fingerprint=payload.sourceSchemaFingerprint,
            expected_destination_fingerprint=payload.destinationSchemaFingerprint,
        )
        _validate_rules_against_objects(
            rules=payload.rules,
            source_obj=src_obj,
            destination_obj=dst_obj,
        )

        # CRUD never self-certifies a mapping as 'valid'; Step 06 validation handles that.
        normalized = payload.model_copy(
            update={
                "status": "draft",
                "validationSummary": None,
                "sourceSchemaFingerprint": src_fp,
                "destinationSchemaFingerprint": dst_fp,
            }
        )
        created = await self._mapping_repo.create(normalized)
        logger.info(
            "Mapping created: id=%s rules=%d version=%d",
            created.id,
            len(created.rules),
            created.mappingVersion,
        )
        return created

    async def get_mapping(self, mapping_id: str) -> MappingResponse:
        """Return one mapping and its ordered rules, or 404 if not found."""
        canonical_id = _parse_mapping_uuid(mapping_id)
        mapping = await self._mapping_repo.get_by_id(canonical_id)
        if mapping is None:
            raise ConnectionApiError(
                error_code="MAPPING_NOT_FOUND",
                message="Mapping not found.",
                http_status=status.HTTP_404_NOT_FOUND,
            )
        return mapping

    async def list_mappings(
        self,
        *,
        source_connection_id: str | None = None,
        destination_connection_id: str | None = None,
        status_filter: MappingStatus | None = None,
        search_query: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> MappingListResponse:
        """List and filter saved mappings with bounded pagination."""
        src_filter = _parse_connection_filter_uuid(source_connection_id, "sourceConnectionId")
        dst_filter = _parse_connection_filter_uuid(
            destination_connection_id, "destinationConnectionId"
        )
        items, total = await self._mapping_repo.list_mappings(
            source_connection_id=src_filter,
            destination_connection_id=dst_filter,
            status_filter=status_filter,
            search_query=search_query,
            limit=limit,
            offset=offset,
        )
        return MappingListResponse(items=items, total=total, limit=limit, offset=offset)

    async def update_mapping(self, mapping_id: str, payload: MappingUpdate) -> MappingResponse:
        """Verify updated bindings/rules, preserve omitted fields, and replace rules atomically."""
        canonical_id = _parse_mapping_uuid(mapping_id)
        existing = await self._mapping_repo.get_by_id(canonical_id)
        if existing is None:
            raise ConnectionApiError(
                error_code="MAPPING_NOT_FOUND",
                message="Mapping not found.",
                http_status=status.HTTP_404_NOT_FOUND,
            )

        fields_set = payload.model_fields_set
        eff_src_conn = payload.sourceConnectionId or existing.sourceConnectionId
        eff_src_key = payload.sourceObjectKey or existing.sourceObjectKey
        eff_dst_conn = payload.destinationConnectionId or existing.destinationConnectionId
        eff_dst_key = payload.destinationObjectKey or existing.destinationObjectKey

        references_updated = (
            eff_src_conn != existing.sourceConnectionId
            or eff_src_key != existing.sourceObjectKey
            or eff_dst_conn != existing.destinationConnectionId
            or eff_dst_key != existing.destinationObjectKey
        )

        expected_src_fp = (
            payload.sourceSchemaFingerprint if "sourceSchemaFingerprint" in fields_set else None
        )
        expected_dst_fp = (
            payload.destinationSchemaFingerprint
            if "destinationSchemaFingerprint" in fields_set
            else None
        )

        src_fp, dst_fp, src_obj, dst_obj = await self._resolve_and_verify_bindings(
            source_connection_id=str(eff_src_conn),
            source_object_key=eff_src_key,
            destination_connection_id=str(eff_dst_conn),
            destination_object_key=eff_dst_key,
            expected_source_fingerprint=expected_src_fp,
            expected_destination_fingerprint=expected_dst_fp,
        )

        effective_rules = (
            payload.rules
            if payload.rules is not None
            else [
                MappingRuleInput.model_validate(
                    {
                        "id": rule.id,
                        "sourceFieldPath": rule.sourceFieldPath,
                        "destinationFieldPath": rule.destinationFieldPath,
                        "mappingType": rule.mappingType,
                        **(
                            {"constantValue": rule.constantValue}
                            if rule.mappingType == "constant"
                            else {}
                        ),
                        "transformRef": (
                            rule.transformRef.model_dump(exclude_none=True)
                            if rule.transformRef is not None
                            else None
                        ),
                        "ordinalPosition": rule.ordinalPosition,
                        "metadata": rule.metadata,
                    }
                )
                for rule in existing.rules
            ]
        )
        _validate_rules_against_objects(
            rules=effective_rules,
            source_obj=src_obj,
            destination_obj=dst_obj,
        )

        next_src_fp = (
            src_fp
            if references_updated or "sourceSchemaFingerprint" in fields_set
            else existing.sourceSchemaFingerprint
        )
        next_dst_fp = (
            dst_fp
            if references_updated or "destinationSchemaFingerprint" in fields_set
            else existing.destinationSchemaFingerprint
        )

        fingerprints_updated = (
            next_src_fp != existing.sourceSchemaFingerprint
            or next_dst_fp != existing.destinationSchemaFingerprint
        )

        # Updating references, fingerprints, or rules resets state to 'draft' (never 'valid').
        if (
            references_updated
            or fingerprints_updated
            or payload.rules is not None
            or payload.status == "valid"
        ):
            next_status: MappingStatus = "draft"
            next_summary = None
        else:
            next_status = payload.status if payload.status is not None else existing.status
            next_summary = (
                payload.validationSummary
                if "validationSummary" in fields_set
                else existing.validationSummary
            )

        expected_version = (
            payload.expectedVersion
            if payload.expectedVersion is not None
            else existing.mappingVersion
        )

        merged_payload = MappingUpdate.model_construct(
            name=payload.name if payload.name is not None else existing.name,
            description=(
                payload.description if "description" in fields_set else existing.description
            ),
            sourceConnectionId=eff_src_conn,
            sourceObjectKey=eff_src_key,
            destinationConnectionId=eff_dst_conn,
            destinationObjectKey=eff_dst_key,
            status=next_status,
            sourceSchemaFingerprint=next_src_fp,
            destinationSchemaFingerprint=next_dst_fp,
            validationSummary=next_summary,
            rules=payload.rules,
            _fields_set=set(fields_set)
            | {
                "name",
                "description",
                "sourceConnectionId",
                "sourceObjectKey",
                "destinationConnectionId",
                "destinationObjectKey",
                "status",
                "sourceSchemaFingerprint",
                "destinationSchemaFingerprint",
                "validationSummary",
                *(["rules"] if payload.rules is not None else []),
            },
        )
        updated = await self._mapping_repo.update(
            canonical_id,
            merged_payload,
            expected_mapping_version=expected_version,
        )
        if updated is None:
            raise ConnectionApiError(
                error_code="MAPPING_NOT_FOUND",
                message="Mapping not found.",
                http_status=status.HTTP_404_NOT_FOUND,
            )
        logger.info(
            "Mapping updated: id=%s rules=%d version=%d",
            updated.id,
            len(updated.rules),
            updated.mappingVersion,
        )
        return updated

    async def delete_mapping(self, mapping_id: str) -> None:
        """Delete a mapping configuration (cascade-deleting mapping_rules)."""
        canonical_id = _parse_mapping_uuid(mapping_id)
        deleted = await self._mapping_repo.delete(canonical_id)
        if not deleted:
            raise ConnectionApiError(
                error_code="MAPPING_NOT_FOUND",
                message="Mapping not found.",
                http_status=status.HTTP_404_NOT_FOUND,
            )
        logger.info("Mapping deleted: id=%s", canonical_id)

    async def validate_mapping(self, mapping_id: str) -> MappingValidationResponse:
        """Validate a saved mapping against latest discovered schemas and persist the outcome."""
        canonical_id = _parse_mapping_uuid(mapping_id)
        existing = await self._mapping_repo.get_by_id(canonical_id)
        if existing is None:
            raise ConnectionApiError(
                error_code="MAPPING_NOT_FOUND",
                message="Mapping not found.",
                http_status=status.HTTP_404_NOT_FOUND,
            )

        src_schema, dst_schema, src_obj, dst_obj = await self._load_validation_schemas(
            source_connection_id=str(existing.sourceConnectionId),
            source_object_key=existing.sourceObjectKey,
            destination_connection_id=str(existing.destinationConnectionId),
            destination_object_key=existing.destinationObjectKey,
        )

        summary = validate_mapping_definition(
            source_object_key=existing.sourceObjectKey,
            destination_object_key=existing.destinationObjectKey,
            stored_source_fingerprint=existing.sourceSchemaFingerprint,
            stored_destination_fingerprint=existing.destinationSchemaFingerprint,
            rules=existing.rules,
            source_schema=src_schema,
            destination_schema=dst_schema,
            source_object=src_obj,
            destination_object=dst_obj,
        )
        next_status: MappingStatus = "valid" if summary.isValid else "invalid"

        updated = await self._mapping_repo.update_validation_outcome(
            canonical_id,
            status=next_status,
            validation_summary=summary,
            expected_mapping_version=existing.mappingVersion,
        )
        if updated is None:
            raise ConnectionApiError(
                error_code="MAPPING_NOT_FOUND",
                message="Mapping not found.",
                http_status=status.HTTP_404_NOT_FOUND,
            )

        logger.info(
            "Mapping validated: id=%s version=%d status=%s errors=%d warnings=%d drift=%s",
            updated.id,
            updated.mappingVersion,
            updated.status,
            summary.errorCount,
            summary.warningCount,
            summary.schemaDriftDetected,
        )
        return MappingValidationResponse(
            mappingId=updated.id,
            mappingVersion=updated.mappingVersion,
            status=updated.status,
            validationSummary=summary,
        )

    async def validate_mapping_definition_preview(
        self,
        payload: MappingCreate,
    ) -> MappingValidationResponse:
        """Validate an unsaved mapping definition against latest schemas without persisting."""
        source_connection_id = str(payload.sourceConnectionId)
        destination_connection_id = str(payload.destinationConnectionId)

        if await self._connection_repo.get_by_id(source_connection_id) is None:
            raise ConnectionApiError(
                error_code="CONNECTION_NOT_FOUND",
                message="Source connection not found.",
                http_status=status.HTTP_404_NOT_FOUND,
            )
        if await self._connection_repo.get_by_id(destination_connection_id) is None:
            raise ConnectionApiError(
                error_code="CONNECTION_NOT_FOUND",
                message="Destination connection not found.",
                http_status=status.HTTP_404_NOT_FOUND,
            )

        src_schema, dst_schema, src_obj, dst_obj = await self._load_validation_schemas(
            source_connection_id=source_connection_id,
            source_object_key=payload.sourceObjectKey,
            destination_connection_id=destination_connection_id,
            destination_object_key=payload.destinationObjectKey,
        )
        summary = validate_mapping_definition(
            source_object_key=payload.sourceObjectKey,
            destination_object_key=payload.destinationObjectKey,
            stored_source_fingerprint=payload.sourceSchemaFingerprint,
            stored_destination_fingerprint=payload.destinationSchemaFingerprint,
            rules=payload.rules,
            source_schema=src_schema,
            destination_schema=dst_schema,
            source_object=src_obj,
            destination_object=dst_obj,
        )
        return MappingValidationResponse(
            mappingId=None,
            mappingVersion=None,
            status="valid" if summary.isValid else "invalid",
            validationSummary=summary,
        )

    async def _load_validation_schemas(
        self,
        *,
        source_connection_id: str,
        source_object_key: str,
        destination_connection_id: str,
        destination_object_key: str,
    ) -> tuple[
        DiscoveredSchemaResponse,
        DiscoveredSchemaResponse,
        SchemaObjectResponse | None,
        SchemaObjectResponse | None,
    ]:
        src_schema = await self._schema_repo.get_latest_by_connection_id(
            source_connection_id,
            include_objects=False,
        )
        if src_schema is None:
            raise ConnectionApiError(
                error_code="SCHEMA_NOT_DISCOVERED",
                message="Source connection schema has not been discovered yet.",
                http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
            )

        dst_schema = await self._schema_repo.get_latest_by_connection_id(
            destination_connection_id,
            include_objects=False,
        )
        if dst_schema is None:
            raise ConnectionApiError(
                error_code="SCHEMA_NOT_DISCOVERED",
                message="Destination connection schema has not been discovered yet.",
                http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
            )

        src_obj = await self._schema_repo.get_object_by_key(
            source_connection_id,
            source_object_key,
        )
        dst_obj = await self._schema_repo.get_object_by_key(
            destination_connection_id,
            destination_object_key,
        )
        return src_schema, dst_schema, src_obj, dst_obj

    async def _resolve_and_verify_bindings(
        self,
        *,
        source_connection_id: str,
        source_object_key: str,
        destination_connection_id: str,
        destination_object_key: str,
        expected_source_fingerprint: str | None,
        expected_destination_fingerprint: str | None,
    ) -> tuple[str, str, SchemaObjectResponse, SchemaObjectResponse]:
        if await self._connection_repo.get_by_id(source_connection_id) is None:
            raise ConnectionApiError(
                error_code="CONNECTION_NOT_FOUND",
                message="Source connection not found.",
                http_status=status.HTTP_404_NOT_FOUND,
            )
        if await self._connection_repo.get_by_id(destination_connection_id) is None:
            raise ConnectionApiError(
                error_code="CONNECTION_NOT_FOUND",
                message="Destination connection not found.",
                http_status=status.HTTP_404_NOT_FOUND,
            )

        src_schema = await self._schema_repo.get_latest_by_connection_id(
            source_connection_id,
            include_objects=False,
        )
        if src_schema is None:
            raise ConnectionApiError(
                error_code="SCHEMA_NOT_DISCOVERED",
                message="Source connection schema has not been discovered yet.",
                http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
            )

        dst_schema = await self._schema_repo.get_latest_by_connection_id(
            destination_connection_id,
            include_objects=False,
        )
        if dst_schema is None:
            raise ConnectionApiError(
                error_code="SCHEMA_NOT_DISCOVERED",
                message="Destination connection schema has not been discovered yet.",
                http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
            )

        if (
            expected_source_fingerprint is not None
            and expected_source_fingerprint != src_schema.schemaFingerprint
        ):
            raise ConnectionApiError(
                error_code="INVALID_MAPPING_REFERENCE",
                message="Source schema fingerprint is stale or does not match the latest snapshot.",
                http_status=status.HTTP_409_CONFLICT,
            )

        if (
            expected_destination_fingerprint is not None
            and expected_destination_fingerprint != dst_schema.schemaFingerprint
        ):
            raise ConnectionApiError(
                error_code="INVALID_MAPPING_REFERENCE",
                message=(
                    "Destination schema fingerprint is stale or does not match the latest snapshot."
                ),
                http_status=status.HTTP_409_CONFLICT,
            )

        src_obj = await self._schema_repo.get_object_by_key(
            source_connection_id,
            source_object_key,
        )
        if src_obj is None:
            raise ConnectionApiError(
                error_code="SOURCE_OBJECT_NOT_FOUND",
                message=(
                    f"Source object '{source_object_key}' was not found in the source "
                    "connection's discovered schema."
                ),
                http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
            )

        dst_obj = await self._schema_repo.get_object_by_key(
            destination_connection_id,
            destination_object_key,
        )
        if dst_obj is None:
            raise ConnectionApiError(
                error_code="DESTINATION_OBJECT_NOT_FOUND",
                message=(
                    f"Destination object '{destination_object_key}' was not found in the "
                    "destination connection's discovered schema."
                ),
                http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
            )

        return src_schema.schemaFingerprint, dst_schema.schemaFingerprint, src_obj, dst_obj
