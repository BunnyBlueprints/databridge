"""Connection testing request and response schemas matching frontend connectionTest.ts."""

from typing import Annotated, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

SUPPORTED_CONNECTOR_TYPES: tuple[str, ...] = (
    "PostgreSQL",
    "MySQL",
    "MongoDB",
    "REST API",
    "CSV",
    "JSON",
)

ConnectorType = Literal[
    "PostgreSQL",
    "MySQL",
    "MongoDB",
    "REST API",
    "CSV",
    "JSON",
]

ConnectionAuthType = Literal["none", "api_key", "bearer"]

ConnectionTestTerminalStatus = Literal["connected", "failed", "timeout"]

KnownConnectionTestErrorCode = Literal[
    "CONNECTION_REFUSED",
    "DNS_RESOLUTION_FAILED",
    "AUTHENTICATION_FAILED",
    "TIMEOUT",
    "INVALID_CONFIGURATION",
    "UNSUPPORTED_CONNECTOR",
    "TESTER_NOT_IMPLEMENTED",
    "SERVER_ERROR",
    "NETWORK_ERROR",
]

ConnectionTestErrorCode = KnownConnectionTestErrorCode | str


class _StrictRequestModel(BaseModel):
    """Base model that forbids unknown fields and hides raw inputs in validation errors."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


def _require_non_empty_text(value: str, field_label: str, max_length: int = 255) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_label} must be a string.")
    if any(ord(ch) < 32 for ch in value):
        raise ValueError(f"{field_label} contains invalid control characters.")
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"{field_label} is required.")
    if len(stripped) > max_length:
        raise ValueError(f"{field_label} exceeds maximum length of {max_length} characters.")
    return stripped


def _validate_host(value: str) -> str:
    host = _require_non_empty_text(value, "Host", max_length=255)
    if any(token in host for token in ("://", "/", "@", "?", "#")) or any(
        ch.isspace() for ch in host
    ):
        raise ValueError(
            "Host must be a valid hostname or address without scheme, path, or credentials."
        )
    return host


def _validate_port(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("Port must be an integer between 1 and 65535.")
    if value < 1 or value > 65535:
        raise ValueError("Port must be between 1 and 65535.")
    return value


def _validate_mongo_uri(value: str, *, allow_userinfo: bool) -> str:
    uri = _require_non_empty_text(value, "MongoDB URI", max_length=1024)
    if not (uri.startswith("mongodb://") or uri.startswith("mongodb+srv://")):
        raise ValueError("MongoDB URI must start with mongodb:// or mongodb+srv://.")
    if any(ch.isspace() for ch in uri):
        raise ValueError("MongoDB URI must not contain whitespace.")

    parsed = urlparse(uri)
    if not parsed.netloc or not parsed.hostname:
        raise ValueError("MongoDB URI must include a valid host.")
    if not allow_userinfo and (
        parsed.username is not None or parsed.password is not None or "@" in parsed.netloc
    ):
        raise ValueError(
            "Non-secret MongoDB config.uri must not contain embedded credentials; "
            "use credentials instead."
        )
    return uri


def _validate_http_url(value: str) -> str:
    url = _require_non_empty_text(value, "Base URL", max_length=2048)
    if any(ch.isspace() for ch in url):
        raise ValueError("Base URL must not contain whitespace.")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("Base URL must use http:// or https://.")
    if not parsed.netloc or not parsed.hostname:
        raise ValueError("Base URL must include a valid hostname.")
    if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
        raise ValueError("Base URL must not contain embedded credentials.")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Base URL contains an invalid port.") from exc
    if port is not None and (port < 1 or port > 65535):
        raise ValueError("Base URL port must be between 1 and 65535.")
    return url


def _validate_file_name(value: str, expected_ext: str) -> str:
    file_name = _require_non_empty_text(value, "File name", max_length=255)
    if ".." in file_name or file_name.startswith(("/", "\\")) or file_name.endswith(("/", "\\")):
        raise ValueError("File name must be a valid relative filename without path traversal.")
    if not file_name.lower().endswith(expected_ext):
        raise ValueError(f"File name must have a {expected_ext} extension.")
    return file_name


class SqlConnectionTestConfig(_StrictRequestModel):
    """Non-secret configuration required to test a PostgreSQL or MySQL connection."""

    host: str
    port: int
    database: str
    username: str

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

    @field_validator("username")
    @classmethod
    def _check_username(cls, value: str) -> str:
        return _require_non_empty_text(value, "Username", max_length=128)


class SqlConnectionTestCredentials(_StrictRequestModel):
    """Transient SQL credentials; never stored or logged."""

    password: SecretStr | None = None


class MongoDbConnectionTestConfig(_StrictRequestModel):
    """Non-secret configuration required to test a MongoDB connection."""

    uri: str
    database: str
    username: str | None = None

    @field_validator("uri")
    @classmethod
    def _check_uri(cls, value: str) -> str:
        return _validate_mongo_uri(value, allow_userinfo=False)

    @field_validator("database")
    @classmethod
    def _check_database(cls, value: str) -> str:
        return _require_non_empty_text(value, "Database name", max_length=128)

    @field_validator("username")
    @classmethod
    def _check_username(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            return None
        return _require_non_empty_text(stripped, "Username", max_length=128)


class MongoDbConnectionTestCredentials(_StrictRequestModel):
    """Transient MongoDB credentials; never stored or logged."""

    password: SecretStr | None = None
    connectionUri: SecretStr | None = None

    @field_validator("connectionUri")
    @classmethod
    def _check_connection_uri(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return None
        raw = value.get_secret_value().strip()
        if not raw:
            return None
        _validate_mongo_uri(raw, allow_userinfo=True)
        return value


class RestApiConnectionTestConfig(_StrictRequestModel):
    """Non-secret configuration required to test a REST API connection."""

    baseUrl: str
    authType: ConnectionAuthType

    @field_validator("baseUrl")
    @classmethod
    def _check_base_url(cls, value: str) -> str:
        return _validate_http_url(value)


class RestApiConnectionTestCredentials(_StrictRequestModel):
    """Transient REST API credentials; never stored or logged."""

    apiKey: SecretStr | None = None
    bearerToken: SecretStr | None = None


class FileConnectionTestConfigBase(_StrictRequestModel):
    """Shared non-secret file connector fields."""

    fileName: str
    endpoint: str | None = None

    @field_validator("endpoint")
    @classmethod
    def _check_endpoint(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = _require_non_empty_text(value, "Endpoint", max_length=512)
        if "://" in cleaned:
            parsed = urlparse(cleaned)
            if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
                raise ValueError("Endpoint must not contain embedded credentials.")
        return cleaned


class CsvConnectionTestConfig(FileConnectionTestConfigBase):
    """Configuration required to test a CSV source."""

    @field_validator("fileName")
    @classmethod
    def _check_csv_file_name(cls, value: str) -> str:
        return _validate_file_name(value, ".csv")


class JsonConnectionTestConfig(FileConnectionTestConfigBase):
    """Configuration required to test a JSON source."""

    @field_validator("fileName")
    @classmethod
    def _check_json_file_name(cls, value: str) -> str:
        return _validate_file_name(value, ".json")


class _ConnectionTestRequestBase(_StrictRequestModel):
    connectionId: str | None = None

    @field_validator("connectionId")
    @classmethod
    def _check_connection_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_non_empty_text(value, "Connection ID", max_length=128)


class PostgresConnectionTestRequest(_ConnectionTestRequestBase):
    """PostgreSQL connection test request payload."""

    connectorType: Literal["PostgreSQL"]
    config: SqlConnectionTestConfig
    credentials: SqlConnectionTestCredentials | None = None


class MysqlConnectionTestRequest(_ConnectionTestRequestBase):
    """MySQL connection test request payload."""

    connectorType: Literal["MySQL"]
    config: SqlConnectionTestConfig
    credentials: SqlConnectionTestCredentials | None = None


class MongoDbConnectionTestRequest(_ConnectionTestRequestBase):
    """MongoDB connection test request payload."""

    connectorType: Literal["MongoDB"]
    config: MongoDbConnectionTestConfig
    credentials: MongoDbConnectionTestCredentials | None = None


class RestApiConnectionTestRequest(_ConnectionTestRequestBase):
    """REST API connection test request payload."""

    connectorType: Literal["REST API"]
    config: RestApiConnectionTestConfig
    credentials: RestApiConnectionTestCredentials | None = None


class CsvConnectionTestRequest(_ConnectionTestRequestBase):
    """CSV connection test request payload with transient fileContent (no credentials)."""

    connectorType: Literal["CSV"]
    config: CsvConnectionTestConfig
    fileContent: str
    credentials: None = None


class JsonConnectionTestRequest(_ConnectionTestRequestBase):
    """JSON connection test request payload with transient fileContent (no credentials)."""

    connectorType: Literal["JSON"]
    config: JsonConnectionTestConfig
    fileContent: str
    credentials: None = None


ConnectionTestRequest = Annotated[
    PostgresConnectionTestRequest
    | MysqlConnectionTestRequest
    | MongoDbConnectionTestRequest
    | RestApiConnectionTestRequest
    | CsvConnectionTestRequest
    | JsonConnectionTestRequest,
    Field(discriminator="connectorType"),
]


class ConnectionTestResult(BaseModel):
    """Normalized, secret-free response returned by POST /api/v1/connections/test."""

    model_config = ConfigDict(extra="forbid")

    success: bool
    status: ConnectionTestTerminalStatus
    message: str
    errorCode: ConnectionTestErrorCode | None = None
    durationMs: int | None = Field(default=None, ge=0)
    testedAt: str | None = None
