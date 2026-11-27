"""Deterministic, side-effect-free validation engine for DataBridge field mappings.

Canonical Type Compatibility Policy
-----------------------------------
All 13 ``CanonicalFieldType`` values (string, integer, number, boolean, date, datetime,
object, array, binary, json, null, mixed, unknown) are evaluated via explicit policy
rather than native SQL/BSON/JSON type-name string matching:

- ``compatible``: Exact match (excluding ``unknown``/``null`` caveats) or lossless widening
  (e.g., ``integer -> number``, ``date -> datetime``, scalars/date/datetime -> ``string``,
  any non-binary JSON-representable type -> ``json``, concrete non-unknown type -> ``mixed``).
- ``warning``: Lossy conversion or runtime coercion risk (e.g., ``number -> integer``,
  ``datetime -> date``, ``string -> integer/number/boolean/date/datetime/object/array/binary``,
  ``integer -> boolean/datetime``, ``boolean -> integer/number``, ``json -> object/array/string``,
  ``object/array -> string``, ``mixed -> concrete``, ``null -> nullable``).
- ``error``: Incompatible conversion (e.g., structured ``object/array`` -> numeric/boolean/date,
  ``binary`` -> non-binary/non-string targets, ``null -> non-nullable``) or either side
  having type ``unknown`` (never silently accepted).
"""

from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any, Literal
from uuid import UUID

from app.schemas.mappings import (
    _FORBIDDEN_TRANSFORM_KEYS,
    MappingRuleInput,
    MappingRuleResponse,
    MappingValidationIssue,
    MappingValidationSummary,
)
from app.schemas.schemas import (
    CanonicalFieldType,
    DiscoveredSchemaResponse,
    SchemaFieldResponse,
    SchemaObjectResponse,
)

CompatibilityOutcome = Literal["compatible", "warning", "error"]

_COMPATIBILITY_RULES: dict[
    tuple[CanonicalFieldType, CanonicalFieldType],
    tuple[CompatibilityOutcome, str | None, str],
] = {
    # Target: string
    ("string", "string"): ("compatible", None, ""),
    ("integer", "string"): ("compatible", None, ""),
    ("number", "string"): ("compatible", None, ""),
    ("boolean", "string"): ("compatible", None, ""),
    ("date", "string"): ("compatible", None, ""),
    ("datetime", "string"): ("compatible", None, ""),
    ("object", "string"): (
        "warning",
        "LOSSY_TYPE_CONVERSION",
        "Serializing structured 'object' values into 'string' requires formatting assumptions.",
    ),
    ("array", "string"): (
        "warning",
        "LOSSY_TYPE_CONVERSION",
        "Serializing 'array' values into 'string' requires serialization formatting.",
    ),
    ("json", "string"): (
        "warning",
        "LOSSY_TYPE_CONVERSION",
        "Serializing arbitrary 'json' values into 'string' requires JSON stringification.",
    ),
    ("binary", "string"): (
        "error",
        "INCOMPATIBLE_CANONICAL_TYPE",
        (
            "Raw 'binary' fields cannot map directly to 'string' without an explicit encoding "
            "transform."
        ),
    ),
    # Target: integer
    ("integer", "integer"): ("compatible", None, ""),
    ("number", "integer"): (
        "warning",
        "LOSSY_TYPE_CONVERSION",
        "Mapping floating/decimal 'number' into 'integer' may truncate fractional precision.",
    ),
    ("boolean", "integer"): (
        "warning",
        "IMPLICIT_COERCION_RISK",
        "Mapping 'boolean' to 'integer' relies on implicit truthy numeric coercion.",
    ),
    ("string", "integer"): (
        "warning",
        "TYPE_COERCION_RISK",
        "Parsing 'string' values as 'integer' can fail at runtime on non-numeric strings.",
    ),
    ("date", "integer"): (
        "error",
        "INCOMPATIBLE_CANONICAL_TYPE",
        "Cannot map 'date' directly to 'integer' without an explicit conversion transform.",
    ),
    ("datetime", "integer"): (
        "error",
        "INCOMPATIBLE_CANONICAL_TYPE",
        "Cannot map 'datetime' directly to 'integer' without an explicit conversion transform.",
    ),
    ("object", "integer"): (
        "error",
        "INCOMPATIBLE_CANONICAL_TYPE",
        "Structured 'object' cannot map directly to 'integer'.",
    ),
    ("array", "integer"): (
        "error",
        "INCOMPATIBLE_CANONICAL_TYPE",
        "'array' values cannot map directly to 'integer'.",
    ),
    ("binary", "integer"): (
        "error",
        "INCOMPATIBLE_CANONICAL_TYPE",
        "'binary' values cannot map directly to 'integer'.",
    ),
    ("json", "integer"): (
        "error",
        "INCOMPATIBLE_CANONICAL_TYPE",
        "Arbitrary 'json' values cannot map directly to 'integer'.",
    ),
    # Target: number
    ("number", "number"): ("compatible", None, ""),
    ("integer", "number"): ("compatible", None, ""),
    ("boolean", "number"): (
        "warning",
        "IMPLICIT_COERCION_RISK",
        "Mapping 'boolean' to 'number' relies on implicit numeric coercion.",
    ),
    ("string", "number"): (
        "warning",
        "TYPE_COERCION_RISK",
        "Parsing 'string' values as 'number' can fail at runtime on non-numeric strings.",
    ),
    ("date", "number"): (
        "error",
        "INCOMPATIBLE_CANONICAL_TYPE",
        "Cannot map 'date' directly to 'number'.",
    ),
    ("datetime", "number"): (
        "error",
        "INCOMPATIBLE_CANONICAL_TYPE",
        "Cannot map 'datetime' directly to 'number'.",
    ),
    ("object", "number"): (
        "error",
        "INCOMPATIBLE_CANONICAL_TYPE",
        "Structured 'object' cannot map directly to 'number'.",
    ),
    ("array", "number"): (
        "error",
        "INCOMPATIBLE_CANONICAL_TYPE",
        "'array' values cannot map directly to 'number'.",
    ),
    ("binary", "number"): (
        "error",
        "INCOMPATIBLE_CANONICAL_TYPE",
        "'binary' values cannot map directly to 'number'.",
    ),
    ("json", "number"): (
        "error",
        "INCOMPATIBLE_CANONICAL_TYPE",
        "Arbitrary 'json' values cannot map directly to 'number'.",
    ),
    # Target: boolean
    ("boolean", "boolean"): ("compatible", None, ""),
    ("integer", "boolean"): (
        "warning",
        "IMPLICIT_COERCION_RISK",
        "Coercing 'integer' to 'boolean' assumes 0/1 conventions.",
    ),
    ("string", "boolean"): (
        "warning",
        "TYPE_COERCION_RISK",
        "Parsing 'string' values as 'boolean' can fail on unstandardized representations.",
    ),
    ("number", "boolean"): (
        "error",
        "INCOMPATIBLE_CANONICAL_TYPE",
        "Floating-point 'number' values cannot map directly to 'boolean'.",
    ),
    ("date", "boolean"): (
        "error",
        "INCOMPATIBLE_CANONICAL_TYPE",
        "Cannot map 'date' to 'boolean'.",
    ),
    ("datetime", "boolean"): (
        "error",
        "INCOMPATIBLE_CANONICAL_TYPE",
        "Cannot map 'datetime' to 'boolean'.",
    ),
    ("object", "boolean"): (
        "error",
        "INCOMPATIBLE_CANONICAL_TYPE",
        "Structured 'object' cannot map directly to 'boolean'.",
    ),
    ("array", "boolean"): (
        "error",
        "INCOMPATIBLE_CANONICAL_TYPE",
        "'array' values cannot map directly to 'boolean'.",
    ),
    ("binary", "boolean"): (
        "error",
        "INCOMPATIBLE_CANONICAL_TYPE",
        "'binary' values cannot map directly to 'boolean'.",
    ),
    ("json", "boolean"): (
        "error",
        "INCOMPATIBLE_CANONICAL_TYPE",
        "Arbitrary 'json' values cannot map directly to 'boolean'.",
    ),
    # Target: date
    ("date", "date"): ("compatible", None, ""),
    ("datetime", "date"): (
        "warning",
        "LOSSY_TYPE_CONVERSION",
        "Mapping 'datetime' to 'date' truncates time-of-day and timezone information.",
    ),
    ("string", "date"): (
        "warning",
        "TYPE_COERCION_RISK",
        "Parsing 'string' values as 'date' requires valid ISO date formatting.",
    ),
    ("integer", "date"): (
        "error",
        "INCOMPATIBLE_CANONICAL_TYPE",
        "Cannot map 'integer' directly to 'date' without an explicit epoch transform.",
    ),
    ("number", "date"): (
        "error",
        "INCOMPATIBLE_CANONICAL_TYPE",
        "Cannot map 'number' directly to 'date'.",
    ),
    ("boolean", "date"): (
        "error",
        "INCOMPATIBLE_CANONICAL_TYPE",
        "Cannot map 'boolean' to 'date'.",
    ),
    ("object", "date"): (
        "error",
        "INCOMPATIBLE_CANONICAL_TYPE",
        "Structured 'object' cannot map directly to 'date'.",
    ),
    ("array", "date"): (
        "error",
        "INCOMPATIBLE_CANONICAL_TYPE",
        "'array' values cannot map directly to 'date'.",
    ),
    ("binary", "date"): (
        "error",
        "INCOMPATIBLE_CANONICAL_TYPE",
        "'binary' values cannot map directly to 'date'.",
    ),
    ("json", "date"): (
        "error",
        "INCOMPATIBLE_CANONICAL_TYPE",
        "Arbitrary 'json' values cannot map directly to 'date'.",
    ),
    # Target: datetime
    ("datetime", "datetime"): ("compatible", None, ""),
    ("date", "datetime"): ("compatible", None, ""),
    ("string", "datetime"): (
        "warning",
        "TYPE_COERCION_RISK",
        "Parsing 'string' values as 'datetime' requires valid ISO-8601 timestamp formatting.",
    ),
    ("integer", "datetime"): (
        "warning",
        "TYPE_COERCION_RISK",
        "Mapping 'integer' directly to 'datetime' has ambiguous epoch unit (seconds vs ms).",
    ),
    ("number", "datetime"): (
        "error",
        "INCOMPATIBLE_CANONICAL_TYPE",
        "Cannot map floating 'number' directly to 'datetime' without an explicit transform.",
    ),
    ("boolean", "datetime"): (
        "error",
        "INCOMPATIBLE_CANONICAL_TYPE",
        "Cannot map 'boolean' to 'datetime'.",
    ),
    ("object", "datetime"): (
        "error",
        "INCOMPATIBLE_CANONICAL_TYPE",
        "Structured 'object' cannot map directly to 'datetime'.",
    ),
    ("array", "datetime"): (
        "error",
        "INCOMPATIBLE_CANONICAL_TYPE",
        "'array' values cannot map directly to 'datetime'.",
    ),
    ("binary", "datetime"): (
        "error",
        "INCOMPATIBLE_CANONICAL_TYPE",
        "'binary' values cannot map directly to 'datetime'.",
    ),
    ("json", "datetime"): (
        "error",
        "INCOMPATIBLE_CANONICAL_TYPE",
        "Arbitrary 'json' values cannot map directly to 'datetime'.",
    ),
    # Target: object
    ("object", "object"): ("compatible", None, ""),
    ("json", "object"): (
        "warning",
        "TYPE_COERCION_RISK",
        "Arbitrary 'json' values may be scalars or arrays rather than JSON objects.",
    ),
    ("string", "object"): (
        "warning",
        "TYPE_COERCION_RISK",
        "Parsing 'string' values as 'object' requires valid serialized JSON object strings.",
    ),
    ("integer", "object"): (
        "error",
        "INCOMPATIBLE_CANONICAL_TYPE",
        "Scalar 'integer' cannot map directly to structured 'object'.",
    ),
    ("number", "object"): (
        "error",
        "INCOMPATIBLE_CANONICAL_TYPE",
        "Scalar 'number' cannot map directly to structured 'object'.",
    ),
    ("boolean", "object"): (
        "error",
        "INCOMPATIBLE_CANONICAL_TYPE",
        "Scalar 'boolean' cannot map directly to structured 'object'.",
    ),
    ("date", "object"): (
        "error",
        "INCOMPATIBLE_CANONICAL_TYPE",
        "Scalar 'date' cannot map directly to structured 'object'.",
    ),
    ("datetime", "object"): (
        "error",
        "INCOMPATIBLE_CANONICAL_TYPE",
        "Scalar 'datetime' cannot map directly to structured 'object'.",
    ),
    ("array", "object"): (
        "error",
        "INCOMPATIBLE_CANONICAL_TYPE",
        "'array' cannot map directly to structured 'object'.",
    ),
    ("binary", "object"): (
        "error",
        "INCOMPATIBLE_CANONICAL_TYPE",
        "'binary' cannot map directly to structured 'object'.",
    ),
    # Target: array
    ("array", "array"): ("compatible", None, ""),
    ("json", "array"): (
        "warning",
        "TYPE_COERCION_RISK",
        "Arbitrary 'json' values may not be JSON arrays at runtime.",
    ),
    ("string", "array"): (
        "warning",
        "TYPE_COERCION_RISK",
        "Parsing 'string' values as 'array' requires JSON array formatting or delimiter splitting.",
    ),
    ("integer", "array"): (
        "error",
        "INCOMPATIBLE_CANONICAL_TYPE",
        "Scalar 'integer' cannot map directly to 'array'.",
    ),
    ("number", "array"): (
        "error",
        "INCOMPATIBLE_CANONICAL_TYPE",
        "Scalar 'number' cannot map directly to 'array'.",
    ),
    ("boolean", "array"): (
        "error",
        "INCOMPATIBLE_CANONICAL_TYPE",
        "Scalar 'boolean' cannot map directly to 'array'.",
    ),
    ("date", "array"): (
        "error",
        "INCOMPATIBLE_CANONICAL_TYPE",
        "Scalar 'date' cannot map directly to 'array'.",
    ),
    ("datetime", "array"): (
        "error",
        "INCOMPATIBLE_CANONICAL_TYPE",
        "Scalar 'datetime' cannot map directly to 'array'.",
    ),
    ("object", "array"): (
        "error",
        "INCOMPATIBLE_CANONICAL_TYPE",
        "Structured 'object' cannot map directly to 'array'.",
    ),
    ("binary", "array"): (
        "error",
        "INCOMPATIBLE_CANONICAL_TYPE",
        "'binary' values cannot map directly to 'array'.",
    ),
    # Target: binary
    ("binary", "binary"): ("compatible", None, ""),
    ("string", "binary"): (
        "warning",
        "TYPE_COERCION_RISK",
        "Converting 'string' to 'binary' relies on implicit UTF-8 or encoding assumptions.",
    ),
    ("integer", "binary"): (
        "error",
        "INCOMPATIBLE_CANONICAL_TYPE",
        "'integer' values cannot map directly to 'binary'.",
    ),
    ("number", "binary"): (
        "error",
        "INCOMPATIBLE_CANONICAL_TYPE",
        "'number' values cannot map directly to 'binary'.",
    ),
    ("boolean", "binary"): (
        "error",
        "INCOMPATIBLE_CANONICAL_TYPE",
        "'boolean' values cannot map directly to 'binary'.",
    ),
    ("date", "binary"): (
        "error",
        "INCOMPATIBLE_CANONICAL_TYPE",
        "'date' values cannot map directly to 'binary'.",
    ),
    ("datetime", "binary"): (
        "error",
        "INCOMPATIBLE_CANONICAL_TYPE",
        "'datetime' values cannot map directly to 'binary'.",
    ),
    ("object", "binary"): (
        "error",
        "INCOMPATIBLE_CANONICAL_TYPE",
        "'object' values cannot map directly to 'binary'.",
    ),
    ("array", "binary"): (
        "error",
        "INCOMPATIBLE_CANONICAL_TYPE",
        "'array' values cannot map directly to 'binary'.",
    ),
    ("json", "binary"): (
        "error",
        "INCOMPATIBLE_CANONICAL_TYPE",
        "'json' values cannot map directly to 'binary'.",
    ),
    # Target: json
    ("json", "json"): ("compatible", None, ""),
    ("object", "json"): ("compatible", None, ""),
    ("array", "json"): ("compatible", None, ""),
    ("string", "json"): ("compatible", None, ""),
    ("integer", "json"): ("compatible", None, ""),
    ("number", "json"): ("compatible", None, ""),
    ("boolean", "json"): ("compatible", None, ""),
    ("date", "json"): ("compatible", None, ""),
    ("datetime", "json"): ("compatible", None, ""),
    ("binary", "json"): (
        "error",
        "INCOMPATIBLE_CANONICAL_TYPE",
        "Raw 'binary' values cannot map directly to 'json' without an encoding transform.",
    ),
}


def evaluate_canonical_type_compatibility(
    source_field: SchemaFieldResponse,
    destination_field: SchemaFieldResponse,
) -> tuple[CompatibilityOutcome, str | None, str]:
    """Evaluate compatibility between discovered source and destination canonical types."""
    src_type = source_field.canonicalType
    dst_type = destination_field.canonicalType

    if src_type == "unknown" or dst_type == "unknown":
        return (
            "error",
            "UNKNOWN_CANONICAL_TYPE",
            (
                f"Cannot validate rule involving unsupported/unknown canonical type "
                f"('{src_type}' -> '{dst_type}')."
            ),
        )

    if src_type == "null":
        if dst_type == "null":
            return ("compatible", None, "")
        if destination_field.nullable:
            return (
                "warning",
                "NULL_ONLY_SOURCE_TYPE",
                (
                    f"Source field '{source_field.fieldPath}' only observed 'null' values during "
                    f"schema discovery while mapping to nullable '{dst_type}'."
                ),
            )
        return (
            "error",
            "INCOMPATIBLE_CANONICAL_TYPE",
            (
                f"Source field '{source_field.fieldPath}' only observed 'null' values and cannot "
                f"map directly to non-nullable '{dst_type}'."
            ),
        )

    if dst_type == "null":
        return (
            "error",
            "INCOMPATIBLE_CANONICAL_TYPE",
            f"Cannot map concrete source type '{src_type}' to destination type 'null'.",
        )

    if dst_type == "mixed":
        return ("compatible", None, "")

    if src_type == "mixed":
        inferred = source_field.metadata.get("inferredTypes")
        if dst_type == "json":
            return ("compatible", None, "")
        if isinstance(inferred, list) and inferred:
            outcomes = [
                _COMPATIBILITY_RULES.get((str(cand), dst_type), ("error", "", ""))[0]  # type: ignore[arg-type]
                for cand in inferred
                if cand != "null"
            ]
            if outcomes and all(item == "error" for item in outcomes):
                return (
                    "error",
                    "INCOMPATIBLE_CANONICAL_TYPE",
                    (
                        f"Source field '{source_field.fieldPath}' inferred types are all "
                        f"incompatible with destination canonical type '{dst_type}'."
                    ),
                )
        return (
            "warning",
            "TYPE_COERCION_RISK",
            (
                f"Source field '{source_field.fieldPath}' has 'mixed' canonical type; mapping to "
                f"'{dst_type}' may require normalization at runtime."
            ),
        )

    outcome = _COMPATIBILITY_RULES.get((src_type, dst_type))
    if outcome is None:
        return (
            "error",
            "UNKNOWN_CANONICAL_TYPE",
            f"Unsupported canonical type pair ('{src_type}' -> '{dst_type}').",
        )
    return outcome


def _describe_constant_kind(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "unsupported"


def _is_iso_date_string(text: str) -> bool:
    try:
        date.fromisoformat(text.strip())
        return True
    except ValueError:
        return False


def _is_iso_datetime_string(text: str) -> bool:
    candidate = text.strip()
    if candidate.endswith("Z"):
        candidate = f"{candidate[:-1]}+00:00"
    try:
        datetime.fromisoformat(candidate)
        return True
    except ValueError:
        return False


def evaluate_constant_type_compatibility(
    constant_value: Any,
    destination_field: SchemaFieldResponse,
) -> tuple[CompatibilityOutcome, str | None, str]:
    """Validate literal constant against destination type/nullability without value echoes."""
    dst_type = destination_field.canonicalType
    value_kind = _describe_constant_kind(constant_value)

    if dst_type == "unknown" or value_kind == "unsupported":
        return (
            "error",
            "UNKNOWN_CANONICAL_TYPE",
            (
                f"Cannot assign constant of kind '{value_kind}' to destination field "
                f"'{destination_field.fieldPath}' with canonical type '{dst_type}'."
            ),
        )

    if constant_value is None:
        if destination_field.nullable:
            return ("compatible", None, "")
        return (
            "error",
            "NULL_CONSTANT_FOR_NON_NULLABLE_FIELD",
            (
                f"Constant 'null' cannot be assigned to non-nullable destination field "
                f"'{destination_field.fieldPath}'."
            ),
        )

    if dst_type == "null":
        return (
            "error",
            "INCOMPATIBLE_CONSTANT_TYPE",
            (
                f"Non-null constant of kind '{value_kind}' cannot target 'null' destination field "
                f"'{destination_field.fieldPath}'."
            ),
        )

    if dst_type in ("json", "mixed"):
        return ("compatible", None, "")

    if dst_type == "string":
        if isinstance(constant_value, str):
            return ("compatible", None, "")
        if isinstance(constant_value, (bool, int, float)):
            return (
                "warning",
                "CONSTANT_TYPE_COERCION",
                (
                    f"Constant of kind '{value_kind}' mapped to 'string' field "
                    f"'{destination_field.fieldPath}' relies on string serialization."
                ),
            )
        return (
            "error",
            "INCOMPATIBLE_CONSTANT_TYPE",
            (
                f"Constant of kind '{value_kind}' is incompatible with 'string' destination field "
                f"'{destination_field.fieldPath}'."
            ),
        )

    if dst_type == "integer":
        if isinstance(constant_value, int) and not isinstance(constant_value, bool):
            return ("compatible", None, "")
        if isinstance(constant_value, float):
            return (
                "warning",
                "LOSSY_TYPE_CONVERSION",
                (
                    f"Floating constant mapped to 'integer' destination field "
                    f"'{destination_field.fieldPath}' may truncate fractional precision."
                ),
            )
        if isinstance(constant_value, str):
            try:
                int(constant_value.strip())
                return (
                    "warning",
                    "CONSTANT_TYPE_COERCION",
                    (
                        f"Numeric string constant mapped to 'integer' destination field "
                        f"'{destination_field.fieldPath}' relies on runtime parsing."
                    ),
                )
            except ValueError:
                pass
        return (
            "error",
            "INCOMPATIBLE_CONSTANT_TYPE",
            (
                f"Constant of kind '{value_kind}' is incompatible with 'integer' destination field "
                f"'{destination_field.fieldPath}'."
            ),
        )

    if dst_type == "number":
        if isinstance(constant_value, (int, float)) and not isinstance(constant_value, bool):
            return ("compatible", None, "")
        if isinstance(constant_value, str):
            try:
                float(constant_value.strip())
                return (
                    "warning",
                    "CONSTANT_TYPE_COERCION",
                    (
                        f"Numeric string constant mapped to 'number' destination field "
                        f"'{destination_field.fieldPath}' relies on runtime parsing."
                    ),
                )
            except ValueError:
                pass
        return (
            "error",
            "INCOMPATIBLE_CONSTANT_TYPE",
            (
                f"Constant of kind '{value_kind}' is incompatible with 'number' destination field "
                f"'{destination_field.fieldPath}'."
            ),
        )

    if dst_type == "boolean":
        if isinstance(constant_value, bool):
            return ("compatible", None, "")
        if isinstance(constant_value, int) and constant_value in (0, 1):
            return (
                "warning",
                "CONSTANT_TYPE_COERCION",
                (
                    f"Numeric 0/1 constant mapped to 'boolean' field "
                    f"'{destination_field.fieldPath}' relies on implicit coercion."
                ),
            )
        if isinstance(constant_value, str) and constant_value.strip().lower() in ("true", "false"):
            return (
                "warning",
                "CONSTANT_TYPE_COERCION",
                (
                    f"Boolean string constant mapped to 'boolean' field "
                    f"'{destination_field.fieldPath}' relies on runtime parsing."
                ),
            )
        return (
            "error",
            "INCOMPATIBLE_CONSTANT_TYPE",
            (
                f"Constant of kind '{value_kind}' is incompatible with 'boolean' destination field "
                f"'{destination_field.fieldPath}'."
            ),
        )

    if dst_type == "date":
        if isinstance(constant_value, str) and _is_iso_date_string(constant_value):
            return ("compatible", None, "")
        return (
            "error",
            "INCOMPATIBLE_CONSTANT_TYPE",
            (
                f"Constant of kind '{value_kind}' is not a valid ISO-8601 date literal for "
                f"destination field '{destination_field.fieldPath}'."
            ),
        )

    if dst_type == "datetime":
        if isinstance(constant_value, str) and (
            _is_iso_datetime_string(constant_value) or _is_iso_date_string(constant_value)
        ):
            return ("compatible", None, "")
        if isinstance(constant_value, (int, float)) and not isinstance(constant_value, bool):
            return (
                "warning",
                "CONSTANT_TYPE_COERCION",
                (
                    f"Numeric constant mapped to 'datetime' destination field "
                    f"'{destination_field.fieldPath}' assumes epoch timestamp interpretation."
                ),
            )
        return (
            "error",
            "INCOMPATIBLE_CONSTANT_TYPE",
            (
                f"Constant of kind '{value_kind}' is not a valid ISO-8601 datetime literal for "
                f"destination field '{destination_field.fieldPath}'."
            ),
        )

    if dst_type == "object":
        if isinstance(constant_value, dict):
            return ("compatible", None, "")
        return (
            "error",
            "INCOMPATIBLE_CONSTANT_TYPE",
            (
                f"Constant of kind '{value_kind}' is incompatible with 'object' destination field "
                f"'{destination_field.fieldPath}'."
            ),
        )

    if dst_type == "array":
        if isinstance(constant_value, list):
            return ("compatible", None, "")
        return (
            "error",
            "INCOMPATIBLE_CONSTANT_TYPE",
            (
                f"Constant of kind '{value_kind}' is incompatible with 'array' destination field "
                f"'{destination_field.fieldPath}'."
            ),
        )

    if dst_type == "binary":
        if isinstance(constant_value, str):
            return (
                "warning",
                "CONSTANT_TYPE_COERCION",
                (
                    f"String constant mapped to 'binary' destination field "
                    f"'{destination_field.fieldPath}' requires encoding assumptions."
                ),
            )
        return (
            "error",
            "INCOMPATIBLE_CONSTANT_TYPE",
            (
                f"Constant of kind '{value_kind}' is incompatible with 'binary' destination field "
                f"'{destination_field.fieldPath}'."
            ),
        )

    return (
        "error",
        "INCOMPATIBLE_CONSTANT_TYPE",
        f"Unsupported constant target type '{dst_type}'.",
    )


def _is_effectively_required_destination_field(field: SchemaFieldResponse) -> bool:
    """Identify destination fields that require explicit mapping rules at write time."""
    if not field.isRequired or field.nullable:
        return False
    if field.defaultMetadata is not None and (
        field.defaultMetadata.hasDefault or field.defaultMetadata.isGenerated
    ):
        return False
    meta = field.metadata
    if any(
        bool(meta.get(flag))
        for flag in ("hasDefault", "isGenerated", "isIdentity", "autoIncrement")
    ):
        return False
    return True


def _extract_rule_id(rule: MappingRuleInput | MappingRuleResponse) -> UUID | None:
    return getattr(rule, "id", None)


def _check_rule_shape(
    rule: MappingRuleInput | MappingRuleResponse,
) -> MappingValidationIssue | None:
    rule_id = _extract_rule_id(rule)
    mapping_type = rule.mappingType

    if mapping_type == "direct":
        has_bad_constant = (
            "constantValue" in rule.model_fields_set
            if isinstance(rule, MappingRuleInput)
            else rule.constantValue is not None
        )
        if not rule.sourceFieldPath or not rule.sourceFieldPath.strip():
            return MappingValidationIssue(
                severity="error",
                code="INVALID_RULE_SHAPE",
                message="Direct rule requires a non-empty sourceFieldPath.",
                destinationFieldPath=rule.destinationFieldPath,
                ruleId=rule_id,
                ordinalPosition=rule.ordinalPosition,
            )
        if has_bad_constant or rule.transformRef is not None:
            return MappingValidationIssue(
                severity="error",
                code="INVALID_RULE_SHAPE",
                message="Direct rule must not specify constantValue or transformRef.",
                destinationFieldPath=rule.destinationFieldPath,
                sourceFieldPath=rule.sourceFieldPath,
                ruleId=rule_id,
                ordinalPosition=rule.ordinalPosition,
            )
        return None

    if mapping_type == "constant":
        missing_constant = (
            isinstance(rule, MappingRuleInput) and "constantValue" not in rule.model_fields_set
        )
        if rule.sourceFieldPath is not None or rule.transformRef is not None or missing_constant:
            return MappingValidationIssue(
                severity="error",
                code="INVALID_RULE_SHAPE",
                message=(
                    "Constant rule requires constantValue and forbids sourceFieldPath/transformRef."
                ),
                destinationFieldPath=rule.destinationFieldPath,
                sourceFieldPath=rule.sourceFieldPath,
                ruleId=rule_id,
                ordinalPosition=rule.ordinalPosition,
            )
        return None

    if mapping_type == "transform":
        if not rule.sourceFieldPath or not rule.sourceFieldPath.strip():
            return MappingValidationIssue(
                severity="error",
                code="INVALID_RULE_SHAPE",
                message="Transform rule requires a non-empty sourceFieldPath.",
                destinationFieldPath=rule.destinationFieldPath,
                ruleId=rule_id,
                ordinalPosition=rule.ordinalPosition,
            )
        if rule.transformRef is None or (
            not rule.transformRef.transformId and not rule.transformRef.presetName
        ):
            return MappingValidationIssue(
                severity="error",
                code="MALFORMED_TRANSFORM_REFERENCE",
                message="Transform rule requires transformRef with transformId or presetName.",
                destinationFieldPath=rule.destinationFieldPath,
                sourceFieldPath=rule.sourceFieldPath,
                ruleId=rule_id,
                ordinalPosition=rule.ordinalPosition,
            )
        forbidden = [
            key
            for key in rule.transformRef.params
            if key.strip().lower() in _FORBIDDEN_TRANSFORM_KEYS
        ]
        if forbidden:
            return MappingValidationIssue(
                severity="error",
                code="MALFORMED_TRANSFORM_REFERENCE",
                message="Transform reference parameters must not contain executable keys.",
                destinationFieldPath=rule.destinationFieldPath,
                sourceFieldPath=rule.sourceFieldPath,
                ruleId=rule_id,
                ordinalPosition=rule.ordinalPosition,
            )
        return None

    return MappingValidationIssue(
        severity="error",
        code="INVALID_RULE_SHAPE",
        message=f"Unsupported mapping rule type '{mapping_type}'.",
        destinationFieldPath=rule.destinationFieldPath,
        ruleId=rule_id,
        ordinalPosition=rule.ordinalPosition,
    )


def validate_mapping_definition(
    *,
    source_object_key: str,
    destination_object_key: str,
    stored_source_fingerprint: str | None,
    stored_destination_fingerprint: str | None,
    rules: Sequence[MappingRuleInput | MappingRuleResponse],
    source_schema: DiscoveredSchemaResponse,
    destination_schema: DiscoveredSchemaResponse,
    source_object: SchemaObjectResponse | None,
    destination_object: SchemaObjectResponse | None,
    validated_at: datetime | None = None,
) -> MappingValidationSummary:
    """Deterministically validate a mapping configuration against latest discovered schemas."""
    issues: list[MappingValidationIssue] = []

    source_drift_detected = False
    destination_drift_detected = False

    if (
        stored_source_fingerprint is not None
        and stored_source_fingerprint != source_schema.schemaFingerprint
    ):
        source_drift_detected = True
        issues.append(
            MappingValidationIssue(
                severity="warning",
                code="SOURCE_SCHEMA_DRIFT",
                message=(
                    f"Source schema snapshot changed since mapping was saved for object "
                    f"'{source_object_key}'."
                ),
            )
        )

    if (
        stored_destination_fingerprint is not None
        and stored_destination_fingerprint != destination_schema.schemaFingerprint
    ):
        destination_drift_detected = True
        issues.append(
            MappingValidationIssue(
                severity="warning",
                code="DESTINATION_SCHEMA_DRIFT",
                message=(
                    f"Destination schema snapshot changed since mapping was saved for object "
                    f"'{destination_object_key}'."
                ),
            )
        )

    if source_object is None:
        source_drift_detected = True
        issues.append(
            MappingValidationIssue(
                severity="error",
                code="SOURCE_OBJECT_NOT_FOUND",
                message=(
                    f"Source object '{source_object_key}' no longer exists in the latest "
                    "discovered source schema."
                ),
            )
        )

    if destination_object is None:
        destination_drift_detected = True
        issues.append(
            MappingValidationIssue(
                severity="error",
                code="DESTINATION_OBJECT_NOT_FOUND",
                message=(
                    f"Destination object '{destination_object_key}' no longer exists in the "
                    "latest discovered destination schema."
                ),
            )
        )

    source_fields_by_path: dict[str, SchemaFieldResponse] = (
        {field.fieldPath: field for field in source_object.fields}
        if source_object is not None
        else {}
    )
    dest_fields_by_path: dict[str, SchemaFieldResponse] = (
        {field.fieldPath: field for field in destination_object.fields}
        if destination_object is not None
        else {}
    )

    sorted_rules = sorted(
        rules,
        key=lambda item: (
            item.ordinalPosition,
            item.destinationFieldPath,
            str(_extract_rule_id(item) or ""),
        ),
    )

    seen_dest_paths: set[str] = set()
    seen_ordinals: set[int] = set()
    validly_mapped_dest_paths: set[str] = set()
    mapped_source_paths: set[str] = set()

    for rule in sorted_rules:
        rule_id = _extract_rule_id(rule)
        rule_has_structural_error = False

        if rule.destinationFieldPath in seen_dest_paths:
            rule_has_structural_error = True
            issues.append(
                MappingValidationIssue(
                    severity="error",
                    code="DUPLICATE_DESTINATION_FIELD",
                    message=(
                        f"Destination field '{rule.destinationFieldPath}' is targeted by more than "
                        "one mapping rule."
                    ),
                    destinationFieldPath=rule.destinationFieldPath,
                    sourceFieldPath=rule.sourceFieldPath,
                    ruleId=rule_id,
                    ordinalPosition=rule.ordinalPosition,
                )
            )
        seen_dest_paths.add(rule.destinationFieldPath)

        if rule.ordinalPosition in seen_ordinals:
            rule_has_structural_error = True
            issues.append(
                MappingValidationIssue(
                    severity="error",
                    code="DUPLICATE_RULE_ORDINAL",
                    message=f"Duplicate rule ordinalPosition {rule.ordinalPosition}.",
                    destinationFieldPath=rule.destinationFieldPath,
                    sourceFieldPath=rule.sourceFieldPath,
                    ruleId=rule_id,
                    ordinalPosition=rule.ordinalPosition,
                )
            )
        seen_ordinals.add(rule.ordinalPosition)

        shape_issue = _check_rule_shape(rule)
        if shape_issue is not None:
            issues.append(shape_issue)
            continue

        src_field: SchemaFieldResponse | None = None
        if rule.mappingType in ("direct", "transform") and rule.sourceFieldPath is not None:
            if source_object is not None:
                src_field = source_fields_by_path.get(rule.sourceFieldPath)
                if src_field is None:
                    rule_has_structural_error = True
                    source_drift_detected = True
                    issues.append(
                        MappingValidationIssue(
                            severity="error",
                            code="INVALID_SOURCE_FIELD",
                            message=(
                                f"Source field '{rule.sourceFieldPath}' does not exist on object "
                                f"'{source_object_key}'."
                            ),
                            sourceFieldPath=rule.sourceFieldPath,
                            destinationFieldPath=rule.destinationFieldPath,
                            ruleId=rule_id,
                            ordinalPosition=rule.ordinalPosition,
                        )
                    )
                else:
                    mapped_source_paths.add(src_field.fieldPath)

        dst_field: SchemaFieldResponse | None = None
        if destination_object is not None:
            dst_field = dest_fields_by_path.get(rule.destinationFieldPath)
            if dst_field is None:
                rule_has_structural_error = True
                destination_drift_detected = True
                issues.append(
                    MappingValidationIssue(
                        severity="error",
                        code="INVALID_DESTINATION_FIELD",
                        message=(
                            f"Destination field '{rule.destinationFieldPath}' does not exist on "
                            f"object '{destination_object_key}'."
                        ),
                        destinationFieldPath=rule.destinationFieldPath,
                        sourceFieldPath=rule.sourceFieldPath,
                        ruleId=rule_id,
                        ordinalPosition=rule.ordinalPosition,
                    )
                )

        if not rule_has_structural_error and dst_field is not None:
            validly_mapped_dest_paths.add(dst_field.fieldPath)

        if dst_field is None:
            continue

        if rule.mappingType == "constant":
            outcome, code, msg = evaluate_constant_type_compatibility(rule.constantValue, dst_field)
            if outcome != "compatible" and code is not None:
                issues.append(
                    MappingValidationIssue(
                        severity="error" if outcome == "error" else "warning",
                        code=code,
                        message=msg,
                        destinationFieldPath=dst_field.fieldPath,
                        ruleId=rule_id,
                        ordinalPosition=rule.ordinalPosition,
                    )
                )
        elif src_field is not None:
            outcome, code, msg = evaluate_canonical_type_compatibility(src_field, dst_field)
            if rule.mappingType == "direct":
                if outcome != "compatible" and code is not None:
                    issues.append(
                        MappingValidationIssue(
                            severity="error" if outcome == "error" else "warning",
                            code=code,
                            message=msg,
                            destinationFieldPath=dst_field.fieldPath,
                            sourceFieldPath=src_field.fieldPath,
                            ruleId=rule_id,
                            ordinalPosition=rule.ordinalPosition,
                        )
                    )
                if outcome != "error" and src_field.nullable and not dst_field.nullable:
                    issues.append(
                        MappingValidationIssue(
                            severity="warning",
                            code="NULLABLE_SOURCE_TO_NON_NULLABLE_DESTINATION",
                            message=(
                                f"Nullable source field '{src_field.fieldPath}' maps to "
                                f"non-nullable destination field '{dst_field.fieldPath}'."
                            ),
                            destinationFieldPath=dst_field.fieldPath,
                            sourceFieldPath=src_field.fieldPath,
                            ruleId=rule_id,
                            ordinalPosition=rule.ordinalPosition,
                        )
                    )
            elif rule.mappingType == "transform":
                if code == "UNKNOWN_CANONICAL_TYPE":
                    issues.append(
                        MappingValidationIssue(
                            severity="error",
                            code=code,
                            message=msg,
                            destinationFieldPath=dst_field.fieldPath,
                            sourceFieldPath=src_field.fieldPath,
                            ruleId=rule_id,
                            ordinalPosition=rule.ordinalPosition,
                        )
                    )
                elif outcome == "error":
                    issues.append(
                        MappingValidationIssue(
                            severity="warning",
                            code="TRANSFORM_TYPE_UNVERIFIED",
                            message=(
                                f"Direct conversion ('{src_field.canonicalType}' -> "
                                f"'{dst_field.canonicalType}') requires declared transformation "
                                f"for '{dst_field.fieldPath}'."
                            ),
                            destinationFieldPath=dst_field.fieldPath,
                            sourceFieldPath=src_field.fieldPath,
                            ruleId=rule_id,
                            ordinalPosition=rule.ordinalPosition,
                        )
                    )

    missing_required: list[str] = []
    unmapped_destination: list[str] = []
    if destination_object is not None:
        ordered_dest_fields = sorted(
            destination_object.fields,
            key=lambda item: (item.ordinalPosition, item.fieldPath),
        )
        for field in ordered_dest_fields:
            if field.fieldPath not in validly_mapped_dest_paths:
                unmapped_destination.append(field.fieldPath)
                if _is_effectively_required_destination_field(field):
                    missing_required.append(field.fieldPath)
                    issues.append(
                        MappingValidationIssue(
                            severity="error",
                            code="MISSING_REQUIRED_FIELD",
                            message=(
                                f"Required non-nullable destination field '{field.fieldPath}' "
                                "without default or generated value has no valid mapping rule."
                            ),
                            destinationFieldPath=field.fieldPath,
                        )
                    )

    unmapped_source: list[str] = []
    if source_object is not None:
        ordered_src_fields = sorted(
            source_object.fields,
            key=lambda item: (item.ordinalPosition, item.fieldPath),
        )
        unmapped_source = [
            field.fieldPath
            for field in ordered_src_fields
            if field.fieldPath not in mapped_source_paths
        ]

    error_count = sum(1 for item in issues if item.severity == "error")
    warning_count = sum(1 for item in issues if item.severity == "warning")
    schema_drift_detected = source_drift_detected or destination_drift_detected
    timestamp = validated_at if validated_at is not None else datetime.now(UTC)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)

    return MappingValidationSummary(
        isValid=(error_count == 0),
        errorCount=error_count,
        warningCount=warning_count,
        missingRequiredDestinationFields=missing_required,
        unmappedSourceFields=unmapped_source,
        unmappedDestinationFields=unmapped_destination,
        schemaDriftDetected=schema_drift_detected,
        sourceDriftDetected=source_drift_detected,
        destinationDriftDetected=destination_drift_detected,
        issues=issues,
        validatedAt=timestamp,
    )
