"""Shared helpers for database connection testers."""

import time
from collections.abc import Iterator

from app.core.errors import build_failure_result, utc_iso_timestamp
from app.schemas.connection_test import ConnectionTestErrorCode, ConnectionTestResult


def elapsed_ms(started: float) -> int:
    """Return elapsed milliseconds since `started` (monotonic clock)."""
    return max(0, round((time.monotonic() - started) * 1000))


def iter_exception_chain(exc: BaseException) -> Iterator[BaseException]:
    """Yield an exception and its __cause__/__context__ chain without cycles."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def build_success_result(message: str, duration_ms: int) -> ConnectionTestResult:
    """Create a normalized success result with an ISO-8601 UTC timestamp."""
    return ConnectionTestResult(
        success=True,
        status="connected",
        message=message,
        durationMs=max(0, duration_ms),
        testedAt=utc_iso_timestamp(),
    )


def build_tester_failure(
    *,
    message: str,
    error_code: ConnectionTestErrorCode,
    duration_ms: int,
    is_timeout: bool = False,
) -> ConnectionTestResult:
    """Create a normalized, sanitized failure result."""
    return build_failure_result(
        message=message,
        error_code=error_code,
        status_value="timeout" if is_timeout else "failed",
        duration_ms=duration_ms,
    )
