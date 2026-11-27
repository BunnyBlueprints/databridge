"""CSV transient content validator implementing ConnectorTester."""

import csv
import io
import time

from app.schemas.connection_test import (
    ConnectionTestRequest,
    ConnectionTestResult,
    ConnectorType,
    CsvConnectionTestRequest,
)
from app.services.testers.common import build_success_result, build_tester_failure, elapsed_ms
from app.services.testers.file_validation import FileValidationError, decode_utf8_file_content

DEFAULT_MAX_FILE_BYTES = 262_144


class CsvConnectionTester:
    """Validates transient CSV file content without persisting or logging file data."""

    connector_type: ConnectorType = "CSV"

    def __init__(self, *, max_file_bytes: int = DEFAULT_MAX_FILE_BYTES) -> None:
        self._max_file_bytes = max_file_bytes

    async def test(
        self,
        request: ConnectionTestRequest,
        timeout_seconds: float,
    ) -> ConnectionTestResult:
        del timeout_seconds
        started = time.monotonic()
        if not isinstance(request, CsvConnectionTestRequest):
            return build_tester_failure(
                message="Invalid request type dispatched to CSV tester.",
                error_code="INVALID_CONFIGURATION",
                duration_ms=elapsed_ms(started),
            )

        if not request.config.fileName.lower().endswith(".csv"):
            return build_tester_failure(
                message="CSV connector requires a filename ending with .csv.",
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
                message="CSV file content is empty.",
                error_code="INVALID_CONFIGURATION",
                duration_ms=elapsed_ms(started),
            )

        try:
            reader = csv.reader(io.StringIO(text, newline=""), strict=True)
            headers: list[str] | None = None
            expected_columns = 0
            data_row_count = 0

            for raw_line_num, row in enumerate(reader, start=1):
                # Skip completely empty lines consistently.
                if len(row) == 0:
                    continue

                if headers is None:
                    normalized_headers = [col.strip() for col in row]
                    if any(not col for col in normalized_headers):
                        return build_tester_failure(
                            message="CSV header row contains empty column names.",
                            error_code="INVALID_CONFIGURATION",
                            duration_ms=elapsed_ms(started),
                        )
                    if len(set(normalized_headers)) != len(normalized_headers):
                        return build_tester_failure(
                            message="CSV header row contains duplicate column names.",
                            error_code="INVALID_CONFIGURATION",
                            duration_ms=elapsed_ms(started),
                        )
                    headers = normalized_headers
                    expected_columns = len(headers)
                    continue

                if len(row) != expected_columns:
                    return build_tester_failure(
                        message=(
                            f"CSV row {raw_line_num} has {len(row)} columns; "
                            f"expected {expected_columns}."
                        ),
                        error_code="INVALID_CONFIGURATION",
                        duration_ms=elapsed_ms(started),
                    )
                data_row_count += 1

            if headers is None:
                return build_tester_failure(
                    message="CSV file does not contain a header row.",
                    error_code="INVALID_CONFIGURATION",
                    duration_ms=elapsed_ms(started),
                )

            return build_success_result(
                f"CSV content validated successfully ({expected_columns} columns, "
                f"{data_row_count} data rows).",
                elapsed_ms(started),
            )
        except csv.Error:
            return build_tester_failure(
                message="CSV content could not be parsed due to malformed syntax.",
                error_code="INVALID_CONFIGURATION",
                duration_ms=elapsed_ms(started),
            )
