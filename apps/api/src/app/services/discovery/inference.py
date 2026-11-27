"""Deterministic structural type inference for JSON, CSV, MongoDB, and REST sample records."""

import math
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.schemas.schemas import (
    CanonicalFieldType,
    FieldDiscoverySource,
    SchemaFieldInput,
    SchemaWarning,
)

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ISO_DATETIME_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?$"
)
_CSV_INT_RE = re.compile(r"^[+-]?(?:0|[1-9]\d*)$")
_CSV_FLOAT_RE = re.compile(r"^[+-]?(?:\d+\.\d+|\d+\.|\.\d+)(?:[eE][+-]?\d+)?$")


@dataclass
class _FieldAccumulator:
    field_path: str
    field_name: str
    parent_path: str | None
    depth: int
    is_array: bool
    ordinal_position: int
    observed_types: set[CanonicalFieldType] = field(default_factory=set)
    native_types: set[str] = field(default_factory=set)
    saw_null: bool = False
    observed_records: int = 0
    truncated: bool = False


def _is_valid_iso_date(value: str) -> bool:
    if not _ISO_DATE_RE.match(value):
        return False
    try:
        date.fromisoformat(value)
        return True
    except ValueError:
        return False


def _is_valid_iso_datetime(value: str) -> bool:
    if not _ISO_DATETIME_RE.match(value):
        return False
    normalized = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        datetime.fromisoformat(normalized)
        return True
    except ValueError:
        return False


def classify_runtime_value(value: Any) -> tuple[CanonicalFieldType, str]:
    """Classify a Python, JSON, or BSON scalar/container value into canonical + native types."""
    if value is None:
        return "null", "null"
    if isinstance(value, bool):
        return "boolean", "boolean"

    type_name = type(value).__name__
    module_name = type(value).__module__

    if module_name.startswith("bson"):
        if type_name == "ObjectId":
            return "string", "bson:objectId"
        if type_name == "Int64":
            return "integer", "bson:long"
        if type_name == "Decimal128":
            return "number", "bson:decimal128"
        if type_name == "Binary":
            return "binary", "bson:binData"
        if type_name == "Regex":
            return "string", "bson:regex"

    if isinstance(value, int):
        return "integer", "integer"
    if isinstance(value, float):
        if not math.isfinite(value):
            return "unknown", "float:non_finite"
        return "number", "number"
    if isinstance(value, Decimal):
        if not value.is_finite():
            return "unknown", "decimal:non_finite"
        return "number", "decimal"
    if isinstance(value, datetime):
        return "datetime", "datetime"
    if isinstance(value, date):
        return "date", "date"
    if isinstance(value, (bytes, bytearray)):
        return "binary", "binary"
    if isinstance(value, str):
        if _is_valid_iso_date(value):
            return "date", "string(iso-date)"
        if _is_valid_iso_datetime(value):
            return "datetime", "string(iso-datetime)"
        return "string", "string"
    if isinstance(value, dict):
        return "object", "object"
    if isinstance(value, list):
        return "array", "array"
    return "unknown", type_name


def classify_csv_cell(raw_cell: str) -> tuple[CanonicalFieldType, str]:
    """Classify one CSV string cell without silently coercing leading-zero identifiers."""
    stripped = raw_cell.strip()
    if stripped == "" or stripped.lower() == "null":
        return "null", "csv:null"
    if stripped.lower() in ("true", "false"):
        return "boolean", "csv:boolean"
    if _CSV_INT_RE.match(stripped):
        return "integer", "csv:integer"
    if _CSV_FLOAT_RE.match(stripped):
        try:
            if math.isfinite(float(stripped)):
                return "number", "csv:number"
        except ValueError:
            pass
    if _is_valid_iso_date(stripped):
        return "date", "csv:date"
    if _is_valid_iso_datetime(stripped):
        return "datetime", "csv:datetime"
    return "string", "csv:string"


def _resolve_canonical_type(
    observed: set[CanonicalFieldType],
) -> tuple[CanonicalFieldType, list[CanonicalFieldType], list[str]]:
    """Merge observed non-null types deterministically and flag conflicts without data leakage."""
    if not observed:
        return "null", [], ["Field contained only null or empty values across sampled records."]
    if len(observed) == 1:
        only_type = next(iter(observed))
        return only_type, [], []
    if observed == {"integer", "number"}:
        return (
            "number",
            ["integer", "number"],
            ["Observed both integer and decimal numeric values; normalized to number."],
        )
    if observed == {"date", "datetime"}:
        return (
            "datetime",
            ["date", "datetime"],
            ["Observed both date and datetime values; normalized to datetime."],
        )
    sorted_types = sorted(observed)
    return (
        "mixed",
        sorted_types,
        [f"Conflicting field types observed across sampled records: {', '.join(sorted_types)}."],
    )


def infer_documents_schema(
    documents: list[dict[str, Any]],
    *,
    object_key: str,
    discovery_source: FieldDiscoverySource,
    max_fields: int,
    max_depth: int = 8,
    primary_key_paths: set[str] | None = None,
) -> tuple[list[SchemaFieldInput], list[SchemaWarning], float]:
    """Deterministically infer nested fields, array element paths, nullability, and conflicts."""
    if not documents:
        return (
            [],
            [
                SchemaWarning(
                    code="EMPTY_SAMPLE",
                    message=f"Object '{object_key}' had no records available to infer fields.",
                    objectKey=object_key,
                )
            ],
            0.50,
        )

    accumulators: dict[str, _FieldAccumulator] = {}
    warnings: list[SchemaWarning] = []
    truncated_fields = False
    truncated_depth_paths: set[str] = set()
    pk_paths = primary_key_paths or set()

    def _get_or_create(
        *,
        field_path: str,
        field_name: str,
        parent_path: str | None,
        depth: int,
        is_array: bool,
    ) -> _FieldAccumulator | None:
        nonlocal truncated_fields
        existing = accumulators.get(field_path)
        if existing is not None:
            return existing
        if len(accumulators) >= max_fields:
            truncated_fields = True
            return None
        acc = _FieldAccumulator(
            field_path=field_path,
            field_name=field_name,
            parent_path=parent_path,
            depth=depth,
            is_array=is_array,
            ordinal_position=len(accumulators),
        )
        accumulators[field_path] = acc
        return acc

    def _observe_doc(
        value: Any,
        *,
        field_path: str,
        field_name: str,
        parent_path: str | None,
        depth: int,
        seen_in_doc: set[str],
    ) -> None:
        if depth > max_depth:
            acc = _get_or_create(
                field_path=field_path,
                field_name=field_name,
                parent_path=parent_path,
                depth=depth,
                is_array=isinstance(value, list),
            )
            if acc is not None:
                if field_path not in seen_in_doc:
                    seen_in_doc.add(field_path)
                    acc.observed_records += 1
                acc.observed_types.add("array" if isinstance(value, list) else "json")
                acc.native_types.add("truncated:max_depth")
                acc.truncated = True
                truncated_depth_paths.add(field_path)
            return

        c_type, n_type = classify_runtime_value(value)
        is_arr = c_type == "array"
        acc = _get_or_create(
            field_path=field_path,
            field_name=field_name,
            parent_path=parent_path,
            depth=depth,
            is_array=is_arr,
        )
        if acc is None:
            return

        if field_path not in seen_in_doc:
            seen_in_doc.add(field_path)
            acc.observed_records += 1

        if c_type == "null":
            acc.saw_null = True
            acc.native_types.add(n_type)
            return

        acc.observed_types.add(c_type)
        acc.native_types.add(n_type)

        if isinstance(value, dict):
            for key in sorted(value.keys(), key=str):
                key_str = str(key).strip()
                if not key_str:
                    continue
                child_path = f"{field_path}.{key_str}"
                _observe_doc(
                    value[key],
                    field_path=child_path,
                    field_name=key_str,
                    parent_path=field_path,
                    depth=depth + 1,
                    seen_in_doc=seen_in_doc,
                )
        elif isinstance(value, list):
            elem_path = f"{field_path}[]"
            for item in value:
                _observe_doc(
                    item,
                    field_path=elem_path,
                    field_name=f"{field_name}[]",
                    parent_path=field_path,
                    depth=depth + 1,
                    seen_in_doc=seen_in_doc,
                )

    total_docs = len(documents)
    for doc in documents:
        if not isinstance(doc, dict):
            continue
        seen_in_doc: set[str] = set()
        for key in sorted(doc.keys(), key=str):
            key_str = str(key).strip()
            if not key_str:
                continue
            _observe_doc(
                doc[key],
                field_path=key_str,
                field_name=key_str,
                parent_path=None,
                depth=0,
                seen_in_doc=seen_in_doc,
            )

    fields: list[SchemaFieldInput] = []
    has_conflicts = False
    for acc in accumulators.values():
        canonical_type, inferred_types, field_warnings = _resolve_canonical_type(acc.observed_types)
        if canonical_type == "mixed":
            has_conflicts = True
            warnings.append(
                SchemaWarning(
                    code="MIXED_FIELD_TYPES",
                    message=(
                        f"Field '{acc.field_path}' has mixed observed types "
                        f"({', '.join(inferred_types)})."
                    ),
                    objectKey=object_key,
                    fieldPath=acc.field_path,
                )
            )
        elif canonical_type == "null":
            warnings.append(
                SchemaWarning(
                    code="ALL_NULL_VALUES",
                    message=f"Field '{acc.field_path}' contained only null values in sample.",
                    objectKey=object_key,
                    fieldPath=acc.field_path,
                )
            )

        if acc.truncated:
            field_warnings.append(
                f"Nested structure exceeded max depth ({max_depth}) and was truncated."
            )

        parent_acc = accumulators.get(acc.parent_path) if acc.parent_path is not None else None
        expected_parent_records = (
            parent_acc.observed_records
            if parent_acc is not None and not acc.parent_path.endswith("[]")
            else total_docs
        )
        # Element paths (`items[]`) appear variable times per document; object properties
        # omitted in some parent records are nullable.
        omitted_in_some_docs = (
            not acc.field_path.endswith("[]")
            and expected_parent_records > 0
            and acc.observed_records < expected_parent_records
        )
        is_nullable = acc.saw_null or omitted_in_some_docs
        if omitted_in_some_docs:
            field_warnings.append(
                f"Present in {acc.observed_records} of {expected_parent_records} sampled records."
            )

        non_null_natives = sorted(t for t in acc.native_types if t != "null")
        native_label = ", ".join(non_null_natives[:3]) if non_null_natives else "null"
        if len(non_null_natives) > 3:
            native_label = f"{native_label}, ..."

        metadata: dict[str, Any] = {
            "depth": acc.depth,
            "isArray": acc.is_array,
            "observedRecords": acc.observed_records,
            "sampleSize": total_docs,
        }
        if acc.parent_path is not None:
            metadata["parentPath"] = acc.parent_path
        if inferred_types:
            metadata["inferredTypes"] = inferred_types
        if field_warnings:
            metadata["warnings"] = field_warnings

        is_pk = acc.field_path in pk_paths
        fields.append(
            SchemaFieldInput(
                fieldPath=acc.field_path,
                fieldName=acc.field_name,
                nativeType=native_label[:128],
                canonicalType=canonical_type,
                nullable=is_nullable,
                isRequired=False,
                primaryKey=is_pk,
                uniqueKey=is_pk,
                ordinalPosition=acc.ordinal_position,
                discoverySource=discovery_source,
                metadata=metadata,
            )
        )

    if truncated_fields:
        warnings.append(
            SchemaWarning(
                code="FIELD_LIMIT_REACHED",
                message=f"Object '{object_key}' exceeded {max_fields} fields and was truncated.",
                objectKey=object_key,
            )
        )
    if truncated_depth_paths:
        warnings.append(
            SchemaWarning(
                code="MAX_DEPTH_TRUNCATED",
                message=f"Nested paths in '{object_key}' exceeded depth {max_depth}.",
                objectKey=object_key,
            )
        )

    confidence = 0.90
    if total_docs < 10:
        confidence -= 0.15
    if has_conflicts:
        confidence -= 0.10
    if truncated_fields or truncated_depth_paths:
        confidence -= 0.05
    confidence = round(max(0.40, min(0.95, confidence)), 2)

    return fields, warnings, confidence


def infer_csv_schema(
    headers: list[str],
    rows: list[list[str]],
    *,
    object_key: str,
    max_fields: int,
) -> tuple[list[SchemaFieldInput], list[SchemaWarning], float]:
    """Deterministically infer ordered CSV column types, nullability, and conflict warnings."""
    warnings: list[SchemaWarning] = []
    limited_headers = headers[:max_fields]
    if len(headers) > max_fields:
        warnings.append(
            SchemaWarning(
                code="FIELD_LIMIT_REACHED",
                message=(
                    f"CSV object '{object_key}' exceeded {max_fields} columns and was truncated."
                ),
                objectKey=object_key,
            )
        )

    if not rows:
        warnings.append(
            SchemaWarning(
                code="EMPTY_SAMPLE",
                message=f"CSV '{object_key}' contains a header row with zero data rows.",
                objectKey=object_key,
            )
        )
        fields = [
            SchemaFieldInput(
                fieldPath=col,
                fieldName=col,
                nativeType="csv:header_only",
                canonicalType="unknown",
                nullable=True,
                isRequired=False,
                ordinalPosition=idx,
                discoverySource="inferred_sample",
                metadata={"depth": 0, "isArray": False, "sampleSize": 0},
            )
            for idx, col in enumerate(limited_headers)
        ]
        return fields, warnings, 0.50

    observed_by_col: dict[int, set[CanonicalFieldType]] = defaultdict(set)
    native_by_col: dict[int, set[str]] = defaultdict(set)
    null_by_col: dict[int, bool] = defaultdict(bool)

    for row in rows:
        for idx in range(len(limited_headers)):
            cell = row[idx] if idx < len(row) else ""
            c_type, n_type = classify_csv_cell(cell)
            if c_type == "null":
                null_by_col[idx] = True
            else:
                observed_by_col[idx].add(c_type)
                native_by_col[idx].add(n_type)

    fields = []
    has_conflicts = False
    for idx, col in enumerate(limited_headers):
        canonical_type, inferred_types, col_warnings = _resolve_canonical_type(observed_by_col[idx])
        if canonical_type == "mixed":
            has_conflicts = True
            warnings.append(
                SchemaWarning(
                    code="MIXED_FIELD_TYPES",
                    message=(
                        f"CSV column '{col}' has mixed observed types "
                        f"({', '.join(inferred_types)})."
                    ),
                    objectKey=object_key,
                    fieldPath=col,
                )
            )
        elif canonical_type == "null":
            warnings.append(
                SchemaWarning(
                    code="ALL_NULL_VALUES",
                    message=f"CSV column '{col}' contained only empty/null cells in sample.",
                    objectKey=object_key,
                    fieldPath=col,
                )
            )

        natives = sorted(native_by_col[idx])
        native_label = ", ".join(natives[:3]) if natives else "csv:null"
        metadata: dict[str, Any] = {
            "depth": 0,
            "isArray": False,
            "sampleSize": len(rows),
        }
        if inferred_types:
            metadata["inferredTypes"] = inferred_types
        if col_warnings:
            metadata["warnings"] = col_warnings

        fields.append(
            SchemaFieldInput(
                fieldPath=col,
                fieldName=col,
                nativeType=native_label[:128],
                canonicalType=canonical_type,
                nullable=null_by_col[idx] or canonical_type == "null",
                isRequired=False,
                ordinalPosition=idx,
                discoverySource="inferred_sample",
                metadata=metadata,
            )
        )

    confidence = 0.90 if len(rows) >= 10 else 0.75
    if has_conflicts:
        confidence = round(max(0.50, confidence - 0.10), 2)
    return fields, warnings, confidence
