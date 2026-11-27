"""Normalized error handling and response sanitization utilities."""

import re
from datetime import UTC, datetime

from fastapi import Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import redact_sensitive_text
from app.db.errors import (
    DatabaseConflictError,
    DatabaseError,
    DatabaseNotConfiguredError,
    DatabaseTimeoutError,
    DatabaseUnavailableError,
)
from app.schemas.connection_test import (
    SUPPORTED_CONNECTOR_TYPES,
    ConnectionTestErrorCode,
    ConnectionTestResult,
)
from app.schemas.connections import ApiErrorResponse, ConnectionApiError
from app.services.credential_encryption import (
    CredentialEncryptionError,
    CredentialPayloadError,
)

_TRACEBACK_OR_PATH_RE = re.compile(
    r"(traceback \(most recent call last\)|file \"[^\"]+\", line \d+"
    r"|/(?:users|home|app|var|etc|opt|srv|tmp)/\S+|[a-z]:\\\S+)",
    re.IGNORECASE,
)
_PRIVATE_IP_RE = re.compile(
    r"\b(?:10(?:\.\d{1,3}){3}|127(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})\b|\[::1\]"
)

MAX_ERROR_MESSAGE_LENGTH = 300


def utc_iso_timestamp() -> str:
    """Return current UTC timestamp in ISO-8601 format."""
    return datetime.now(UTC).isoformat()


def sanitize_error_message(
    raw_message: str | None,
    fallback: str,
    *,
    known_secrets: tuple[str, ...] = (),
    known_usernames: tuple[str, ...] = (),
) -> str:
    """Strip tracebacks, filesystem paths, secrets, and internal IPs from error text."""
    if not raw_message or not raw_message.strip():
        return fallback

    first_line = raw_message.replace("\r\n", "\n").strip().split("\n")[0].strip()
    if not first_line or _TRACEBACK_OR_PATH_RE.search(raw_message):
        return fallback

    sanitized = first_line
    for secret in sorted((s for s in known_secrets if len(s) >= 3), key=len, reverse=True):
        sanitized = sanitized.replace(secret, "[REDACTED]")
    for username in sorted((u for u in known_usernames if u), key=len, reverse=True):
        sanitized = re.sub(
            rf"(?<![A-Za-z0-9_]){re.escape(username)}(?![A-Za-z0-9_])",
            "[REDACTED]",
            sanitized,
        )

    sanitized = redact_sensitive_text(sanitized)
    sanitized = _PRIVATE_IP_RE.sub("[internal-host]", sanitized)

    if len(sanitized) > MAX_ERROR_MESSAGE_LENGTH:
        return f"{sanitized[: MAX_ERROR_MESSAGE_LENGTH - 1]}…"
    return sanitized


def build_failure_result(
    *,
    message: str,
    error_code: ConnectionTestErrorCode,
    status_value: str = "failed",
    duration_ms: int = 0,
) -> ConnectionTestResult:
    """Construct a normalized, secret-free ConnectionTestResult failure."""
    normalized_status = "timeout" if status_value == "timeout" else "failed"
    return ConnectionTestResult(
        success=False,
        status=normalized_status,
        message=sanitize_error_message(message, "Unable to complete the connection test."),
        errorCode=error_code,
        durationMs=max(0, duration_ms),
        testedAt=utc_iso_timestamp(),
    )


def _format_validation_error_summary(
    exc: RequestValidationError,
) -> tuple[ConnectionTestErrorCode, str, int]:
    """Derive a safe error code, message, and HTTP status without exposing input values."""
    errors = exc.errors()
    for err in errors:
        if err.get("type") == "json_invalid":
            return (
                "INVALID_CONFIGURATION",
                "Malformed JSON request payload.",
                status.HTTP_400_BAD_REQUEST,
            )

    for err in errors:
        loc = err.get("loc", ())
        err_type = str(err.get("type", ""))
        if err_type == "union_tag_invalid" or loc == ("body", "connectorType"):
            allowed = ", ".join(SUPPORTED_CONNECTOR_TYPES)
            return (
                "UNSUPPORTED_CONNECTOR",
                f"Unsupported connector type. Supported types: {allowed}.",
                status.HTTP_422_UNPROCESSABLE_CONTENT,
            )

    # Extract only safe field locations and sanitized messages; never include `input`.
    safe_details: list[str] = []
    for err in errors[:3]:
        loc_parts = [str(part) for part in err.get("loc", ()) if part != "body"]
        # Filter out discriminated-union branch tags in loc when possible for readability.
        field_path = ".".join(part for part in loc_parts if part not in SUPPORTED_CONNECTOR_TYPES)
        msg = sanitize_error_message(str(err.get("msg", "")), "Invalid value.")
        # Strip Pydantic's "Value error, " prefix if present.
        if msg.startswith("Value error, "):
            msg = msg[len("Value error, ") :]
        if field_path:
            safe_details.append(f"{field_path}: {msg}")
        else:
            safe_details.append(msg)

    summary = "; ".join(safe_details) if safe_details else "Invalid connection test configuration."
    return (
        "INVALID_CONFIGURATION",
        sanitize_error_message(
            f"Invalid connection configuration: {summary}",
            "Invalid connection configuration.",
        ),
        status.HTTP_422_UNPROCESSABLE_CONTENT,
    )


def _is_connection_test_path(request: Request) -> bool:
    """Return True when the request targets a connection-testing endpoint."""
    path = request.url.path
    return path.startswith("/api/v1/connections") and path.endswith("/test")


def _build_api_error_response(
    request: Request,
    *,
    http_status: int,
    error_code: ConnectionTestErrorCode | str,
    message: str,
    fallback: str,
    status_value: str = "failed",
) -> JSONResponse:
    """Return ConnectionTestResult on test routes and ApiErrorResponse on CRUD/other routes."""
    safe_message = sanitize_error_message(message, fallback)
    if _is_connection_test_path(request):
        result = build_failure_result(
            message=safe_message,
            error_code=error_code,
            status_value=status_value,
        )
        return JSONResponse(
            status_code=http_status,
            content=result.model_dump(exclude_none=True),
        )

    payload = ApiErrorResponse(
        errorCode=str(error_code),
        message=safe_message,
        timestamp=utc_iso_timestamp(),
    )
    return JSONResponse(
        status_code=http_status,
        content=payload.model_dump(),
    )


async def connection_api_exception_handler(
    request: Request, exc: ConnectionApiError
) -> JSONResponse:
    """Return normalized, secret-free error JSON for domain-level ConnectionApiError."""
    return _build_api_error_response(
        request,
        http_status=exc.http_status,
        error_code=exc.error_code,
        message=exc.message,
        fallback="Connection request could not be completed.",
    )


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Return normalized errors without leaking raw input or secrets."""
    error_code, message, http_status = _format_validation_error_summary(exc)
    return _build_api_error_response(
        request,
        http_status=http_status,
        error_code=error_code,
        message=message,
        fallback="Invalid connection configuration.",
    )


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Return normalized error JSON for HTTPException."""
    is_test_route = _is_connection_test_path(request)
    error_code: ConnectionTestErrorCode | str = "SERVER_ERROR"
    if exc.status_code in (400, 413, 422):
        error_code = "INVALID_CONFIGURATION"
    elif exc.status_code == 404:
        error_code = (
            "CONNECTION_NOT_FOUND"
            if request.url.path.startswith("/api/v1/connections")
            else "NOT_FOUND"
        )
    elif exc.status_code == 409:
        error_code = "INVALID_CONFIGURATION" if is_test_route else "DUPLICATE_CONNECTION"
    elif exc.status_code in (401, 403):
        error_code = "AUTHENTICATION_FAILED"
    elif exc.status_code in (408, 504):
        error_code = "TIMEOUT"
    elif exc.status_code == 501:
        error_code = "TESTER_NOT_IMPLEMENTED"

    detail_str = exc.detail if isinstance(exc.detail, str) else ""
    return _build_api_error_response(
        request,
        http_status=exc.status_code,
        error_code=error_code,
        message=detail_str,
        fallback="Request could not be processed.",
        status_value="timeout" if error_code == "TIMEOUT" else "failed",
    )


async def database_exception_handler(request: Request, exc: DatabaseError) -> JSONResponse:
    """Return normalized, secret-free error JSON for database layer failures."""
    is_test_route = _is_connection_test_path(request)
    if isinstance(exc, DatabaseTimeoutError):
        http_status = status.HTTP_504_GATEWAY_TIMEOUT
        error_code: ConnectionTestErrorCode | str = "TIMEOUT"
        status_value = "timeout"
    elif isinstance(exc, DatabaseConflictError):
        http_status = status.HTTP_409_CONFLICT
        error_code = "INVALID_CONFIGURATION" if is_test_route else "DUPLICATE_CONNECTION"
        status_value = "failed"
    elif isinstance(exc, (DatabaseNotConfiguredError, DatabaseUnavailableError)):
        http_status = status.HTTP_503_SERVICE_UNAVAILABLE
        error_code = "SERVER_ERROR" if is_test_route else "DATABASE_UNAVAILABLE"
        status_value = "failed"
    else:
        http_status = status.HTTP_500_INTERNAL_SERVER_ERROR
        error_code = "SERVER_ERROR"
        status_value = "failed"

    return _build_api_error_response(
        request,
        http_status=http_status,
        error_code=error_code,
        message=str(exc),
        fallback="Database operation failed.",
        status_value=status_value,
    )


async def credential_exception_handler(
    request: Request, exc: CredentialEncryptionError
) -> JSONResponse:
    """Return normalized, secret-free error JSON for credential encryption/decryption errors."""
    is_test_route = _is_connection_test_path(request)
    if isinstance(exc, CredentialPayloadError):
        http_status = status.HTTP_422_UNPROCESSABLE_CONTENT
        error_code: ConnectionTestErrorCode | str = "INVALID_CONFIGURATION"
    else:
        http_status = status.HTTP_500_INTERNAL_SERVER_ERROR
        error_code = "SERVER_ERROR" if is_test_route else "CREDENTIAL_ERROR"

    return _build_api_error_response(
        request,
        http_status=http_status,
        error_code=error_code,
        message=str(exc),
        fallback="Credential security operation failed.",
    )


async def unhandled_exception_handler(request: Request, _exc: Exception) -> JSONResponse:
    """Return generic sanitized error JSON for unexpected server exceptions."""
    return _build_api_error_response(
        request,
        http_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        error_code="SERVER_ERROR",
        message="An internal server error occurred while processing the request.",
        fallback="An internal server error occurred while processing the request.",
    )
