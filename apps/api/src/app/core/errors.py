"""Normalized error handling and response sanitization utilities."""

import re
from datetime import UTC, datetime

from fastapi import Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import redact_sensitive_text
from app.schemas.connection_test import (
    SUPPORTED_CONNECTOR_TYPES,
    ConnectionTestErrorCode,
    ConnectionTestResult,
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


def sanitize_error_message(raw_message: str | None, fallback: str) -> str:
    """Strip tracebacks, filesystem paths, secrets, and internal IPs from error text."""
    if not raw_message or not raw_message.strip():
        return fallback

    first_line = raw_message.replace("\r\n", "\n").strip().split("\n")[0].strip()
    if not first_line or _TRACEBACK_OR_PATH_RE.search(raw_message):
        return fallback

    sanitized = redact_sensitive_text(first_line)
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


async def validation_exception_handler(
    _request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Return normalized ConnectionTestResult errors without leaking raw input or secrets."""
    error_code, message, http_status = _format_validation_error_summary(exc)
    result = build_failure_result(message=message, error_code=error_code)
    return JSONResponse(
        status_code=http_status,
        content=result.model_dump(exclude_none=True),
    )


async def http_exception_handler(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Return normalized error JSON for HTTPException."""
    error_code: ConnectionTestErrorCode = "SERVER_ERROR"
    if exc.status_code in (400, 413, 422):
        error_code = "INVALID_CONFIGURATION"
    elif exc.status_code in (401, 403):
        error_code = "AUTHENTICATION_FAILED"
    elif exc.status_code in (408, 504):
        error_code = "TIMEOUT"
    elif exc.status_code == 501:
        error_code = "TESTER_NOT_IMPLEMENTED"

    detail_str = exc.detail if isinstance(exc.detail, str) else None
    result = build_failure_result(
        message=sanitize_error_message(detail_str, "Request could not be processed."),
        error_code=error_code,
        status_value="timeout" if error_code == "TIMEOUT" else "failed",
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=result.model_dump(exclude_none=True),
    )


async def unhandled_exception_handler(_request: Request, _exc: Exception) -> JSONResponse:
    """Return generic sanitized error JSON for unexpected server exceptions."""
    result = build_failure_result(
        message="An internal server error occurred while processing the connection test.",
        error_code="SERVER_ERROR",
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=result.model_dump(exclude_none=True),
    )
