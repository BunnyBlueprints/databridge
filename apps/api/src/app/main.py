"""FastAPI application factory and ASGI entrypoint."""

from fastapi import FastAPI, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.routes.connections import router as connections_router
from app.api.routes.health import router as health_router
from app.core.config import Settings, get_settings
from app.core.errors import (
    build_failure_result,
    http_exception_handler,
    unhandled_exception_handler,
    validation_exception_handler,
)
from app.core.logging import configure_logging
from app.services.connection_test_service import ConnectionTestService
from app.services.testers.registry import (
    ConnectorTesterRegistry,
    create_default_tester_registry,
)

API_DESCRIPTION = """
Backend API for **DataBridge** connection testing and integration workflows.

### Supported Connectors
- `PostgreSQL`
- `MySQL`
- `MongoDB`
- `REST API`
- `CSV`
- `JSON`

### Current Scope (Step 4)
- Enforces strict connector-specific schema validation and normalized responses via
  `POST /api/v1/connections/test`.
- Dispatches validated requests to the connector tester registry for all six supported
  connectors (`PostgreSQL`, `MySQL`, `MongoDB`, `REST API`, `CSV`, `JSON`).
- Transient credentials and transient file contents are never persisted, cached, or logged.
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
        result = build_failure_result(
            message="Request payload exceeds the maximum allowed size.",
            error_code="INVALID_CONFIGURATION",
        )
        response = JSONResponse(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            content=result.model_dump(exclude_none=True),
        )
        await response(scope, receive, send)


def create_app(
    settings: Settings | None = None,
    registry: ConnectorTesterRegistry | None = None,
) -> FastAPI:
    """Create and configure the FastAPI application instance."""
    resolved_settings = settings or get_settings()
    configure_logging(resolved_settings.log_level)

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
    )

    resolved_registry = registry or create_default_tester_registry(
        max_file_bytes=resolved_settings.connection_test_max_file_bytes,
        max_rest_response_bytes=resolved_settings.rest_api_max_response_bytes,
    )
    application.state.settings = resolved_settings
    application.state.tester_registry = resolved_registry
    application.state.connection_test_service = ConnectionTestService(
        resolved_registry,
        timeout_seconds=resolved_settings.connection_test_timeout_seconds,
    )

    application.add_middleware(
        RequestSizeLimitMiddleware,
        max_bytes=resolved_settings.api_max_request_bytes,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=resolved_settings.cors_allowed_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "Accept"],
    )

    application.add_exception_handler(RequestValidationError, validation_exception_handler)  # type: ignore[arg-type]
    application.add_exception_handler(StarletteHTTPException, http_exception_handler)  # type: ignore[arg-type]
    application.add_exception_handler(Exception, unhandled_exception_handler)

    application.include_router(health_router)
    application.include_router(connections_router)

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
