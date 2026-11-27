"""Unit, API, concurrency, and live PostgreSQL 16 tests for Step 06 mapping validation."""

import base64
import copy
import json
import os
import re
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.types.json import Jsonb

from app.core.config import Settings
from app.db.errors import DatabaseConflictError
from app.db.migrations import MigrationRunner
from app.db.pool import DatabaseManager
from app.main import create_app
from app.repositories.connections import ConnectionRepository
from app.repositories.mappings import MappingRepository
from app.repositories.schemas import SchemaRepository
from app.schemas.mappings import (
    MappingRuleResponse,
    MappingUpdate,
    MappingValidationSummary,
    TransformReference,
)
from app.schemas.schemas import (
    CanonicalFieldType,
    DiscoveredSchemaResponse,
    SchemaDefaultMetadata,
    SchemaFieldResponse,
    SchemaObjectResponse,
)
from app.services.mapping_validation import (
    evaluate_canonical_type_compatibility,
    evaluate_constant_type_compatibility,
    validate_mapping_definition,
)
from app.services.mappings import MappingService

_KEY_V1_B64 = base64.b64encode(bytes(range(1, 33))).decode("ascii")
_KEY_V2_B64 = base64.b64encode(bytes(range(201, 233))).decode("ascii")


def _make_app_settings() -> Settings:
    keyring_json = json.dumps({"k1": _KEY_V1_B64, "k2": _KEY_V2_B64})
    return Settings(
        database_url="postgresql://databridge:test_db_pw@127.0.0.1:5432/databridge",  # type: ignore[arg-type]
        credential_encryption_keys=keyring_json,  # type: ignore[arg-type]
        credential_active_key_id="k1",
    )


def _field(
    field_path: str,
    canonical_type: CanonicalFieldType,
    *,
    native_type: str = "text",
    nullable: bool = True,
    is_required: bool = False,
    primary_key: bool = False,
    default_metadata: SchemaDefaultMetadata | None = None,
    ordinal_position: int = 0,
    metadata: dict[str, Any] | None = None,
) -> SchemaFieldResponse:
    now = datetime.now(UTC)
    return SchemaFieldResponse(
        id=uuid.uuid4(),
        schemaObjectId=uuid.uuid4(),
        fieldPath=field_path,
        fieldName=field_path.split(".")[-1],
        nativeType=native_type,
        canonicalType=canonical_type,
        nullable=nullable,
        isRequired=is_required,
        primaryKey=primary_key,
        uniqueKey=False,
        defaultMetadata=default_metadata,
        ordinalPosition=ordinal_position,
        discoverySource="catalog",
        metadata=metadata or {},
        createdAt=now,
        updatedAt=now,
    )


def _schema_snapshot(
    connection_id: uuid.UUID,
    fingerprint: str,
) -> DiscoveredSchemaResponse:
    now = datetime.now(UTC)
    return DiscoveredSchemaResponse(
        id=uuid.uuid4(),
        connectionId=connection_id,
        discoveryStatus="completed",
        discoveredAt=now,
        schemaFingerprint=fingerprint,
        discoverySource="catalog",
        metadata={},
        createdAt=now,
        updatedAt=now,
    )


def _schema_object(
    object_key: str,
    fields: list[SchemaFieldResponse],
) -> SchemaObjectResponse:
    now = datetime.now(UTC)
    return SchemaObjectResponse(
        id=uuid.uuid4(),
        discoveredSchemaId=uuid.uuid4(),
        objectKey=object_key,
        objectName=object_key.split(".")[-1],
        objectType="table",
        discoverySource="catalog",
        nativeMetadata={},
        canonicalMetadata={},
        fieldCount=len(fields),
        fields=fields,
        createdAt=now,
        updatedAt=now,
    )


def _rule_resp(
    *,
    mapping_id: uuid.UUID,
    mapping_type: str,
    destination_field: str,
    source_field: str | None = None,
    constant_value: Any = None,
    transform_ref: TransformReference | None = None,
    ordinal: int = 0,
) -> MappingRuleResponse:
    now = datetime.now(UTC)
    return MappingRuleResponse(
        id=uuid.uuid4(),
        mappingId=mapping_id,
        sourceFieldPath=source_field,
        destinationFieldPath=destination_field,
        mappingType=mapping_type,  # type: ignore[arg-type]
        constantValue=constant_value,
        transformRef=transform_ref,
        ordinalPosition=ordinal,
        metadata={},
        createdAt=now,
        updatedAt=now,
    )


# ---------------------------------------------------------------------------
# Pure Unit Tests: Canonical Type Matrix, Constants, Required Fields, Drift
# ---------------------------------------------------------------------------


def test_canonical_type_matrix_exact_widening_lossy_incompatible_and_unknown() -> None:
    """Verify exact matches, safe widening, lossy warnings, errors, and unknown rejection."""
    # Exact concrete matches.
    for ctype in (
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
    ):
        outcome, code, _msg = evaluate_canonical_type_compatibility(
            _field("s", ctype),
            _field("d", ctype),
        )
        assert outcome == "compatible", f"Failed exact match for {ctype}"
        assert code is None

    # Safe lossless widening conversions.
    for src_t, dst_t in (
        ("integer", "number"),
        ("date", "datetime"),
        ("integer", "string"),
        ("number", "string"),
        ("boolean", "string"),
        ("date", "string"),
        ("datetime", "string"),
        ("object", "json"),
        ("array", "json"),
        ("string", "json"),
    ):
        outcome, _code, _msg = evaluate_canonical_type_compatibility(
            _field("s", src_t),  # type: ignore[arg-type]
            _field("d", dst_t),  # type: ignore[arg-type]
        )
        assert outcome == "compatible", f"Expected compatible widening for {src_t} -> {dst_t}"

    # Lossy / runtime coercion warnings.
    for src_t, dst_t, expected_code in (
        ("number", "integer", "LOSSY_TYPE_CONVERSION"),
        ("datetime", "date", "LOSSY_TYPE_CONVERSION"),
        ("string", "integer", "TYPE_COERCION_RISK"),
        ("string", "datetime", "TYPE_COERCION_RISK"),
        ("boolean", "integer", "IMPLICIT_COERCION_RISK"),
        ("object", "string", "LOSSY_TYPE_CONVERSION"),
        ("json", "object", "TYPE_COERCION_RISK"),
        ("json", "array", "TYPE_COERCION_RISK"),
    ):
        outcome, code, _msg = evaluate_canonical_type_compatibility(
            _field("s", src_t),  # type: ignore[arg-type]
            _field("d", dst_t),  # type: ignore[arg-type]
        )
        assert outcome == "warning", f"Expected warning for {src_t} -> {dst_t}"
        assert code == expected_code

    # Incompatible types -> error.
    for src_t, dst_t in (
        ("object", "integer"),
        ("array", "boolean"),
        ("binary", "string"),
        ("binary", "json"),
        ("date", "number"),
        ("json", "datetime"),
    ):
        outcome, code, _msg = evaluate_canonical_type_compatibility(
            _field("s", src_t),  # type: ignore[arg-type]
            _field("d", dst_t),  # type: ignore[arg-type]
        )
        assert outcome == "error", f"Expected error for {src_t} -> {dst_t}"
        assert code == "INCOMPATIBLE_CANONICAL_TYPE"

    # Unknown canonical types are NEVER accepted silently, even if nativeType matches identically.
    for src_t, dst_t in (
        ("unknown", "unknown"),
        ("unknown", "string"),
        ("string", "unknown"),
        ("unknown", "json"),
    ):
        outcome, code, _msg = evaluate_canonical_type_compatibility(
            _field("s", src_t, native_type="custom_vendor_type"),  # type: ignore[arg-type]
            _field("d", dst_t, native_type="custom_vendor_type"),  # type: ignore[arg-type]
        )
        assert outcome == "error"
        assert code == "UNKNOWN_CANONICAL_TYPE"


def test_constant_type_compatibility_and_no_raw_value_echo() -> None:
    """Validate scalar, null, date/datetime, array, object constants without echoing literals."""
    assert evaluate_constant_type_compatibility(None, _field("d", "string", nullable=True))[0] == (
        "compatible"
    )
    err_null = evaluate_constant_type_compatibility(None, _field("d", "string", nullable=False))
    assert err_null[0] == "error"
    assert err_null[1] == "NULL_CONSTANT_FOR_NON_NULLABLE_FIELD"

    # bool must NOT be treated as int in Python!
    bool_to_int = evaluate_constant_type_compatibility(True, _field("cnt", "integer"))
    assert bool_to_int[0] == "error"
    assert bool_to_int[1] == "INCOMPATIBLE_CONSTANT_TYPE"

    # Compatible JSON literals.
    assert evaluate_constant_type_compatibility(42, _field("n", "integer"))[0] == "compatible"
    assert evaluate_constant_type_compatibility(42, _field("n", "number"))[0] == "compatible"
    assert evaluate_constant_type_compatibility("2026-11-27", _field("d", "date"))[0] == (
        "compatible"
    )
    assert (
        evaluate_constant_type_compatibility("2026-11-27T12:00:00Z", _field("dt", "datetime"))[0]
        == "compatible"
    )
    assert evaluate_constant_type_compatibility(["a", 1], _field("tags", "array"))[0] == (
        "compatible"
    )
    assert evaluate_constant_type_compatibility({"env": "prod"}, _field("cfg", "object"))[0] == (
        "compatible"
    )
    assert evaluate_constant_type_compatibility({"k": [1, True]}, _field("j", "json"))[0] == (
        "compatible"
    )

    # Incompatible constant checks never echo raw literal values.
    bad_date = evaluate_constant_type_compatibility("SECRET_RAW_BAD_DATE", _field("d", "date"))
    assert bad_date[0] == "error"
    assert "SECRET_RAW_BAD_DATE" not in bad_date[2]

    bad_obj = evaluate_constant_type_compatibility(
        {"raw": "LEAKED_LITERAL"}, _field("n", "integer")
    )
    assert bad_obj[0] == "error"
    assert "LEAKED_LITERAL" not in bad_obj[2]


def test_required_defaults_generated_and_nullable_destination_fields() -> None:
    """Only non-nullable, non-default, non-generated required fields demand mapping rules."""
    src_conn_id = uuid.uuid4()
    dst_conn_id = uuid.uuid4()
    mapping_id = uuid.uuid4()
    fp = "a" * 64

    src_obj = _schema_object(
        "public.src",
        [_field("email", "string", nullable=False, ordinal_position=0)],
    )
    dst_obj = _schema_object(
        "public.dst",
        [
            # Generated PK -> excluded even if marked isRequired=True defensively.
            _field(
                "id",
                "integer",
                nullable=False,
                is_required=True,
                primary_key=True,
                default_metadata=SchemaDefaultMetadata(hasDefault=True, isGenerated=True),
                ordinal_position=0,
            ),
            # Server default column -> excluded!
            _field(
                "created_at",
                "datetime",
                nullable=False,
                is_required=True,
                default_metadata=SchemaDefaultMetadata(hasDefault=True, expression="now()"),
                ordinal_position=1,
            ),
            # Nullable optional column -> excluded!
            _field("nickname", "string", nullable=True, is_required=False, ordinal_position=2),
            # Mapped required column -> satisfied!
            _field("email", "string", nullable=False, is_required=True, ordinal_position=3),
            # Unmapped required column -> MISSING_REQUIRED_FIELD!
            _field("tenant_id", "string", nullable=False, is_required=True, ordinal_position=4),
        ],
    )

    summary = validate_mapping_definition(
        source_object_key="public.src",
        destination_object_key="public.dst",
        stored_source_fingerprint=fp,
        stored_destination_fingerprint=fp,
        rules=[
            _rule_resp(
                mapping_id=mapping_id,
                mapping_type="direct",
                source_field="email",
                destination_field="email",
                ordinal=0,
            )
        ],
        source_schema=_schema_snapshot(src_conn_id, fp),
        destination_schema=_schema_snapshot(dst_conn_id, fp),
        source_object=src_obj,
        destination_object=dst_obj,
    )

    assert summary.isValid is False
    assert summary.errorCount == 1
    assert summary.missingRequiredDestinationFields == ["tenant_id"]
    assert summary.unmappedDestinationFields == ["id", "created_at", "nickname", "tenant_id"]
    assert [i.code for i in summary.issues] == ["MISSING_REQUIRED_FIELD"]

    # Adding a constant rule for tenant_id satisfies requirement cleanly.
    valid_summary = validate_mapping_definition(
        source_object_key="public.src",
        destination_object_key="public.dst",
        stored_source_fingerprint=fp,
        stored_destination_fingerprint=fp,
        rules=[
            _rule_resp(
                mapping_id=mapping_id,
                mapping_type="direct",
                source_field="email",
                destination_field="email",
                ordinal=0,
            ),
            _rule_resp(
                mapping_id=mapping_id,
                mapping_type="constant",
                destination_field="tenant_id",
                constant_value="acme",
                ordinal=1,
            ),
        ],
        source_schema=_schema_snapshot(src_conn_id, fp),
        destination_schema=_schema_snapshot(dst_conn_id, fp),
        source_object=src_obj,
        destination_object=dst_obj,
    )
    assert valid_summary.isValid is True
    assert valid_summary.errorCount == 0
    assert valid_summary.missingRequiredDestinationFields == []


def test_drift_detection_transform_rules_and_deterministic_ordering() -> None:
    """Verify source/dest/both drift flags, transform rules, missing fields, and determinism."""
    src_conn_id = uuid.uuid4()
    dst_conn_id = uuid.uuid4()
    mapping_id = uuid.uuid4()
    fp_old = "1" * 64
    fp_src_new = "2" * 64
    fp_dst_new = "3" * 64

    src_fields = [
        _field("raw_payload", "object", nullable=False, ordinal_position=0),
        _field("deleted_col", "string", nullable=True, ordinal_position=1),
        _field("extra_src", "string", nullable=True, ordinal_position=2),
    ]
    dst_fields = [
        _field("item_count", "integer", nullable=False, is_required=True, ordinal_position=0),
        _field("bad_target_unknown", "unknown", nullable=True, ordinal_position=1),
    ]
    rules = [
        _rule_resp(
            mapping_id=mapping_id,
            mapping_type="transform",
            source_field="raw_payload",
            destination_field="item_count",
            transform_ref=TransformReference(presetName="extract_count"),
            ordinal=1,
        ),
        _rule_resp(
            mapping_id=mapping_id,
            mapping_type="direct",
            source_field="missing_src_field",
            destination_field="missing_dst_field",
            ordinal=0,
        ),
        _rule_resp(
            mapping_id=mapping_id,
            mapping_type="direct",
            source_field="deleted_col",
            destination_field="bad_target_unknown",
            ordinal=2,
        ),
    ]

    fixed_ts = datetime(2026, 11, 27, 18, 0, tzinfo=UTC)
    summary_a = validate_mapping_definition(
        source_object_key="public.src",
        destination_object_key="public.dst",
        stored_source_fingerprint=fp_old,
        stored_destination_fingerprint=fp_old,
        rules=rules,
        source_schema=_schema_snapshot(src_conn_id, fp_src_new),
        destination_schema=_schema_snapshot(dst_conn_id, fp_dst_new),
        source_object=_schema_object("public.src", src_fields),
        destination_object=_schema_object("public.dst", dst_fields),
        validated_at=fixed_ts,
    )
    # Reversed inputs must produce byte-for-byte identical output.
    summary_b = validate_mapping_definition(
        source_object_key="public.src",
        destination_object_key="public.dst",
        stored_source_fingerprint=fp_old,
        stored_destination_fingerprint=fp_old,
        rules=list(reversed(rules)),
        source_schema=_schema_snapshot(src_conn_id, fp_src_new),
        destination_schema=_schema_snapshot(dst_conn_id, fp_dst_new),
        source_object=_schema_object("public.src", list(reversed(src_fields))),
        destination_object=_schema_object("public.dst", list(reversed(dst_fields))),
        validated_at=fixed_ts,
    )

    assert summary_a.model_dump() == summary_b.model_dump()
    assert summary_a.schemaDriftDetected is True
    assert summary_a.sourceDriftDetected is True
    assert summary_a.destinationDriftDetected is True
    assert summary_a.isValid is False
    codes = [issue.code for issue in summary_a.issues]
    assert codes == [
        "SOURCE_SCHEMA_DRIFT",
        "DESTINATION_SCHEMA_DRIFT",
        "INVALID_SOURCE_FIELD",
        "INVALID_DESTINATION_FIELD",
        "TRANSFORM_TYPE_UNVERIFIED",
        "UNKNOWN_CANONICAL_TYPE",
    ]
    # Rule issues preserve ordinal ordering and attach ruleId.
    rule_issues = [issue for issue in summary_a.issues if issue.ruleId is not None]
    assert [issue.ordinalPosition for issue in rule_issues] == [0, 0, 1, 2]

    # Check single-side drift flags independently.
    src_only = validate_mapping_definition(
        source_object_key="public.src",
        destination_object_key="public.dst",
        stored_source_fingerprint=fp_old,
        stored_destination_fingerprint=fp_old,
        rules=[rules[0]],
        source_schema=_schema_snapshot(src_conn_id, fp_src_new),
        destination_schema=_schema_snapshot(dst_conn_id, fp_old),
        source_object=_schema_object("public.src", src_fields[:1]),
        destination_object=_schema_object("public.dst", dst_fields[:1]),
        validated_at=fixed_ts,
    )
    assert src_only.sourceDriftDetected is True
    assert src_only.destinationDriftDetected is False
    assert src_only.schemaDriftDetected is True
    assert src_only.isValid is True


# ---------------------------------------------------------------------------
# In-Memory Stub for API & Concurrency Tests
# ---------------------------------------------------------------------------


class _FakeValidationCursor:
    def __init__(self, rows: list[tuple[Any, ...]] | None = None) -> None:
        self._rows = rows or []

    async def fetchone(self) -> tuple[Any, ...] | None:
        return self._rows[0] if self._rows else None

    async def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self._rows)


class _FakeValidationTx:
    def __init__(self, conn: "_ValidationInMemoryDbConn") -> None:
        self._conn = conn
        self._snapshot: tuple[Any, ...] | None = None

    async def __aenter__(self) -> "_FakeValidationTx":
        if self._conn.concurrent_bump_on_validate_for_id is not None:
            bumped = self._conn.mappings.get(self._conn.concurrent_bump_on_validate_for_id)
            if bumped is not None:
                bumped["mapping_version"] += 1
                bumped["status"] = "draft"
                bumped["validation_summary"] = None
            self._conn.concurrent_bump_on_validate_for_id = None
        self._snapshot = (
            copy.deepcopy(self._conn.connections),
            copy.deepcopy(self._conn.schemas),
            copy.deepcopy(self._conn.schema_objects),
            copy.deepcopy(self._conn.schema_fields),
            copy.deepcopy(self._conn.mappings),
            copy.deepcopy(self._conn.mapping_rules),
        )
        return self

    async def __aexit__(self, exc_type: Any, _exc: Any, _tb: Any) -> None:
        if exc_type is not None and self._snapshot is not None:
            (
                self._conn.connections,
                self._conn.schemas,
                self._conn.schema_objects,
                self._conn.schema_fields,
                self._conn.mappings,
                self._conn.mapping_rules,
            ) = self._snapshot


class _ValidationInMemoryDbConn:
    """In-memory stub supporting mapping CRUD, validation persistence, and version races."""

    def __init__(self) -> None:
        self.connections: dict[str, dict[str, Any]] = {}
        self.schemas: dict[str, dict[str, Any]] = {}
        self.schema_objects: dict[str, dict[str, Any]] = {}
        self.schema_fields: dict[str, list[dict[str, Any]]] = {}
        self.mappings: dict[str, dict[str, Any]] = {}
        self.mapping_rules: dict[str, dict[str, Any]] = {}
        self.concurrent_bump_on_validate_for_id: str | None = None

    def transaction(self) -> _FakeValidationTx:
        return _FakeValidationTx(self)

    def seed_connection(self, *, name: str, connector_type: str = "PostgreSQL") -> str:
        conn_id = str(uuid.uuid4())
        now = datetime.now(UTC)
        self.connections[conn_id] = {
            "id": conn_id,
            "name": name,
            "connector_type": connector_type,
            "description": None,
            "endpoint": "localhost:5432/app",
            "config": {"host": "localhost", "port": 5432, "database": "app"},
            "auth_configured": False,
            "status": "connected",
            "last_tested_at": now,
            "last_test_status": "success",
            "last_error_message": None,
            "last_successful_sync": None,
            "sync_frequency": None,
            "created_at": now,
            "updated_at": now,
        }
        return conn_id

    def seed_schema(
        self,
        *,
        connection_id: str,
        fingerprint: str,
        objects: dict[str, list[dict[str, Any]]],
    ) -> str:
        for existing_sid in [
            sid for sid, row in self.schemas.items() if row["connection_id"] == connection_id
        ]:
            self.schemas.pop(existing_sid, None)
            for oid in [
                oid
                for oid, obj in self.schema_objects.items()
                if obj["discovered_schema_id"] == existing_sid
            ]:
                self.schema_objects.pop(oid, None)
                self.schema_fields.pop(oid, None)

        schema_id = str(uuid.uuid4())
        now = datetime.now(UTC)
        self.schemas[schema_id] = {
            "id": schema_id,
            "connection_id": connection_id,
            "discovery_status": "completed",
            "discovered_at": now,
            "schema_fingerprint": fingerprint,
            "discovery_source": "catalog",
            "metadata": {},
            "created_at": now,
            "updated_at": now,
        }
        for obj_key, fields in objects.items():
            obj_id = str(uuid.uuid4())
            self.schema_objects[obj_id] = {
                "id": obj_id,
                "discovered_schema_id": schema_id,
                "object_key": obj_key,
                "object_name": obj_key.split(".")[-1],
                "object_type": "table",
                "discovery_source": "catalog",
                "native_metadata": {},
                "canonical_metadata": {},
                "created_at": now,
                "updated_at": now,
            }
            self.schema_fields[obj_id] = [
                {
                    "id": str(uuid.uuid4()),
                    "schema_object_id": obj_id,
                    "field_path": f["fieldPath"],
                    "field_name": f.get("fieldName", f["fieldPath"].split(".")[-1]),
                    "native_type": f.get("nativeType", "text"),
                    "canonical_type": f.get("canonicalType", "string"),
                    "nullable": f.get("nullable", True),
                    "is_required": f.get("isRequired", False),
                    "primary_key": f.get("primaryKey", False),
                    "unique_key": f.get("uniqueKey", False),
                    "default_metadata": f.get("defaultMetadata"),
                    "ordinal_position": f.get("ordinalPosition", idx),
                    "discovery_source": "catalog",
                    "metadata": f.get("metadata", {}),
                    "created_at": now,
                    "updated_at": now,
                }
                for idx, f in enumerate(fields)
            ]
        return schema_id

    @staticmethod
    def _unwrap(value: Any) -> Any:
        if isinstance(value, Jsonb):
            return copy.deepcopy(value.obj)
        return copy.deepcopy(value.obj) if hasattr(value, "obj") else copy.deepcopy(value)

    async def execute(self, sql_text: str, params: Any = None) -> _FakeValidationCursor:
        normalized = " ".join(sql_text.split())
        now = datetime.now(UTC)

        if "FROM connections WHERE id = %s" in normalized:
            row = self.connections.get(str(params[0]))
            return _FakeValidationCursor([self._conn_tuple(row)] if row else [])

        if "FROM discovered_schemas WHERE connection_id = %s" in normalized:
            conn_id = str(params[0])
            match = next((s for s in self.schemas.values() if s["connection_id"] == conn_id), None)
            return _FakeValidationCursor([self._schema_tuple(match)] if match else [])

        if "WHERE ds.connection_id = %s AND so.object_key = %s" in normalized:
            conn_id, obj_key = str(params[0]), str(params[1])
            schema = next((s for s in self.schemas.values() if s["connection_id"] == conn_id), None)
            if not schema:
                return _FakeValidationCursor([])
            obj = next(
                (
                    o
                    for o in self.schema_objects.values()
                    if o["discovered_schema_id"] == schema["id"] and o["object_key"] == obj_key
                ),
                None,
            )
            return _FakeValidationCursor([self._obj_summary_tuple(obj)] if obj else [])

        if "FROM schema_fields WHERE schema_object_id = %s" in normalized:
            obj_id = str(params[0])
            rows = sorted(
                self.schema_fields.get(obj_id, []),
                key=lambda f: (f["ordinal_position"], f["field_path"]),
            )
            return _FakeValidationCursor([self._field_tuple(r) for r in rows])

        if normalized.startswith("INSERT INTO mappings"):
            (
                mapping_id,
                name,
                description,
                src_conn_id,
                src_obj_key,
                dst_conn_id,
                dst_obj_key,
                status_val,
                version_val,
                src_fp,
                dst_fp,
                summary_param,
            ) = params
            row = {
                "id": str(mapping_id),
                "name": str(name),
                "description": description,
                "source_connection_id": str(src_conn_id),
                "source_object_key": str(src_obj_key),
                "destination_connection_id": str(dst_conn_id),
                "destination_object_key": str(dst_obj_key),
                "status": str(status_val),
                "mapping_version": int(version_val),
                "source_schema_fingerprint": str(src_fp),
                "destination_schema_fingerprint": str(dst_fp),
                "validation_summary": self._unwrap(summary_param),
                "created_at": now,
                "updated_at": now,
            }
            self.mappings[str(mapping_id)] = row
            return _FakeValidationCursor([self._mapping_tuple(row)])

        if normalized.startswith("SELECT") and "FROM mappings WHERE id = %s" in normalized:
            mapping_id = str(params[0])
            row = self.mappings.get(mapping_id)
            return _FakeValidationCursor([self._mapping_tuple(row)] if row else [])

        if normalized.startswith("UPDATE mappings SET name = %s"):
            (
                name,
                description,
                src_conn_id,
                src_obj_key,
                dst_conn_id,
                dst_obj_key,
                status_val,
                src_fp,
                dst_fp,
                summary_param,
                mapping_id,
            ) = params
            existing = self.mappings.get(str(mapping_id))
            if existing is None:
                return _FakeValidationCursor([])
            existing.update(
                {
                    "name": str(name),
                    "description": description,
                    "source_connection_id": str(src_conn_id),
                    "source_object_key": str(src_obj_key),
                    "destination_connection_id": str(dst_conn_id),
                    "destination_object_key": str(dst_obj_key),
                    "status": str(status_val),
                    "mapping_version": existing["mapping_version"] + 1,
                    "source_schema_fingerprint": str(src_fp),
                    "destination_schema_fingerprint": str(dst_fp),
                    "validation_summary": self._unwrap(summary_param),
                    "updated_at": now,
                }
            )
            return _FakeValidationCursor([self._mapping_tuple(existing)])

        if normalized.startswith("UPDATE mappings SET status = %s, validation_summary = %s"):
            (
                status_val,
                summary_param,
                src_fp,
                dst_fp,
                mapping_id,
                expected_ver,
                _expected_ver_dup,
            ) = params
            existing = self.mappings.get(str(mapping_id))
            if existing is None:
                return _FakeValidationCursor([])
            if expected_ver is not None and existing["mapping_version"] != int(expected_ver):
                return _FakeValidationCursor([])
            existing.update(
                {
                    "status": str(status_val),
                    "validation_summary": self._unwrap(summary_param),
                    "source_schema_fingerprint": (
                        src_fp if src_fp is not None else existing["source_schema_fingerprint"]
                    ),
                    "destination_schema_fingerprint": (
                        dst_fp if dst_fp is not None else existing["destination_schema_fingerprint"]
                    ),
                    "updated_at": now,
                }
            )
            return _FakeValidationCursor([self._mapping_tuple(existing)])

        if normalized.startswith("DELETE FROM mapping_rules WHERE mapping_id = %s"):
            mapping_id = str(params[0])
            for rid in [
                rid for rid, r in self.mapping_rules.items() if r["mapping_id"] == mapping_id
            ]:
                self.mapping_rules.pop(rid, None)
            return _FakeValidationCursor([])

        if normalized.startswith("INSERT INTO mapping_rules"):
            (
                rule_id,
                mapping_id,
                src_path,
                dst_path,
                mtype,
                const_val,
                transform_ref,
                ordinal,
                metadata,
            ) = params
            rule_row = {
                "id": str(rule_id),
                "mapping_id": str(mapping_id),
                "source_field_path": src_path,
                "destination_field_path": str(dst_path),
                "mapping_type": str(mtype),
                "constant_value": self._unwrap(const_val) if const_val is not None else None,
                "transform_ref": self._unwrap(transform_ref) if transform_ref is not None else None,
                "ordinal_position": int(ordinal),
                "metadata": self._unwrap(metadata),
                "created_at": now,
                "updated_at": now,
            }
            self.mapping_rules[str(rule_id)] = rule_row
            return _FakeValidationCursor([self._rule_tuple(rule_row)])

        if "FROM mapping_rules WHERE mapping_id = ANY" in normalized:
            target_ids = {str(mid) for mid in params[0]}
            rows = sorted(
                (r for r in self.mapping_rules.values() if r["mapping_id"] in target_ids),
                key=lambda r: (
                    r["mapping_id"],
                    r["ordinal_position"],
                    r["destination_field_path"],
                ),
            )
            return _FakeValidationCursor([self._rule_tuple(r) for r in rows])

        raise ValueError(f"Unhandled SQL in validation stub: {normalized}")

    @staticmethod
    def _conn_tuple(row: dict[str, Any]) -> tuple[Any, ...]:
        return (
            row["id"],
            row["name"],
            row["connector_type"],
            row["description"],
            row["endpoint"],
            row["config"],
            row["auth_configured"],
            row["status"],
            row["last_tested_at"],
            row["last_test_status"],
            row["last_error_message"],
            row["last_successful_sync"],
            row["sync_frequency"],
            row["created_at"],
            row["updated_at"],
        )

    @staticmethod
    def _schema_tuple(row: dict[str, Any]) -> tuple[Any, ...]:
        return (
            row["id"],
            row["connection_id"],
            row["discovery_status"],
            row["discovered_at"],
            row["schema_fingerprint"],
            row["discovery_source"],
            row["metadata"],
            row["created_at"],
            row["updated_at"],
        )

    @staticmethod
    def _obj_summary_tuple(row: dict[str, Any]) -> tuple[Any, ...]:
        return (
            row["id"],
            row["discovered_schema_id"],
            row["object_key"],
            row["object_name"],
            row["object_type"],
            row["discovery_source"],
            row["native_metadata"],
            row["canonical_metadata"],
            0,
            row["created_at"],
            row["updated_at"],
        )

    @staticmethod
    def _field_tuple(row: dict[str, Any]) -> tuple[Any, ...]:
        return (
            row["id"],
            row["schema_object_id"],
            row["field_path"],
            row["field_name"],
            row["native_type"],
            row["canonical_type"],
            row["nullable"],
            row["is_required"],
            row["primary_key"],
            row["unique_key"],
            row["default_metadata"],
            row["ordinal_position"],
            row["discovery_source"],
            row["metadata"],
            row["created_at"],
            row["updated_at"],
        )

    @staticmethod
    def _mapping_tuple(row: dict[str, Any]) -> tuple[Any, ...]:
        return (
            row["id"],
            row["name"],
            row["description"],
            row["source_connection_id"],
            row["source_object_key"],
            row["destination_connection_id"],
            row["destination_object_key"],
            row["status"],
            row["mapping_version"],
            row["source_schema_fingerprint"],
            row["destination_schema_fingerprint"],
            row["validation_summary"],
            row["created_at"],
            row["updated_at"],
        )

    @staticmethod
    def _rule_tuple(row: dict[str, Any]) -> tuple[Any, ...]:
        return (
            row["id"],
            row["mapping_id"],
            row["source_field_path"],
            row["destination_field_path"],
            row["mapping_type"],
            row["constant_value"],
            row["transform_ref"],
            row["ordinal_position"],
            row["metadata"],
            row["created_at"],
            row["updated_at"],
        )


class _InMemoryValidationDbManager:
    def __init__(self, conn: _ValidationInMemoryDbConn) -> None:
        self._conn = conn

    async def startup(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def verify_connectivity(self) -> bool:
        return True

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[_ValidationInMemoryDbConn]:
        yield self._conn

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[_ValidationInMemoryDbConn]:
        async with self._conn.transaction():
            yield self._conn


# ---------------------------------------------------------------------------
# API Tests (Validation Endpoint, Revalidation, Concurrency, Drift, Preview)
# ---------------------------------------------------------------------------


def test_validation_api_valid_invalid_revalidation_and_preview() -> None:
    """Test valid/invalid transitions, revalidation, 404, missing schemas, and preview."""
    stub = _ValidationInMemoryDbConn()
    src_conn_id = stub.seed_connection(name="pg-src")
    dst_conn_id = stub.seed_connection(name="pg-dst")
    undiscovered_conn_id = stub.seed_connection(name="csv-undisc", connector_type="CSV")

    src_fp = "a" * 64
    dst_fp = "b" * 64
    stub.seed_schema(
        connection_id=src_conn_id,
        fingerprint=src_fp,
        objects={
            "public.orders": [
                {"fieldPath": "order_id", "canonicalType": "integer", "nullable": False},
                {"fieldPath": "payload", "canonicalType": "object", "nullable": False},
                {"fieldPath": "email", "canonicalType": "string", "nullable": False},
            ]
        },
    )
    stub.seed_schema(
        connection_id=dst_conn_id,
        fingerprint=dst_fp,
        objects={
            "public.target_orders": [
                {
                    "fieldPath": "id",
                    "canonicalType": "integer",
                    "nullable": False,
                    "isRequired": True,
                    "defaultMetadata": {"hasDefault": True, "isGenerated": True},
                },
                {
                    "fieldPath": "email",
                    "canonicalType": "string",
                    "nullable": False,
                    "isRequired": True,
                },
                {
                    "fieldPath": "total",
                    "canonicalType": "number",
                    "nullable": False,
                    "isRequired": True,
                },
            ]
        },
    )

    app = create_app(
        settings=_make_app_settings(),
        database_manager=_InMemoryValidationDbManager(stub),  # type: ignore[arg-type]
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        # 1. Non-persisting preview endpoint checks invalid definition without storing anything.
        preview_resp = client.post(
            "/api/v1/mappings/validate",
            json={
                "name": "Unsaved Preview",
                "sourceConnectionId": src_conn_id,
                "sourceObjectKey": "public.orders",
                "destinationConnectionId": dst_conn_id,
                "destinationObjectKey": "public.target_orders",
                "rules": [
                    {
                        "sourceFieldPath": "email",
                        "destinationFieldPath": "email",
                        "mappingType": "direct",
                        "ordinalPosition": 0,
                    }
                ],
            },
        )
        assert preview_resp.status_code == 200, preview_resp.text
        preview_json = preview_resp.json()
        assert preview_json["mappingId"] is None
        assert preview_json["status"] == "invalid"
        assert preview_json["validationSummary"]["missingRequiredDestinationFields"] == ["total"]
        assert stub.mappings == {}

        # 2. Unknown / malformed mapping -> 404 MAPPING_NOT_FOUND.
        assert client.post(f"/api/v1/mappings/{uuid.uuid4()}/validate").status_code == 404
        assert client.post("/api/v1/mappings/not-a-uuid/validate").status_code == 404

        # 3. Create mapping with incompatible type & missing required field -> starts as 'draft'.
        created = client.post(
            "/api/v1/mappings",
            json={
                "name": "Orders Validation Lifecycle",
                "sourceConnectionId": src_conn_id,
                "sourceObjectKey": "public.orders",
                "destinationConnectionId": dst_conn_id,
                "destinationObjectKey": "public.target_orders",
                "rules": [
                    {
                        "sourceFieldPath": "payload",
                        "destinationFieldPath": "total",
                        "mappingType": "direct",
                        "ordinalPosition": 0,
                    }
                ],
            },
        ).json()
        mapping_id = created["id"]
        assert created["status"] == "draft"

        # 4. Validate invalid mapping -> 200 OK, status='invalid', summary persisted.
        invalid_resp = client.post(f"/api/v1/mappings/{mapping_id}/validate")
        assert invalid_resp.status_code == 200, invalid_resp.text
        invalid_json = invalid_resp.json()
        assert invalid_json["status"] == "invalid"
        assert invalid_json["validationSummary"]["isValid"] is False
        assert invalid_json["validationSummary"]["missingRequiredDestinationFields"] == ["email"]
        codes = {i["code"] for i in invalid_json["validationSummary"]["issues"]}
        assert codes == {"INCOMPATIBLE_CANONICAL_TYPE", "MISSING_REQUIRED_FIELD"}

        fetched_invalid = client.get(f"/api/v1/mappings/{mapping_id}").json()
        assert fetched_invalid["status"] == "invalid"
        assert fetched_invalid["validationSummary"]["errorCount"] == 2
        assert fetched_invalid["sourceSchemaFingerprint"] == src_fp

        # 5. Update mapping via PUT with valid widening rules -> revalidate becomes 'valid'.
        client.put(
            f"/api/v1/mappings/{mapping_id}",
            json={
                "rules": [
                    {
                        "sourceFieldPath": "email",
                        "destinationFieldPath": "email",
                        "mappingType": "direct",
                        "ordinalPosition": 0,
                    },
                    {
                        "sourceFieldPath": "order_id",
                        "destinationFieldPath": "total",
                        "mappingType": "direct",
                        "ordinalPosition": 1,
                    },
                ]
            },
        )
        valid_resp = client.post(f"/api/v1/mappings/{mapping_id}/validate")
        assert valid_resp.status_code == 200, valid_resp.text
        valid_json = valid_resp.json()
        assert valid_json["mappingVersion"] == 2
        assert valid_json["status"] == "valid"
        assert valid_json["validationSummary"]["isValid"] is True
        assert valid_json["validationSummary"]["errorCount"] == 0

        # 6. Missing schema snapshot returns 422 SCHEMA_NOT_DISCOVERED & does NOT mark 'valid'.
        stub.schemas.clear()
        missing_schema_resp = client.post(f"/api/v1/mappings/{mapping_id}/validate")
        assert missing_schema_resp.status_code == 422
        assert missing_schema_resp.json()["errorCode"] == "SCHEMA_NOT_DISCOVERED"

        # Verify preview endpoint also rejects undiscovered connection schema cleanly.
        undisc_preview = client.post(
            "/api/v1/mappings/validate",
            json={
                "name": "Missing Schema",
                "sourceConnectionId": undiscovered_conn_id,
                "sourceObjectKey": "any.csv",
                "destinationConnectionId": dst_conn_id,
                "destinationObjectKey": "public.target_orders",
                "rules": [],
            },
        )
        assert undisc_preview.status_code == 422
        assert undisc_preview.json()["errorCode"] == "SCHEMA_NOT_DISCOVERED"


def test_validation_concurrency_prevents_stale_overwrite() -> None:
    """A concurrent PUT bumping mapping_version must reject stale validation writes with 409."""
    stub = _ValidationInMemoryDbConn()
    src_conn_id = stub.seed_connection(name="pg-src")
    dst_conn_id = stub.seed_connection(name="pg-dst")
    fp = "c" * 64
    stub.seed_schema(
        connection_id=src_conn_id,
        fingerprint=fp,
        objects={"public.src": [{"fieldPath": "email", "canonicalType": "string"}]},
    )
    stub.seed_schema(
        connection_id=dst_conn_id,
        fingerprint=fp,
        objects={
            "public.dst": [
                {
                    "fieldPath": "email",
                    "canonicalType": "string",
                    "nullable": False,
                    "isRequired": True,
                }
            ]
        },
    )

    app = create_app(
        settings=_make_app_settings(),
        database_manager=_InMemoryValidationDbManager(stub),  # type: ignore[arg-type]
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        created = client.post(
            "/api/v1/mappings",
            json={
                "name": "Concurrency Mapping",
                "sourceConnectionId": src_conn_id,
                "sourceObjectKey": "public.src",
                "destinationConnectionId": dst_conn_id,
                "destinationObjectKey": "public.dst",
                "rules": [
                    {
                        "sourceFieldPath": "email",
                        "destinationFieldPath": "email",
                        "mappingType": "direct",
                        "ordinalPosition": 0,
                    }
                ],
            },
        ).json()
        mapping_id = created["id"]

        # Trigger concurrent edit right as update_validation_outcome acquires FOR UPDATE lock.
        stub.concurrent_bump_on_validate_for_id = mapping_id
        conflict_resp = client.post(f"/api/v1/mappings/{mapping_id}/validate")
        assert conflict_resp.status_code == 409
        assert conflict_resp.json()["errorCode"] == "MAPPING_VERSION_CONFLICT"

        # Newer version remains 'draft' and was NOT marked 'valid' by stale validation!
        after_conflict = client.get(f"/api/v1/mappings/{mapping_id}").json()
        assert after_conflict["mappingVersion"] == 2
        assert after_conflict["status"] == "draft"
        assert after_conflict["validationSummary"] is None


# ---------------------------------------------------------------------------
# Live PostgreSQL 16 Integration Test
# ---------------------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.skipif(
    not os.environ.get("DATABRIDGE_TEST_DATABASE_URL"),
    reason="Live PostgreSQL integration test requires DATABRIDGE_TEST_DATABASE_URL.",
)
async def test_live_postgres16_mapping_validation_drift_and_concurrency() -> None:
    """Exercise full PostgreSQL 16 validation persistence, drift checks, and version guard."""
    db_url = os.environ["DATABRIDGE_TEST_DATABASE_URL"]
    conn_info = psycopg.conninfo.conninfo_to_dict(db_url)
    host = str(conn_info.get("host") or "127.0.0.1")
    port = int(conn_info.get("port") or 5432)
    database = str(conn_info.get("dbname") or "postgres")
    username = str(conn_info.get("user") or "postgres")
    password = conn_info.get("password")
    password_str = str(password) if password is not None else None

    src_schema = f"val_src_{uuid.uuid4().hex[:8]}"
    dst_schema = f"val_dst_{uuid.uuid4().hex[:8]}"
    src_conn_id: str | None = None
    dst_conn_id: str | None = None
    mapping_id: str | None = None

    settings = Settings(
        database_url=db_url,  # type: ignore[arg-type]
        credential_encryption_keys=json.dumps({"k1": _KEY_V1_B64, "k2": _KEY_V2_B64}),  # type: ignore[arg-type]
        credential_active_key_id="k1",
    )
    manager = DatabaseManager(settings)
    await manager.startup()

    try:
        runner = MigrationRunner(manager)
        await runner.apply_pending()

        async with await psycopg.AsyncConnection.connect(db_url) as setup_conn:
            await setup_conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(src_schema)))
            await setup_conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(dst_schema)))
            await setup_conn.execute(
                sql.SQL(
                    """
                    CREATE TABLE {}.users (
                        id SERIAL PRIMARY KEY,
                        email VARCHAR(255) NOT NULL,
                        signup_date DATE NOT NULL
                    )
                    """
                ).format(sql.Identifier(src_schema))
            )
            await setup_conn.execute(
                sql.SQL(
                    """
                    CREATE TABLE {}.contacts (
                        contact_id SERIAL PRIMARY KEY,
                        email VARCHAR(255) NOT NULL,
                        status_code INTEGER NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        notes TEXT NULL
                    )
                    """
                ).format(sql.Identifier(dst_schema))
            )
            await setup_conn.commit()

        app = create_app(settings=settings, database_manager=manager)
        with TestClient(app, raise_server_exceptions=False) as client:
            creds_payload: dict[str, Any] = {"username": username}
            if password_str:
                creds_payload["password"] = password_str

            src_conn_id = client.post(
                "/api/v1/connections",
                json={
                    "name": f"val-src-{uuid.uuid4().hex[:8]}",
                    "connectorType": "PostgreSQL",
                    "config": {"host": host, "port": port, "database": database},
                    "credentials": creds_payload,
                },
            ).json()["id"]

            dst_conn_id = client.post(
                "/api/v1/connections",
                json={
                    "name": f"val-dst-{uuid.uuid4().hex[:8]}",
                    "connectorType": "PostgreSQL",
                    "config": {"host": host, "port": port, "database": database},
                    "credentials": creds_payload,
                },
            ).json()["id"]

            src_disc = client.post(
                f"/api/v1/connections/{src_conn_id}/discover",
                json={"options": {"includeSchemas": [src_schema], "includeViews": False}},
            )
            assert src_disc.status_code == 200, src_disc.text
            orig_src_fp = src_disc.json()["schemaFingerprint"]

            dst_disc = client.post(
                f"/api/v1/connections/{dst_conn_id}/discover",
                json={"options": {"includeSchemas": [dst_schema], "includeViews": False}},
            )
            assert dst_disc.status_code == 200, dst_disc.text
            orig_dst_fp = dst_disc.json()["schemaFingerprint"]

            # 1. Create mapping missing required destination 'status_code' (while omitting generated
            # 'contact_id' and defaulted 'created_at', which must NOT trigger missing errors).
            create_map = client.post(
                "/api/v1/mappings",
                json={
                    "name": f"Live Validation {uuid.uuid4().hex[:6]}",
                    "sourceConnectionId": src_conn_id,
                    "sourceObjectKey": f"{src_schema}.users",
                    "destinationConnectionId": dst_conn_id,
                    "destinationObjectKey": f"{dst_schema}.contacts",
                    "rules": [
                        {
                            "sourceFieldPath": "email",
                            "destinationFieldPath": "email",
                            "mappingType": "direct",
                            "ordinalPosition": 0,
                        }
                    ],
                },
            )
            assert create_map.status_code == 201, create_map.text
            mapping_id = create_map.json()["id"]

            val_invalid = client.post(f"/api/v1/mappings/{mapping_id}/validate")
            assert val_invalid.status_code == 200, val_invalid.text
            assert val_invalid.json()["status"] == "invalid"
            assert val_invalid.json()["validationSummary"]["missingRequiredDestinationFields"] == [
                "status_code"
            ]
            assert val_invalid.json()["validationSummary"]["errorCount"] == 1

            # 2. Fix mapping via PUT (adding integer constant) -> validate becomes 'valid'.
            upd = client.put(
                f"/api/v1/mappings/{mapping_id}",
                json={
                    "rules": [
                        {
                            "sourceFieldPath": "email",
                            "destinationFieldPath": "email",
                            "mappingType": "direct",
                            "ordinalPosition": 0,
                        },
                        {
                            "destinationFieldPath": "status_code",
                            "mappingType": "constant",
                            "constantValue": 1,
                            "ordinalPosition": 1,
                        },
                    ]
                },
            )
            assert upd.status_code == 200, upd.text
            assert upd.json()["mappingVersion"] == 2
            assert upd.json()["status"] == "draft"

            val_valid = client.post(f"/api/v1/mappings/{mapping_id}/validate")
            assert val_valid.status_code == 200, val_valid.text
            assert val_valid.json()["status"] == "valid"
            assert val_valid.json()["mappingVersion"] == 2
            assert val_valid.json()["validationSummary"]["isValid"] is True

            # Validation must NOT alter stored fingerprints or rules, and secrets must never leak.
            assert re.fullmatch(r"[0-9a-f]{64}", orig_src_fp)
            fetched = client.get(f"/api/v1/mappings/{mapping_id}").json()
            assert fetched["sourceSchemaFingerprint"] == orig_src_fp
            assert fetched["destinationSchemaFingerprint"] == orig_dst_fp
            assert len(fetched["rules"]) == 2
            if password_str:
                assert password_str not in val_valid.text

            # 3. Verify live PostgreSQL 16 optimistic version check blocks stale version writes.
            mapping_repo = MappingRepository(manager)
            with pytest.raises(DatabaseConflictError) as conflict_exc:
                await mapping_repo.update_validation_outcome(
                    mapping_id,
                    status="invalid",
                    validation_summary=MappingValidationSummary(
                        isValid=False,
                        errorCount=1,
                        warningCount=0,
                        validatedAt=datetime.now(UTC),
                    ),
                    expected_mapping_version=1,  # Current DB row is version 2!
                )
            assert conflict_exc.value.error_code == "MAPPING_VERSION_CONFLICT"
            still_valid = client.get(f"/api/v1/mappings/{mapping_id}").json()
            assert still_valid["status"] == "valid"
            assert still_valid["mappingVersion"] == 2

            # 4. Alter destination table in PostgreSQL (add required org_id), rediscover, validate.
            async with await psycopg.AsyncConnection.connect(db_url) as alter_conn:
                await alter_conn.execute(
                    sql.SQL("ALTER TABLE {}.contacts ADD COLUMN org_id INTEGER NOT NULL").format(
                        sql.Identifier(dst_schema)
                    )
                )
                await alter_conn.commit()

            redisc = client.post(
                f"/api/v1/connections/{dst_conn_id}/discover",
                json={"options": {"includeSchemas": [dst_schema], "includeViews": False}},
            )
            assert redisc.status_code == 200, redisc.text
            assert redisc.json()["schemaFingerprint"] != orig_dst_fp

            val_drift = client.post(f"/api/v1/mappings/{mapping_id}/validate")
            assert val_drift.status_code == 200, val_drift.text
            drift_json = val_drift.json()
            assert drift_json["status"] == "invalid"
            assert drift_json["validationSummary"]["schemaDriftDetected"] is True
            assert drift_json["validationSummary"]["destinationDriftDetected"] is True
            assert drift_json["validationSummary"]["sourceDriftDetected"] is False
            assert drift_json["validationSummary"]["missingRequiredDestinationFields"] == ["org_id"]
            # Stored fingerprint stays at orig_dst_fp because validation must not rewrite it.
            assert (
                client.get(f"/api/v1/mappings/{mapping_id}").json()["destinationSchemaFingerprint"]
                == orig_dst_fp
            )

            # 5. Stale expectedVersion on PUT -> 409 MAPPING_VERSION_CONFLICT (v2 preserved).
            stale_put = client.put(
                f"/api/v1/mappings/{mapping_id}",
                json={
                    "description": "Should not overwrite v2",
                    "expectedVersion": 1,
                },
            )
            assert stale_put.status_code == 409
            assert stale_put.json()["errorCode"] == "MAPPING_VERSION_CONFLICT"

            # 6. Update mapping to cover org_id and sync new destination fingerprint -> Valid!
            new_dst_fp = redisc.json()["schemaFingerprint"]
            drift_fix = client.put(
                f"/api/v1/mappings/{mapping_id}",
                json={
                    "expectedVersion": 2,
                    "destinationSchemaFingerprint": new_dst_fp,
                    "rules": [
                        {
                            "sourceFieldPath": "email",
                            "destinationFieldPath": "email",
                            "mappingType": "direct",
                            "ordinalPosition": 0,
                        },
                        {
                            "destinationFieldPath": "status_code",
                            "mappingType": "constant",
                            "constantValue": 1,
                            "ordinalPosition": 1,
                        },
                        {
                            "destinationFieldPath": "org_id",
                            "mappingType": "constant",
                            "constantValue": 10,
                            "ordinalPosition": 2,
                        },
                    ],
                },
            )
            assert drift_fix.status_code == 200, drift_fix.text
            assert drift_fix.json()["mappingVersion"] == 3
            assert drift_fix.json()["status"] == "draft"
            assert drift_fix.json()["destinationSchemaFingerprint"] == new_dst_fp

            reval = client.post(f"/api/v1/mappings/{mapping_id}/validate")
            assert reval.status_code == 200, reval.text
            assert reval.json()["status"] == "valid"
            assert reval.json()["validationSummary"]["schemaDriftDetected"] is False
            assert reval.json()["validationSummary"]["errorCount"] == 0

            # Confirm MappingService.get_mapping returns version 3 without using credentials.
            svc = MappingService(
                connection_repo=ConnectionRepository(manager),
                schema_repo=SchemaRepository(manager),
                mapping_repo=mapping_repo,
            )
            assert (await svc.get_mapping(mapping_id)).mappingVersion == 3
            assert isinstance(MappingUpdate(description="noop"), MappingUpdate)
    finally:
        async with await psycopg.AsyncConnection.connect(db_url) as cleanup_conn:
            if mapping_id is not None:
                await cleanup_conn.execute("DELETE FROM mappings WHERE id = %s", (mapping_id,))
            if src_conn_id is not None:
                await cleanup_conn.execute("DELETE FROM connections WHERE id = %s", (src_conn_id,))
            if dst_conn_id is not None:
                await cleanup_conn.execute("DELETE FROM connections WHERE id = %s", (dst_conn_id,))
            await cleanup_conn.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(src_schema))
            )
            await cleanup_conn.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(dst_schema))
            )
            await cleanup_conn.commit()
        await manager.shutdown()
