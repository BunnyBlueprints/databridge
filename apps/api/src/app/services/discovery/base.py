"""Schema discovery contracts, context models, and safe discovery exceptions."""

from dataclasses import dataclass, field
from typing import Any, Protocol

from app.schemas.connection_test import ConnectionTestErrorCode, ConnectorType
from app.schemas.connections import ConfigModelType
from app.schemas.schemas import (
    DiscoveryOptionsInput,
    DiscoveryStatus,
    SchemaObjectInput,
    SchemaWarning,
    SnapshotDiscoverySource,
)
from app.services.credential_encryption import StoredCredentialPayload


class DiscoveryExecutionError(Exception):
    """Sanitized failure raised by a connector discoverer (never carries credentials)."""

    def __init__(
        self,
        *,
        error_code: ConnectionTestErrorCode | str,
        message: str,
        http_status: int,
        is_timeout: bool = False,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.http_status = http_status
        self.is_timeout = is_timeout


@dataclass(frozen=True)
class DiscoveryContext:
    """In-memory execution context passed to a connector discoverer (never persisted directly)."""

    connection_id: str
    connector_type: ConnectorType
    config: ConfigModelType
    credentials: StoredCredentialPayload | None
    legacy_username: str | None
    options: DiscoveryOptionsInput
    file_name: str | None
    file_content: str | None
    timeout_seconds: float
    max_objects: int
    max_fields_per_object: int
    sample_limit: int


@dataclass(frozen=True)
class DiscoveryExecutionResult:
    """Normalized structural discovery output returned by a connector discoverer."""

    discovery_status: DiscoveryStatus
    discovery_source: SnapshotDiscoverySource
    objects: list[SchemaObjectInput]
    warnings: list[SchemaWarning] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class ConnectorDiscoverer(Protocol):
    """Protocol implemented by all connector-specific schema discoverers."""

    connector_type: ConnectorType

    async def discover(self, ctx: DiscoveryContext) -> DiscoveryExecutionResult:
        """Execute bounded, read-only discovery and return normalized schema objects."""
        ...
