"""JSON transient content validator implementing ConnectorTester."""

import json
import time
from typing import Any

from app.schemas.connection_test import (
    ConnectionTestRequest,
    ConnectionTestResult,
    ConnectorType,
    JsonConnectionTestRequest,
)
from app.services.testers.common import build_success_result, build_tester_failure, elapsed_ms
from app.services.testers.file_validation import FileValidationError, decode_utf8_file_content

DEFAULT_MAX_FILE_BYTES = 262_144


def _reject_non_standard_constant(_token: str) -> Any:
    raise ValueError("Non-standard JSON numeric constant is not permitted.")


def _summarize_json_structure(value: Any) -> str:
    if isinstance(value, dict):
        return f"top-level object with {len(value)} keys"
    if isinstance(value, list):
        return f"top-level array with {len(value)} items"
    if isinstance(value, bool):
        return "top-level boolean"
    if isinstance(value, (int, float)):
        return "top-level number"
    if isinstance(value, str):
        return "top-level string"
    if value is None:
        return "top-level null"
    return "valid JSON value"


class JsonConnectionTester:
    """Validates transient JSON file content without persisting or logging file data."""

    connector_type: ConnectorType = "JSON"

    def __init__(self, *, max_file_bytes: int = DEFAULT_MAX_FILE_BYTES) -> None:
        self._max_file_bytes = max_file_bytes

    async def test(
        self,
        request: ConnectionTestRequest,
        timeout_seconds: float,
    ) -> ConnectionTestResult:
        del timeout_seconds
        started = time.monotonic()
        if not isinstance(request, JsonConnectionTestRequest):
            return build_tester_failure(
                message="Invalid request type dispatched to JSON tester.",
                error_code="INVALID_CONFIGURATION",
                duration_ms=elapsed_ms(started),
            )

        if not request.config.fileName.lower().endswith(".json"):
            return build_tester_failure(
                message="JSON connector requires a filename ending with .json.",
                error_code="INVALID_CONFIGURATION",
                duration_ms=elapsed_ms(started),
            )

        try:
            text = decode_utf8_file_content(
                request.fileContent,
                max_bytes=self._max_file_bytes,
            )
        except FileValidationError as exc:
            return build_tester_failure(
                message=str(exc),
                error_code="INVALID_CONFIGURATION",
                duration_ms=elapsed_ms(started),
            )

        if not text.strip():
            return build_tester_failure(
                message="JSON file content is empty.",
                error_code="INVALID_CONFIGURATION",
                duration_ms=elapsed_ms(started),
            )

        try:
            parsed = json.loads(text, parse_constant=_reject_non_standard_constant)
        except json.JSONDecodeError as exc:
            return build_tester_failure(
                message=f"Invalid JSON syntax near line {exc.lineno}, column {exc.colno}.",
                error_code="INVALID_CONFIGURATION",
                duration_ms=elapsed_ms(started),
            )
        except (ValueError, RecursionError):
            return build_tester_failure(
                message="Invalid JSON syntax.",
                error_code="INVALID_CONFIGURATION",
                duration_ms=elapsed_ms(started),
            )

        summary = _summarize_json_structure(parsed)
        return build_success_result(
            f"JSON content validated successfully ({summary}).",
            elapsed_ms(started),
        )
