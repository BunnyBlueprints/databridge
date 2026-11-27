"""Pydantic v2 domain and API persistence models for field mappings and mapping rules."""

import math
import re
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.connection_test import _require_non_empty_text, _StrictRequestModel
from app.schemas.schemas import (
    _EMBEDDED_USERINFO_RE,
    _FORBIDDEN_METADATA_KEYS,
    validate_safe_metadata_dict,
    validate_sha256_fingerprint,
)

MappingStatus = Literal["draft", "valid", "invalid"]
MappingRuleType = Literal["direct", "constant", "transform"]
MappingIssueSeverity = Literal["error", "warning"]

ScalarJsonValue = str | int | float | bool | None
ConstantJsonValue = ScalarJsonValue | list[Any] | dict[str, Any]


def _validate_safe_constant_value(value: Any, *, max_depth: int = 6) -> ConstantJsonValue:
    """Validate any JSON constant (null, scalar, array, object) safely without NaN or secrets."""

    def _walk(node: Any, depth: int) -> None:
        if depth > max_depth:
            raise ValueError(f"Constant value exceeds maximum nesting depth of {max_depth}.")
        if node is None or isinstance(node, bool):
            return
        if isinstance(node, int):
            return
        if isinstance(node, float):
            if not math.isfinite(node):
                raise ValueError("Constant numeric value must be finite.")
            return
        if isinstance(node, str):
            if _EMBEDDED_USERINFO_RE.search(node):
                raise ValueError("Constant string must not contain embedded URI credentials.")
            if len(node) > 4096:
                raise ValueError("Constant string exceeds maximum length of 4096 characters.")
            return
        if isinstance(node, list):
            if len(node) > 200:
                raise ValueError("Constant array exceeds maximum length of 200 items.")
            for item in node:
                _walk(item, depth + 1)
            return
        if isinstance(node, dict):
            if len(node) > 100:
                raise ValueError("Constant object exceeds maximum of 100 keys.")
            for raw_key, child in node.items():
                if not isinstance(raw_key, str) or not raw_key.strip():
                    raise ValueError("Constant object keys must be non-empty strings.")
                lowered = raw_key.strip().lower()
                if lowered in _FORBIDDEN_METADATA_KEYS or lowered in _FORBIDDEN_TRANSFORM_KEYS:
                    raise ValueError(f"Constant object must not contain forbidden key '{raw_key}'.")
                _walk(child, depth + 1)
            return
        raise ValueError("Constant value must be valid JSON (null, primitive, array, or object).")

    _walk(value, 0)
    return value


_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")
_FORBIDDEN_TRANSFORM_KEYS = frozenset(
    {
        "code",
        "expression",
        "expr",
        "eval",
        "exec",
        "function",
        "lambda",
        "python",
        "script",
        "sql",
        "statement",
    }
)


class TransformReference(_StrictRequestModel):
    """Non-executable structural reference to a future named or registered transformation."""

    transformId: str | None = None
    presetName: str | None = None
    params: dict[str, ScalarJsonValue] = Field(default_factory=dict)

    @field_validator("transformId", "presetName")
    @classmethod
    def _check_identifier(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = _require_non_empty_text(value, "Transformation identifier", max_length=120)
        if not _IDENTIFIER_RE.match(cleaned):
            raise ValueError(
                "Transformation identifier must contain only alphanumeric characters, "
                "underscores, hyphens, dots, or colons."
            )
        return cleaned

    @field_validator("params")
    @classmethod
    def _check_params(cls, value: dict[str, ScalarJsonValue]) -> dict[str, ScalarJsonValue]:
        if len(value) > 32:
            raise ValueError("Transform parameters exceed maximum of 32 keys.")
        for key, param_val in value.items():
            cleaned_key = _require_non_empty_text(key, "Transform parameter key", max_length=64)
            if cleaned_key.lower() in _FORBIDDEN_TRANSFORM_KEYS:
                raise ValueError(
                    f"Executable transformation parameter key '{cleaned_key}' is not permitted."
                )
            if isinstance(param_val, str) and len(param_val) > 512:
                raise ValueError("Transform parameter string exceeds maximum length of 512.")
        return dict(value)

    @model_validator(mode="after")
    def _require_id_or_preset(self) -> "TransformReference":
        if self.transformId is None and self.presetName is None:
            raise ValueError("TransformReference requires either transformId or presetName.")
        return self


class MappingRuleInput(_StrictRequestModel):
    """Input model for creating or replacing one ordered field mapping rule."""

    id: UUID | None = None
    sourceFieldPath: str | None = None
    destinationFieldPath: str
    mappingType: MappingRuleType
    constantValue: ConstantJsonValue = None
    transformRef: TransformReference | None = None
    ordinalPosition: int = Field(ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("constantValue")
    @classmethod
    def _check_constant_value(cls, value: ConstantJsonValue) -> ConstantJsonValue:
        return _validate_safe_constant_value(value)

    @field_validator("sourceFieldPath")
    @classmethod
    def _check_source_field_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_non_empty_text(value, "Source field path", max_length=512)

    @field_validator("destinationFieldPath")
    @classmethod
    def _check_destination_field_path(cls, value: str) -> str:
        return _require_non_empty_text(value, "Destination field path", max_length=512)

    @field_validator("metadata")
    @classmethod
    def _check_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_safe_metadata_dict(value, field_label="mapping rule metadata")

    @model_validator(mode="after")
    def _validate_rule_shape(self) -> "MappingRuleInput":
        has_constant_field = "constantValue" in self.model_fields_set

        if self.mappingType == "direct":
            if self.sourceFieldPath is None:
                raise ValueError("Direct mapping rule requires sourceFieldPath.")
            if has_constant_field:
                raise ValueError("Direct mapping rule must not set constantValue.")
            if self.transformRef is not None:
                raise ValueError("Direct mapping rule must not set transformRef.")
        elif self.mappingType == "constant":
            if self.sourceFieldPath is not None:
                raise ValueError("Constant mapping rule must not set sourceFieldPath.")
            if not has_constant_field:
                raise ValueError("Constant mapping rule requires constantValue.")
            if self.transformRef is not None:
                raise ValueError("Constant mapping rule must not set transformRef.")
        elif self.mappingType == "transform":
            if self.sourceFieldPath is None:
                raise ValueError("Transform mapping rule requires sourceFieldPath.")
            if has_constant_field:
                raise ValueError("Transform mapping rule must not set constantValue.")
            if self.transformRef is None:
                raise ValueError("Transform mapping rule requires transformRef.")
        return self


class MappingRuleResponse(BaseModel):
    """Persisted field mapping rule returned by repositories and API responses."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    mappingId: UUID
    sourceFieldPath: str | None = None
    destinationFieldPath: str
    mappingType: MappingRuleType
    constantValue: ConstantJsonValue = None
    transformRef: TransformReference | None = None
    ordinalPosition: int = Field(ge=0)
    metadata: dict[str, Any]
    createdAt: datetime
    updatedAt: datetime


class MappingValidationIssue(_StrictRequestModel):
    """Structured issue emitted during structural/type mapping validation."""

    severity: MappingIssueSeverity
    code: str
    message: str
    destinationFieldPath: str | None = None
    sourceFieldPath: str | None = None
    ruleId: UUID | None = None
    ordinalPosition: int | None = None

    @field_validator("code")
    @classmethod
    def _check_code(cls, value: str) -> str:
        return _require_non_empty_text(value, "Validation issue code", max_length=64)

    @field_validator("message")
    @classmethod
    def _check_message(cls, value: str) -> str:
        return _require_non_empty_text(value, "Validation issue message", max_length=500)


class MappingValidationSummary(_StrictRequestModel):
    """Persisted validation summary for a saved mapping configuration."""

    isValid: bool
    errorCount: int = Field(ge=0)
    warningCount: int = Field(ge=0)
    missingRequiredDestinationFields: list[str] = Field(default_factory=list)
    unmappedSourceFields: list[str] = Field(default_factory=list)
    unmappedDestinationFields: list[str] = Field(default_factory=list)
    schemaDriftDetected: bool = False
    sourceDriftDetected: bool = False
    destinationDriftDetected: bool = False
    issues: list[MappingValidationIssue] = Field(default_factory=list)
    validatedAt: datetime


class MappingValidationResponse(BaseModel):
    """Response returned by mapping validation endpoints."""

    model_config = ConfigDict(extra="forbid")

    mappingId: UUID | None = None
    mappingVersion: int | None = Field(default=None, ge=1)
    status: MappingStatus
    validationSummary: MappingValidationSummary


class MappingCreate(_StrictRequestModel):
    """Strict input model for creating a persistent mapping configuration."""

    id: UUID | None = None
    name: str
    description: str | None = None
    sourceConnectionId: UUID
    sourceObjectKey: str
    destinationConnectionId: UUID
    destinationObjectKey: str
    status: MappingStatus = "draft"
    sourceSchemaFingerprint: str | None = None
    destinationSchemaFingerprint: str | None = None
    validationSummary: MappingValidationSummary | None = None
    rules: list[MappingRuleInput] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str) -> str:
        return _require_non_empty_text(value, "Mapping name", max_length=120)

    @field_validator("description")
    @classmethod
    def _check_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            return None
        if len(stripped) > 500:
            raise ValueError("Mapping description exceeds maximum length of 500 characters.")
        return stripped

    @field_validator("sourceObjectKey", "destinationObjectKey")
    @classmethod
    def _check_object_keys(cls, value: str) -> str:
        return _require_non_empty_text(value, "Object key", max_length=512)

    @field_validator("sourceSchemaFingerprint", "destinationSchemaFingerprint")
    @classmethod
    def _check_fingerprints(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_sha256_fingerprint(value)

    @model_validator(mode="after")
    def _check_rule_uniqueness(self) -> "MappingCreate":
        _validate_rule_list(self.rules)
        return self


class MappingUpdate(_StrictRequestModel):
    """Strict input model for updating an existing mapping configuration and replacing rules."""

    name: str | None = None
    description: str | None = None
    sourceConnectionId: UUID | None = None
    sourceObjectKey: str | None = None
    destinationConnectionId: UUID | None = None
    destinationObjectKey: str | None = None
    status: MappingStatus | None = None
    sourceSchemaFingerprint: str | None = None
    destinationSchemaFingerprint: str | None = None
    validationSummary: MappingValidationSummary | None = None
    expectedVersion: int | None = Field(default=None, ge=1)
    rules: list[MappingRuleInput] | None = None

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_non_empty_text(value, "Mapping name", max_length=120)

    @field_validator("description")
    @classmethod
    def _check_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            return None
        if len(stripped) > 500:
            raise ValueError("Mapping description exceeds maximum length of 500 characters.")
        return stripped

    @field_validator("sourceObjectKey", "destinationObjectKey")
    @classmethod
    def _check_object_keys(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_non_empty_text(value, "Object key", max_length=512)

    @field_validator("sourceSchemaFingerprint", "destinationSchemaFingerprint")
    @classmethod
    def _check_fingerprints(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_sha256_fingerprint(value)

    @model_validator(mode="after")
    def _check_rule_uniqueness(self) -> "MappingUpdate":
        if self.rules is not None:
            _validate_rule_list(self.rules)
        return self


class MappingResponse(BaseModel):
    """Persisted mapping configuration returned by repositories and API responses."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    name: str
    description: str | None = None
    sourceConnectionId: UUID
    sourceObjectKey: str
    destinationConnectionId: UUID
    destinationObjectKey: str
    status: MappingStatus
    mappingVersion: int = Field(ge=1)
    sourceSchemaFingerprint: str | None = None
    destinationSchemaFingerprint: str | None = None
    validationSummary: MappingValidationSummary | None = None
    rules: list[MappingRuleResponse] = Field(default_factory=list)
    createdAt: datetime
    updatedAt: datetime


class MappingListResponse(BaseModel):
    """Paginated list response contract for future GET /api/v1/mappings."""

    model_config = ConfigDict(extra="forbid")

    items: list[MappingResponse]
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)


def _validate_rule_list(rules: list[MappingRuleInput]) -> None:
    seen_destinations: set[str] = set()
    seen_ordinals: set[int] = set()
    for rule in rules:
        if rule.destinationFieldPath in seen_destinations:
            raise ValueError(
                f"Duplicate mapping rule for destination field '{rule.destinationFieldPath}'."
            )
        if rule.ordinalPosition in seen_ordinals:
            raise ValueError(f"Duplicate ordinalPosition {rule.ordinalPosition} in mapping rules.")
        seen_destinations.add(rule.destinationFieldPath)
        seen_ordinals.add(rule.ordinalPosition)
