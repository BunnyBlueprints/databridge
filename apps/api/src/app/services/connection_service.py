"""Business logic and transaction orchestration for persistent connection management."""

import logging
import uuid
from datetime import UTC, datetime

from fastapi import status

from app.core.errors import sanitize_error_message
from app.repositories.connections import ConnectionRecord, ConnectionRepository
from app.repositories.credentials import CredentialRepository
from app.schemas.connection_test import (
    ConnectionTestRequest,
    ConnectionTestResult,
    ConnectorType,
    CsvConnectionTestConfig,
    CsvConnectionTestRequest,
    JsonConnectionTestConfig,
    JsonConnectionTestRequest,
    MongoDbConnectionTestConfig,
    MongoDbConnectionTestCredentials,
    MongoDbConnectionTestRequest,
    MysqlConnectionTestRequest,
    PostgresConnectionTestRequest,
    RestApiConnectionTestConfig,
    RestApiConnectionTestCredentials,
    RestApiConnectionTestRequest,
    SqlConnectionTestConfig,
    SqlConnectionTestCredentials,
)
from app.schemas.connections import (
    ConfigModelType,
    ConnectionApiError,
    ConnectionListResponse,
    ConnectionResponse,
    ConnectionStatus,
    CreateConnectionRequest,
    CsvPersistentConfig,
    JsonPersistentConfig,
    MongoDbPersistentConfig,
    SavedConnectionTestRequest,
    SqlPersistentConfig,
    UpdateConnectionRequest,
    build_safe_endpoint_and_details,
    normalize_stored_config_for_read,
    to_stored_credential_payload,
    validate_connector_config,
    validate_connector_credentials,
)
from app.services.connection_test_service import ConnectionTestService
from app.services.credential_encryption import (
    CredentialConfigurationError,
    CredentialDecryptionError,
    CredentialEncryptionService,
    EncryptedCredential,
    MongoDbStoredCredentials,
    SqlStoredCredentials,
    StoredCredentialPayload,
)

logger = logging.getLogger("databridge.api")


def _parse_connection_uuid(connection_id: str) -> str:
    """Validate and normalize a connection UUID, raising 404 when malformed."""
    try:
        return str(uuid.UUID(connection_id))
    except ValueError as exc:
        raise ConnectionApiError(
            error_code="CONNECTION_NOT_FOUND",
            message="Connection not found.",
            http_status=status.HTTP_404_NOT_FOUND,
        ) from exc


def _parse_iso_timestamp(value: str | None) -> datetime:
    if value:
        try:
            parsed = datetime.fromisoformat(value)
            return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
        except ValueError:
            pass
    return datetime.now(UTC)


def _to_connection_response(record: ConnectionRecord) -> ConnectionResponse:
    """Convert an internal DB ConnectionRecord into a strict secret-free API response."""
    config_model = normalize_stored_config_for_read(record.connector_type, record.config)
    endpoint, details, safe_config = build_safe_endpoint_and_details(
        connector_type=record.connector_type,
        config=config_model,
        auth_configured=record.auth_configured,
    )
    return ConnectionResponse(
        id=record.id,
        name=record.name,
        type=record.connector_type,
        status=record.status,
        endpoint=endpoint,
        description=record.description,
        config=safe_config,
        details=details,
        lastTestedAt=record.last_tested_at,
        lastTestStatus=record.last_test_status,
        errorMessage=record.last_error_message,
        lastSuccessfulSync=record.last_successful_sync,
        syncFrequency=record.sync_frequency,
        createdAt=record.created_at,
        updatedAt=record.updated_at,
    )


def _collect_secret_tokens(
    decrypted_creds: StoredCredentialPayload | None,
    *,
    legacy_username: str | None = None,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Collect plaintext secret/username values strictly for defensive error sanitization."""
    secrets: list[str] = []
    usernames: list[str] = [legacy_username] if legacy_username else []

    if isinstance(decrypted_creds, SqlStoredCredentials):
        if decrypted_creds.username is not None:
            usernames.append(decrypted_creds.username.get_secret_value())
        if decrypted_creds.password is not None:
            secrets.append(decrypted_creds.password.get_secret_value())
    elif isinstance(decrypted_creds, MongoDbStoredCredentials):
        if decrypted_creds.username is not None:
            usernames.append(decrypted_creds.username.get_secret_value())
        if decrypted_creds.password is not None:
            secrets.append(decrypted_creds.password.get_secret_value())
        if decrypted_creds.connectionUri is not None:
            secrets.append(decrypted_creds.connectionUri.get_secret_value())
    elif isinstance(decrypted_creds, RestApiConnectionTestCredentials):
        if decrypted_creds.apiKey is not None:
            secrets.append(decrypted_creds.apiKey.get_secret_value())
        if decrypted_creds.bearerToken is not None:
            secrets.append(decrypted_creds.bearerToken.get_secret_value())

    return tuple(s for s in secrets if s), tuple(u for u in usernames if u)


def _build_test_request(
    *,
    record: ConnectionRecord,
    config_model: ConfigModelType,
    decrypted_creds: StoredCredentialPayload | None,
    file_content: str | None,
) -> ConnectionTestRequest:
    """Assemble an in-memory transient ConnectionTestRequest from saved metadata and secrets."""
    connector_type = record.connector_type
    legacy_username_raw = record.config.get("username")
    legacy_username = (
        legacy_username_raw.strip()
        if isinstance(legacy_username_raw, str) and legacy_username_raw.strip()
        else None
    )

    if connector_type in ("PostgreSQL", "MySQL"):
        assert isinstance(config_model, SqlPersistentConfig)
        resolved_username = (
            decrypted_creds.username.get_secret_value()
            if isinstance(decrypted_creds, SqlStoredCredentials)
            and decrypted_creds.username is not None
            else legacy_username
        )
        if not resolved_username:
            raise ConnectionApiError(
                error_code="INVALID_CONFIGURATION",
                message=(
                    f"{connector_type} username credential is required to test this connection."
                ),
                http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
            )

        password_val = (
            decrypted_creds.password if isinstance(decrypted_creds, SqlStoredCredentials) else None
        )
        test_sql_config = SqlConnectionTestConfig(
            host=config_model.host,
            port=config_model.port,
            database=config_model.database,
            username=resolved_username,
        )
        test_sql_creds = (
            SqlConnectionTestCredentials(password=password_val)
            if password_val is not None
            else None
        )
        if connector_type == "PostgreSQL":
            return PostgresConnectionTestRequest(
                connectionId=record.id,
                connectorType="PostgreSQL",
                config=test_sql_config,
                credentials=test_sql_creds,
            )
        return MysqlConnectionTestRequest(
            connectionId=record.id,
            connectorType="MySQL",
            config=test_sql_config,
            credentials=test_sql_creds,
        )

    if connector_type == "MongoDB":
        assert isinstance(config_model, MongoDbPersistentConfig)
        resolved_mongo_user = (
            decrypted_creds.username.get_secret_value()
            if isinstance(decrypted_creds, MongoDbStoredCredentials)
            and decrypted_creds.username is not None
            else legacy_username
        )
        mongo_password = (
            decrypted_creds.password
            if isinstance(decrypted_creds, MongoDbStoredCredentials)
            else None
        )
        mongo_legacy_uri = (
            decrypted_creds.connectionUri
            if isinstance(decrypted_creds, MongoDbStoredCredentials)
            else None
        )
        test_mongo_creds = (
            MongoDbConnectionTestCredentials(
                password=mongo_password,
                connectionUri=mongo_legacy_uri,
            )
            if (mongo_password is not None or mongo_legacy_uri is not None)
            else None
        )
        return MongoDbConnectionTestRequest(
            connectionId=record.id,
            connectorType="MongoDB",
            config=MongoDbConnectionTestConfig(
                uri=config_model.uri,
                database=config_model.database,
                username=resolved_mongo_user,
            ),
            credentials=test_mongo_creds,
        )

    if connector_type == "REST API":
        assert isinstance(config_model, RestApiConnectionTestConfig)
        if config_model.authType == "none":
            return RestApiConnectionTestRequest(
                connectionId=record.id,
                connectorType="REST API",
                config=config_model,
                credentials=None,
            )

        rest_creds = (
            decrypted_creds
            if isinstance(decrypted_creds, RestApiConnectionTestCredentials)
            else None
        )
        if config_model.authType == "api_key":
            has_key = bool(
                rest_creds
                and rest_creds.apiKey is not None
                and rest_creds.apiKey.get_secret_value().strip()
            )
            if not has_key or (rest_creds and rest_creds.bearerToken is not None):
                raise ConnectionApiError(
                    error_code="INVALID_CONFIGURATION",
                    message="Stored API key credentials do not match REST API authType 'api_key'.",
                    http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
                )
        elif config_model.authType == "bearer":
            has_bearer = bool(
                rest_creds
                and rest_creds.bearerToken is not None
                and rest_creds.bearerToken.get_secret_value().strip()
            )
            if not has_bearer or (rest_creds and rest_creds.apiKey is not None):
                raise ConnectionApiError(
                    error_code="INVALID_CONFIGURATION",
                    message=(
                        "Stored bearer token credentials do not match REST API authType 'bearer'."
                    ),
                    http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
                )

        return RestApiConnectionTestRequest(
            connectionId=record.id,
            connectorType="REST API",
            config=config_model,
            credentials=rest_creds,
        )

    if connector_type == "CSV":
        assert isinstance(config_model, CsvPersistentConfig)
        if file_content is None:
            raise ConnectionApiError(
                error_code="INVALID_CONFIGURATION",
                message="CSV fileContent is required to test this connection.",
                http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
            )
        return CsvConnectionTestRequest(
            connectionId=record.id,
            connectorType="CSV",
            config=CsvConnectionTestConfig(
                fileName=config_model.fileName,
                endpoint=config_model.endpoint,
            ),
            fileContent=file_content,
        )

    if connector_type == "JSON":
        assert isinstance(config_model, JsonPersistentConfig)
        if file_content is None:
            raise ConnectionApiError(
                error_code="INVALID_CONFIGURATION",
                message="JSON fileContent is required to test this connection.",
                http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
            )
        return JsonConnectionTestRequest(
            connectionId=record.id,
            connectorType="JSON",
            config=JsonConnectionTestConfig(
                fileName=config_model.fileName,
                endpoint=config_model.endpoint,
            ),
            fileContent=file_content,
        )

    raise ConnectionApiError(
        error_code="UNSUPPORTED_CONNECTOR",
        message=f"Unsupported connector type '{connector_type}'.",
        http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
    )


class ConnectionService:
    """Orchestrates connection persistence, credential vault encryption, and saved testing."""

    def __init__(
        self,
        *,
        connection_repo: ConnectionRepository,
        credential_repo: CredentialRepository,
        encryption_service: CredentialEncryptionService | None,
        connection_test_service: ConnectionTestService,
    ) -> None:
        self._connection_repo = connection_repo
        self._credential_repo = credential_repo
        self._encryption_service = encryption_service
        self._connection_test_service = connection_test_service

    def _require_encryption_service(self) -> CredentialEncryptionService:
        if self._encryption_service is None:
            raise CredentialConfigurationError(
                "Credential encryption keys must be configured for persistent credentials."
            )
        return self._encryption_service

    def _encrypt_payload(
        self,
        *,
        connection_id: str,
        connector_type: ConnectorType,
        credentials: StoredCredentialPayload,
    ) -> EncryptedCredential:
        encryption_service = self._require_encryption_service()
        return encryption_service.encrypt(
            connection_id=connection_id,
            connector_type=connector_type,
            credentials=credentials,
        )

    async def create_connection(self, payload: CreateConnectionRequest) -> ConnectionResponse:
        """Validate, encrypt credentials, and persist a new connection atomically."""
        connection_id = str(uuid.uuid4())
        connector_type: ConnectorType = payload.connectorType
        config_model = payload.config
        encrypted: EncryptedCredential | None = None
        auth_configured = False

        if payload.credentials is not None:
            storable = to_stored_credential_payload(payload.credentials)
            encrypted = self._encrypt_payload(
                connection_id=connection_id,
                connector_type=connector_type,
                credentials=storable,
            )
            auth_configured = True

        endpoint, _, safe_config = build_safe_endpoint_and_details(
            connector_type=connector_type,
            config=config_model,
            auth_configured=auth_configured,
        )

        async with self._connection_repo.transaction() as conn:
            created = await self._connection_repo.create(
                connection_id=connection_id,
                name=payload.name,
                connector_type=connector_type,
                description=payload.description,
                endpoint=endpoint,
                config=safe_config,
                auth_configured=auth_configured,
                status="Disconnected",
                conn=conn,
            )
            if encrypted is not None:
                await self._credential_repo.upsert(
                    connection_id=connection_id,
                    encrypted=encrypted,
                    conn=conn,
                )

        logger.info(
            "Connection created: id=%s connectorType=%s authConfigured=%s",
            created.id,
            created.connector_type,
            created.auth_configured,
        )
        return _to_connection_response(created)

    async def list_connections(
        self,
        *,
        status_filter: ConnectionStatus | None = None,
        type_filter: ConnectorType | None = None,
        search_query: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> ConnectionListResponse:
        """List non-secret connection records without reading or decrypting credentials."""
        records, total = await self._connection_repo.list_connections(
            status_filter=status_filter,
            type_filter=type_filter,
            search_query=search_query,
            limit=limit,
            offset=offset,
        )
        return ConnectionListResponse(
            items=[_to_connection_response(record) for record in records],
            total=total,
            limit=limit,
            offset=offset,
        )

    async def get_connection(self, connection_id: str) -> ConnectionResponse:
        """Load one connection record without querying or decrypting stored credentials."""
        canonical_id = _parse_connection_uuid(connection_id)
        record = await self._connection_repo.get_by_id(canonical_id)
        if record is None:
            raise ConnectionApiError(
                error_code="CONNECTION_NOT_FOUND",
                message="Connection not found.",
                http_status=status.HTTP_404_NOT_FOUND,
            )
        return _to_connection_response(record)

    async def update_connection(
        self,
        connection_id: str,
        payload: UpdateConnectionRequest,
    ) -> ConnectionResponse:
        """Update connection metadata/config and apply credential keep/replace/clear atomically."""
        canonical_id = _parse_connection_uuid(connection_id)

        async with self._connection_repo.transaction() as conn:
            existing = await self._connection_repo.get_by_id(
                canonical_id,
                for_update=True,
                conn=conn,
            )
            if existing is None:
                raise ConnectionApiError(
                    error_code="CONNECTION_NOT_FOUND",
                    message="Connection not found.",
                    http_status=status.HTTP_404_NOT_FOUND,
                )

            if (
                payload.connectorType is not None
                and payload.connectorType != existing.connector_type
            ):
                raise ConnectionApiError(
                    error_code="INVALID_CONFIGURATION",
                    message="Connector type cannot be changed for an existing connection.",
                    http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
                )

            next_name = payload.name if payload.name is not None else existing.name
            next_description = (
                payload.description
                if "description" in payload.model_fields_set
                else existing.description
            )

            try:
                prev_config_model = normalize_stored_config_for_read(
                    existing.connector_type,
                    existing.config,
                )
                next_config_model = (
                    validate_connector_config(existing.connector_type, payload.config)
                    if payload.config is not None
                    else prev_config_model
                )
            except ValueError as exc:
                raise ConnectionApiError(
                    error_code="INVALID_CONFIGURATION",
                    message=str(exc),
                    http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
                ) from exc

            next_auth_configured, credentials_changed = await self._apply_credential_update(
                existing=existing,
                prev_config_model=prev_config_model,
                next_config_model=next_config_model,
                payload=payload,
                conn=conn,
            )

            next_endpoint, _, next_safe_config = build_safe_endpoint_and_details(
                connector_type=existing.connector_type,
                config=next_config_model,
                auth_configured=next_auth_configured,
            )
            prev_safe_config = prev_config_model.model_dump(exclude_none=True)
            config_changed = (next_safe_config != prev_safe_config) or (
                next_endpoint != existing.endpoint
            )

            if config_changed or credentials_changed:
                next_status: ConnectionStatus = "Disconnected"
                clear_error_message = True
            else:
                next_status = existing.status
                clear_error_message = False

            updated = await self._connection_repo.update(
                connection_id=canonical_id,
                name=next_name,
                description=next_description,
                endpoint=next_endpoint,
                config=next_safe_config,
                auth_configured=next_auth_configured,
                status=next_status,
                clear_error_message=clear_error_message,
                conn=conn,
            )
            if updated is None:
                raise ConnectionApiError(
                    error_code="CONNECTION_NOT_FOUND",
                    message="Connection not found.",
                    http_status=status.HTTP_404_NOT_FOUND,
                )

        logger.info(
            "Connection updated: id=%s connectorType=%s configChanged=%s "
            "credentialsChanged=%s status=%s",
            updated.id,
            updated.connector_type,
            config_changed,
            credentials_changed,
            updated.status,
        )
        return _to_connection_response(updated)

    async def _apply_credential_update(
        self,
        *,
        existing: ConnectionRecord,
        prev_config_model: ConfigModelType,
        next_config_model: ConfigModelType,
        payload: UpdateConnectionRequest,
        conn: object,
    ) -> tuple[bool, bool]:
        action = payload.credentialAction
        connector_type = existing.connector_type

        if connector_type in ("CSV", "JSON"):
            if action == "replace" or payload.credentials is not None:
                raise ConnectionApiError(
                    error_code="INVALID_CONFIGURATION",
                    message=(
                        f"Connector type '{connector_type}' does not accept stored credentials."
                    ),
                    http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
                )
            if action == "clear" and existing.auth_configured:
                deleted = await self._credential_repo.delete_by_connection_id(
                    existing.id, conn=conn
                )
                return False, deleted or existing.auth_configured
            return False, False

        if connector_type == "REST API":
            assert isinstance(prev_config_model, RestApiConnectionTestConfig)
            assert isinstance(next_config_model, RestApiConnectionTestConfig)
            prev_auth_type = prev_config_model.authType
            next_auth_type = next_config_model.authType

            if action == "replace":
                if next_auth_type == "none":
                    raise ConnectionApiError(
                        error_code="INVALID_CONFIGURATION",
                        message="Credentials cannot be stored when REST API authType is 'none'.",
                        http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    )
                validated_creds = self._validate_replacement_credentials(
                    connector_type=connector_type,
                    raw_credentials=payload.credentials,
                    config=next_config_model,
                )
                encrypted = self._encrypt_payload(
                    connection_id=existing.id,
                    connector_type=connector_type,
                    credentials=validated_creds,
                )
                await self._credential_repo.upsert(
                    connection_id=existing.id,
                    encrypted=encrypted,
                    conn=conn,
                )
                return True, True

            if action == "clear":
                if next_auth_type in ("api_key", "bearer"):
                    raise ConnectionApiError(
                        error_code="INVALID_CONFIGURATION",
                        message=(
                            "Cannot clear credentials while REST API "
                            f"authType is '{next_auth_type}'."
                        ),
                        http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    )
                deleted = await self._credential_repo.delete_by_connection_id(
                    existing.id, conn=conn
                )
                return False, deleted or existing.auth_configured

            # action == "keep"
            if next_auth_type == "none":
                deleted = await self._credential_repo.delete_by_connection_id(
                    existing.id, conn=conn
                )
                return False, deleted or existing.auth_configured

            if not existing.auth_configured or prev_auth_type != next_auth_type:
                raise ConnectionApiError(
                    error_code="INVALID_CONFIGURATION",
                    message=(
                        "Credentials must be provided via credentialAction='replace' when "
                        f"configuring REST API authType '{next_auth_type}'."
                    ),
                    http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
                )
            return True, False

        if connector_type in ("PostgreSQL", "MySQL"):
            if action == "clear":
                raise ConnectionApiError(
                    error_code="INVALID_CONFIGURATION",
                    message=(
                        f"{connector_type} requires encrypted username credentials; "
                        "use credentialAction='replace' to update credentials."
                    ),
                    http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
                )
            if action == "replace":
                validated_creds = self._validate_replacement_credentials(
                    connector_type=connector_type,
                    raw_credentials=payload.credentials,
                    config=next_config_model,
                )
                encrypted = self._encrypt_payload(
                    connection_id=existing.id,
                    connector_type=connector_type,
                    credentials=validated_creds,
                )
                await self._credential_repo.upsert(
                    connection_id=existing.id,
                    encrypted=encrypted,
                    conn=conn,
                )
                return True, True

            if "username" in existing.config:
                stored_cred = await self._credential_repo.get_by_connection_id(
                    existing.id, conn=conn
                )
                has_encrypted_username = False
                if stored_cred is not None:
                    decrypted_legacy = self._require_encryption_service().decrypt(
                        encrypted=stored_cred.encrypted,
                        connection_id=existing.id,
                        connector_type=existing.connector_type,
                    )
                    has_encrypted_username = (
                        isinstance(decrypted_legacy, SqlStoredCredentials)
                        and decrypted_legacy.username is not None
                    )
                if not has_encrypted_username:
                    raise ConnectionApiError(
                        error_code="INVALID_CONFIGURATION",
                        message=(
                            f"Legacy {connector_type} connection stores an unencrypted username "
                            "in configuration; update using credentialAction='replace' with "
                            "username to migrate credentials safely."
                        ),
                        http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    )

            return existing.auth_configured, False

        # MongoDB (optional authentication; allows keep, replace, or clear)
        if action == "replace":
            validated_creds = self._validate_replacement_credentials(
                connector_type=connector_type,
                raw_credentials=payload.credentials,
                config=next_config_model,
            )
            encrypted = self._encrypt_payload(
                connection_id=existing.id,
                connector_type=connector_type,
                credentials=validated_creds,
            )
            await self._credential_repo.upsert(
                connection_id=existing.id,
                encrypted=encrypted,
                conn=conn,
            )
            return True, True

        if action == "clear":
            deleted = await self._credential_repo.delete_by_connection_id(existing.id, conn=conn)
            return False, deleted or existing.auth_configured

        return existing.auth_configured, False

    @staticmethod
    def _validate_replacement_credentials(
        *,
        connector_type: ConnectorType,
        raw_credentials: dict[str, object] | None,
        config: ConfigModelType,
    ) -> StoredCredentialPayload:
        try:
            validated = validate_connector_credentials(
                connector_type,
                raw_credentials,
                config=config,
                require_non_empty=True,
            )
        except ValueError as exc:
            raise ConnectionApiError(
                error_code="INVALID_CONFIGURATION",
                message=str(exc),
                http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
            ) from exc
        if validated is None:
            raise ConnectionApiError(
                error_code="INVALID_CONFIGURATION",
                message="Non-empty credentials are required when credentialAction is 'replace'.",
                http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
            )
        return validated

    async def delete_connection(self, connection_id: str) -> None:
        """Delete a connection and its associated encrypted credentials atomically."""
        canonical_id = _parse_connection_uuid(connection_id)
        async with self._connection_repo.transaction() as conn:
            await self._credential_repo.delete_by_connection_id(canonical_id, conn=conn)
            deleted = await self._connection_repo.delete(canonical_id, conn=conn)
            if not deleted:
                raise ConnectionApiError(
                    error_code="CONNECTION_NOT_FOUND",
                    message="Connection not found.",
                    http_status=status.HTTP_404_NOT_FOUND,
                )
        logger.info("Connection deleted: id=%s", canonical_id)

    async def test_saved_connection(
        self,
        connection_id: str,
        payload: SavedConnectionTestRequest,
    ) -> tuple[ConnectionTestResult, int]:
        """Decrypt saved credentials in memory, execute connector test, and persist outcome."""
        canonical_id = _parse_connection_uuid(connection_id)
        record = await self._connection_repo.get_by_id(canonical_id)
        if record is None:
            raise ConnectionApiError(
                error_code="CONNECTION_NOT_FOUND",
                message="Connection not found.",
                http_status=status.HTTP_404_NOT_FOUND,
            )

        if record.connector_type in ("CSV", "JSON"):
            if payload.fileContent is None:
                raise ConnectionApiError(
                    error_code="INVALID_CONFIGURATION",
                    message=(
                        f"{record.connector_type} fileContent is required to test this connection."
                    ),
                    http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
                )
        elif payload.fileContent is not None:
            raise ConnectionApiError(
                error_code="INVALID_CONFIGURATION",
                message="fileContent is only supported when testing CSV or JSON connections.",
                http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
            )

        try:
            config_model = normalize_stored_config_for_read(record.connector_type, record.config)
        except ValueError as exc:
            raise ConnectionApiError(
                error_code="INVALID_CONFIGURATION",
                message=str(exc),
                http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
            ) from exc

        decrypted_creds: StoredCredentialPayload | None = None
        if record.auth_configured:
            stored_cred = await self._credential_repo.get_by_connection_id(canonical_id)
            if stored_cred is None:
                raise CredentialDecryptionError(
                    "Stored credentials for this connection are missing and must be replaced."
                )
            encryption_service = self._require_encryption_service()
            decrypted_creds = encryption_service.decrypt(
                encrypted=stored_cred.encrypted,
                connection_id=record.id,
                connector_type=record.connector_type,
            )
        elif record.connector_type in ("PostgreSQL", "MySQL"):
            raise ConnectionApiError(
                error_code="INVALID_CONFIGURATION",
                message=f"Stored credentials are required for {record.connector_type} testing.",
                http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
            )
        elif (
            record.connector_type == "REST API"
            and isinstance(config_model, RestApiConnectionTestConfig)
            and config_model.authType in ("api_key", "bearer")
        ):
            raise ConnectionApiError(
                error_code="INVALID_CONFIGURATION",
                message="Stored credentials are required for this REST API authentication type.",
                http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
            )

        test_request = _build_test_request(
            record=record,
            config_model=config_model,
            decrypted_creds=decrypted_creds,
            file_content=payload.fileContent,
        )

        # Execute external connectivity check outside DB transaction locks.
        raw_result, http_status = await self._connection_test_service.test_connection(test_request)

        legacy_username = record.config.get("username")
        known_secrets, known_usernames = _collect_secret_tokens(
            decrypted_creds,
            legacy_username=legacy_username if isinstance(legacy_username, str) else None,
        )
        safe_message = (
            raw_result.message
            if raw_result.success
            else sanitize_error_message(
                raw_result.message,
                "Unable to complete the connection test.",
                known_secrets=known_secrets,
                known_usernames=known_usernames,
            )
        )
        sanitized_result = ConnectionTestResult(
            success=raw_result.success,
            status=raw_result.status,
            message=safe_message,
            errorCode=raw_result.errorCode,
            durationMs=raw_result.durationMs,
            testedAt=raw_result.testedAt,
        )

        tested_at = _parse_iso_timestamp(sanitized_result.testedAt)
        next_status: ConnectionStatus = "Connected" if sanitized_result.success else "Disconnected"
        last_error_message = None if sanitized_result.success else safe_message

        async with self._connection_repo.transaction() as conn:
            updated = await self._connection_repo.update_test_outcome(
                connection_id=canonical_id,
                status=next_status,
                last_tested_at=tested_at,
                last_test_status=sanitized_result.status,
                last_error_message=last_error_message,
                conn=conn,
            )
            if updated is None:
                raise ConnectionApiError(
                    error_code="CONNECTION_NOT_FOUND",
                    message="Connection not found.",
                    http_status=status.HTTP_404_NOT_FOUND,
                )

        return sanitized_result, http_status
