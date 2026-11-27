"""Transient CSV and JSON file schema discoverers (never persist uploaded file contents)."""

import csv
import io
import json
from typing import Any

from fastapi import status

from app.schemas.connection_test import ConnectorType
from app.schemas.connections import CsvPersistentConfig, JsonPersistentConfig
from app.schemas.schemas import SchemaObjectInput, SchemaWarning
from app.services.discovery.base import (
    DiscoveryContext,
    DiscoveryExecutionError,
    DiscoveryExecutionResult,
)
from app.services.discovery.inference import infer_csv_schema, infer_documents_schema
from app.services.testers.csv_tester import DEFAULT_MAX_FILE_BYTES
from app.services.testers.file_validation import FileValidationError, decode_utf8_file_content
from app.services.testers.json_tester import _reject_non_standard_constant


def extract_json_records_for_inference(
    parsed: Any,
    *,
    record_path: str | None,
    sample_limit: int,
    source_label: str,
) -> tuple[list[dict[str, Any]], list[SchemaWarning]]:
    """Extract bounded dict records from a parsed JSON document without storing raw values."""
    warnings: list[SchemaWarning] = []
    target = parsed

    if record_path:
        for segment in record_path.split("."):
            if not isinstance(target, dict) or segment not in target:
                raise DiscoveryExecutionError(
                    error_code="INVALID_CONFIGURATION",
                    message=(
                        f"Configured recordPath '{record_path}' was not found in {source_label}."
                    ),
                    http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
                )
            target = target[segment]

    if isinstance(target, dict):
        return [target], warnings

    if isinstance(target, list):
        dict_items = [item for item in target[:sample_limit] if isinstance(item, dict)]
        if len(target) > sample_limit:
            warnings.append(
                SchemaWarning(
                    code="SAMPLE_LIMIT_REACHED",
                    message=f"Sampled first {sample_limit} records from {source_label}.",
                )
            )
        if target and not dict_items:
            warnings.append(
                SchemaWarning(
                    code="NON_OBJECT_JSON_ITEMS",
                    message=(
                        f"{source_label} array contains scalar items without named object fields."
                    ),
                )
            )
        return dict_items, warnings

    warnings.append(
        SchemaWarning(
            code="NON_OBJECT_JSON_ROOT",
            message=f"{source_label} resolved to a scalar value rather than JSON objects.",
        )
    )
    return [], warnings


class CsvSchemaDiscoverer:
    """Infers ordered CSV schema metadata from transient UTF-8 file content."""

    connector_type: ConnectorType = "CSV"

    def __init__(self, *, max_file_bytes: int = DEFAULT_MAX_FILE_BYTES) -> None:
        self._max_file_bytes = max_file_bytes

    async def discover(self, ctx: DiscoveryContext) -> DiscoveryExecutionResult:
        if not isinstance(ctx.config, CsvPersistentConfig):
            raise DiscoveryExecutionError(
                error_code="INVALID_CONFIGURATION",
                message="Invalid configuration dispatched to CSV discoverer.",
                http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
            )

        if ctx.file_content is None:
            raise DiscoveryExecutionError(
                error_code="FILE_REQUIRED_FOR_DISCOVERY",
                message="Transient CSV fileContent is required to discover CSV schema.",
                http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
            )

        target_name = ctx.file_name or ctx.config.fileName
        if not target_name.lower().endswith(".csv"):
            raise DiscoveryExecutionError(
                error_code="INVALID_CONFIGURATION",
                message="CSV discovery requires a filename ending with .csv.",
                http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
            )

        try:
            text = decode_utf8_file_content(ctx.file_content, max_bytes=self._max_file_bytes)
        except FileValidationError as exc:
            raise DiscoveryExecutionError(
                error_code="INVALID_CONFIGURATION",
                message=str(exc),
                http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
            ) from exc

        if not text.strip():
            raise DiscoveryExecutionError(
                error_code="INVALID_CONFIGURATION",
                message="CSV file content is empty.",
                http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
            )

        try:
            reader = csv.reader(io.StringIO(text, newline=""), strict=True)
            headers: list[str] | None = None
            expected_columns = 0
            sampled_rows: list[list[str]] = []
            truncated_rows = False

            for line_num, row in enumerate(reader, start=1):
                if len(row) == 0:
                    continue
                if headers is None:
                    normalized_headers = [col.strip() for col in row]
                    if any(not col for col in normalized_headers):
                        raise DiscoveryExecutionError(
                            error_code="INVALID_CONFIGURATION",
                            message="CSV header row contains empty column names.",
                            http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
                        )
                    if len(set(normalized_headers)) != len(normalized_headers):
                        raise DiscoveryExecutionError(
                            error_code="INVALID_CONFIGURATION",
                            message="CSV header row contains duplicate column names.",
                            http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
                        )
                    headers = normalized_headers
                    expected_columns = len(headers)
                    continue

                if len(row) != expected_columns:
                    raise DiscoveryExecutionError(
                        error_code="INVALID_CONFIGURATION",
                        message=(
                            f"CSV row {line_num} has {len(row)} columns; "
                            f"expected {expected_columns}."
                        ),
                        http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    )
                if len(sampled_rows) < ctx.sample_limit:
                    sampled_rows.append(row)
                else:
                    truncated_rows = True
                    break
        except csv.Error as exc:
            raise DiscoveryExecutionError(
                error_code="INVALID_CONFIGURATION",
                message="CSV file content could not be parsed safely.",
                http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
            ) from exc

        if headers is None:
            raise DiscoveryExecutionError(
                error_code="INVALID_CONFIGURATION",
                message="CSV file does not contain a header row.",
                http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
            )

        object_key = ctx.config.fileName
        fields, warnings, confidence = infer_csv_schema(
            headers,
            sampled_rows,
            object_key=object_key,
            max_fields=ctx.max_fields_per_object,
        )
        if truncated_rows:
            warnings.append(
                SchemaWarning(
                    code="SAMPLE_LIMIT_REACHED",
                    message=f"CSV schema inferred from first {ctx.sample_limit} rows.",
                    objectKey=object_key,
                )
            )

        is_partial = any(w.code == "FIELD_LIMIT_REACHED" for w in warnings)
        obj = SchemaObjectInput(
            objectKey=object_key,
            objectName=object_key,
            objectType="file",
            discoverySource="inferred_sample",
            nativeMetadata={
                "hasHeaderRow": True,
                "sampleSize": len(sampled_rows),
            },
            canonicalMetadata={
                "confidence": confidence,
                "primaryKeyFields": [],
            },
            fields=fields,
        )
        return DiscoveryExecutionResult(
            discovery_status="partial" if is_partial else "completed",
            discovery_source="inferred_sample",
            objects=[obj],
            warnings=warnings,
            metadata={"sampleLimit": ctx.sample_limit},
        )


class JsonSchemaDiscoverer:
    """Infers nested JSON schema metadata from transient UTF-8 file content."""

    connector_type: ConnectorType = "JSON"

    def __init__(self, *, max_file_bytes: int = DEFAULT_MAX_FILE_BYTES) -> None:
        self._max_file_bytes = max_file_bytes

    async def discover(self, ctx: DiscoveryContext) -> DiscoveryExecutionResult:
        if not isinstance(ctx.config, JsonPersistentConfig):
            raise DiscoveryExecutionError(
                error_code="INVALID_CONFIGURATION",
                message="Invalid configuration dispatched to JSON discoverer.",
                http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
            )

        if ctx.file_content is None:
            raise DiscoveryExecutionError(
                error_code="FILE_REQUIRED_FOR_DISCOVERY",
                message="Transient JSON fileContent is required to discover JSON schema.",
                http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
            )

        target_name = ctx.file_name or ctx.config.fileName
        if not target_name.lower().endswith(".json"):
            raise DiscoveryExecutionError(
                error_code="INVALID_CONFIGURATION",
                message="JSON discovery requires a filename ending with .json.",
                http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
            )

        try:
            text = decode_utf8_file_content(ctx.file_content, max_bytes=self._max_file_bytes)
        except FileValidationError as exc:
            raise DiscoveryExecutionError(
                error_code="INVALID_CONFIGURATION",
                message=str(exc),
                http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
            ) from exc

        if not text.strip():
            raise DiscoveryExecutionError(
                error_code="INVALID_CONFIGURATION",
                message="JSON file content is empty.",
                http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
            )

        try:
            parsed = json.loads(text, parse_constant=_reject_non_standard_constant)
        except json.JSONDecodeError as exc:
            raise DiscoveryExecutionError(
                error_code="INVALID_CONFIGURATION",
                message=f"Invalid JSON syntax near line {exc.lineno}, column {exc.colno}.",
                http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
            ) from exc
        except (ValueError, RecursionError) as exc:
            raise DiscoveryExecutionError(
                error_code="INVALID_CONFIGURATION",
                message="Invalid JSON syntax.",
                http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
            ) from exc

        object_key = ctx.config.fileName
        records, extract_warnings = extract_json_records_for_inference(
            parsed,
            record_path=ctx.options.recordPath,
            sample_limit=ctx.sample_limit,
            source_label="JSON file",
        )
        fields, infer_warnings, confidence = infer_documents_schema(
            records,
            object_key=object_key,
            discovery_source="inferred_sample",
            max_fields=ctx.max_fields_per_object,
        )
        warnings = [*extract_warnings, *infer_warnings]
        is_partial = any(w.code in ("FIELD_LIMIT_REACHED", "MAX_DEPTH_TRUNCATED") for w in warnings)

        obj = SchemaObjectInput(
            objectKey=object_key,
            objectName=object_key,
            objectType="file",
            discoverySource="inferred_sample",
            nativeMetadata={
                "sampleSize": len(records),
                **({"recordPath": ctx.options.recordPath} if ctx.options.recordPath else {}),
            },
            canonicalMetadata={
                "confidence": confidence,
                "primaryKeyFields": [],
            },
            fields=fields,
        )
        return DiscoveryExecutionResult(
            discovery_status="partial" if is_partial else "completed",
            discovery_source="inferred_sample",
            objects=[obj],
            warnings=warnings,
            metadata={"sampleLimit": ctx.sample_limit},
        )
