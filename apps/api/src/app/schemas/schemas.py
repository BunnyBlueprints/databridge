"""Pydantic v2 domain and API persistence models for discovered schemas, objects, and fields."""

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.connection_test import _require_non_empty_text, _StrictRequestModel

DiscoveryStatus = Literal["completed", "partial"]
FieldDiscoverySource = Literal["catalog", "openapi", "inferred_sample", "manual"]
SnapshotDiscoverySource = Literal["catalog", "openapi", "inferred_sample", "manual", "mixed"]
SchemaObjectType = Literal["table", "view", "collection", "endpoint", "file", "record_set"]
CanonicalFieldType = Literal[
    "string",
    "integer",
    "number",
    "boolean",
    "date",
    "datetime",
    "object",
    "array",
    "binary",
    "json",
    "null",
    "mixed",
    "unknown",
]

_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
_EMBEDDED_USERINFO_RE = re.compile(r"://[^/\s:@]+:[^/\s@]*@")
_FORBIDDEN_METADATA_KEYS = frozenset(
    {
        "password",
        "passwd",
        "pwd",
        "secret",
        "apikey",
        "api_key",
        "bearertoken",
        "bearer_token",
        "access_token",
        "refresh_token",
        "connectionuri",
        "connection_uri",
        "ciphertext",
        "nonce",
        "keyid",
        "key_id",
        "username",
        "credentials",
        "filecontent",
        "file_content",
        "rawcontent",
        "raw_content",
        "samplerows",
        "sample_rows",
    }
)


def validate_sha256_fingerprint(value: str, label: str = "Schema fingerprint") -> str:
    """Validate a lowercase 64-character SHA-256 hex digest."""
    cleaned = _require_non_empty_text(value, label, max_length=64).lower()
    if not _SHA256_HEX_RE.match(cleaned):
        raise ValueError(f"{label} must be a 64-character lowercase SHA-256 hex string.")
    return cleaned


def validate_safe_metadata_dict(
    value: dict[str, Any],
    *,
    field_label: str = "metadata",
    max_depth: int = 4,
    max_keys: int = 100,
) -> dict[str, Any]:
    """Ensure persisted schema/mapping JSONB metadata never embeds credentials or file data."""
    if not isinstance(value, dict):
        raise ValueError(f"{field_label} must be a JSON object.")

    def _walk(node: Any, depth: int) -> None:
        if depth > max_depth:
            raise ValueError(f"{field_label} exceeds maximum nesting depth of {max_depth}.")
        if isinstance(node, dict):
            if len(node) > max_keys:
                raise ValueError(f"{field_label} exceeds maximum of {max_keys} keys per object.")
            for raw_key, child in node.items():
                if not isinstance(raw_key, str) or not raw_key.strip():
                    raise ValueError(f"{field_label} keys must be non-empty strings.")
                if raw_key.strip().lower() in _FORBIDDEN_METADATA_KEYS:
                    raise ValueError(
                        f"{field_label} must not contain credential or raw data key '{raw_key}'."
                    )
                _walk(child, depth + 1)
        elif isinstance(node, list):
            if len(node) > 200:
                raise ValueError(f"{field_label} list exceeds maximum length of 200 items.")
            for item in node:
                _walk(item, depth + 1)
        elif isinstance(node, str):
            if _EMBEDDED_USERINFO_RE.search(node):
                raise ValueError(f"{field_label} must not contain embedded URI credentials.")
            if len(node) > 4096:
                raise ValueError(f"{field_label} string value exceeds 4096 characters.")
        elif node is not None and not isinstance(node, (bool, int, float)):
            raise ValueError(f"{field_label} contains unsupported non-JSON value type.")

    _walk(value, 0)
    return dict(value)


def normalize_utc_timestamp(value: datetime | None = None) -> datetime:
    """Normalize a datetime to timezone-aware UTC."""
    if value is None:
        return datetime.now(UTC)
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class ForeignKeyReference(_StrictRequestModel):
    """Non-secret normalized foreign-key metadata for relational schema fields."""

    targetObjectKey: str
    targetFieldPath: str

    @field_validator("targetObjectKey")
    @classmethod
    def _check_target_object_key(cls, value: str) -> str:
        return _require_non_empty_text(value, "Target object key", max_length=512)

    @field_validator("targetFieldPath")
    @classmethod
    def _check_target_field_path(cls, value: str) -> str:
        return _require_non_empty_text(value, "Target field path", max_length=512)


class SchemaDefaultMetadata(_StrictRequestModel):
    """Structured default-value metadata for a discovered field."""

    hasDefault: bool = True
    expression: str | None = None
    isGenerated: bool = False

    @field_validator("expression")
    @classmethod
    def _check_expression(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_non_empty_text(value, "Default expression", max_length=512)


class SchemaWarning(_StrictRequestModel):
    """Structured warning produced during schema discovery or inference."""

    code: str
    message: str
    objectKey: str | None = None
    fieldPath: str | None = None

    @field_validator("code")
    @classmethod
    def _check_code(cls, value: str) -> str:
        return _require_non_empty_text(value, "Warning code", max_length=64)

    @field_validator("message")
    @classmethod
    def _check_message(cls, value: str) -> str:
        return _require_non_empty_text(value, "Warning message", max_length=500)


class SchemaFieldInput(_StrictRequestModel):
    """Input model for persisting one discovered or declared schema field."""

    id: UUID | None = None
    fieldPath: str
    fieldName: str
    nativeType: str
    canonicalType: CanonicalFieldType
    nullable: bool = True
    isRequired: bool = False
    primaryKey: bool = False
    uniqueKey: bool = False
    defaultMetadata: SchemaDefaultMetadata | None = None
    ordinalPosition: int = Field(ge=0)
    discoverySource: FieldDiscoverySource
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("fieldPath")
    @classmethod
    def _check_field_path(cls, value: str) -> str:
        return _require_non_empty_text(value, "Field path", max_length=512)

    @field_validator("fieldName")
    @classmethod
    def _check_field_name(cls, value: str) -> str:
        return _require_non_empty_text(value, "Field name", max_length=255)

    @field_validator("nativeType")
    @classmethod
    def _check_native_type(cls, value: str) -> str:
        return _require_non_empty_text(value, "Native type", max_length=128)

    @field_validator("metadata")
    @classmethod
    def _check_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_safe_metadata_dict(value, field_label="field metadata")


class SchemaObjectInput(_StrictRequestModel):
    """Input model for persisting one discovered schema object and its ordered fields."""

    id: UUID | None = None
    objectKey: str
    objectName: str
    objectType: SchemaObjectType
    discoverySource: FieldDiscoverySource
    nativeMetadata: dict[str, Any] = Field(default_factory=dict)
    canonicalMetadata: dict[str, Any] = Field(default_factory=dict)
    fields: list[SchemaFieldInput] = Field(default_factory=list)

    @field_validator("objectKey")
    @classmethod
    def _check_object_key(cls, value: str) -> str:
        return _require_non_empty_text(value, "Object key", max_length=512)

    @field_validator("objectName")
    @classmethod
    def _check_object_name(cls, value: str) -> str:
        return _require_non_empty_text(value, "Object name", max_length=255)

    @field_validator("nativeMetadata")
    @classmethod
    def _check_native_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_safe_metadata_dict(value, field_label="nativeMetadata")

    @field_validator("canonicalMetadata")
    @classmethod
    def _check_canonical_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_safe_metadata_dict(value, field_label="canonicalMetadata")

    @model_validator(mode="after")
    def _check_unique_field_paths(self) -> "SchemaObjectInput":
        seen_paths: set[str] = set()
        for field in self.fields:
            if field.fieldPath in seen_paths:
                raise ValueError(
                    f"Duplicate fieldPath '{field.fieldPath}' in object '{self.objectKey}'."
                )
            seen_paths.add(field.fieldPath)
        return self


class DiscoveredSchemaCreate(_StrictRequestModel):
    """Repository/service contract for persisting the latest schema snapshot for a connection."""

    id: UUID | None = None
    connectionId: UUID
    discoveryStatus: DiscoveryStatus
    discoveredAt: datetime = Field(default_factory=lambda: datetime.now(UTC))
    schemaFingerprint: str | None = None
    discoverySource: SnapshotDiscoverySource
    metadata: dict[str, Any] = Field(default_factory=dict)
    objects: list[SchemaObjectInput] = Field(default_factory=list)

    @field_validator("discoveredAt")
    @classmethod
    def _check_discovered_at(cls, value: datetime) -> datetime:
        return normalize_utc_timestamp(value)

    @field_validator("schemaFingerprint")
    @classmethod
    def _check_fingerprint(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_sha256_fingerprint(value)

    @field_validator("metadata")
    @classmethod
    def _check_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_safe_metadata_dict(value, field_label="schema metadata")

    @model_validator(mode="after")
    def _check_unique_object_keys_and_fingerprint(self) -> "DiscoveredSchemaCreate":
        seen_keys: set[str] = set()
        for obj in self.objects:
            if obj.objectKey in seen_keys:
                raise ValueError(f"Duplicate objectKey '{obj.objectKey}' in schema snapshot.")
            seen_keys.add(obj.objectKey)
        if self.schemaFingerprint is None:
            self.schemaFingerprint = compute_schema_fingerprint(self.objects)
        return self


class SchemaFieldResponse(BaseModel):
    """Safe persisted schema field model returned by repositories and API responses."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    schemaObjectId: UUID
    fieldPath: str
    fieldName: str
    nativeType: str
    canonicalType: CanonicalFieldType
    nullable: bool
    isRequired: bool
    primaryKey: bool
    uniqueKey: bool
    defaultMetadata: SchemaDefaultMetadata | None = None
    ordinalPosition: int = Field(ge=0)
    discoverySource: FieldDiscoverySource
    metadata: dict[str, Any]
    createdAt: datetime
    updatedAt: datetime


class SchemaObjectSummaryResponse(BaseModel):
    """Lightweight persisted schema object summary without embedded field array."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    discoveredSchemaId: UUID
    objectKey: str
    objectName: str
    objectType: SchemaObjectType
    discoverySource: FieldDiscoverySource
    nativeMetadata: dict[str, Any]
    canonicalMetadata: dict[str, Any]
    fieldCount: int = Field(ge=0)
    createdAt: datetime
    updatedAt: datetime


class SchemaObjectResponse(SchemaObjectSummaryResponse):
    """Full persisted schema object including ordered fields."""

    fields: list[SchemaFieldResponse] = Field(default_factory=list)


class DiscoveredSchemaResponse(BaseModel):
    """Safe persisted schema snapshot response for a connection."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    connectionId: UUID
    discoveryStatus: DiscoveryStatus
    discoveredAt: datetime
    schemaFingerprint: str
    discoverySource: SnapshotDiscoverySource
    objectCount: int = Field(default=0, ge=0)
    warnings: list[SchemaWarning] = Field(default_factory=list)
    metadata: dict[str, Any]
    objects: list[SchemaObjectSummaryResponse] = Field(default_factory=list)
    createdAt: datetime
    updatedAt: datetime


RestDiscoveryMode = Literal["openapi", "sample_get", "manual"]


def _validate_relative_http_path(value: str | None, label: str) -> str | None:
    if value is None:
        return None
    cleaned = _require_non_empty_text(value, label, max_length=1024)
    if any(ch.isspace() for ch in cleaned):
        raise ValueError(f"{label} must not contain whitespace.")
    if "://" in cleaned or cleaned.startswith("//") or "@" in cleaned:
        raise ValueError(
            f"{label} must be a relative path on the saved connection host (starting with '/')."
        )
    if not cleaned.startswith("/"):
        cleaned = f"/{cleaned}"
    return cleaned


def _validate_record_path(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = _require_non_empty_text(value, "Record path", max_length=255)
    if any(ch.isspace() for ch in cleaned):
        raise ValueError("Record path must not contain whitespace.")
    segments = cleaned.split(".")
    if any(not seg for seg in segments):
        raise ValueError("Record path must not contain empty dot segments.")
    return cleaned


class ManualSchemaFieldInput(_StrictRequestModel):
    """Strict input model for manually declared fields on opaque or write-only endpoints."""

    fieldPath: str
    fieldName: str | None = None
    canonicalType: CanonicalFieldType
    nativeType: str | None = None
    nullable: bool = True
    isRequired: bool = False
    primaryKey: bool = False
    uniqueKey: bool = False
    defaultMetadata: SchemaDefaultMetadata | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("fieldPath")
    @classmethod
    def _check_field_path(cls, value: str) -> str:
        return _require_non_empty_text(value, "Field path", max_length=512)

    @field_validator("fieldName")
    @classmethod
    def _check_field_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_non_empty_text(value, "Field name", max_length=255)

    @field_validator("nativeType")
    @classmethod
    def _check_native_type(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_non_empty_text(value, "Native type", max_length=128)

    @field_validator("metadata")
    @classmethod
    def _check_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_safe_metadata_dict(value, field_label="manual field metadata")


class ManualSchemaObjectInput(_StrictRequestModel):
    """Strict input model for manually declaring REST endpoint objects without introspection."""

    objectKey: str
    objectName: str | None = None
    objectType: SchemaObjectType = "endpoint"
    nativeMetadata: dict[str, Any] = Field(default_factory=dict)
    fields: list[ManualSchemaFieldInput] = Field(min_length=1, max_length=500)

    @field_validator("objectKey")
    @classmethod
    def _check_object_key(cls, value: str) -> str:
        return _require_non_empty_text(value, "Object key", max_length=512)

    @field_validator("objectName")
    @classmethod
    def _check_object_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_non_empty_text(value, "Object name", max_length=255)

    @field_validator("nativeMetadata")
    @classmethod
    def _check_native_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_safe_metadata_dict(value, field_label="manual object metadata")

    @model_validator(mode="after")
    def _check_unique_manual_fields(self) -> "ManualSchemaObjectInput":
        seen: set[str] = set()
        for fld in self.fields:
            if fld.fieldPath in seen:
                raise ValueError(
                    f"Duplicate fieldPath '{fld.fieldPath}' in manual object '{self.objectKey}'."
                )
            seen.add(fld.fieldPath)
        return self


class DiscoveryOptionsInput(_StrictRequestModel):
    """Bounded connector-specific discovery options accepted by POST /discover."""

    includeSchemas: list[str] | None = Field(default=None, max_length=50)
    includeViews: bool = True
    sampleLimit: int | None = Field(default=None, ge=1, le=1000)
    mode: RestDiscoveryMode | None = None
    openApiPath: str | None = None
    resourcePath: str | None = None
    recordPath: str | None = None
    openApiSpec: dict[str, Any] | None = None
    manualObjects: list[ManualSchemaObjectInput] | None = Field(default=None, max_length=100)

    @field_validator("includeSchemas")
    @classmethod
    def _check_include_schemas(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        cleaned: list[str] = []
        for schema_name in value:
            cleaned.append(_require_non_empty_text(schema_name, "Schema name", max_length=128))
        return cleaned

    @field_validator("openApiPath")
    @classmethod
    def _check_openapi_path(cls, value: str | None) -> str | None:
        return _validate_relative_http_path(value, "openApiPath")

    @field_validator("resourcePath")
    @classmethod
    def _check_resource_path(cls, value: str | None) -> str | None:
        return _validate_relative_http_path(value, "resourcePath")

    @field_validator("recordPath")
    @classmethod
    def _check_record_path(cls, value: str | None) -> str | None:
        return _validate_record_path(value)


class DiscoverConnectionSchemaRequest(_StrictRequestModel):
    """Optional request body for POST /api/v1/connections/{connection_id}/discover."""

    fileName: str | None = None
    fileContent: str | None = Field(default=None, repr=False)
    options: DiscoveryOptionsInput = Field(default_factory=DiscoveryOptionsInput)

    @field_validator("fileName")
    @classmethod
    def _check_file_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_non_empty_text(value, "File name", max_length=255)


class DiscoverSchemaResponse(BaseModel):
    """Response returned by POST /api/v1/connections/{connection_id}/discover."""

    model_config = ConfigDict(extra="forbid")

    snapshotId: UUID
    connectionId: UUID
    discoveryStatus: DiscoveryStatus
    discoverySource: SnapshotDiscoverySource
    schemaFingerprint: str
    objectCount: int = Field(ge=0)
    warnings: list[SchemaWarning] = Field(default_factory=list)
    discoveredAt: datetime
    objects: list[SchemaObjectSummaryResponse] = Field(default_factory=list)


class SchemaObjectListResponse(BaseModel):
    """Paginated response returned by GET /api/v1/connections/{connection_id}/objects."""

    model_config = ConfigDict(extra="forbid")

    connectionId: UUID
    schemaId: UUID
    items: list[SchemaObjectSummaryResponse]
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)


class SchemaObjectFieldsResponse(BaseModel):
    """Ordered fields response returned by GET /api/v1/connections/{id}/objects/{obj_id}/fields."""

    model_config = ConfigDict(extra="forbid")

    connectionId: UUID
    schemaId: UUID
    object: SchemaObjectSummaryResponse
    fields: list[SchemaFieldResponse]
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)


def compute_schema_fingerprint(objects: list[SchemaObjectInput]) -> str:
    """Compute a deterministic SHA-256 structural fingerprint over sorted objects and fields."""
    canonical_payload = [
        {
            "objectKey": obj.objectKey,
            "objectType": obj.objectType,
            "discoverySource": obj.discoverySource,
            "fields": [
                {
                    "fieldPath": fld.fieldPath,
                    "canonicalType": fld.canonicalType,
                    "nativeType": fld.nativeType,
                    "nullable": fld.nullable,
                    "isRequired": fld.isRequired,
                    "primaryKey": fld.primaryKey,
                    "uniqueKey": fld.uniqueKey,
                }
                for fld in sorted(obj.fields, key=lambda item: item.fieldPath)
            ],
        }
        for obj in sorted(objects, key=lambda item: item.objectKey)
    ]
    serialized = json.dumps(canonical_payload, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
