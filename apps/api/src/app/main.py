"""FastAPI application factory and ASGI entrypoint."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.routes.connections import router as connections_router
from app.api.routes.health import router as health_router
from app.api.routes.mappings import router as mappings_router
from app.api.routes.schemas import router as schemas_router
from app.core.config import Settings, get_settings
from app.core.errors import (
    build_failure_result,
    connection_api_exception_handler,
    credential_exception_handler,
    database_exception_handler,
    http_exception_handler,
    unhandled_exception_handler,
    utc_iso_timestamp,
    validation_exception_handler,
)
from app.core.logging import configure_logging
from app.db.errors import DatabaseError
from app.db.pool import DatabaseManager
from app.repositories.connections import ConnectionRepository
from app.repositories.credentials import CredentialRepository
from app.repositories.mappings import MappingRepository
from app.repositories.schemas import SchemaRepository
from app.schemas.connections import ApiErrorResponse, ConnectionApiError
from app.services.connection_service import ConnectionService
from app.services.connection_test_service import ConnectionTestService
from app.services.credential_encryption import (
    CredentialEncryptionError,
    CredentialEncryptionService,
)
from app.services.discovery.registry import (
    ConnectorDiscovererRegistry,
    create_default_discoverer_registry,
)
from app.services.discovery.service import SchemaDiscoveryService
from app.services.mappings import MappingService
from app.services.testers.registry import (
    ConnectorTesterRegistry,
    create_default_tester_registry,
)

API_DESCRIPTION = """
Backend API for **DataBridge** persistent connection management and connectivity testing.

### Supported Connectors
- `PostgreSQL`
- `MySQL`
- `MongoDB`
- `REST API`
- `CSV`
- `JSON`

### Capabilities
- Persistent connection CRUD (`POST/GET/PUT/DELETE /api/v1/connections`) backed by PostgreSQL
  and AES-256-GCM encrypted credentials (`connection_credentials`).
- Saved connection testing (`POST /api/v1/connections/{connection_id}/test`) using decrypted
  credentials inside backend memory only.
- Stateless transient testing (`POST /api/v1/connections/test`) for all six connector types.

### Deployment Security Boundary
These endpoints must only be deployed behind an authenticated access-control boundary; UUIDs
alone do not provide authorization or tenant isolation.
""".strip()


class _PayloadTooLargeError(Exception):
    """Internal sentinel raised when streamed request body exceeds max size."""


class RequestSizeLimitMiddleware:
    """ASGI middleware enforcing maximum request body size without buffering unbounded payloads."""

    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") not in ("POST", "PUT", "PATCH"):
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        raw_content_length = headers.get(b"content-length")
        if raw_content_length is not None:
            try:
                content_length = int(raw_content_length.decode("ascii"))
            except ValueError:
                content_length = None
            if content_length is not None and content_length > self.max_bytes:
                await self._send_payload_too_large(scope, receive, send)
                return

        bytes_received = 0
        response_started = False

        async def guarded_receive() -> Message:
            nonlocal bytes_received
            message = await receive()
            if message["type"] == "http.request":
                bytes_received += len(message.get("body", b""))
                if bytes_received > self.max_bytes:
                    raise _PayloadTooLargeError()
            return message

        async def tracked_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, guarded_receive, tracked_send)
        except _PayloadTooLargeError:
            if not response_started:
                await self._send_payload_too_large(scope, receive, send)

    @staticmethod
    async def _send_payload_too_large(scope: Scope, receive: Receive, send: Send) -> None:
        path = str(scope.get("path", ""))
        is_test_route = path.startswith("/api/v1/connections") and path.endswith("/test")
        if is_test_route:
            body = build_failure_result(
                message="Request payload exceeds the maximum allowed size.",
                error_code="INVALID_CONFIGURATION",
            ).model_dump(exclude_none=True)
        else:
            body = ApiErrorResponse(
                errorCode="INVALID_CONFIGURATION",
                message="Request payload exceeds the maximum allowed size.",
                timestamp=utc_iso_timestamp(),
            ).model_dump()
        response = JSONResponse(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            content=body,
        )
        await response(scope, receive, send)


def create_app(
    settings: Settings | None = None,
    registry: ConnectorTesterRegistry | None = None,
    database_manager: DatabaseManager | None = None,
    credential_encryption_service: CredentialEncryptionService | None = None,
    discoverer_registry: ConnectorDiscovererRegistry | None = None,
) -> FastAPI:
    """Create and configure the FastAPI application instance."""
    resolved_settings = settings or get_settings()
    configure_logging(resolved_settings.log_level)

    resolved_encryption_service = credential_encryption_service
    if resolved_encryption_service is None:
        if resolved_settings.database_enabled or resolved_settings.encryption_configured:
            resolved_encryption_service = CredentialEncryptionService.from_settings(
                resolved_settings
            )

    resolved_db_manager = database_manager or DatabaseManager(resolved_settings)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        await application.state.database_manager.startup()
        try:
            yield
        finally:
            await application.state.database_manager.shutdown()

    docs_url = "/docs" if resolved_settings.enable_api_docs else None
    redoc_url = "/redoc" if resolved_settings.enable_api_docs else None
    openapi_url = "/openapi.json" if resolved_settings.enable_api_docs else None

    application = FastAPI(
        title="DataBridge API",
        version="0.1.0",
        description=API_DESCRIPTION,
        docs_url=docs_url,
        redoc_url=redoc_url,
        openapi_url=openapi_url,
        lifespan=lifespan,
    )

    resolved_registry = registry or create_default_tester_registry(
        max_file_bytes=resolved_settings.connection_test_max_file_bytes,
        max_rest_response_bytes=resolved_settings.rest_api_max_response_bytes,
    )
    connection_test_service = ConnectionTestService(
        resolved_registry,
        timeout_seconds=resolved_settings.connection_test_timeout_seconds,
    )
    connection_repo = ConnectionRepository(resolved_db_manager)
    credential_repo = CredentialRepository(resolved_db_manager)
    schema_repo = SchemaRepository(resolved_db_manager)
    mapping_repo = MappingRepository(resolved_db_manager)
    connection_service = ConnectionService(
        connection_repo=connection_repo,
        credential_repo=credential_repo,
        encryption_service=resolved_encryption_service,
        connection_test_service=connection_test_service,
    )
    resolved_discoverer_registry = discoverer_registry or create_default_discoverer_registry(
        max_file_bytes=resolved_settings.connection_test_max_file_bytes,
        max_rest_response_bytes=resolved_settings.rest_api_max_response_bytes,
    )
    schema_discovery_service = SchemaDiscoveryService(
        connection_repo=connection_repo,
        credential_repo=credential_repo,
        schema_repo=schema_repo,
        encryption_service=resolved_encryption_service,
        discoverer_registry=resolved_discoverer_registry,
        timeout_seconds=resolved_settings.schema_discovery_timeout_seconds,
        max_objects=resolved_settings.schema_discovery_max_objects,
        max_fields_per_object=resolved_settings.schema_discovery_max_fields_per_object,
        default_sample_limit=resolved_settings.schema_discovery_default_sample_limit,
        max_sample_limit=resolved_settings.schema_discovery_max_sample_limit,
    )
    mapping_service = MappingService(
        connection_repo=connection_repo,
        schema_repo=schema_repo,
        mapping_repo=mapping_repo,
    )

    application.state.settings = resolved_settings
    application.state.database_manager = resolved_db_manager
    application.state.credential_encryption_service = resolved_encryption_service
    application.state.tester_registry = resolved_registry
    application.state.discoverer_registry = resolved_discoverer_registry
    application.state.connection_test_service = connection_test_service
    application.state.connection_repository = connection_repo
    application.state.credential_repository = credential_repo
    application.state.schema_repository = schema_repo
    application.state.mapping_repository = mapping_repo
    application.state.connection_service = connection_service
    application.state.schema_discovery_service = schema_discovery_service
    application.state.mapping_service = mapping_service

    application.add_middleware(
        RequestSizeLimitMiddleware,
        max_bytes=resolved_settings.api_max_request_bytes,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=resolved_settings.cors_allowed_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Accept"],
    )

    application.add_exception_handler(ConnectionApiError, connection_api_exception_handler)  # type: ignore[arg-type]
    application.add_exception_handler(RequestValidationError, validation_exception_handler)  # type: ignore[arg-type]
    application.add_exception_handler(StarletteHTTPException, http_exception_handler)  # type: ignore[arg-type]
    application.add_exception_handler(DatabaseError, database_exception_handler)  # type: ignore[arg-type]
    application.add_exception_handler(CredentialEncryptionError, credential_exception_handler)  # type: ignore[arg-type]
    application.add_exception_handler(Exception, unhandled_exception_handler)

    application.include_router(health_router)
    application.include_router(connections_router)
    application.include_router(schemas_router)
    application.include_router(mappings_router)

    return application


app = create_app()

if __name__ == "__main__":
    import uvicorn

    cfg = get_settings()
    uvicorn.run(
        "app.main:app",
        host=cfg.api_host,
        port=cfg.api_port,
        reload=cfg.app_env == "development",
        access_log=False,
    )
