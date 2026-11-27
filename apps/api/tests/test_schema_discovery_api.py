"""API and live PostgreSQL 16 integration tests for schema discovery endpoints."""

import base64
import copy
import json
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql

from app.core.config import Settings
from app.db.migrations import MigrationRunner
from app.db.pool import DatabaseManager
from app.main import create_app
from app.schemas.schemas import SchemaFieldInput, SchemaObjectInput, SchemaWarning
from app.services.discovery.base import (
    DiscoveryContext,
    DiscoveryExecutionError,
    DiscoveryExecutionResult,
)
from app.services.discovery.registry import (
    ConnectorDiscovererRegistry,
    create_default_discoverer_registry,
)
from app.services.discovery.rest_discoverer import RestApiSchemaDiscoverer

_KEY_V1_B64 = base64.b64encode(bytes(range(1, 33))).decode("ascii")
_KEY_V2_B64 = base64.b64encode(bytes(range(201, 233))).decode("ascii")


def _make_app_settings() -> Settings:
    keyring_json = json.dumps({"k1": _KEY_V1_B64, "k2": _KEY_V2_B64})
    return Settings(
        database_url="postgresql://databridge:test_db_pw@127.0.0.1:5432/databridge",  # type: ignore[arg-type]
        credential_encryption_keys=keyring_json,  # type: ignore[arg-type]
        credential_active_key_id="k1",
    )


class _FakeSchemaCursor:
    def __init__(self, rows: list[tuple[Any, ...]] | None = None) -> None:
        self._rows = rows or []

    async def fetchone(self) -> tuple[Any, ...] | None:
        return self._rows[0] if self._rows else None

    async def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self._rows)

    async def executemany(self, _sql: str, _params_seq: list[Any]) -> None:
        return None


class _FakeTx:
    def __init__(self, conn: "_SchemaInMemoryDbConn") -> None:
        self._conn = conn
        self._snapshot: tuple[Any, ...] | None = None

    async def __aenter__(self) -> "_FakeTx":
        self._snapshot = (
            copy.deepcopy(self._conn.connections),
            copy.deepcopy(self._conn.credentials),
            copy.deepcopy(self._conn.schemas),
            copy.deepcopy(self._conn.schema_objects),
            copy.deepcopy(self._conn.schema_fields),
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
            ) = self._snapshot


class _SchemaInMemoryDbConn:
    """Self-contained in-memory persistence stub for connections, credentials, and schemas."""

    def __init__(self) -> None:
        self.connections: dict[str, dict[str, Any]] = {}
        self.credentials: dict[str, dict[str, Any]] = {}
        self.schemas: dict[str, dict[str, Any]] = {}
        self.schema_objects: dict[str, dict[str, Any]] = {}
        self.schema_fields: dict[str, list[dict[str, Any]]] = {}

    def transaction(self) -> _FakeTx:
        return _FakeTx(self)

    def cursor(self) -> "_SchemaCursorCtx":
        return _SchemaCursorCtx(self)

    async def execute(self, sql_text: str, params: Any = None) -> _FakeSchemaCursor:
        normalized = " ".join(sql_text.split())
        now = datetime.now(UTC)

        if normalized.startswith("INSERT INTO connections"):
            conn_id, name, ctype, desc, endpoint, cfg, auth_cfg, status_val = params
            row = {
                "id": str(conn_id),
                "name": str(name),
                "connector_type": str(ctype),
                "description": desc,
                "endpoint": str(endpoint),
                "config": dict(cfg.obj) if hasattr(cfg, "obj") else dict(cfg),
                "auth_configured": bool(auth_cfg),
                "status": str(status_val),
                "last_tested_at": None,
                "last_test_status": None,
                "last_error_message": None,
                "last_successful_sync": None,
                "sync_frequency": None,
                "created_at": now.isoformat(),
                "updated_at": now.isoformat(),
            }
            self.connections[str(conn_id)] = row
            return _FakeSchemaCursor([self._conn_tuple(row)])

        if "FROM connections WHERE id = %s" in normalized:
            row = self.connections.get(str(params[0]))
            return _FakeSchemaCursor([self._conn_tuple(row)] if row else [])

        if normalized.startswith("INSERT INTO connection_credentials"):
            cid, ct, nonce, key_id, alg, fmt_ver = params
            row = {
                "connection_id": str(cid),
                "ciphertext": bytes(ct),
                "nonce": bytes(nonce),
                "key_id": str(key_id),
                "algorithm": str(alg),
                "format_version": int(fmt_ver),
                "created_at": now.isoformat(),
                "updated_at": now.isoformat(),
            }
            self.credentials[str(cid)] = row
            return _FakeSchemaCursor([self._cred_tuple(row)])

        if "FROM connection_credentials WHERE connection_id = %s" in normalized:
            row = self.credentials.get(str(params[0]))
            return _FakeSchemaCursor([self._cred_tuple(row)] if row else [])

        if normalized.startswith("DELETE FROM discovered_schemas WHERE connection_id = %s"):
            conn_id = str(params[0])
            to_del = [sid for sid, s in self.schemas.items() if s["connection_id"] == conn_id]
            for sid in to_del:
                self.schemas.pop(sid, None)
                obj_ids = [
                    oid
                    for oid, obj in self.schema_objects.items()
                    if obj["discovered_schema_id"] == sid
                ]
                for oid in obj_ids:
                    self.schema_objects.pop(oid, None)
                    self.schema_fields.pop(oid, None)
            return _FakeSchemaCursor([(sid,)] if to_del else [])

        if normalized.startswith("INSERT INTO discovered_schemas"):
            (
                schema_id,
                conn_id,
                discovery_status,
                discovered_at,
                fingerprint,
                discovery_source,
                meta,
            ) = params
            row = {
                "id": str(schema_id),
                "connection_id": str(conn_id),
                "discovery_status": str(discovery_status),
                "discovered_at": discovered_at,
                "schema_fingerprint": str(fingerprint),
                "discovery_source": str(discovery_source),
                "metadata": dict(meta.obj) if hasattr(meta, "obj") else dict(meta),
                "created_at": now,
                "updated_at": now,
            }
            self.schemas[str(schema_id)] = row
            return _FakeSchemaCursor([self._schema_tuple(row)])

        if normalized.startswith("INSERT INTO schema_objects"):
            (
                obj_id,
                schema_id,
                obj_key,
                obj_name,
                obj_type,
                disc_src,
                native_meta,
                canonical_meta,
            ) = params
            row = {
                "id": str(obj_id),
                "discovered_schema_id": str(schema_id),
                "object_key": str(obj_key),
                "object_name": str(obj_name),
                "object_type": str(obj_type),
                "discovery_source": str(disc_src),
                "native_metadata": (
                    dict(native_meta.obj) if hasattr(native_meta, "obj") else dict(native_meta)
                ),
                "canonical_metadata": (
                    dict(canonical_meta.obj)
                    if hasattr(canonical_meta, "obj")
                    else dict(canonical_meta)
                ),
                "created_at": now,
                "updated_at": now,
            }
            self.schema_objects[str(obj_id)] = row
            self.schema_fields.setdefault(str(obj_id), [])
            return _FakeSchemaCursor(
                [
                    (
                        row["id"],
                        row["discovered_schema_id"],
                        row["object_key"],
                        row["object_name"],
                        row["object_type"],
                        row["discovery_source"],
                        row["native_metadata"],
                        row["canonical_metadata"],
                        row["created_at"],
                        row["updated_at"],
                    )
                ]
            )

        if "FROM discovered_schemas WHERE connection_id = %s" in normalized:
            conn_id = str(params[0])
            match = next((s for s in self.schemas.values() if s["connection_id"] == conn_id), None)
            return _FakeSchemaCursor([self._schema_tuple(match)] if match else [])

        if "FROM schema_objects so LEFT JOIN schema_fields" in normalized:
            schema_id = str(params[0])
            objs = sorted(
                (o for o in self.schema_objects.values() if o["discovered_schema_id"] == schema_id),
                key=lambda o: o["object_key"],
            )
            return _FakeSchemaCursor([self._obj_summary_tuple(o) for o in objs])

        if normalized.startswith("SELECT COUNT(*) FROM schema_objects so"):
            filtered = self._filter_objects(params)
            return _FakeSchemaCursor([(len(filtered),)])

        if (
            "FROM schema_objects so JOIN discovered_schemas ds" in normalized
            and "LIMIT %s OFFSET %s" in normalized
        ):
            filter_params = params[:-2]
            limit, offset = int(params[-2]), int(params[-1])
            filtered = self._filter_objects(filter_params)
            return _FakeSchemaCursor(
                [self._obj_summary_tuple(o) for o in filtered[offset : offset + limit]]
            )

        if "WHERE ds.connection_id = %s AND so.id = %s" in normalized:
            conn_id, obj_id = str(params[0]), str(params[1])
            schema = next((s for s in self.schemas.values() if s["connection_id"] == conn_id), None)
            obj = self.schema_objects.get(obj_id)
            if schema and obj and obj["discovered_schema_id"] == schema["id"]:
                return _FakeSchemaCursor([self._obj_summary_tuple(obj)])
            return _FakeSchemaCursor([])

        if "FROM schema_fields WHERE schema_object_id = %s" in normalized:
            obj_id = str(params[0])
            raw_search = params[1]
            needle = str(raw_search).strip("%").lower() if raw_search else None
            rows = sorted(
                self.schema_fields.get(obj_id, []),
                key=lambda f: (f["ordinal_position"], f["field_path"]),
            )
            if needle:
                rows = [
                    r
                    for r in rows
                    if needle in r["field_path"].lower() or needle in r["field_name"].lower()
                ]
            return _FakeSchemaCursor([self._field_tuple(r) for r in rows])

        raise ValueError(f"Unhandled fake SQL: {normalized}")

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
    def _cred_tuple(row: dict[str, Any]) -> tuple[Any, ...]:
        return (
            row["connection_id"],
            row["ciphertext"],
            row["nonce"],
            row["key_id"],
            row["algorithm"],
            row["format_version"],
            row["created_at"],
            row["updated_at"],
        )

    def _filter_objects(self, params: tuple[Any, ...]) -> list[dict[str, Any]]:
        conn_id = str(params[0])
        obj_type = params[1]
        obj_key = params[3]
        raw_search = params[5]
        needle = str(raw_search).strip("%").lower() if raw_search else None
        schema = next((s for s in self.schemas.values() if s["connection_id"] == conn_id), None)
        if not schema:
            return []
        objs = [
            o for o in self.schema_objects.values() if o["discovered_schema_id"] == schema["id"]
        ]
        if obj_type is not None:
            objs = [o for o in objs if o["object_type"] == obj_type]
        if obj_key is not None:
            objs = [o for o in objs if o["object_key"] == obj_key]
        if needle:
            objs = [
                o
                for o in objs
                if needle in o["object_key"].lower() or needle in o["object_name"].lower()
            ]
        return sorted(objs, key=lambda o: o["object_key"])

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

    def _obj_summary_tuple(self, row: dict[str, Any]) -> tuple[Any, ...]:
        return (
            row["id"],
            row["discovered_schema_id"],
            row["object_key"],
            row["object_name"],
            row["object_type"],
            row["discovery_source"],
            row["native_metadata"],
            row["canonical_metadata"],
            len(self.schema_fields.get(row["id"], [])),
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


class _SchemaCursorCtx:
    def __init__(self, conn: _SchemaInMemoryDbConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> "_SchemaCursorCtx":
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return None

    async def executemany(self, sql: str, params_seq: list[tuple[Any, ...]]) -> None:
        normalized = " ".join(sql.split())
        now = datetime.now(UTC)
        if normalized.startswith("INSERT INTO schema_fields"):
            for row in params_seq:
                (
                    fid,
                    obj_id,
                    fpath,
                    fname,
                    ntype,
                    ctype,
                    nullable,
                    is_req,
                    pk,
                    uk,
                    def_meta,
                    ordinal,
                    disc_src,
                    meta,
                ) = row
                self._conn.schema_fields.setdefault(str(obj_id), []).append(
                    {
                        "id": str(fid),
                        "schema_object_id": str(obj_id),
                        "field_path": str(fpath),
                        "field_name": str(fname),
                        "native_type": str(ntype),
                        "canonical_type": str(ctype),
                        "nullable": bool(nullable),
                        "is_required": bool(is_req),
                        "primary_key": bool(pk),
                        "unique_key": bool(uk),
                        "default_metadata": (
                            dict(def_meta.obj)
                            if def_meta is not None and hasattr(def_meta, "obj")
                            else def_meta
                        ),
                        "ordinal_position": int(ordinal),
                        "discovery_source": str(disc_src),
                        "metadata": dict(meta.obj) if hasattr(meta, "obj") else dict(meta),
                        "created_at": now,
                        "updated_at": now,
                    }
                )


class _StubDiscoverer:
    def __init__(
        self,
        connector_type: str,
        *,
        result: DiscoveryExecutionResult | None = None,
        error: BaseException | None = None,
    ) -> None:
        self.connector_type = connector_type  # type: ignore[assignment]
        self._result = result
        self._error = error

    async def discover(self, ctx: DiscoveryContext) -> DiscoveryExecutionResult:
        del ctx
        if self._error is not None:
            raise self._error
        assert self._result is not None
        return self._result


class _InMemorySchemaDbManager(DatabaseManager):
    def __init__(self, conn: _SchemaInMemoryDbConn) -> None:
        super().__init__(_make_app_settings())
        self._conn = conn

    async def startup(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[_SchemaInMemoryDbConn]:
        yield self._conn


def _make_test_app(
    *,
    db_conn: _SchemaInMemoryDbConn | None = None,
    discoverer_registry: ConnectorDiscovererRegistry | None = None,
) -> tuple[Any, _SchemaInMemoryDbConn]:
    conn = db_conn or _SchemaInMemoryDbConn()
    mgr = _InMemorySchemaDbManager(conn)
    app = create_app(
        settings=_make_app_settings(),
        database_manager=mgr,
        discoverer_registry=discoverer_registry or create_default_discoverer_registry(),
    )
    return app, conn


def test_discovery_api_lifecycle_ownership_pagination_and_secret_safety() -> None:
    pg_discoverer = _StubDiscoverer(
        "PostgreSQL",
        result=DiscoveryExecutionResult(
            discovery_status="partial",
            discovery_source="catalog",
            objects=[
                SchemaObjectInput(
                    objectKey="public.customers",
                    objectName="customers",
                    objectType="table",
                    discoverySource="catalog",
                    fields=[
                        SchemaFieldInput(
                            fieldPath="id",
                            fieldName="id",
                            nativeType="bigint",
                            canonicalType="integer",
                            nullable=False,
                            isRequired=False,
                            primaryKey=True,
                            ordinalPosition=0,
                            discoverySource="catalog",
                        ),
                        SchemaFieldInput(
                            fieldPath="email",
                            fieldName="email",
                            nativeType="varchar(255)",
                            canonicalType="string",
                            nullable=False,
                            isRequired=True,
                            uniqueKey=True,
                            ordinalPosition=1,
                            discoverySource="catalog",
                        ),
                    ],
                ),
                SchemaObjectInput(
                    objectKey="public.orders",
                    objectName="orders",
                    objectType="table",
                    discoverySource="catalog",
                    fields=[
                        SchemaFieldInput(
                            fieldPath="id",
                            fieldName="id",
                            nativeType="uuid",
                            canonicalType="string",
                            nullable=False,
                            isRequired=True,
                            primaryKey=True,
                            ordinalPosition=0,
                            discoverySource="catalog",
                        ),
                    ],
                ),
            ],
            warnings=[
                SchemaWarning(
                    code="FIELD_LIMIT_REACHED",
                    message="Partial warning sample.",
                    objectKey="public.orders",
                )
            ],
        ),
    )
    registry = create_default_discoverer_registry()
    registry.register(pg_discoverer)
    app, _ = _make_test_app(discoverer_registry=registry)

    with TestClient(app, raise_server_exceptions=False) as client:
        pg_create = client.post(
            "/api/v1/connections",
            json={
                "name": "prod-pg",
                "connectorType": "PostgreSQL",
                "config": {"host": "db.internal", "port": 5432, "database": "app"},
                "credentials": {"username": "pg_secret_user", "password": "pg_secret_password"},
            },
        )
        assert pg_create.status_code == 201
        pg_id = pg_create.json()["id"]

        csv_create = client.post(
            "/api/v1/connections",
            json={
                "name": "leads-csv",
                "connectorType": "CSV",
                "config": {"fileName": "leads.csv"},
            },
        )
        assert csv_create.status_code == 201
        csv_id = csv_create.json()["id"]

        # Schema before discovery -> 404 SCHEMA_NOT_DISCOVERED.
        pre_schema = client.get(f"/api/v1/connections/{pg_id}/schema")
        assert pre_schema.status_code == 404
        assert pre_schema.json()["errorCode"] == "SCHEMA_NOT_DISCOVERED"

        # Unknown connection -> 404 CONNECTION_NOT_FOUND.
        assert client.get(f"/api/v1/connections/{uuid.uuid4()}/schema").status_code == 404
        assert client.get("/api/v1/connections/not-a-uuid/schema").status_code == 404

        # Trigger PostgreSQL discovery -> 200 partial snapshot.
        disc_resp = client.post(f"/api/v1/connections/{pg_id}/discover", json={})
        assert disc_resp.status_code == 200, disc_resp.text
        disc_json = disc_resp.json()
        assert disc_json["discoveryStatus"] == "partial"
        assert disc_json["objectCount"] == 2
        assert len(disc_json["warnings"]) == 1
        assert "pg_secret_password" not in disc_resp.text
        assert "pg_secret_user" not in disc_resp.text

        # Retrieve latest schema.
        schema_resp = client.get(f"/api/v1/connections/{pg_id}/schema")
        assert schema_resp.status_code == 200
        assert schema_resp.json()["schemaFingerprint"] == disc_json["schemaFingerprint"]

        # List objects with pagination & filter.
        objs_page1 = client.get(f"/api/v1/connections/{pg_id}/objects?limit=1&offset=0")
        assert objs_page1.status_code == 200
        assert objs_page1.json()["total"] == 2
        assert len(objs_page1.json()["items"]) == 1
        cust_obj_id = objs_page1.json()["items"][0]["id"]

        # Retrieve fields in exact ordinal order.
        fields_resp = client.get(f"/api/v1/connections/{pg_id}/objects/{cust_obj_id}/fields")
        assert fields_resp.status_code == 200
        assert [f["fieldPath"] for f in fields_resp.json()["fields"]] == ["id", "email"]

        # Discover CSV with transient payload -> verify cross-connection object ownership check.
        csv_disc = client.post(
            f"/api/v1/connections/{csv_id}/discover",
            json={"fileContent": "lead_id,name\n101,SecretRowValue\n"},
        )
        assert csv_disc.status_code == 200
        assert "SecretRowValue" not in csv_disc.text

        # Requesting PostgreSQL object ID under CSV connection ID must return 404 OBJECT_NOT_FOUND!
        wrong_owner = client.get(f"/api/v1/connections/{csv_id}/objects/{cust_obj_id}/fields")
        assert wrong_owner.status_code == 404
        assert wrong_owner.json()["errorCode"] == "OBJECT_NOT_FOUND"


def test_discovery_api_errors_timeouts_invalid_payloads_and_sanitization() -> None:
    empty_registry = ConnectorDiscovererRegistry()
    # Register failing PostgreSQL discoverer leaking secret in error message to verify redaction.
    empty_registry.register(
        _StubDiscoverer(
            "PostgreSQL",
            error=DiscoveryExecutionError(
                error_code="AUTHENTICATION_FAILED",
                message="Auth failed for pg_secret_user with pg_secret_pw",
                http_status=422,
            ),
        )
    )
    empty_registry.register(
        _StubDiscoverer(
            "MySQL",
            error=DiscoveryExecutionError(
                error_code="TIMEOUT",
                message="MySQL catalog query timed out.",
                http_status=504,
                is_timeout=True,
            ),
        )
    )
    empty_registry.register(
        RestApiSchemaDiscoverer(
            transport=httpx.MockTransport(lambda _r: httpx.Response(200, json={"ok": True})),
            resolver=lambda _h, _p: _async_list(["93.184.216.34"]),
        )
    )

    async def _async_list(val: list[str]) -> list[str]:
        return val

    app, _ = _make_test_app(discoverer_registry=empty_registry)
    with TestClient(app, raise_server_exceptions=False) as client:
        pg_id = client.post(
            "/api/v1/connections",
            json={
                "name": "pg-err",
                "connectorType": "PostgreSQL",
                "config": {"host": "db.internal", "port": 5432, "database": "app"},
                "credentials": {"username": "pg_secret_user", "password": "pg_secret_pw"},
            },
        ).json()["id"]
        mysql_id = client.post(
            "/api/v1/connections",
            json={
                "name": "mysql-timeout",
                "connectorType": "MySQL",
                "config": {"host": "db.internal", "port": 3306, "database": "app"},
                "credentials": {"username": "my_user", "password": "my_pw"},
            },
        ).json()["id"]
        csv_id = client.post(
            "/api/v1/connections",
            json={
                "name": "unsupported-csv",
                "connectorType": "CSV",
                "config": {"fileName": "test.csv"},
            },
        ).json()["id"]

        # Secret tokens redacted in API error response.
        auth_err = client.post(f"/api/v1/connections/{pg_id}/discover", json={})
        assert auth_err.status_code == 422
        assert auth_err.json()["errorCode"] == "AUTHENTICATION_FAILED"
        assert "pg_secret_pw" not in auth_err.text
        assert "pg_secret_user" not in auth_err.text

        # Timeout maps to 504.
        timeout_err = client.post(f"/api/v1/connections/{mysql_id}/discover", json={})
        assert timeout_err.status_code == 504
        assert timeout_err.json()["errorCode"] == "TIMEOUT"

        # Unsupported connector in custom registry -> 422 UNSUPPORTED_CONNECTOR.
        unsup = client.post(
            f"/api/v1/connections/{csv_id}/discover",
            json={"fileContent": "a,b\n1,2\n"},
        )
        assert unsup.status_code == 422
        assert unsup.json()["errorCode"] == "UNSUPPORTED_CONNECTOR"

        # Reject transient fileContent on PostgreSQL connection.
        bad_file = client.post(
            f"/api/v1/connections/{pg_id}/discover",
            json={"fileContent": "a,b\n1,2\n"},
        )
        assert bad_file.status_code == 422
        assert bad_file.json()["errorCode"] == "INVALID_CONFIGURATION"

        # Reject REST discovery options on PostgreSQL connection.
        bad_opt = client.post(
            f"/api/v1/connections/{pg_id}/discover",
            json={"options": {"mode": "openapi"}},
        )
        assert bad_opt.status_code == 422
        assert bad_opt.json()["errorCode"] == "DISCOVERY_NOT_SUPPORTED"


@pytest.mark.anyio
@pytest.mark.skipif(
    not os.environ.get("DATABRIDGE_TEST_DATABASE_URL"),
    reason="DATABRIDGE_TEST_DATABASE_URL not set; skipping live PostgreSQL discovery test",
)
async def test_live_postgresql_16_schema_discovery_end_to_end() -> None:
    """Run real PostgreSQL 16 catalog discovery over tables, a view, keys, and native types."""
    db_url = os.environ["DATABRIDGE_TEST_DATABASE_URL"]
    parsed = httpx.URL(db_url)
    host = parsed.host or "127.0.0.1"
    port = parsed.port or 5432
    database = parsed.path.lstrip("/")
    username = parsed.username or "databridge"
    password = parsed.password or ""

    settings = Settings(
        database_url=db_url,  # type: ignore[arg-type]
        credential_encryption_keys=f'{{"k1":"{_KEY_V1_B64}","k2":"{_KEY_V2_B64}"}}',  # type: ignore[arg-type]
        credential_active_key_id="k1",
    )
    manager = DatabaseManager(settings)
    await manager.startup()

    iso_schema = f"disc_live_{uuid.uuid4().hex[:10]}"
    conn_id: str | None = None
    try:
        runner = MigrationRunner(manager)
        await runner.apply_pending()

        async with await psycopg.AsyncConnection.connect(db_url) as setup_conn:
            await setup_conn.execute(f"CREATE SCHEMA {iso_schema}")
            await setup_conn.execute(
                f"""
                CREATE TABLE {iso_schema}.customers (
                    id SERIAL PRIMARY KEY,
                    email VARCHAR(255) NOT NULL UNIQUE,
                    balance NUMERIC(12,2) NOT NULL DEFAULT 0.00,
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    signup_date DATE NULL,
                    payload JSONB NULL,
                    tags TEXT[] NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            await setup_conn.execute(
                f"""
                CREATE TABLE {iso_schema}.orders (
                    order_id UUID PRIMARY KEY,
                    customer_id INTEGER NOT NULL REFERENCES {iso_schema}.customers(id),
                    notes TEXT NULL,
                    raw_hash BYTEA NULL
                )
                """
            )
            await setup_conn.execute(
                sql.SQL(
                    "CREATE VIEW {}.active_customers_view AS "
                    "SELECT id, email, balance FROM {}.customers WHERE is_active = TRUE"
                ).format(sql.Identifier(iso_schema), sql.Identifier(iso_schema))
            )
            await setup_conn.commit()

        app = create_app(settings=settings, database_manager=manager)
        with TestClient(app, raise_server_exceptions=False) as client:
            creds_payload: dict[str, Any] = {"username": username}
            if password:
                creds_payload["password"] = password

            create_resp = client.post(
                "/api/v1/connections",
                json={
                    "name": f"live-disc-{uuid.uuid4().hex[:8]}",
                    "connectorType": "PostgreSQL",
                    "config": {"host": host, "port": port, "database": database},
                    "credentials": creds_payload,
                },
            )
            assert create_resp.status_code == 201, create_resp.text
            conn_id = create_resp.json()["id"]

            disc_resp = client.post(
                f"/api/v1/connections/{conn_id}/discover",
                json={"options": {"includeSchemas": [iso_schema], "includeViews": True}},
            )
            assert disc_resp.status_code == 200, disc_resp.text
            disc_json = disc_resp.json()
            assert disc_json["discoveryStatus"] == "completed"
            assert disc_json["discoverySource"] == "catalog"
            assert disc_json["objectCount"] == 3
            object_keys = {obj["objectKey"] for obj in disc_json["objects"]}
            assert object_keys == {
                f"{iso_schema}.active_customers_view",
                f"{iso_schema}.customers",
                f"{iso_schema}.orders",
            }
            if password:
                assert password not in disc_resp.text

            objs_resp = client.get(f"/api/v1/connections/{conn_id}/objects")
            assert objs_resp.status_code == 200
            by_key = {o["objectKey"]: o for o in objs_resp.json()["items"]}
            assert by_key[f"{iso_schema}.active_customers_view"]["objectType"] == "view"

            cust_fields_resp = client.get(
                f"/api/v1/connections/{conn_id}/objects/{by_key[f'{iso_schema}.customers']['id']}/fields"
            )
            assert cust_fields_resp.status_code == 200
            cust_fields = {f["fieldPath"]: f for f in cust_fields_resp.json()["fields"]}
            assert cust_fields["id"]["primaryKey"] is True
            assert cust_fields["id"]["isRequired"] is False
            assert cust_fields["email"]["uniqueKey"] is True
            assert cust_fields["email"]["isRequired"] is True
            assert cust_fields["email"]["nativeType"] == "character varying(255)"
            assert cust_fields["balance"]["canonicalType"] == "number"
            assert cust_fields["balance"]["nativeType"] == "numeric(12,2)"
            assert cust_fields["is_active"]["canonicalType"] == "boolean"
            assert cust_fields["signup_date"]["canonicalType"] == "date"
            assert cust_fields["signup_date"]["nullable"] is True
            assert cust_fields["payload"]["canonicalType"] == "json"
            assert cust_fields["tags"]["canonicalType"] == "array"
            assert cust_fields["created_at"]["canonicalType"] == "datetime"

            orders_fields_resp = client.get(
                f"/api/v1/connections/{conn_id}/objects/{by_key[f'{iso_schema}.orders']['id']}/fields"
            )
            assert orders_fields_resp.status_code == 200
            ord_fields = {f["fieldPath"]: f for f in orders_fields_resp.json()["fields"]}
            assert ord_fields["order_id"]["primaryKey"] is True
            assert ord_fields["customer_id"]["isRequired"] is True
            assert ord_fields["customer_id"]["metadata"]["foreignKey"] == {
                "targetObjectKey": f"{iso_schema}.customers",
                "targetFieldPath": "id",
            }
            assert ord_fields["raw_hash"]["canonicalType"] == "binary"
    finally:
        async with await psycopg.AsyncConnection.connect(db_url) as cleanup_conn:
            if conn_id is not None:
                await cleanup_conn.execute("DELETE FROM connections WHERE id = %s", (conn_id,))
            await cleanup_conn.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(iso_schema))
            )
            await cleanup_conn.commit()
        await manager.shutdown()
