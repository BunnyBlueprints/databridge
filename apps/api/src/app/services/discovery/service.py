"""Orchestration service for bounded schema discovery and persistent metadata retrieval."""

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import status

from app.core.errors import sanitize_error_message
from app.repositories.connections import ConnectionRecord, ConnectionRepository
from app.repositories.credentials import CredentialRepository
from app.repositories.schemas import SchemaRepository
from app.schemas.connection_test import RestApiConnectionTestConfig
from app.schemas.connections import (
    ConfigModelType,
    ConnectionApiError,
    normalize_stored_config_for_read,
)
from app.schemas.schemas import (
    DiscoverConnectionSchemaRequest,
    DiscoveredSchemaCreate,
    DiscoveredSchemaResponse,
    DiscoverSchemaResponse,
    SchemaObjectFieldsResponse,
    SchemaObjectListResponse,
    SchemaObjectType,
)
from app.services.connection_service import _collect_secret_tokens, _parse_connection_uuid
from app.services.credential_encryption import (
    CredentialConfigurationError,
    CredentialDecryptionError,
    CredentialEncryptionService,
    RestApiConnectionTestCredentials,
    StoredCredentialPayload,
)
from app.services.discovery.base import DiscoveryContext, DiscoveryExecutionError
from app.services.discovery.registry import ConnectorDiscovererRegistry

logger = logging.getLogger("databridge.api")

_MAX_PERSISTED_WARNINGS = 100


def _parse_object_uuid(object_id: str) -> str:
    try:
        return str(uuid.UUID(object_id))
    except ValueError as exc:
        raise ConnectionApiError(
            error_code="OBJECT_NOT_FOUND",
            message="Schema object not found.",
            http_status=status.HTTP_404_NOT_FOUND,
        ) from exc


def _validate_discover_request_for_connector(
    *,
    record: ConnectionRecord,
    config_model: ConfigModelType,
    decrypted_creds: StoredCredentialPayload | None,
    payload: DiscoverConnectionSchemaRequest,
) -> None:
    connector_type = record.connector_type
    opts = payload.options

    if connector_type not in ("CSV", "JSON") and (
        payload.fileContent is not None or payload.fileName is not None
    ):
        raise ConnectionApiError(
            error_code="INVALID_CONFIGURATION",
            message=(
                f"Transient fileContent and fileName are only valid for CSV or JSON connectors, "
                f"not '{connector_type}'."
            ),
            http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )

    if connector_type != "REST API" and (
        opts.mode is not None
        or opts.openApiPath is not None
        or opts.openApiSpec is not None
        or opts.resourcePath is not None
        or opts.manualObjects is not None
    ):
        raise ConnectionApiError(
            error_code="DISCOVERY_NOT_SUPPORTED",
            message=f"REST discovery options are not supported for '{connector_type}' connections.",
            http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )

    if connector_type not in ("JSON", "REST API") and opts.recordPath is not None:
        raise ConnectionApiError(
            error_code="DISCOVERY_NOT_SUPPORTED",
            message=f"Option 'recordPath' is not supported for '{connector_type}' connections.",
            http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )

    if connector_type not in ("PostgreSQL", "MySQL") and opts.includeSchemas is not None:
        raise ConnectionApiError(
            error_code="DISCOVERY_NOT_SUPPORTED",
            message=f"Option 'includeSchemas' is not supported for '{connector_type}' connections.",
            http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )

    if connector_type == "REST API":
        assert isinstance(config_model, RestApiConnectionTestConfig)
        if config_model.authType == "api_key":
            has_key = isinstance(decrypted_creds, RestApiConnectionTestCredentials) and bool(
                decrypted_creds.apiKey and decrypted_creds.apiKey.get_secret_value().strip()
            )
            if not has_key and opts.mode != "manual" and opts.openApiSpec is None:
                raise ConnectionApiError(
                    error_code="INVALID_CONFIGURATION",
                    message="Stored API key credentials do not match REST API authType 'api_key'.",
                    http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
                )
        elif config_model.authType == "bearer":
            has_bearer = isinstance(decrypted_creds, RestApiConnectionTestCredentials) and bool(
                decrypted_creds.bearerToken
                and decrypted_creds.bearerToken.get_secret_value().strip()
            )
            if not has_bearer and opts.mode != "manual" and opts.openApiSpec is None:
                raise ConnectionApiError(
                    error_code="INVALID_CONFIGURATION",
                    message=(
                        "Stored bearer token credentials do not match REST API authType 'bearer'."
                    ),
                    http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
                )


class SchemaDiscoveryService:
    """Coordinates credential decryption, connector discovery, and snapshot persistence."""

    def __init__(
        self,
        *,
        connection_repo: ConnectionRepository,
        credential_repo: CredentialRepository,
        schema_repo: SchemaRepository,
        encryption_service: CredentialEncryptionService | None,
        discoverer_registry: ConnectorDiscovererRegistry,
        timeout_seconds: float = 15.0,
        max_objects: int = 250,
        max_fields_per_object: int = 500,
        default_sample_limit: int = 100,
        max_sample_limit: int = 500,
    ) -> None:
        self._connection_repo = connection_repo
        self._credential_repo = credential_repo
        self._schema_repo = schema_repo
        self._encryption_service = encryption_service
        self._discoverer_registry = discoverer_registry
        self._timeout_seconds = timeout_seconds
        self._max_objects = max_objects
        self._max_fields_per_object = max_fields_per_object
        self._default_sample_limit = default_sample_limit
        self._max_sample_limit = max_sample_limit

    async def discover_connection_schema(
        self,
        connection_id: str,
        payload: DiscoverConnectionSchemaRequest,
    ) -> DiscoverSchemaResponse:
        """Execute discovery outside long-lived DB transactions and persist the latest snapshot."""
        canonical_id = _parse_connection_uuid(connection_id)
        record = await self._connection_repo.get_by_id(canonical_id)
        if record is None:
            raise ConnectionApiError(
                error_code="CONNECTION_NOT_FOUND",
                message="Connection not found.",
                http_status=status.HTTP_404_NOT_FOUND,
            )

        try:
            config_model = normalize_stored_config_for_read(record.connector_type, record.config)
        except ValueError as exc:
            raise ConnectionApiError(
                error_code="INVALID_CONFIGURATION",
                message=str(exc),
                http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
            ) from exc

        decrypted_creds = await self._load_decrypted_credentials(record)
        raw_legacy_username = record.config.get("username")
        legacy_username = (
            raw_legacy_username.strip()
            if isinstance(raw_legacy_username, str) and raw_legacy_username.strip()
            else None
        )
        secret_tokens, username_tokens = _collect_secret_tokens(
            decrypted_creds,
            legacy_username=legacy_username,
        )

        _validate_discover_request_for_connector(
            record=record,
            config_model=config_model,
            decrypted_creds=decrypted_creds,
            payload=payload,
        )

        discoverer = self._discoverer_registry.get(record.connector_type)
        if discoverer is None:
            raise ConnectionApiError(
                error_code="UNSUPPORTED_CONNECTOR",
                message=f"Schema discovery is not supported for '{record.connector_type}'.",
                http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
            )

        requested_sample = payload.options.sampleLimit
        effective_sample_limit = min(
            self._max_sample_limit,
            max(
                1,
                requested_sample if requested_sample is not None else self._default_sample_limit,
            ),
        )

        ctx = DiscoveryContext(
            connection_id=record.id,
            connector_type=record.connector_type,
            config=config_model,
            credentials=decrypted_creds,
            legacy_username=legacy_username,
            options=payload.options,
            file_name=payload.fileName,
            file_content=payload.fileContent,
            timeout_seconds=self._timeout_seconds,
            max_objects=self._max_objects,
            max_fields_per_object=self._max_fields_per_object,
            sample_limit=effective_sample_limit,
        )

        try:
            async with asyncio.timeout(self._timeout_seconds):
                result = await discoverer.discover(ctx)
        except DiscoveryExecutionError as exc:
            safe_msg = sanitize_error_message(
                exc.message,
                "Schema discovery failed.",
                known_secrets=secret_tokens,
                known_usernames=username_tokens,
            )
            raise ConnectionApiError(
                error_code=exc.error_code,
                message=safe_msg,
                http_status=exc.http_status,
            ) from exc
        except TimeoutError as exc:
            raise ConnectionApiError(
                error_code="TIMEOUT",
                message="Schema discovery timed out before completion.",
                http_status=status.HTTP_504_GATEWAY_TIMEOUT,
            ) from exc
        except Exception as exc:
            logger.warning(
                "Unexpected discovery failure for connection id=%s type=%s: %s",
                record.id,
                record.connector_type,
                type(exc).__name__,
            )
            raise ConnectionApiError(
                error_code="SERVER_ERROR",
                message="An unexpected error occurred during schema discovery.",
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            ) from exc
        finally:
            decrypted_creds = None
            secret_tokens = ()
            username_tokens = ()

        bounded_warnings = result.warnings[:_MAX_PERSISTED_WARNINGS]
        snapshot_meta: dict[str, Any] = dict(result.metadata)
        snapshot_meta["warnings"] = [w.model_dump(exclude_none=True) for w in bounded_warnings]
        snapshot_meta["warningCount"] = len(result.warnings)

        saved = await self._schema_repo.replace_snapshot(
            DiscoveredSchemaCreate(
                connectionId=uuid.UUID(record.id),
                discoveryStatus=result.discovery_status,
                discoveredAt=datetime.now(UTC),
                discoverySource=result.discovery_source,
                metadata=snapshot_meta,
                objects=result.objects,
            )
        )

        logger.info(
            "Schema discovery completed: connection_id=%s type=%s status=%s objects=%d",
            record.id,
            record.connector_type,
            saved.discoveryStatus,
            saved.objectCount,
        )
        return DiscoverSchemaResponse(
            snapshotId=saved.id,
            connectionId=saved.connectionId,
            discoveryStatus=saved.discoveryStatus,
            discoverySource=saved.discoverySource,
            schemaFingerprint=saved.schemaFingerprint,
            objectCount=saved.objectCount,
            warnings=saved.warnings,
            discoveredAt=saved.discoveredAt,
            objects=saved.objects,
        )

    async def get_latest_schema(self, connection_id: str) -> DiscoveredSchemaResponse:
        """Retrieve the latest persisted discovered schema snapshot for a connection."""
        canonical_id = _parse_connection_uuid(connection_id)
        await self._require_connection_exists(canonical_id)
        snapshot = await self._schema_repo.get_latest_by_connection_id(
            canonical_id,
            include_objects=True,
        )
        if snapshot is None:
            raise ConnectionApiError(
                error_code="SCHEMA_NOT_DISCOVERED",
                message="Schema has not been discovered for this connection yet.",
                http_status=status.HTTP_404_NOT_FOUND,
            )
        return snapshot

    async def list_objects(
        self,
        connection_id: str,
        *,
        search_query: str | None = None,
        object_type: SchemaObjectType | None = None,
        object_key: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> SchemaObjectListResponse:
        """List and paginate discovered objects for the latest schema snapshot."""
        canonical_id = _parse_connection_uuid(connection_id)
        await self._require_connection_exists(canonical_id)
        snapshot = await self._schema_repo.get_latest_by_connection_id(
            canonical_id,
            include_objects=False,
        )
        if snapshot is None:
            raise ConnectionApiError(
                error_code="SCHEMA_NOT_DISCOVERED",
                message="Schema has not been discovered for this connection yet.",
                http_status=status.HTTP_404_NOT_FOUND,
            )

        items, total = await self._schema_repo.list_objects(
            canonical_id,
            search_query=search_query,
            object_type=object_type,
            object_key=object_key,
            limit=limit,
            offset=offset,
        )
        return SchemaObjectListResponse(
            connectionId=snapshot.connectionId,
            schemaId=snapshot.id,
            items=items,
            total=total,
            limit=limit,
            offset=offset,
        )

    async def get_object_fields(
        self,
        connection_id: str,
        object_id: str,
        *,
        field_query: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> SchemaObjectFieldsResponse:
        """Retrieve ordered fields for a connection-owned schema object."""
        canonical_conn_id = _parse_connection_uuid(connection_id)
        canonical_obj_id = _parse_object_uuid(object_id)

        await self._require_connection_exists(canonical_conn_id)
        snapshot = await self._schema_repo.get_latest_by_connection_id(
            canonical_conn_id,
            include_objects=False,
        )
        if snapshot is None:
            raise ConnectionApiError(
                error_code="SCHEMA_NOT_DISCOVERED",
                message="Schema has not been discovered for this connection yet.",
                http_status=status.HTTP_404_NOT_FOUND,
            )

        obj = await self._schema_repo.get_object_with_fields(
            canonical_conn_id,
            canonical_obj_id,
            field_query=field_query,
        )
        if obj is None:
            raise ConnectionApiError(
                error_code="OBJECT_NOT_FOUND",
                message="Schema object not found for this connection.",
                http_status=status.HTTP_404_NOT_FOUND,
            )

        total_fields = len(obj.fields)
        paged_fields = obj.fields[offset : offset + limit]
        obj_summary = obj.model_copy(update={"fieldCount": total_fields})
        # Build lightweight summary without fields array for `object` field.
        from app.schemas.schemas import SchemaObjectSummaryResponse

        summary = SchemaObjectSummaryResponse(
            id=obj_summary.id,
            discoveredSchemaId=obj_summary.discoveredSchemaId,
            objectKey=obj_summary.objectKey,
            objectName=obj_summary.objectName,
            objectType=obj_summary.objectType,
            discoverySource=obj_summary.discoverySource,
            nativeMetadata=obj_summary.nativeMetadata,
            canonicalMetadata=obj_summary.canonicalMetadata,
            fieldCount=total_fields,
            createdAt=obj_summary.createdAt,
            updatedAt=obj_summary.updatedAt,
        )
        return SchemaObjectFieldsResponse(
            connectionId=snapshot.connectionId,
            schemaId=snapshot.id,
            object=summary,
            fields=paged_fields,
            total=total_fields,
            limit=limit,
            offset=offset,
        )

    async def _require_connection_exists(self, canonical_id: str) -> None:
        if await self._connection_repo.get_by_id(canonical_id) is None:
            raise ConnectionApiError(
                error_code="CONNECTION_NOT_FOUND",
                message="Connection not found.",
                http_status=status.HTTP_404_NOT_FOUND,
            )

    async def _load_decrypted_credentials(
        self,
        record: ConnectionRecord,
    ) -> StoredCredentialPayload | None:
        stored_cred = await self._credential_repo.get_by_connection_id(record.id)
        if stored_cred is None:
            return None
        if self._encryption_service is None:
            raise CredentialConfigurationError(
                "Credential encryption service is not configured for decryption."
            )
        try:
            return self._encryption_service.decrypt(
                connection_id=record.id,
                connector_type=record.connector_type,
                encrypted=stored_cred.encrypted,
            )
        except CredentialDecryptionError:
            raise
