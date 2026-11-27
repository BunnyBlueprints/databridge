"""Pydantic schemas for persistent connection CRUD, safe responses, and saved connection tests."""

import re
from typing import Annotated, Any, Literal
from urllib.parse import parse_qsl, urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)

from app.schemas.connection_test import (
    ConnectionAuthType,
    ConnectionTestErrorCode,
    ConnectionTestTerminalStatus,
    ConnectorType,
    RestApiConnectionTestConfig,
    RestApiConnectionTestCredentials,
    _require_non_empty_text,
    _StrictRequestModel,
    _validate_host,
    _validate_mongo_uri,
    _validate_port,
)
from app.services.credential_encryption import (
    MongoDbStoredCredentials,
    SqlStoredCredentials,
    StoredCredentialPayload,
)

ConnectionStatus = Literal["Connected", "Disconnected", "Error"]
CredentialAction = Literal["keep", "replace", "clear"]

_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:[/\\]")
_ALLOWED_FILE_ENDPOINT_SCHEMES = ("local", "http", "https", "s3", "gs", "az")
_SENSITIVE_QUERY_KEYS = {
    "password",
    "passwd",
    "pwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "bearer",
    "bearertoken",
    "access_token",
    "refresh_token",
    "aws_session_token",
}


class ConnectionApiError(Exception):
    """Application-level exception carrying safe error metadata for CRUD/test endpoints."""

    def __init__(
        self,
        *,
        error_code: ConnectionTestErrorCode | str,
        message: str,
        http_status: int,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.http_status = http_status


class SqlPersistentConfig(_StrictRequestModel):
    """Non-secret SQL configuration stored in JSONB (`username` lives only in credentials)."""

    host: str
    port: int
    database: str

    @field_validator("host")
    @classmethod
    def _check_host(cls, value: str) -> str:
        return _validate_host(value)

    @field_validator("port", mode="before")
    @classmethod
    def _check_port(cls, value: object) -> int:
        return _validate_port(value)

    @field_validator("database")
    @classmethod
    def _check_database(cls, value: str) -> str:
        return _require_non_empty_text(value, "Database name", max_length=128)


class SqlPersistentCredentials(_StrictRequestModel):
    """Encrypted SQL credentials payload requiring username and optional password."""

    username: SecretStr
    password: SecretStr | None = None

    @field_validator("username")
    @classmethod
    def _check_username(cls, value: SecretStr) -> SecretStr:
        cleaned = _require_non_empty_text(value.get_secret_value(), "Username", max_length=128)
        return SecretStr(cleaned)

    @field_validator("password")
    @classmethod
    def _check_password(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None or not value.get_secret_value():
            return None
        return value


class MongoDbPersistentConfig(_StrictRequestModel):
    """Authoritative, credential-free MongoDB configuration stored in JSONB."""

    uri: str
    database: str

    @field_validator("uri")
    @classmethod
    def _check_uri(cls, value: str) -> str:
        uri = _validate_mongo_uri(value, allow_userinfo=False)
        parsed = urlsplit(uri)
        for param_key, _ in parse_qsl(parsed.query, keep_blank_values=True):
            normalized_key = param_key.strip().lower()
            if normalized_key in _SENSITIVE_QUERY_KEYS or "aws_session_token" in normalized_key:
                raise ValueError(
                    "Non-secret MongoDB config.uri must not contain credential query parameters."
                )
        return uri

    @field_validator("database")
    @classmethod
    def _check_database(cls, value: str) -> str:
        return _require_non_empty_text(value, "Database name", max_length=128)


class MongoDbPersistentCredentials(_StrictRequestModel):
    """Encrypted MongoDB credentials payload (single authoritative credential model)."""

    username: SecretStr | None = None
    password: SecretStr | None = None

    @field_validator("username")
    @classmethod
    def _check_username(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return None
        raw = value.get_secret_value().strip()
        if not raw:
            return None
        cleaned = _require_non_empty_text(raw, "Username", max_length=128)
        return SecretStr(cleaned)

    @field_validator("password")
    @classmethod
    def _check_password(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None or not value.get_secret_value():
            return None
        return value


def _validate_persistent_file_name(file_name: str, expected_ext: str) -> str:
    """Require a clean filename only (never a local OS, home-dir, or browser path)."""
    cleaned = _require_non_empty_text(file_name, "File name", max_length=255)
    lowered = cleaned.lower()
    if (
        any(token in cleaned for token in ("/", "\\", ":", ".."))
        or cleaned.startswith("~")
        or "%2f" in lowered
        or "%5c" in lowered
    ):
        raise ValueError("File name must be a bare filename without directory paths or traversal.")
    if not lowered.endswith(expected_ext):
        raise ValueError(f"File name must have a {expected_ext} extension.")
    return cleaned


def _validate_persistent_file_endpoint(endpoint: str | None) -> str | None:
    """Reject local OS/browser filesystem paths when storing optional file connector endpoints."""
    if endpoint is None:
        return None
    cleaned = _require_non_empty_text(endpoint, "Endpoint", max_length=512)
    lowered = cleaned.lower()
    if (
        lowered.startswith("file:")
        or cleaned.startswith(("/", "\\", "./", "../", "~"))
        or bool(_WINDOWS_DRIVE_RE.match(cleaned))
        or "fakepath" in lowered
        or ".." in cleaned
    ):
        raise ValueError(
            "Persistent file configuration must not reference local OS or browser paths."
        )
    if "://" not in cleaned:
        raise ValueError(
            "Persistent file endpoint must omit endpoint or use an allowed logical/remote URI."
        )
    parsed = urlsplit(cleaned)
    if parsed.scheme.lower() not in _ALLOWED_FILE_ENDPOINT_SCHEMES:
        raise ValueError("Persistent file endpoint uses an unsupported URI scheme.")
    if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
        raise ValueError("Endpoint must not contain embedded credentials.")
    if parsed.scheme.lower() == "local":
        if parsed.path or parsed.query or parsed.fragment or not parsed.netloc:
            raise ValueError("Logical local:// file endpoint must reference only a filename label.")
    elif not parsed.netloc:
        raise ValueError("Remote file endpoint must include a valid host or bucket.")
    return cleaned


class CsvPersistentConfig(_StrictRequestModel):
    """Persistent non-secret CSV configuration (never stores file content or local paths)."""

    fileName: str
    endpoint: str | None = None

    @field_validator("fileName")
    @classmethod
    def _check_persistent_csv_filename(cls, value: str) -> str:
        return _validate_persistent_file_name(value, ".csv")

    @field_validator("endpoint")
    @classmethod
    def _check_persistent_csv_endpoint(cls, value: str | None) -> str | None:
        return _validate_persistent_file_endpoint(value)


class JsonPersistentConfig(_StrictRequestModel):
    """Persistent non-secret JSON configuration (never stores file content or local paths)."""

    fileName: str
    endpoint: str | None = None

    @field_validator("fileName")
    @classmethod
    def _check_persistent_json_filename(cls, value: str) -> str:
        return _validate_persistent_file_name(value, ".json")

    @field_validator("endpoint")
    @classmethod
    def _check_persistent_json_endpoint(cls, value: str | None) -> str | None:
        return _validate_persistent_file_endpoint(value)


def _clean_connection_name(value: str) -> str:
    return _require_non_empty_text(value, "Connection name", max_length=120)


def _clean_description(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("Description must be a string.")
    if any(ord(ch) < 32 and ch not in ("\n", "\r", "\t") for ch in value):
        raise ValueError("Description contains invalid control characters.")
    stripped = value.strip()
    if not stripped:
        return None
    if len(stripped) > 500:
        raise ValueError("Description exceeds maximum length of 500 characters.")
    return stripped


def _has_non_empty_mongo_credentials(creds: MongoDbPersistentCredentials | None) -> bool:
    if creds is None:
        return False
    has_user = creds.username is not None and bool(creds.username.get_secret_value().strip())
    has_pw = creds.password is not None and bool(creds.password.get_secret_value())
    return has_user or has_pw


def _validate_rest_credentials_for_auth(
    *,
    auth_type: ConnectionAuthType,
    credentials: RestApiConnectionTestCredentials | None,
    require_when_auth_enabled: bool,
) -> bool:
    """Validate REST credentials match `authType` and return True if secret payload is non-empty."""
    has_api_key = bool(
        credentials
        and credentials.apiKey is not None
        and credentials.apiKey.get_secret_value().strip()
    )
    has_bearer = bool(
        credentials
        and credentials.bearerToken is not None
        and credentials.bearerToken.get_secret_value().strip()
    )

    if auth_type == "none":
        if credentials is not None:
            raise ValueError("Credentials must be omitted when REST API authType is 'none'.")
        return False

    if auth_type == "api_key":
        if has_bearer:
            raise ValueError("bearerToken is not permitted when REST API authType is 'api_key'.")
        if require_when_auth_enabled and not has_api_key:
            raise ValueError("apiKey is required when REST API authType is 'api_key'.")
        return has_api_key

    if auth_type == "bearer":
        if has_api_key:
            raise ValueError("apiKey is not permitted when REST API authType is 'bearer'.")
        if require_when_auth_enabled and not has_bearer:
            raise ValueError("bearerToken is required when REST API authType is 'bearer'.")
        return has_bearer

    raise ValueError("Unsupported REST API authentication type.")


class _CreateConnectionBase(_StrictRequestModel):
    name: str
    description: str | None = None

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str) -> str:
        return _clean_connection_name(value)

    @field_validator("description")
    @classmethod
    def _check_description(cls, value: str | None) -> str | None:
        return _clean_description(value)


class CreatePostgresConnectionRequest(_CreateConnectionBase):
    """Create request for PostgreSQL persistent connection (username & password encrypted)."""

    connectorType: Literal["PostgreSQL"]
    config: SqlPersistentConfig
    credentials: SqlPersistentCredentials


class CreateMysqlConnectionRequest(_CreateConnectionBase):
    """Create request for MySQL persistent connection (username & password encrypted)."""

    connectorType: Literal["MySQL"]
    config: SqlPersistentConfig
    credentials: SqlPersistentCredentials


class CreateMongoDbConnectionRequest(_CreateConnectionBase):
    """Create request for MongoDB persistent connection (usernames/passwords encrypted)."""

    connectorType: Literal["MongoDB"]
    config: MongoDbPersistentConfig
    credentials: MongoDbPersistentCredentials | None = None

    @model_validator(mode="after")
    def _check_credentials_non_empty(self) -> "CreateMongoDbConnectionRequest":
        if self.credentials is not None and not _has_non_empty_mongo_credentials(self.credentials):
            raise ValueError(
                "MongoDB credentials object must provide username or password, or be omitted."
            )
        return self


class CreateRestApiConnectionRequest(_CreateConnectionBase):
    """Create request for REST API persistent connection."""

    connectorType: Literal["REST API"]
    config: RestApiConnectionTestConfig
    credentials: RestApiConnectionTestCredentials | None = None

    @model_validator(mode="after")
    def _check_rest_credentials(self) -> "CreateRestApiConnectionRequest":
        _validate_rest_credentials_for_auth(
            auth_type=self.config.authType,
            credentials=self.credentials,
            require_when_auth_enabled=True,
        )
        return self


class CreateCsvConnectionRequest(_CreateConnectionBase):
    """Create request for CSV persistent connection (no persistent credentials or file content)."""

    connectorType: Literal["CSV"]
    config: CsvPersistentConfig
    credentials: None = None


class CreateJsonConnectionRequest(_CreateConnectionBase):
    """Create request for JSON persistent connection (no persistent credentials or file content)."""

    connectorType: Literal["JSON"]
    config: JsonPersistentConfig
    credentials: None = None


CreateConnectionRequest = Annotated[
    CreatePostgresConnectionRequest
    | CreateMysqlConnectionRequest
    | CreateMongoDbConnectionRequest
    | CreateRestApiConnectionRequest
    | CreateCsvConnectionRequest
    | CreateJsonConnectionRequest,
    Field(discriminator="connectorType"),
]


class UpdateConnectionRequest(_StrictRequestModel):
    """Update request supporting metadata edits, config updates, and explicit credential actions."""

    name: str | None = None
    connectorType: ConnectorType | None = None
    description: str | None = None
    config: dict[str, Any] | None = None
    credentialAction: CredentialAction = "keep"
    credentials: dict[str, Any] | None = None

    @field_validator("name")
    @classmethod
    def _check_optional_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _clean_connection_name(value)

    @field_validator("description")
    @classmethod
    def _check_optional_description(cls, value: str | None) -> str | None:
        return _clean_description(value)

    @model_validator(mode="after")
    def _check_action_and_credentials_pairing(self) -> "UpdateConnectionRequest":
        if "name" in self.model_fields_set and self.name is None:
            raise ValueError("Connection name cannot be null.")
        if "config" in self.model_fields_set and self.config is None:
            raise ValueError("Configuration cannot be null when provided.")
        if self.credentialAction == "replace" and self.credentials is None:
            raise ValueError("credentials must be provided when credentialAction is 'replace'.")
        if self.credentialAction != "replace" and self.credentials is not None:
            raise ValueError("credentials can only be supplied when credentialAction is 'replace'.")
        return self


class SavedConnectionTestRequest(_StrictRequestModel):
    """Request body for POST /api/v1/connections/{connection_id}/test (no credentials allowed)."""

    fileContent: str | None = None


class ConnectionSafeDetails(BaseModel):
    """Non-secret connector details aligned with frontend ConnectionSafeDetails."""

    model_config = ConfigDict(extra="forbid")

    host: str | None = None
    port: int | None = None
    database: str | None = None
    sanitizedUri: str | None = None
    baseUrl: str | None = None
    authType: ConnectionAuthType | None = None
    authConfigured: bool | None = None
    fileName: str | None = None


class ConnectionResponse(BaseModel):
    """Public, secret-free connection representation returned by CRUD endpoints."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    type: ConnectorType
    status: ConnectionStatus
    endpoint: str
    description: str | None = None
    config: dict[str, Any]
    details: ConnectionSafeDetails
    lastTestedAt: str | None = None
    lastTestStatus: ConnectionTestTerminalStatus | None = None
    errorMessage: str | None = None
    lastSuccessfulSync: str | None = None
    syncFrequency: str | None = None
    createdAt: str
    updatedAt: str


class ConnectionListResponse(BaseModel):
    """Paginated list response returned by GET /api/v1/connections."""

    model_config = ConfigDict(extra="forbid")

    items: list[ConnectionResponse]
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)


class ApiErrorResponse(BaseModel):
    """Normalized, secret-free error envelope returned by connection CRUD endpoints."""

    model_config = ConfigDict(extra="forbid")

    errorCode: str
    message: str
    timestamp: str


ConfigModelType = (
    SqlPersistentConfig
    | MongoDbPersistentConfig
    | RestApiConnectionTestConfig
    | CsvPersistentConfig
    | JsonPersistentConfig
)

CredentialsInputModelType = (
    SqlPersistentCredentials | MongoDbPersistentCredentials | RestApiConnectionTestCredentials
)


def to_stored_credential_payload(credentials: CredentialsInputModelType) -> StoredCredentialPayload:
    """Convert strict CRUD input credentials into encrypted payload models."""
    if isinstance(credentials, SqlPersistentCredentials):
        return SqlStoredCredentials(username=credentials.username, password=credentials.password)
    if isinstance(credentials, MongoDbPersistentCredentials):
        return MongoDbStoredCredentials(
            username=credentials.username,
            password=credentials.password,
        )
    if isinstance(credentials, RestApiConnectionTestCredentials):
        return credentials
    raise ValueError("Unsupported credential payload type.")


def normalize_stored_config_for_read(
    connector_type: ConnectorType,
    raw_config: dict[str, Any],
) -> ConfigModelType:
    """Validate persisted JSONB config safely while stripping legacy username fields on read."""
    cleaned = dict(raw_config)
    if connector_type in ("PostgreSQL", "MySQL", "MongoDB"):
        cleaned.pop("username", None)
    return validate_connector_config(connector_type, cleaned)


def validate_connector_config(
    connector_type: ConnectorType,
    raw_config: ConfigModelType | dict[str, Any],
) -> ConfigModelType:
    """Validate connector-specific non-secret configuration using strict Pydantic models."""
    model_map: dict[ConnectorType, type[ConfigModelType]] = {
        "PostgreSQL": SqlPersistentConfig,
        "MySQL": SqlPersistentConfig,
        "MongoDB": MongoDbPersistentConfig,
        "REST API": RestApiConnectionTestConfig,
        "CSV": CsvPersistentConfig,
        "JSON": JsonPersistentConfig,
    }
    model_cls = model_map[connector_type]
    if isinstance(raw_config, model_cls):
        return raw_config
    try:
        return model_cls.model_validate(raw_config)
    except ValidationError as exc:
        detail = "; ".join(str(err.get("msg", "Invalid field")) for err in exc.errors()[:2])
        if detail.startswith("Value error, "):
            detail = detail[len("Value error, ") :]
        raise ValueError(f"Invalid {connector_type} configuration: {detail}") from exc


def validate_connector_credentials(
    connector_type: ConnectorType,
    raw_credentials: CredentialsInputModelType | dict[str, Any] | None,
    *,
    config: ConfigModelType,
    require_non_empty: bool,
) -> StoredCredentialPayload | None:
    """Validate CRUD connector credentials and convert to storable secret payload."""
    if connector_type in ("CSV", "JSON"):
        if raw_credentials is not None:
            raise ValueError(f"Connector type '{connector_type}' does not accept credentials.")
        return None

    if connector_type in ("PostgreSQL", "MySQL"):
        if raw_credentials is None:
            raise ValueError(f"{connector_type} credentials (including username) are required.")
        try:
            validated = (
                raw_credentials
                if isinstance(raw_credentials, SqlPersistentCredentials)
                else SqlPersistentCredentials.model_validate(raw_credentials)
            )
        except ValidationError as exc:
            raise ValueError(f"Invalid {connector_type} credential payload.") from exc
        return to_stored_credential_payload(validated)

    if connector_type == "MongoDB":
        if raw_credentials is None:
            if require_non_empty:
                raise ValueError("MongoDB credentials are required when replacing credentials.")
            return None
        try:
            validated_mongo = (
                raw_credentials
                if isinstance(raw_credentials, MongoDbPersistentCredentials)
                else MongoDbPersistentCredentials.model_validate(raw_credentials)
            )
        except ValidationError as exc:
            raise ValueError("Invalid MongoDB credential payload.") from exc
        if not _has_non_empty_mongo_credentials(validated_mongo):
            raise ValueError("MongoDB credentials must include a non-empty username or password.")
        return to_stored_credential_payload(validated_mongo)

    if connector_type == "REST API":
        if not isinstance(config, RestApiConnectionTestConfig):
            raise ValueError("Invalid REST API configuration.")
        if raw_credentials is None:
            if config.authType in ("api_key", "bearer") and require_non_empty:
                raise ValueError(
                    f"Credentials are required when REST API authType is '{config.authType}'."
                )
            return None
        try:
            validated_rest = (
                raw_credentials
                if isinstance(raw_credentials, RestApiConnectionTestCredentials)
                else RestApiConnectionTestCredentials.model_validate(raw_credentials)
            )
        except ValidationError as exc:
            raise ValueError("Invalid REST API credential payload.") from exc
        _validate_rest_credentials_for_auth(
            auth_type=config.authType,
            credentials=validated_rest,
            require_when_auth_enabled=require_non_empty or config.authType != "none",
        )
        return validated_rest

    raise ValueError(f"Unsupported connector type '{connector_type}'.")


def build_safe_endpoint_and_details(
    *,
    connector_type: ConnectorType,
    config: ConfigModelType,
    auth_configured: bool,
) -> tuple[str, ConnectionSafeDetails, dict[str, Any]]:
    """Derive sanitized endpoint, non-secret details, and canonical JSONB-safe config."""
    safe_config = config.model_dump(exclude_none=True)

    if connector_type in ("PostgreSQL", "MySQL"):
        assert isinstance(config, SqlPersistentConfig)
        endpoint = f"{config.host}:{config.port}/{config.database}"
        details = ConnectionSafeDetails(
            host=config.host,
            port=config.port,
            database=config.database,
            authConfigured=auth_configured,
        )
        return endpoint, details, safe_config

    if connector_type == "MongoDB":
        assert isinstance(config, MongoDbPersistentConfig)
        parsed = urlsplit(config.uri)
        endpoint = f"{parsed.scheme}://{parsed.netloc}/{config.database}"
        details = ConnectionSafeDetails(
            sanitizedUri=endpoint,
            database=config.database,
            authConfigured=auth_configured,
        )
        return endpoint, details, safe_config

    if connector_type == "REST API":
        assert isinstance(config, RestApiConnectionTestConfig)
        parsed = urlsplit(config.baseUrl)
        clean_path = "" if parsed.path in ("", "/") else parsed.path.rstrip("/")
        endpoint = f"{parsed.scheme}://{parsed.netloc}{clean_path}"
        if parsed.query:
            endpoint = f"{endpoint}?{parsed.query}"
        details = ConnectionSafeDetails(
            baseUrl=endpoint,
            authType=config.authType,
            authConfigured=auth_configured,
        )
        return endpoint, details, safe_config

    if connector_type in ("CSV", "JSON"):
        assert isinstance(config, (CsvPersistentConfig, JsonPersistentConfig))
        endpoint = config.endpoint or f"local://{config.fileName}"
        details = ConnectionSafeDetails(
            fileName=config.fileName,
            authConfigured=False,
        )
        return endpoint, details, safe_config

    raise ValueError(f"Unsupported connector type '{connector_type}'.")
