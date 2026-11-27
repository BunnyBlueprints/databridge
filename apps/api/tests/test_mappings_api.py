"""API, service, rollback, and live PostgreSQL 16 tests for Task 05 Step 05 mappings."""

import base64
import copy
import inspect
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
from psycopg import errors as pg_errors
from psycopg import sql
from psycopg.types.json import Jsonb

from app.api.routes import mappings as mappings_router_module
from app.core.config import Settings
from app.db.errors import DatabaseError
from app.db.migrations import MigrationRunner
from app.db.pool import DatabaseManager
from app.main import create_app
from app.repositories.connections import ConnectionRepository
from app.repositories.mappings import MappingRepository
from app.repositories.schemas import SchemaRepository
from app.schemas.mappings import MappingCreate, MappingRuleInput, MappingUpdate
from app.services import mappings as mappings_service_module
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


def _unwrap_jsonb(value: Any) -> Any:
    if isinstance(value, Jsonb):
        return copy.deepcopy(value.obj)
    if hasattr(value, "obj"):
        return copy.deepcopy(value.obj)
    return copy.deepcopy(value)


class _UniqueViolationWithDiag(pg_errors.UniqueViolation):
    def __init__(self, constraint_name: str, detail: str = "") -> None:
        super().__init__(detail or constraint_name)
        self._constraint_name = constraint_name

    @property
    def diag(self) -> Any:
        name = self._constraint_name

        class _Diag:
            constraint_name = name
            message_detail = None

        return _Diag()


class _FakeMappingCursor:
    def __init__(self, rows: list[tuple[Any, ...]] | None = None) -> None:
        self._rows = rows or []

    async def fetchone(self) -> tuple[Any, ...] | None:
        return self._rows[0] if self._rows else None

    async def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self._rows)


class _FakeTx:
    def __init__(self, conn: "_MappingInMemoryDbConn") -> None:
        self._conn = conn
        self._snapshot: tuple[Any, ...] | None = None

    async def __aenter__(self) -> "_FakeTx":
        self._snapshot = (
            copy.deepcopy(self._conn.connections),
            copy.deepcopy(self._conn.credentials),
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
                self._conn.credentials,
                self._conn.schemas,
                self._conn.schema_objects,
                self._conn.schema_fields,
                self._conn.mappings,
                self._conn.mapping_rules,
            ) = self._snapshot


class _MappingInMemoryDbConn:
    """Self-contained in-memory persistence stub for connections, schemas, and mappings."""

    def __init__(self) -> None:
        self.connections: dict[str, dict[str, Any]] = {}
        self.credentials: dict[str, dict[str, Any]] = {}
        self.schemas: dict[str, dict[str, Any]] = {}
        self.schema_objects: dict[str, dict[str, Any]] = {}
        self.schema_fields: dict[str, list[dict[str, Any]]] = {}
        self.mappings: dict[str, dict[str, Any]] = {}
        self.mapping_rules: dict[str, dict[str, Any]] = {}
        self.fail_on_rule_insert_nth: int | None = None
        self._rule_insert_counter = 0

    def transaction(self) -> _FakeTx:
        return _FakeTx(self)

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
        # Enforce one latest snapshot per connection.
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

    async def execute(self, sql_text: str, params: Any = None) -> _FakeMappingCursor:
        normalized = " ".join(sql_text.split())
        now = datetime.now(UTC)

        if "FROM connections WHERE id = %s" in normalized:
            row = self.connections.get(str(params[0]))
            return _FakeMappingCursor([self._conn_tuple(row)] if row else [])

        if "FROM discovered_schemas WHERE connection_id = %s" in normalized:
            conn_id = str(params[0])
            match = next((s for s in self.schemas.values() if s["connection_id"] == conn_id), None)
            return _FakeMappingCursor([self._schema_tuple(match)] if match else [])

        if "WHERE ds.connection_id = %s AND so.object_key = %s" in normalized:
            conn_id, obj_key = str(params[0]), str(params[1])
            schema = next((s for s in self.schemas.values() if s["connection_id"] == conn_id), None)
            if not schema:
                return _FakeMappingCursor([])
            obj = next(
                (
                    o
                    for o in self.schema_objects.values()
                    if o["discovered_schema_id"] == schema["id"] and o["object_key"] == obj_key
                ),
                None,
            )
            return _FakeMappingCursor([self._obj_summary_tuple(obj)] if obj else [])

        if "FROM schema_fields WHERE schema_object_id = %s" in normalized:
            obj_id = str(params[0])
            rows = sorted(
                self.schema_fields.get(obj_id, []),
                key=lambda f: (f["ordinal_position"], f["field_path"]),
            )
            return _FakeMappingCursor([self._field_tuple(r) for r in rows])

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
            if any(m["name"].lower() == str(name).lower() for m in self.mappings.values()):
                raise _UniqueViolationWithDiag("uq_mappings_lower_name")
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
                "validation_summary": _unwrap_jsonb(summary_param),
                "created_at": now,
                "updated_at": now,
            }
            self.mappings[str(mapping_id)] = row
            return _FakeMappingCursor([self._mapping_tuple(row)])

        if normalized.startswith("SELECT") and "FROM mappings WHERE id = %s" in normalized:
            row = self.mappings.get(str(params[0]))
            return _FakeMappingCursor([self._mapping_tuple(row)] if row else [])

        if normalized.startswith("SELECT COUNT(*) FROM mappings"):
            matched = self._filter_mappings(params)
            return _FakeMappingCursor([(len(matched),)])

        if "FROM mappings WHERE (" in normalized and "LIMIT %s OFFSET %s" in normalized:
            matched = self._filter_mappings(params[:-2])
            limit, offset = int(params[-2]), int(params[-1])
            page = matched[offset : offset + limit]
            return _FakeMappingCursor([self._mapping_tuple(row) for row in page])

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
                return _FakeMappingCursor([])
            if any(
                m["id"] != str(mapping_id) and m["name"].lower() == str(name).lower()
                for m in self.mappings.values()
            ):
                raise _UniqueViolationWithDiag("uq_mappings_lower_name")
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
                    "validation_summary": _unwrap_jsonb(summary_param),
                    "updated_at": now,
                }
            )
            return _FakeMappingCursor([self._mapping_tuple(existing)])

        if normalized.startswith("DELETE FROM mappings WHERE id = %s"):
            mapping_id = str(params[0])
            removed = self.mappings.pop(mapping_id, None)
            if removed is None:
                return _FakeMappingCursor([])
            for rid in [
                rid for rid, r in self.mapping_rules.items() if r["mapping_id"] == mapping_id
            ]:
                self.mapping_rules.pop(rid, None)
            return _FakeMappingCursor([(mapping_id,)])

        if normalized.startswith("DELETE FROM mapping_rules WHERE mapping_id = %s"):
            mapping_id = str(params[0])
            for rid in [
                rid for rid, r in self.mapping_rules.items() if r["mapping_id"] == mapping_id
            ]:
                self.mapping_rules.pop(rid, None)
            return _FakeMappingCursor([])

        if normalized.startswith("INSERT INTO mapping_rules"):
            self._rule_insert_counter += 1
            if (
                self.fail_on_rule_insert_nth is not None
                and self._rule_insert_counter == self.fail_on_rule_insert_nth
            ):
                raise RuntimeError("Simulated mid-transaction rule insertion failure")

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
            for existing_rule in self.mapping_rules.values():
                if existing_rule["mapping_id"] != str(mapping_id):
                    continue
                if existing_rule["destination_field_path"] == str(dst_path):
                    raise _UniqueViolationWithDiag("uq_mapping_rules_destination")
                if existing_rule["ordinal_position"] == int(ordinal):
                    raise _UniqueViolationWithDiag("uq_mapping_rules_ordinal")

            rule_row = {
                "id": str(rule_id),
                "mapping_id": str(mapping_id),
                "source_field_path": src_path,
                "destination_field_path": str(dst_path),
                "mapping_type": str(mtype),
                "constant_value": _unwrap_jsonb(const_val) if const_val is not None else None,
                "transform_ref": _unwrap_jsonb(transform_ref)
                if transform_ref is not None
                else None,
                "ordinal_position": int(ordinal),
                "metadata": _unwrap_jsonb(metadata),
                "created_at": now,
                "updated_at": now,
            }
            self.mapping_rules[str(rule_id)] = rule_row
            return _FakeMappingCursor([self._rule_tuple(rule_row)])

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
            return _FakeMappingCursor([self._rule_tuple(r) for r in rows])

        raise ValueError(f"Unhandled SQL in mapping stub: {normalized}")

    def _filter_mappings(self, params: tuple[Any, ...]) -> list[dict[str, Any]]:
        src_conn = str(params[0]) if params[0] is not None else None
        dst_conn = str(params[2]) if params[2] is not None else None
        status_val = str(params[4]) if params[4] is not None else None
        raw_search = params[6]
        needle = None
        if raw_search is not None:
            needle = re.sub(r"\\(.)", r"\1", str(raw_search).strip("%")).lower()

        rows = list(self.mappings.values())
        if src_conn is not None:
            rows = [r for r in rows if r["source_connection_id"] == src_conn]
        if dst_conn is not None:
            rows = [r for r in rows if r["destination_connection_id"] == dst_conn]
        if status_val is not None:
            rows = [r for r in rows if r["status"] == status_val]
        if needle:
            rows = [
                r
                for r in rows
                if needle in r["name"].lower()
                or needle in r["source_object_key"].lower()
                or needle in r["destination_object_key"].lower()
                or needle in (r["description"] or "").lower()
            ]
        return sorted(rows, key=lambda r: (r["created_at"], r["id"]), reverse=True)

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


class _InMemoryMappingDbManager:
    def __init__(self, conn: _MappingInMemoryDbConn) -> None:
        self._conn = conn

    async def startup(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def verify_connectivity(self) -> bool:
        return True

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[_MappingInMemoryDbConn]:
        yield self._conn

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[_MappingInMemoryDbConn]:
        async with self._conn.transaction():
            yield self._conn


def _seed_source_and_dest(
    stub: _MappingInMemoryDbConn,
) -> tuple[str, str, str, str]:
    src_conn_id = stub.seed_connection(name="pg-source", connector_type="PostgreSQL")
    dst_conn_id = stub.seed_connection(name="rest-dest", connector_type="REST API")

    src_fp = "f" * 64
    dst_fp = "a" * 64

    stub.seed_schema(
        connection_id=src_conn_id,
        fingerprint=src_fp,
        objects={
            "public.customers": [
                {"fieldPath": "id", "canonicalType": "integer", "primaryKey": True},
                {"fieldPath": "full_name", "canonicalType": "string"},
                {"fieldPath": "email", "canonicalType": "string"},
                {"fieldPath": "signup_at", "canonicalType": "datetime"},
            ],
            "public.invoices": [
                {"fieldPath": "invoice_id", "canonicalType": "string"},
            ],
        },
    )
    stub.seed_schema(
        connection_id=dst_conn_id,
        fingerprint=dst_fp,
        objects={
            "POST /v1/contacts": [
                {"fieldPath": "body.contact_name", "canonicalType": "string"},
                {"fieldPath": "body.email", "canonicalType": "string"},
                {"fieldPath": "body.source_system", "canonicalType": "string"},
                {"fieldPath": "body.nullable_note", "canonicalType": "string"},
                {"fieldPath": "body.tags", "canonicalType": "array"},
                {"fieldPath": "body.extra", "canonicalType": "json"},
            ],
            "POST /v1/leads": [
                {"fieldPath": "body.lead_email", "canonicalType": "string"},
            ],
        },
    )
    return src_conn_id, dst_conn_id, src_fp, dst_fp


def test_mapping_service_and_router_never_reference_credentials() -> None:
    """MappingService and its API router must remain strictly decoupled from credentials."""
    service_src = inspect.getsource(mappings_service_module)
    router_src = inspect.getsource(mappings_router_module)
    for forbidden in (
        "CredentialRepository",
        "CredentialEncryptionService",
        "connection_credentials",
        "decrypt",
    ):
        assert forbidden not in service_src
        assert forbidden not in router_src


def test_create_get_list_update_delete_mapping_lifecycle() -> None:
    """Exercise full mapping CRUD, rule preservation/replacement, and search filters."""
    stub = _MappingInMemoryDbConn()
    src_conn_id, dst_conn_id, src_fp, dst_fp = _seed_source_and_dest(stub)
    app = create_app(
        settings=_make_app_settings(),
        database_manager=_InMemoryMappingDbManager(stub),  # type: ignore[arg-type]
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        # Create with ordered direct, constant (scalar/null/array/object), and transform rules.
        # Note: deliberately attempt status='valid' to confirm CRUD always initializes as 'draft'.
        create_payload = {
            "name": "Customers to Contacts",
            "description": "Initial mapping",
            "sourceConnectionId": src_conn_id,
            "sourceObjectKey": "public.customers",
            "destinationConnectionId": dst_conn_id,
            "destinationObjectKey": "POST /v1/contacts",
            "status": "valid",
            "rules": [
                {
                    "sourceFieldPath": "email",
                    "destinationFieldPath": "body.email",
                    "mappingType": "transform",
                    "transformRef": {
                        "presetName": "lowercase_trim",
                        "params": {"locale": "en_US"},
                    },
                    "ordinalPosition": 2,
                },
                {
                    "destinationFieldPath": "body.source_system",
                    "mappingType": "constant",
                    "constantValue": "postgresql_crm",
                    "ordinalPosition": 0,
                },
                {
                    "sourceFieldPath": "full_name",
                    "destinationFieldPath": "body.contact_name",
                    "mappingType": "direct",
                    "ordinalPosition": 1,
                },
                {
                    "destinationFieldPath": "body.nullable_note",
                    "mappingType": "constant",
                    "constantValue": None,
                    "ordinalPosition": 3,
                },
                {
                    "destinationFieldPath": "body.tags",
                    "mappingType": "constant",
                    "constantValue": ["crm", 2026],
                    "ordinalPosition": 4,
                },
                {
                    "destinationFieldPath": "body.extra",
                    "mappingType": "constant",
                    "constantValue": {"env": "prod", "flags": {"sync": True}},
                    "ordinalPosition": 5,
                },
            ],
        }
        create_resp = client.post("/api/v1/mappings", json=create_payload)
        assert create_resp.status_code == 201, create_resp.text
        created = create_resp.json()
        mapping_id = created["id"]
        assert created["status"] == "draft"
        assert created["mappingVersion"] == 1
        assert created["sourceSchemaFingerprint"] == src_fp
        assert created["destinationSchemaFingerprint"] == dst_fp
        assert created["validationSummary"] is None
        assert [r["ordinalPosition"] for r in created["rules"]] == [0, 1, 2, 3, 4, 5]
        assert created["rules"][0]["destinationFieldPath"] == "body.source_system"
        assert created["rules"][2]["transformRef"]["presetName"] == "lowercase_trim"
        assert created["rules"][3]["mappingType"] == "constant"
        assert created["rules"][3]["constantValue"] is None
        assert created["rules"][4]["constantValue"] == ["crm", 2026]
        assert created["rules"][5]["constantValue"] == {"env": "prod", "flags": {"sync": True}}

        # Duplicate name conflict -> 409 DUPLICATE_MAPPING.
        dup_resp = client.post("/api/v1/mappings", json=create_payload)
        assert dup_resp.status_code == 409
        assert dup_resp.json()["errorCode"] == "DUPLICATE_MAPPING"

        # GET by ID returns ordered rules.
        get_resp = client.get(f"/api/v1/mappings/{mapping_id}")
        assert get_resp.status_code == 200
        assert [r["ordinalPosition"] for r in get_resp.json()["rules"]] == [0, 1, 2, 3, 4, 5]

        # GET unknown / malformed UUID -> 404 MAPPING_NOT_FOUND.
        assert client.get(f"/api/v1/mappings/{uuid.uuid4()}").status_code == 404
        malformed_get = client.get("/api/v1/mappings/not-a-uuid")
        assert malformed_get.status_code == 404
        assert malformed_get.json()["errorCode"] == "MAPPING_NOT_FOUND"

        # Create a second mapping for filtering assertions.
        second_resp = client.post(
            "/api/v1/mappings",
            json={
                "name": "Customers to Leads",
                "sourceConnectionId": src_conn_id,
                "sourceObjectKey": "public.customers",
                "destinationConnectionId": dst_conn_id,
                "destinationObjectKey": "POST /v1/leads",
                "rules": [
                    {
                        "sourceFieldPath": "email",
                        "destinationFieldPath": "body.lead_email",
                        "mappingType": "direct",
                        "ordinalPosition": 0,
                    }
                ],
            },
        )
        assert second_resp.status_code == 201, second_resp.text

        # List filtering & search.
        all_list = client.get("/api/v1/mappings").json()
        assert all_list["total"] == 2
        by_search = client.get("/api/v1/mappings", params={"q": "Contacts"}).json()
        assert by_search["total"] == 1
        assert by_search["items"][0]["id"] == mapping_id

        by_conn = client.get(
            "/api/v1/mappings",
            params={"sourceConnectionId": src_conn_id, "limit": 1, "offset": 0},
        ).json()
        assert by_conn["total"] == 2
        assert len(by_conn["items"]) == 1

        bad_filter = client.get("/api/v1/mappings", params={"sourceConnectionId": "bad-uuid"})
        assert bad_filter.status_code == 422
        assert bad_filter.json()["errorCode"] == "INVALID_MAPPING_REFERENCE"

        # Metadata-only PUT preserves omitted fields and rules, increments mappingVersion.
        meta_update = client.put(
            f"/api/v1/mappings/{mapping_id}",
            json={"name": "Customers to Contacts v2"},
        )
        assert meta_update.status_code == 200, meta_update.text
        meta_json = meta_update.json()
        assert meta_json["name"] == "Customers to Contacts v2"
        assert meta_json["description"] == "Initial mapping"
        assert meta_json["mappingVersion"] == 2
        assert meta_json["status"] == "draft"
        assert len(meta_json["rules"]) == 6

        # Rule-replacing PUT swaps ordered rules atomically and updates object reference & FP.
        new_dst_fp = "b" * 64
        stub.seed_schema(
            connection_id=dst_conn_id,
            fingerprint=new_dst_fp,
            objects={
                "POST /v1/contacts": [
                    {"fieldPath": "body.email", "canonicalType": "string"},
                ],
                "POST /v1/leads": [
                    {"fieldPath": "body.lead_email", "canonicalType": "string"},
                ],
            },
        )
        ref_update = client.put(
            f"/api/v1/mappings/{mapping_id}",
            json={
                "destinationObjectKey": "POST /v1/leads",
                "status": "valid",
                "rules": [
                    {
                        "sourceFieldPath": "email",
                        "destinationFieldPath": "body.lead_email",
                        "mappingType": "direct",
                        "ordinalPosition": 0,
                    }
                ],
            },
        )
        assert ref_update.status_code == 200, ref_update.text
        ref_json = ref_update.json()
        assert ref_json["mappingVersion"] == 3
        assert ref_json["status"] == "draft"
        assert ref_json["destinationObjectKey"] == "POST /v1/leads"
        assert ref_json["destinationSchemaFingerprint"] == new_dst_fp
        assert len(ref_json["rules"]) == 1
        assert ref_json["rules"][0]["destinationFieldPath"] == "body.lead_email"

        # DELETE removes mapping and returns 204; subsequent GET returns 404.
        del_resp = client.delete(f"/api/v1/mappings/{mapping_id}")
        assert del_resp.status_code == 204
        assert client.get(f"/api/v1/mappings/{mapping_id}").status_code == 404
        assert client.delete(f"/api/v1/mappings/{mapping_id}").status_code == 404


def test_mapping_validation_and_reference_error_cases() -> None:
    """Reject missing bindings, undiscovered schemas, stale FPs, bad rules, and secret payloads."""
    stub = _MappingInMemoryDbConn()
    src_conn_id, dst_conn_id, src_fp, _dst_fp = _seed_source_and_dest(stub)
    undiscovered_conn_id = stub.seed_connection(name="undiscovered", connector_type="CSV")

    app = create_app(
        settings=_make_app_settings(),
        database_manager=_InMemoryMappingDbManager(stub),  # type: ignore[arg-type]
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        # Missing connection -> 404 CONNECTION_NOT_FOUND.
        missing_conn = client.post(
            "/api/v1/mappings",
            json={
                "name": "Missing conn",
                "sourceConnectionId": str(uuid.uuid4()),
                "sourceObjectKey": "public.customers",
                "destinationConnectionId": dst_conn_id,
                "destinationObjectKey": "POST /v1/contacts",
                "rules": [],
            },
        )
        assert missing_conn.status_code == 404
        assert missing_conn.json()["errorCode"] == "CONNECTION_NOT_FOUND"

        # Undiscovered schema -> 422 SCHEMA_NOT_DISCOVERED.
        undisc_resp = client.post(
            "/api/v1/mappings",
            json={
                "name": "No schema yet",
                "sourceConnectionId": undiscovered_conn_id,
                "sourceObjectKey": "data.csv",
                "destinationConnectionId": dst_conn_id,
                "destinationObjectKey": "POST /v1/contacts",
                "rules": [],
            },
        )
        assert undisc_resp.status_code == 422
        assert undisc_resp.json()["errorCode"] == "SCHEMA_NOT_DISCOVERED"

        # Cross-connection / missing source object -> 422 SOURCE_OBJECT_NOT_FOUND.
        bad_src_obj = client.post(
            "/api/v1/mappings",
            json={
                "name": "Wrong src obj",
                "sourceConnectionId": src_conn_id,
                "sourceObjectKey": "POST /v1/contacts",
                "destinationConnectionId": dst_conn_id,
                "destinationObjectKey": "POST /v1/contacts",
                "rules": [],
            },
        )
        assert bad_src_obj.status_code == 422
        assert bad_src_obj.json()["errorCode"] == "SOURCE_OBJECT_NOT_FOUND"

        # Cross-connection / missing destination object -> 422 DESTINATION_OBJECT_NOT_FOUND.
        bad_dst_obj = client.post(
            "/api/v1/mappings",
            json={
                "name": "Wrong dst obj",
                "sourceConnectionId": src_conn_id,
                "sourceObjectKey": "public.customers",
                "destinationConnectionId": dst_conn_id,
                "destinationObjectKey": "public.customers",
                "rules": [],
            },
        )
        assert bad_dst_obj.status_code == 422
        assert bad_dst_obj.json()["errorCode"] == "DESTINATION_OBJECT_NOT_FOUND"

        # Stale explicit fingerprint -> 409 INVALID_MAPPING_REFERENCE.
        stale_fp = client.post(
            "/api/v1/mappings",
            json={
                "name": "Stale FP",
                "sourceConnectionId": src_conn_id,
                "sourceObjectKey": "public.customers",
                "destinationConnectionId": dst_conn_id,
                "destinationObjectKey": "POST /v1/contacts",
                "sourceSchemaFingerprint": "9" * 64,
                "rules": [],
            },
        )
        assert stale_fp.status_code == 409
        assert stale_fp.json()["errorCode"] == "INVALID_MAPPING_REFERENCE"

        # Non-existent source field path -> 422 INVALID_MAPPING_RULE.
        bad_src_field = client.post(
            "/api/v1/mappings",
            json={
                "name": "Bad src field",
                "sourceConnectionId": src_conn_id,
                "sourceObjectKey": "public.customers",
                "destinationConnectionId": dst_conn_id,
                "destinationObjectKey": "POST /v1/contacts",
                "rules": [
                    {
                        "sourceFieldPath": "does_not_exist",
                        "destinationFieldPath": "body.email",
                        "mappingType": "direct",
                        "ordinalPosition": 0,
                    }
                ],
            },
        )
        assert bad_src_field.status_code == 422
        assert bad_src_field.json()["errorCode"] == "INVALID_MAPPING_RULE"

        # Non-existent destination field path -> 422 INVALID_MAPPING_RULE.
        bad_dst_field = client.post(
            "/api/v1/mappings",
            json={
                "name": "Bad dst field",
                "sourceConnectionId": src_conn_id,
                "sourceObjectKey": "public.customers",
                "destinationConnectionId": dst_conn_id,
                "destinationObjectKey": "POST /v1/contacts",
                "rules": [
                    {
                        "sourceFieldPath": "email",
                        "destinationFieldPath": "body.nope",
                        "mappingType": "direct",
                        "ordinalPosition": 0,
                    }
                ],
            },
        )
        assert bad_dst_field.status_code == 422
        assert bad_dst_field.json()["errorCode"] == "INVALID_MAPPING_RULE"

        # Duplicate destination field -> 422 DUPLICATE_DESTINATION_FIELD.
        dup_dst = client.post(
            "/api/v1/mappings",
            json={
                "name": "Dup dst field",
                "sourceConnectionId": src_conn_id,
                "sourceObjectKey": "public.customers",
                "destinationConnectionId": dst_conn_id,
                "destinationObjectKey": "POST /v1/contacts",
                "rules": [
                    {
                        "sourceFieldPath": "email",
                        "destinationFieldPath": "body.email",
                        "mappingType": "direct",
                        "ordinalPosition": 0,
                    },
                    {
                        "destinationFieldPath": "body.email",
                        "mappingType": "constant",
                        "constantValue": "override@example.com",
                        "ordinalPosition": 1,
                    },
                ],
            },
        )
        assert dup_dst.status_code == 422
        assert dup_dst.json()["errorCode"] == "DUPLICATE_DESTINATION_FIELD"

        # Executable expression / code in transformRef -> 422 INVALID_MAPPING_RULE without echo.
        exec_transform = client.post(
            "/api/v1/mappings",
            json={
                "name": "Executable transform",
                "sourceConnectionId": src_conn_id,
                "sourceObjectKey": "public.customers",
                "destinationConnectionId": dst_conn_id,
                "destinationObjectKey": "POST /v1/contacts",
                "rules": [
                    {
                        "sourceFieldPath": "email",
                        "destinationFieldPath": "body.email",
                        "mappingType": "transform",
                        "transformRef": {
                            "presetName": "custom",
                            "params": {"expression": "__import__('os').system('id')"},
                        },
                        "ordinalPosition": 0,
                    }
                ],
            },
        )
        assert exec_transform.status_code == 422
        assert exec_transform.json()["errorCode"] == "INVALID_MAPPING_RULE"
        assert "__import__" not in exec_transform.text

        # Secret-like key in constant JSON object -> 422 INVALID_MAPPING_RULE without secret leak.
        secret_const = client.post(
            "/api/v1/mappings",
            json={
                "name": "Secret const",
                "sourceConnectionId": src_conn_id,
                "sourceObjectKey": "public.customers",
                "destinationConnectionId": dst_conn_id,
                "destinationObjectKey": "POST /v1/contacts",
                "sourceSchemaFingerprint": src_fp,
                "rules": [
                    {
                        "destinationFieldPath": "body.extra",
                        "mappingType": "constant",
                        "constantValue": {"apiKey": "super-secret-value-123"},
                        "ordinalPosition": 0,
                    }
                ],
            },
        )
        assert secret_const.status_code == 422
        assert secret_const.json()["errorCode"] == "INVALID_MAPPING_RULE"
        assert "super-secret-value-123" not in secret_const.text

        # Direct rule missing sourceFieldPath -> 422 INVALID_MAPPING_RULE.
        bad_direct = client.post(
            "/api/v1/mappings",
            json={
                "name": "Bad direct",
                "sourceConnectionId": src_conn_id,
                "sourceObjectKey": "public.customers",
                "destinationConnectionId": dst_conn_id,
                "destinationObjectKey": "POST /v1/contacts",
                "rules": [
                    {
                        "destinationFieldPath": "body.email",
                        "mappingType": "direct",
                        "ordinalPosition": 0,
                    }
                ],
            },
        )
        assert bad_direct.status_code == 422
        assert bad_direct.json()["errorCode"] == "INVALID_MAPPING_RULE"

        # Constant rule setting sourceFieldPath -> 422 INVALID_MAPPING_RULE.
        bad_const = client.post(
            "/api/v1/mappings",
            json={
                "name": "Bad const",
                "sourceConnectionId": src_conn_id,
                "sourceObjectKey": "public.customers",
                "destinationConnectionId": dst_conn_id,
                "destinationObjectKey": "POST /v1/contacts",
                "rules": [
                    {
                        "sourceFieldPath": "email",
                        "destinationFieldPath": "body.email",
                        "mappingType": "constant",
                        "constantValue": "x",
                        "ordinalPosition": 0,
                    }
                ],
            },
        )
        assert bad_const.status_code == 422
        assert bad_const.json()["errorCode"] == "INVALID_MAPPING_RULE"


@pytest.mark.anyio
async def test_mapping_create_and_update_rollback_atomically_on_rule_failure() -> None:
    """Mid-transaction rule insertion failure rolls back create and rule-replacing update."""
    stub = _MappingInMemoryDbConn()
    src_conn_id, dst_conn_id, _src_fp, _dst_fp = _seed_source_and_dest(stub)
    db_mgr = _InMemoryMappingDbManager(stub)
    service = MappingService(
        connection_repo=ConnectionRepository(db_mgr),  # type: ignore[arg-type]
        schema_repo=SchemaRepository(db_mgr),  # type: ignore[arg-type]
        mapping_repo=MappingRepository(db_mgr),  # type: ignore[arg-type]
    )

    # Simulate failure inserting rule #2 on create -> neither mapping nor rule #1 persists.
    stub.fail_on_rule_insert_nth = 2
    with pytest.raises(DatabaseError):
        await service.create_mapping(
            MappingCreate(
                name="Atomic Create",
                sourceConnectionId=uuid.UUID(src_conn_id),
                sourceObjectKey="public.customers",
                destinationConnectionId=uuid.UUID(dst_conn_id),
                destinationObjectKey="POST /v1/contacts",
                rules=[
                    MappingRuleInput(
                        sourceFieldPath="email",
                        destinationFieldPath="body.email",
                        mappingType="direct",
                        ordinalPosition=0,
                    ),
                    MappingRuleInput(
                        destinationFieldPath="body.source_system",
                        mappingType="constant",
                        constantValue="pg",
                        ordinalPosition=1,
                    ),
                ],
            )
        )
    assert stub.mappings == {}
    assert stub.mapping_rules == {}

    # Create valid 1-rule mapping cleanly, then fail rule #2 during PUT -> v1 and rule #1 remain.
    stub.fail_on_rule_insert_nth = None
    created = await service.create_mapping(
        MappingCreate(
            name="Atomic Update",
            sourceConnectionId=uuid.UUID(src_conn_id),
            sourceObjectKey="public.customers",
            destinationConnectionId=uuid.UUID(dst_conn_id),
            destinationObjectKey="POST /v1/contacts",
            rules=[
                MappingRuleInput(
                    sourceFieldPath="email",
                    destinationFieldPath="body.email",
                    mappingType="direct",
                    ordinalPosition=0,
                )
            ],
        )
    )
    stub._rule_insert_counter = 0
    stub.fail_on_rule_insert_nth = 2
    with pytest.raises(DatabaseError):
        await service.update_mapping(
            str(created.id),
            MappingUpdate(
                name="Should Roll Back",
                rules=[
                    MappingRuleInput(
                        sourceFieldPath="full_name",
                        destinationFieldPath="body.contact_name",
                        mappingType="direct",
                        ordinalPosition=0,
                    ),
                    MappingRuleInput(
                        destinationFieldPath="body.source_system",
                        mappingType="constant",
                        constantValue="pg",
                        ordinalPosition=1,
                    ),
                ],
            ),
        )
    persisted = await service.get_mapping(str(created.id))
    assert persisted.name == "Atomic Update"
    assert persisted.mappingVersion == 1
    assert len(persisted.rules) == 1
    assert persisted.rules[0].destinationFieldPath == "body.email"


@pytest.mark.skipif(
    not os.environ.get("DATABRIDGE_TEST_DATABASE_URL"),
    reason="Live PostgreSQL integration test requires DATABRIDGE_TEST_DATABASE_URL.",
)
@pytest.mark.anyio
async def test_live_postgres16_mapping_crud_and_connection_restrict() -> None:
    """Verify full mapping CRUD, rule ordering, and ON DELETE RESTRICT against PostgreSQL 16."""
    db_url = os.environ["DATABRIDGE_TEST_DATABASE_URL"]
    conn_info = psycopg.conninfo.conninfo_to_dict(db_url)
    host = str(conn_info.get("host") or "127.0.0.1")
    port = int(conn_info.get("port") or 5432)
    database = str(conn_info.get("dbname") or "postgres")
    username = str(conn_info.get("user") or "postgres")
    password = conn_info.get("password")
    password_str = str(password) if password is not None else None

    iso_schema = f"map_iso_{uuid.uuid4().hex[:10]}"
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
            await setup_conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(iso_schema)))
            await setup_conn.execute(
                sql.SQL(
                    """
                    CREATE TABLE {}.accounts (
                        account_id SERIAL PRIMARY KEY,
                        email VARCHAR(255) NOT NULL UNIQUE,
                        display_name TEXT NOT NULL
                    )
                    """
                ).format(sql.Identifier(iso_schema))
            )
            await setup_conn.commit()

        app = create_app(settings=settings, database_manager=manager)
        with TestClient(app, raise_server_exceptions=False) as client:
            creds_payload: dict[str, Any] = {"username": username}
            if password_str:
                creds_payload["password"] = password_str

            src_resp = client.post(
                "/api/v1/connections",
                json={
                    "name": f"live-map-src-{uuid.uuid4().hex[:8]}",
                    "connectorType": "PostgreSQL",
                    "config": {"host": host, "port": port, "database": database},
                    "credentials": creds_payload,
                },
            )
            assert src_resp.status_code == 201, src_resp.text
            src_conn_id = src_resp.json()["id"]

            dst_resp = client.post(
                "/api/v1/connections",
                json={
                    "name": f"live-map-dst-{uuid.uuid4().hex[:8]}",
                    "connectorType": "REST API",
                    "config": {
                        "baseUrl": "https://api.example.com",
                        "authType": "none",
                    },
                },
            )
            assert dst_resp.status_code == 201, dst_resp.text
            dst_conn_id = dst_resp.json()["id"]

            # Discover source PostgreSQL schema.
            src_disc = client.post(
                f"/api/v1/connections/{src_conn_id}/discover",
                json={"options": {"includeSchemas": [iso_schema], "includeViews": False}},
            )
            assert src_disc.status_code == 200, src_disc.text
            src_fp = src_disc.json()["schemaFingerprint"]

            # Discover destination REST manual schema.
            dst_disc = client.post(
                f"/api/v1/connections/{dst_conn_id}/discover",
                json={
                    "options": {
                        "mode": "manual",
                        "manualObjects": [
                            {
                                "objectKey": "POST /v1/accounts",
                                "objectName": "POST /v1/accounts",
                                "objectType": "endpoint",
                                "fields": [
                                    {
                                        "fieldPath": "body.email",
                                        "fieldName": "email",
                                        "canonicalType": "string",
                                        "isRequired": True,
                                    },
                                    {
                                        "fieldPath": "body.display_name",
                                        "fieldName": "display_name",
                                        "canonicalType": "string",
                                    },
                                    {
                                        "fieldPath": "body.source_tag",
                                        "fieldName": "source_tag",
                                        "canonicalType": "string",
                                    },
                                    {
                                        "fieldPath": "body.nullable_meta",
                                        "fieldName": "nullable_meta",
                                        "canonicalType": "json",
                                    },
                                ],
                            }
                        ],
                    }
                },
            )
            assert dst_disc.status_code == 200, dst_disc.text
            dst_fp = dst_disc.json()["schemaFingerprint"]

            # Create mapping via POST /api/v1/mappings.
            create_map = client.post(
                "/api/v1/mappings",
                json={
                    "name": f"PG Accounts to REST {uuid.uuid4().hex[:6]}",
                    "description": "Live PostgreSQL 16 mapping test",
                    "sourceConnectionId": src_conn_id,
                    "sourceObjectKey": f"{iso_schema}.accounts",
                    "destinationConnectionId": dst_conn_id,
                    "destinationObjectKey": "POST /v1/accounts",
                    "rules": [
                        {
                            "sourceFieldPath": "email",
                            "destinationFieldPath": "body.email",
                            "mappingType": "transform",
                            "transformRef": {"presetName": "lowercase_trim"},
                            "ordinalPosition": 0,
                        },
                        {
                            "sourceFieldPath": "display_name",
                            "destinationFieldPath": "body.display_name",
                            "mappingType": "direct",
                            "ordinalPosition": 1,
                        },
                        {
                            "destinationFieldPath": "body.source_tag",
                            "mappingType": "constant",
                            "constantValue": "pg16",
                            "ordinalPosition": 2,
                        },
                        {
                            "destinationFieldPath": "body.nullable_meta",
                            "mappingType": "constant",
                            "constantValue": None,
                            "ordinalPosition": 3,
                        },
                    ],
                },
            )
            assert create_map.status_code == 201, create_map.text
            map_json = create_map.json()
            mapping_id = map_json["id"]
            assert map_json["status"] == "draft"
            assert map_json["mappingVersion"] == 1
            assert map_json["sourceSchemaFingerprint"] == src_fp
            assert map_json["destinationSchemaFingerprint"] == dst_fp
            assert [r["ordinalPosition"] for r in map_json["rules"]] == [0, 1, 2, 3]
            assert map_json["rules"][3]["constantValue"] is None

            # Connection referenced by mapping cannot be deleted (ON DELETE RESTRICT -> 409).
            in_use_del = client.delete(f"/api/v1/connections/{src_conn_id}")
            assert in_use_del.status_code == 409
            assert in_use_del.json()["errorCode"] == "CONNECTION_IN_USE"

            # List & filter via GET /api/v1/mappings.
            list_resp = client.get(
                "/api/v1/mappings",
                params={"sourceConnectionId": src_conn_id, "status": "draft"},
            )
            assert list_resp.status_code == 200
            assert any(item["id"] == mapping_id for item in list_resp.json()["items"])

            # Update mapping rules via PUT -> mappingVersion increments and rules replace.
            update_resp = client.put(
                f"/api/v1/mappings/{mapping_id}",
                json={
                    "description": "Updated live mapping",
                    "rules": [
                        {
                            "sourceFieldPath": "email",
                            "destinationFieldPath": "body.email",
                            "mappingType": "direct",
                            "ordinalPosition": 0,
                        },
                        {
                            "destinationFieldPath": "body.nullable_meta",
                            "mappingType": "constant",
                            "constantValue": {"tags": ["a", "b"]},
                            "ordinalPosition": 1,
                        },
                    ],
                },
            )
            assert update_resp.status_code == 200, update_resp.text
            updated_json = update_resp.json()
            assert updated_json["mappingVersion"] == 2
            assert updated_json["description"] == "Updated live mapping"
            assert len(updated_json["rules"]) == 2
            assert updated_json["rules"][1]["constantValue"] == {"tags": ["a", "b"]}

            # Delete mapping -> 204 and cascade-deletes mapping_rules.
            del_map = client.delete(f"/api/v1/mappings/{mapping_id}")
            assert del_map.status_code == 204

            async with await psycopg.AsyncConnection.connect(db_url) as verify_conn:
                cur = await verify_conn.execute(
                    "SELECT COUNT(*) FROM mapping_rules WHERE mapping_id = %s",
                    (mapping_id,),
                )
                rule_row = await cur.fetchone()
                assert rule_row is not None and int(rule_row[0]) == 0
            mapping_id = None

            # Now connections can be deleted cleanly.
            assert client.delete(f"/api/v1/connections/{src_conn_id}").status_code == 204
            src_conn_id = None
            assert client.delete(f"/api/v1/connections/{dst_conn_id}").status_code == 204
            dst_conn_id = None
    finally:
        async with await psycopg.AsyncConnection.connect(db_url) as cleanup_conn:
            if mapping_id is not None:
                await cleanup_conn.execute("DELETE FROM mappings WHERE id = %s", (mapping_id,))
            if src_conn_id is not None:
                await cleanup_conn.execute("DELETE FROM connections WHERE id = %s", (src_conn_id,))
            if dst_conn_id is not None:
                await cleanup_conn.execute("DELETE FROM connections WHERE id = %s", (dst_conn_id,))
            await cleanup_conn.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(iso_schema))
            )
            await cleanup_conn.commit()
        await manager.shutdown()
