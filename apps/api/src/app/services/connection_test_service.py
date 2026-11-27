"""Connection testing orchestration and dispatcher service."""

import asyncio
import logging
import time

from fastapi import status

from app.core.errors import build_failure_result, sanitize_error_message, utc_iso_timestamp
from app.schemas.connection_test import (
    SUPPORTED_CONNECTOR_TYPES,
    ConnectionTestRequest,
    ConnectionTestResult,
)
from app.services.testers.registry import ConnectorTesterRegistry

logger = logging.getLogger("databridge.api")


class ConnectionTestService:
    """Dispatches validated connection test requests to registered connector testers."""

    def __init__(
        self,
        registry: ConnectorTesterRegistry,
        *,
        timeout_seconds: float,
    ) -> None:
        self._registry = registry
        self._timeout_seconds = timeout_seconds

    async def test_connection(
        self,
        request: ConnectionTestRequest,
    ) -> tuple[ConnectionTestResult, int]:
        """Execute a connection test and return `(normalized_result, http_status)`."""
        started = time.monotonic()
        connector_type = request.connectorType
        has_connection_id = bool(request.connectionId)

        if connector_type not in SUPPORTED_CONNECTOR_TYPES:
            elapsed_ms = self._elapsed_ms(started)
            logger.warning(
                "Connection test rejected: connectorType=%s hasConnectionId=%s outcome=unsupported",
                connector_type,
                has_connection_id,
            )
            return (
                build_failure_result(
                    message=f"Connector type '{connector_type}' is not supported.",
                    error_code="UNSUPPORTED_CONNECTOR",
                    duration_ms=elapsed_ms,
                ),
                status.HTTP_422_UNPROCESSABLE_CONTENT,
            )

        tester = self._registry.get(connector_type)
        if tester is None:
            elapsed_ms = self._elapsed_ms(started)
            logger.info(
                "Connection test dispatched: connectorType=%s hasConnectionId=%s "
                "outcome=not_implemented durationMs=%d",
                connector_type,
                has_connection_id,
                elapsed_ms,
            )
            return (
                build_failure_result(
                    message=(f"Connection testing for '{connector_type}' is not implemented yet."),
                    error_code="TESTER_NOT_IMPLEMENTED",
                    duration_ms=elapsed_ms,
                ),
                status.HTTP_501_NOT_IMPLEMENTED,
            )

        try:
            raw_result = await asyncio.wait_for(
                tester.test(request, self._timeout_seconds),
                timeout=self._timeout_seconds,
            )
            elapsed_ms = self._elapsed_ms(started)
            validated = ConnectionTestResult.model_validate(raw_result)
            normalized = ConnectionTestResult(
                success=validated.success,
                status=validated.status,
                message=sanitize_error_message(
                    validated.message,
                    "Connection established successfully."
                    if validated.success
                    else "Unable to complete the connection test.",
                ),
                errorCode=None if validated.success else (validated.errorCode or "SERVER_ERROR"),
                durationMs=validated.durationMs if validated.durationMs is not None else elapsed_ms,
                testedAt=validated.testedAt or utc_iso_timestamp(),
            )
            logger.info(
                "Connection test completed: connectorType=%s hasConnectionId=%s "
                "outcome=%s errorCode=%s durationMs=%d",
                connector_type,
                has_connection_id,
                normalized.status,
                normalized.errorCode or "none",
                normalized.durationMs or elapsed_ms,
            )
            http_status = self._resolve_http_status(normalized)
            return normalized, http_status
        except TimeoutError:
            elapsed_ms = self._elapsed_ms(started)
            logger.warning(
                "Connection test timed out: connectorType=%s hasConnectionId=%s durationMs=%d",
                connector_type,
                has_connection_id,
                elapsed_ms,
            )
            return (
                build_failure_result(
                    message="Connection test timed out before the target responded.",
                    error_code="TIMEOUT",
                    status_value="timeout",
                    duration_ms=elapsed_ms,
                ),
                status.HTTP_504_GATEWAY_TIMEOUT,
            )
        except Exception as exc:
            elapsed_ms = self._elapsed_ms(started)
            # Log only exception class name; never log exception message or request secrets.
            logger.error(
                "Connection test error: connectorType=%s hasConnectionId=%s "
                "exceptionType=%s durationMs=%d",
                connector_type,
                has_connection_id,
                type(exc).__name__,
                elapsed_ms,
            )
            return (
                build_failure_result(
                    message="An internal error occurred while executing the connection test.",
                    error_code="SERVER_ERROR",
                    duration_ms=elapsed_ms,
                ),
                status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @staticmethod
    def _elapsed_ms(started: float) -> int:
        return max(0, round((time.monotonic() - started) * 1000))

    @staticmethod
    def _resolve_http_status(result: ConnectionTestResult) -> int:
        if result.success:
            return status.HTTP_200_OK
        if result.status == "timeout":
            return status.HTTP_504_GATEWAY_TIMEOUT
        if result.errorCode == "TESTER_NOT_IMPLEMENTED":
            return status.HTTP_501_NOT_IMPLEMENTED
        if result.errorCode in ("INVALID_CONFIGURATION", "UNSUPPORTED_CONNECTOR"):
            return status.HTTP_422_UNPROCESSABLE_CONTENT
        return status.HTTP_200_OK
