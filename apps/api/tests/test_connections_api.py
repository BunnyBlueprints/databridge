"""Tests for persistent connections CRUD, strict credential isolation, and saved testing."""

import base64
import copy
import logging
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import httpx
import psycopg
import psycopg.errors
import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from app.core.config import Settings
from app.core.errors import sanitize_error_message
from app.db.migrations import MigrationRunner
from app.db.pool import DatabaseManager
from app.main import create_app
from app.repositories.connections import ConnectionRepository
from app.repositories.credentials import CredentialRepository
from app.schemas.connection_test import (
    ConnectionTestRequest,
    ConnectionTestResult,
    ConnectorType,
    SqlConnectionTestCredentials,
)
from app.services.credential_encryption import CredentialEncryptionService
from app.services.testers.csv_tester import CsvConnectionTester
from app.services.testers.json_tester import JsonConnectionTester
from app.services.testers.mongo_tester import MongoDbConnectionTester
from app.services.testers.mysql_tester import MysqlConnectionTester
from app.services.testers.postgres_tester import PostgresConnectionTester
from app.services.testers.registry import ConnectorTesterRegistry
from app.services.testers.rest_tester import RestApiConnectionTester

_KEY_V1_BYTES = bytes(range(1, 33))
_KEY_V2_BYTES = bytes(range(201, 233))
_KEY_V1_B64 = base64.b64encode(_KEY_V1_BYTES).decode("ascii")
_KEY_V2_B64 = base64.b64encode(_KEY_V2_BYTES).decode("ascii")


def _persistent_settings(
    *,
    keys: dict[str, str] | None = None,
    active_key_id: str = "k1",
) -> Settings:
    import json

    keyring_json = json.dumps(keys or {"k1": _KEY_V1_B64, "k2": _KEY_V2_B64})
    return Settings(
        database_url="postgresql://databridge:test_db_pw@127.0.0.1:5432/databridge",  # type: ignore[arg-type]
        credential_encryption_keys=keyring_json,  # type: ignore[arg-type]
        credential_active_key_id=active_key_id,
    )


class _InMemoryCursor:
    def __init__(self, rows: list[tuple[Any, ...]] | None = None) -> None:
        self._rows = rows or []

    async def fetchone(self) -> tuple[Any, ...] | None:
        return self._rows[0] if self._rows else None

    async def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self._rows)


class _InMemoryTransaction:
    def __init__(self, db: "_InMemoryPostgresConnection") -> None:
        self._db = db
        self._snapshot: tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]] | None = None

    async def __aenter__(self) -> "_InMemoryTransaction":
        self._db.active_transactions += 1
        self._snapshot = (
            copy.deepcopy(self._db.connections),
            copy.deepcopy(self._db.credentials),
        )
        return self

    async def __aexit__(self, exc_type: Any, _exc: Any, _tb: Any) -> None:
        self._db.active_transactions -= 1
        if exc_type is not None and self._snapshot is not None:
            self._db.connections, self._db.credentials = self._snapshot
            self._db.rollbacks += 1
        elif exc_type is None:
            self._db.commits += 1


class _InMemoryPostgresConnection:
    """Faithful in-memory SQL double supporting connections, credentials, cascade, and rollback."""

    def __init__(self) -> None:
        self.connections: dict[str, dict[str, Any]] = {}
        self.credentials: dict[str, dict[str, Any]] = {}
        self.fail_on_credential_upsert = False
        self.credential_select_count = 0
        self.active_transactions = 0
        self.active_pool_checkouts = 0
        self.commits = 0
        self.rollbacks = 0

    def transaction(self) -> _InMemoryTransaction:
        return _InMemoryTransaction(self)

    @staticmethod
    def _connection_tuple(row: dict[str, Any]) -> tuple[Any, ...]:
        return (
            row["id"],
            row["name"],
            row["connector_type"],
            row["description"],
            row["endpoint"],
            copy.deepcopy(row["config"]),
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
    def _credential_tuple(row: dict[str, Any]) -> tuple[Any, ...]:
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

    async def execute(self, sql: str, params: Any = None) -> _InMemoryCursor:
        normalized = " ".join(sql.split())

        if normalized == "SELECT 1":
            return _InMemoryCursor([(1,)])

        if normalized.startswith("INSERT INTO connections"):
            conn_id, name, ctype, desc, endpoint, config_json, auth_cfg, status_val = params
            for existing in self.connections.values():
                if existing["name"].lower() == str(name).lower():
                    raise psycopg.errors.UniqueViolation(
                        "duplicate key value violates unique constraint uq_connections_lower_name"
                    )
            now = datetime.now(UTC)
            raw_config = config_json.obj if isinstance(config_json, Jsonb) else dict(config_json)
            row = {
                "id": str(conn_id),
                "name": str(name),
                "connector_type": str(ctype),
                "description": desc,
                "endpoint": str(endpoint),
                "config": copy.deepcopy(raw_config),
                "auth_configured": bool(auth_cfg),
                "status": str(status_val),
                "last_tested_at": None,
                "last_test_status": None,
                "last_error_message": None,
                "last_successful_sync": None,
                "sync_frequency": None,
                "created_at": now,
                "updated_at": now,
            }
            self.connections[row["id"]] = row
            return _InMemoryCursor([self._connection_tuple(row)])

        if normalized.startswith("SELECT ") and "FROM connections WHERE id = %s" in normalized:
            conn_id = str(params[0])
            row = self.connections.get(conn_id)
            return _InMemoryCursor([self._connection_tuple(row)] if row else [])

        if normalized.startswith("SELECT COUNT(*) FROM connections"):
            filtered = self._filter_rows(params or ())
            return _InMemoryCursor([(len(filtered),)])

        if "FROM connections" in normalized and "ORDER BY created_at DESC, id DESC" in normalized:
            filter_params = params[:-2]
            limit, offset = params[-2], params[-1]
            filtered = self._filter_rows(filter_params)
            filtered.sort(key=lambda r: (r["created_at"], r["id"]), reverse=True)
            sliced = filtered[offset : offset + limit]
            return _InMemoryCursor([self._connection_tuple(r) for r in sliced])

        if normalized.startswith("UPDATE connections SET status = %s, last_tested_at = %s"):
            new_status, tested_at, test_status, err_msg, conn_id = params
            row = self.connections.get(str(conn_id))
            if row is None:
                return _InMemoryCursor([])
            row["status"] = new_status
            row["last_tested_at"] = tested_at
            row["last_test_status"] = test_status
            row["last_error_message"] = err_msg
            row["updated_at"] = datetime.now(UTC)
            return _InMemoryCursor([self._connection_tuple(row)])

        if normalized.startswith("UPDATE connections SET name = %s"):
            (
                new_name,
                desc,
                endpoint,
                config_json,
                auth_cfg,
                new_status,
                clear_err,
                conn_id,
            ) = params
            row = self.connections.get(str(conn_id))
            if row is None:
                return _InMemoryCursor([])
            for cid, existing in self.connections.items():
                if cid != str(conn_id) and existing["name"].lower() == str(new_name).lower():
                    raise psycopg.errors.UniqueViolation("duplicate name")
            row["name"] = str(new_name)
            row["description"] = desc
            row["endpoint"] = str(endpoint)
            row["config"] = copy.deepcopy(
                config_json.obj if isinstance(config_json, Jsonb) else dict(config_json)
            )
            row["auth_configured"] = bool(auth_cfg)
            row["status"] = str(new_status)
            if clear_err:
                row["last_error_message"] = None
            row["updated_at"] = datetime.now(UTC)
            return _InMemoryCursor([self._connection_tuple(row)])

        if normalized.startswith("DELETE FROM connections WHERE id = %s"):
            conn_id = str(params[0])
            existed = self.connections.pop(conn_id, None)
            if existed is None:
                return _InMemoryCursor([])
            self.credentials.pop(conn_id, None)
            return _InMemoryCursor([(conn_id,)])

        if normalized.startswith("INSERT INTO connection_credentials"):
            if self.fail_on_credential_upsert:
                raise psycopg.OperationalError("simulated credential storage write failure")
            conn_id, ciphertext, nonce, key_id, alg, fmt_ver = params
            now = datetime.now(UTC)
            prev = self.credentials.get(str(conn_id))
            row = {
                "connection_id": str(conn_id),
                "ciphertext": bytes(ciphertext),
                "nonce": bytes(nonce),
                "key_id": str(key_id),
                "algorithm": str(alg),
                "format_version": int(fmt_ver),
                "created_at": prev["created_at"] if prev else now,
                "updated_at": now,
            }
            self.credentials[str(conn_id)] = row
            return _InMemoryCursor([self._credential_tuple(row)])

        if (
            normalized.startswith("SELECT ")
            and "FROM connection_credentials WHERE connection_id = %s" in normalized
        ):
            self.credential_select_count += 1
            cred_row = self.credentials.get(str(params[0]))
            return _InMemoryCursor([self._credential_tuple(cred_row)] if cred_row else [])

        if normalized.startswith("DELETE FROM connection_credentials WHERE connection_id = %s"):
            removed = self.credentials.pop(str(params[0]), None)
            return _InMemoryCursor([(removed["connection_id"],)] if removed else [])

        raise AssertionError(f"Unhandled SQL in in-memory fake: {normalized}")

    def _filter_rows(self, params: tuple[Any, ...]) -> list[dict[str, Any]]:
        rows = list(self.connections.values())
        status_val, _, type_val, _, search_pattern = params[:5]
        if status_val is not None:
            rows = [r for r in rows if r["status"] == status_val]
        if type_val is not None:
            rows = [r for r in rows if r["connector_type"] == type_val]
        if search_pattern is not None:
            raw_pattern = str(search_pattern)
            term = raw_pattern[1:-1].replace("\\%", "%").replace("\\_", "_").replace("\\\\", "\\")
            term_lower = term.lower()
            rows = [
                r
                for r in rows
                if term_lower in r["name"].lower()
                or term_lower in r["endpoint"].lower()
                or term_lower in (r["description"] or "").lower()
            ]
        return rows


class _InMemoryDatabaseManager(DatabaseManager):
    def __init__(self, settings: Settings, conn: _InMemoryPostgresConnection) -> None:
        super().__init__(settings)
        self._fake_conn = conn

    async def startup(self) -> None:
        self._is_open = True

    async def shutdown(self) -> None:
        self._is_open = False

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[Any]:
        self._fake_conn.active_pool_checkouts += 1
        try:
            yield self._fake_conn
        finally:
            self._fake_conn.active_pool_checkouts -= 1


class _RecordedDispatchRegistry(ConnectorTesterRegistry):
    """Registry backed by deterministic testers observing credentials and transaction state."""

    def __init__(
        self,
        *,
        db_conn: _InMemoryPostgresConnection | None = None,
        force_fail: bool = False,
    ) -> None:
        super().__init__()
        self.dispatched_requests: list[ConnectionTestRequest] = []
        self.tx_active_during_dispatch: list[int] = []
        self.pool_checkouts_during_dispatch: list[int] = []
        self.force_fail = force_fail
        self._db_conn = db_conn
        self._setup_testers()

    def _setup_testers(self) -> None:
        force_fail = self.force_fail
        recorded = self.dispatched_requests
        tx_states = self.tx_active_during_dispatch
        pool_states = self.pool_checkouts_during_dispatch
        db_conn = self._db_conn

        class _PgCursor:
            async def execute(self, _q: str) -> None:
                return None

            async def fetchone(self) -> tuple[int]:
                return (1,)

            async def close(self) -> None:
                return None

        class _PgConn:
            def cursor(self) -> _PgCursor:
                return _PgCursor()

            async def close(self) -> None:
                return None

        async def pg_connect(**kwargs: Any) -> _PgConn:
            if force_fail:
                raise psycopg.errors.InvalidPassword("password authentication failed")
            assert kwargs.get("user") in ("ro_user", "reader", "ro_live", "ro_replaced")
            assert kwargs.get("password") in (None, "pg_pw_v1", "pg_pw_v2")
            return _PgConn()

        class _MyCursor:
            def execute(self, _q: str) -> None:
                return None

            def fetchone(self) -> tuple[int]:
                return (1,)

            def close(self) -> None:
                return None

        class _MyConn:
            def cursor(self) -> _MyCursor:
                return _MyCursor()

            def close(self) -> None:
                return None

        def my_connect(**kwargs: Any) -> _MyConn:
            if force_fail:
                raise ConnectionRefusedError("refused")
            assert kwargs.get("user") in ("ro_mysql", "mysql_secret_user")
            assert kwargs.get("password") in (None, "mysql_pw")
            return _MyConn()

        class _MongoAdmin:
            async def command(self, _cmd: str) -> dict[str, Any]:
                return {"ok": 1.0}

        class _MongoClient:
            admin = _MongoAdmin()

            async def close(self) -> None:
                return None

        def mongo_factory(**kwargs: Any) -> _MongoClient:
            if force_fail:
                raise TimeoutError("timed out")
            assert kwargs.get("host")
            return _MongoClient()

        async def resolver(_host: str, _port: int) -> list[str]:
            return ["93.184.216.34"]

        def rest_handler(_request: httpx.Request) -> httpx.Response:
            if force_fail:
                return httpx.Response(401, json={"error": "unauthorized"})
            return httpx.Response(200, json={"ok": True})

        class _RecordingWrapper:
            def __init__(self, inner: Any) -> None:
                self._inner = inner
                self.connector_type = inner.connector_type

            async def test(self, request: ConnectionTestRequest, timeout_seconds: float) -> Any:
                recorded.append(request)
                if db_conn is not None:
                    tx_states.append(db_conn.active_transactions)
                    pool_states.append(db_conn.active_pool_checkouts)
                return await self._inner.test(request, timeout_seconds)

        for tester in (
            PostgresConnectionTester(connect_fn=pg_connect),
            MysqlConnectionTester(connect_fn=my_connect),
            MongoDbConnectionTester(client_factory=mongo_factory),
            RestApiConnectionTester(
                transport=httpx.MockTransport(rest_handler),
                resolver=resolver,
            ),
            CsvConnectionTester(),
            JsonConnectionTester(),
        ):
            self.register(_RecordingWrapper(tester))


def _make_test_client(
    *,
    db_conn: _InMemoryPostgresConnection | None = None,
    settings: Settings | None = None,
    force_fail: bool = False,
) -> tuple[TestClient, _InMemoryPostgresConnection, _RecordedDispatchRegistry]:
    resolved_conn = db_conn or _InMemoryPostgresConnection()
    resolved_settings = settings or _persistent_settings(active_key_id="k1")
    db_manager = _InMemoryDatabaseManager(resolved_settings, resolved_conn)
    registry = _RecordedDispatchRegistry(db_conn=resolved_conn, force_fail=force_fail)
    app = create_app(
        settings=resolved_settings,
        registry=registry,
        database_manager=db_manager,
    )
    client = TestClient(app, raise_server_exceptions=False)
    return client, resolved_conn, registry


def _assert_no_secrets_in_payload(payload: Any, secrets: list[str]) -> None:
    serialized = str(payload)
    for forbidden_key in (
        "ciphertext",
        "nonce",
        "key_id",
        "keyId",
        "password",
        "username",
        "connectionUri",
        "apiKey",
        "bearerToken",
    ):
        assert forbidden_key not in serialized
    for secret in secrets:
        assert secret not in serialized


def test_crud_all_six_connectors_encrypts_usernames_and_secrets_and_filters() -> None:
    client, db_conn, _registry = _make_test_client()
    secret_values = [
        "ro_user",
        "pg_pw_v1",
        "ro_mysql",
        "mysql_pw",
        "events_ro",
        "mongo_secret_pw",
        "sk_live_rest_999",
    ]

    payloads = [
        {
            "name": "orders-postgres",
            "connectorType": "PostgreSQL",
            "description": "Primary Orders DB",
            "config": {
                "host": "pg.internal",
                "port": 5432,
                "database": "orders",
            },
            "credentials": {
                "username": "ro_user",
                "password": "pg_pw_v1",
            },
        },
        {
            "name": "billing-mysql",
            "connectorType": "MySQL",
            "config": {
                "host": "mysql.internal",
                "port": 3306,
                "database": "billing",
            },
            "credentials": {
                "username": "ro_mysql",
                "password": "mysql_pw",
            },
        },
        {
            "name": "events-mongo",
            "connectorType": "MongoDB",
            "config": {
                "uri": "mongodb+srv://cluster01.example.net",
                "database": "analytics",
            },
            "credentials": {
                "username": "events_ro",
                "password": "mongo_secret_pw",
            },
        },
        {
            "name": "crm-rest",
            "connectorType": "REST API",
            "config": {
                "baseUrl": "https://api.example.com/v1",
                "authType": "api_key",
            },
            "credentials": {"apiKey": "sk_live_rest_999"},
        },
        {
            "name": "customers-csv",
            "connectorType": "CSV",
            "config": {"fileName": "customers.csv"},
        },
        {
            "name": "catalog-json",
            "connectorType": "JSON",
            "config": {"fileName": "catalog.json"},
        },
    ]

    created_ids: dict[str, str] = {}
    with client:
        for body in payloads:
            resp = client.post("/api/v1/connections", json=body)
            assert resp.status_code == 201, resp.text
            data = resp.json()
            uuid.UUID(data["id"])
            assert data["status"] == "Disconnected"
            assert data["type"] == body["connectorType"]
            _assert_no_secrets_in_payload(data, secret_values)
            created_ids[body["name"]] = data["id"]

        # Verify SQL & MongoDB JSONB contain NO usernames, passwords, or credential URIs
        assert len(db_conn.credentials) == 4
        for name in ("orders-postgres", "billing-mysql", "events-mongo"):
            cid = created_ids[name]
            assert "username" not in db_conn.connections[cid]["config"]
            assert "password" not in db_conn.connections[cid]["config"]
            assert "connectionUri" not in db_conn.connections[cid]["config"]
        assert b"ro_user" not in db_conn.credentials[created_ids["orders-postgres"]]["ciphertext"]
        assert b"pg_pw_v1" not in db_conn.credentials[created_ids["orders-postgres"]]["ciphertext"]
        assert b"ro_mysql" not in db_conn.credentials[created_ids["billing-mysql"]]["ciphertext"]
        assert b"events_ro" not in db_conn.credentials[created_ids["events-mongo"]]["ciphertext"]
        assert (
            b"mongo_secret_pw" not in db_conn.credentials[created_ids["events-mongo"]]["ciphertext"]
        )

        # Duplicate name conflict -> 409 DUPLICATE_CONNECTION
        dup = client.post("/api/v1/connections", json=payloads[0])
        assert dup.status_code == 409
        assert dup.json()["errorCode"] == "DUPLICATE_CONNECTION"

        # List + filter never queries connection_credentials
        before_selects = db_conn.credential_select_count
        all_resp = client.get("/api/v1/connections")
        assert all_resp.status_code == 200
        assert all_resp.json()["total"] == 6
        _assert_no_secrets_in_payload(all_resp.json(), secret_values)

        mongo_id = created_ids["events-mongo"]
        filtered = client.get(
            "/api/v1/connections",
            params={"type": "MongoDB", "status": "Disconnected", "q": "analytics"},
        )
        assert filtered.status_code == 200
        assert filtered.json()["total"] == 1
        assert filtered.json()["items"][0]["id"] == mongo_id

        detail = client.get(f"/api/v1/connections/{mongo_id}")
        assert detail.status_code == 200
        assert detail.json()["details"]["authConfigured"] is True
        assert db_conn.credential_select_count == before_selects

        assert client.delete(f"/api/v1/connections/{mongo_id}").status_code == 204
        assert mongo_id not in db_conn.connections
        assert mongo_id not in db_conn.credentials
        assert client.get(f"/api/v1/connections/{mongo_id}").status_code == 404


def test_sql_and_mongodb_reject_usernames_in_jsonb_config_and_duplicate_mongo_uri() -> None:
    client, _, _ = _make_test_client()

    with client:
        # Reject SQL username inside config
        bad_pg_cfg = client.post(
            "/api/v1/connections",
            json={
                "name": "bad-pg-user-in-config",
                "connectorType": "PostgreSQL",
                "config": {
                    "host": "db.internal",
                    "port": 5432,
                    "database": "app",
                    "username": "leaked_in_cfg",
                },
                "credentials": {"username": "proper_user", "password": "pw"},
            },
        )
        assert bad_pg_cfg.status_code == 422
        assert "leaked_in_cfg" not in bad_pg_cfg.text

        # Reject PG/MySQL without required encrypted username
        bad_mysql_no_creds = client.post(
            "/api/v1/connections",
            json={
                "name": "bad-mysql-missing-user",
                "connectorType": "MySQL",
                "config": {"host": "mysql.internal", "port": 3306, "database": "app"},
                "credentials": {"password": "pw"},
            },
        )
        assert bad_mysql_no_creds.status_code == 422

        # Reject MongoDB username in config, embedded URI userinfo, query secret, and connectionUri
        for bad_mongo_body in (
            {
                "name": "bad-mongo-1",
                "connectorType": "MongoDB",
                "config": {
                    "uri": "mongodb://cluster.example.net",
                    "database": "app",
                    "username": "not_allowed_in_jsonb",
                },
            },
            {
                "name": "bad-mongo-2",
                "connectorType": "MongoDB",
                "config": {
                    "uri": "mongodb://alice:top_secret_pw@db.example.net",
                    "database": "app",
                },
            },
            {
                "name": "bad-mongo-3",
                "connectorType": "MongoDB",
                "config": {
                    "uri": "mongodb://db.example.net/?password=query_secret",
                    "database": "app",
                },
            },
            {
                "name": "bad-mongo-4",
                "connectorType": "MongoDB",
                "config": {"uri": "mongodb://db.example.net", "database": "app"},
                "credentials": {
                    "connectionUri": "mongodb://alice:pw@db.example.net",
                    "password": "pw",
                },
            },
        ):
            r = client.post("/api/v1/connections", json=bad_mongo_body)
            assert r.status_code == 422
            assert "top_secret_pw" not in r.text
            assert "query_secret" not in r.text


def test_update_credential_actions_status_lifecycle_and_rotation() -> None:
    db_conn = _InMemoryPostgresConnection()
    client_k1, _, _ = _make_test_client(
        db_conn=db_conn,
        settings=_persistent_settings(active_key_id="k1"),
    )

    with client_k1:
        create_resp = client_k1.post(
            "/api/v1/connections",
            json={
                "name": "pg-rotate",
                "connectorType": "PostgreSQL",
                "description": "initial desc",
                "config": {"host": "db1.internal", "port": 5432, "database": "app"},
                "credentials": {"username": "reader", "password": "pg_pw_v1"},
            },
        )
        conn_id = create_resp.json()["id"]
        assert db_conn.credentials[conn_id]["key_id"] == "k1"

        # Clear is disallowed for PG because encrypted username is required
        pg_clear = client_k1.put(
            f"/api/v1/connections/{conn_id}",
            json={"credentialAction": "clear"},
        )
        assert pg_clear.status_code == 422

        # Test succeeds -> status becomes Connected; tester runs outside DB locks
        test_ok = client_k1.post(f"/api/v1/connections/{conn_id}/test")
        assert test_ok.status_code == 200
        assert test_ok.json()["success"] is True
        assert client_k1.get(f"/api/v1/connections/{conn_id}").json()["status"] == "Connected"

        # Metadata-only update preserves Connected status & k1 key
        meta_upd = client_k1.put(
            f"/api/v1/connections/{conn_id}",
            json={"name": "pg-rotate-renamed", "description": "updated desc"},
        )
        assert meta_upd.status_code == 200
        assert meta_upd.json()["status"] == "Connected"
        assert db_conn.credentials[conn_id]["key_id"] == "k1"

    # Rotate active key to k2 while keeping k1 in keyring
    client_k2, _, registry_k2 = _make_test_client(
        db_conn=db_conn,
        settings=_persistent_settings(active_key_id="k2"),
    )
    with client_k2:
        hist_test = client_k2.post(f"/api/v1/connections/{conn_id}/test")
        assert hist_test.status_code == 200
        assert hist_test.json()["success"] is True
        assert registry_k2.tx_active_during_dispatch == [0]
        assert registry_k2.pool_checkouts_during_dispatch == [0]

        # Valid replace re-encrypts credentials under k2 and resets Connected -> Disconnected
        good_replace = client_k2.put(
            f"/api/v1/connections/{conn_id}",
            json={
                "credentialAction": "replace",
                "credentials": {"username": "ro_replaced", "password": "pg_pw_v2"},
            },
        )
        assert good_replace.status_code == 200
        assert good_replace.json()["status"] == "Disconnected"
        assert db_conn.credentials[conn_id]["key_id"] == "k2"
        assert "username" not in db_conn.connections[conn_id]["config"]

        # Verify updated encrypted username & password are used on saved test
        assert client_k2.post(f"/api/v1/connections/{conn_id}/test").json()["success"] is True
        dispatched = registry_k2.dispatched_requests[-1]
        assert dispatched.config.username == "ro_replaced"  # type: ignore[union-attr]
        assert dispatched.credentials is not None
        assert dispatched.credentials.password.get_secret_value() == "pg_pw_v2"  # type: ignore[union-attr]

        # MongoDB optional auth DOES allow clear
        mongo_id = client_k2.post(
            "/api/v1/connections",
            json={
                "name": "mongo-clearable",
                "connectorType": "MongoDB",
                "config": {"uri": "mongodb://mongo.example.com:27017", "database": "events"},
                "credentials": {"username": "mongo_u", "password": "mongo_pw"},
            },
        ).json()["id"]
        mongo_clear = client_k2.put(
            f"/api/v1/connections/{mongo_id}",
            json={"credentialAction": "clear"},
        )
        assert mongo_clear.status_code == 200
        assert mongo_clear.json()["details"]["authConfigured"] is False
        assert mongo_id not in db_conn.credentials
        # Passwordless MongoDB test succeeds with credentials=None
        assert client_k2.post(f"/api/v1/connections/{mongo_id}/test").json()["success"] is True


def test_rest_auth_transitions_and_csv_json_local_path_rejection() -> None:
    db_conn = _InMemoryPostgresConnection()
    client, _, registry = _make_test_client(db_conn=db_conn)

    with client:
        # Strict CSV/JSON local path rejections
        for bad_cfg in (
            {"fileName": "C:\\fakepath\\data.csv"},
            {"fileName": "~/secrets.csv"},
            {"fileName": "dir/data.csv"},
            {"fileName": "data.csv", "endpoint": "file:///Users/alice/data.csv"},
            {"fileName": "data.csv", "endpoint": "./relative.csv"},
            {"fileName": "data.csv", "endpoint": "../parent.csv"},
            {"fileName": "data.csv", "endpoint": "~/home.csv"},
            {"fileName": "data.csv", "endpoint": "dir/data.csv"},
            {"fileName": "data.csv", "endpoint": "local://sub/data.csv"},
        ):
            r = client.post(
                "/api/v1/connections",
                json={"name": "bad-csv", "connectorType": "CSV", "config": bad_cfg},
            )
            assert r.status_code == 422, f"Expected 422 for {bad_cfg}"

        # Safe logical/remote CSV endpoint allowed
        ok_csv = client.post(
            "/api/v1/connections",
            json={
                "name": "safe-csv",
                "connectorType": "CSV",
                "config": {"fileName": "data.csv", "endpoint": "local://data.csv"},
            },
        )
        assert ok_csv.status_code == 201

        # REST auth transitions
        rest_id = client.post(
            "/api/v1/connections",
            json={
                "name": "rest-transitions",
                "connectorType": "REST API",
                "config": {"baseUrl": "https://api.example.com", "authType": "api_key"},
                "credentials": {"apiKey": "key_one"},
            },
        ).json()["id"]

        # clear forbidden when api_key
        assert (
            client.put(
                f"/api/v1/connections/{rest_id}",
                json={"credentialAction": "clear"},
            ).status_code
            == 422
        )

        # Switching api_key -> bearer with keep is FORBIDDEN (prevents incompatible reuse)
        assert (
            client.put(
                f"/api/v1/connections/{rest_id}",
                json={
                    "config": {"baseUrl": "https://api.example.com", "authType": "bearer"},
                    "credentialAction": "keep",
                },
            ).status_code
            == 422
        )

        # Switching api_key -> bearer with incompatible apiKey inside replace is FORBIDDEN
        assert (
            client.put(
                f"/api/v1/connections/{rest_id}",
                json={
                    "config": {"baseUrl": "https://api.example.com", "authType": "bearer"},
                    "credentialAction": "replace",
                    "credentials": {"apiKey": "wrong_field"},
                },
            ).status_code
            == 422
        )

        # Switching api_key -> bearer with valid bearerToken succeeds & replaces old secret
        to_bearer = client.put(
            f"/api/v1/connections/{rest_id}",
            json={
                "config": {"baseUrl": "https://api.example.com", "authType": "bearer"},
                "credentialAction": "replace",
                "credentials": {"bearerToken": "tok_two"},
            },
        )
        assert to_bearer.status_code == 200
        assert client.post(f"/api/v1/connections/{rest_id}/test").json()["success"] is True
        dispatched_bearer = registry.dispatched_requests[-1]
        assert dispatched_bearer.credentials is not None
        assert dispatched_bearer.credentials.bearerToken.get_secret_value() == "tok_two"  # type: ignore[union-attr]
        assert dispatched_bearer.credentials.apiKey is None  # type: ignore[union-attr]

        # Switching bearer -> none removes obsolete credentials completely
        to_none = client.put(
            f"/api/v1/connections/{rest_id}",
            json={"config": {"baseUrl": "https://api.example.com", "authType": "none"}},
        )
        assert to_none.status_code == 200
        assert to_none.json()["details"]["authConfigured"] is False
        assert rest_id not in db_conn.credentials

        # Cannot switch none -> api_key with keep
        assert (
            client.put(
                f"/api/v1/connections/{rest_id}",
                json={"config": {"baseUrl": "https://api.example.com", "authType": "api_key"}},
            ).status_code
            == 422
        )


def test_error_sanitization_and_saved_test_failure_never_leak_secrets(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Direct sanitization checks covering @ in passwords, Basic/Bearer headers, quoted KV, JSON
    sample = (
        "Connection error for mongodb://alice:p@ssw0rd!@db.internal:27017 "
        'Authorization="Bearer top_secret_jwt" Basic dXNlcjpwYXNz '
        'password="my space pw" {"apiKey": "sk_live_987"}'
    )
    scrubbed = sanitize_error_message(
        sample,
        "fallback",
        known_secrets=("p@ssw0rd!", "sk_live_987"),
        known_usernames=("alice",),
    )
    for sensitive in (
        "alice",
        "p@ssw0rd!",
        "top_secret_jwt",
        "dXNlcjpwYXNz",
        "my space pw",
        "sk_live_987",
    ):
        assert sensitive not in scrubbed

    # Saved test with a hostile leaky tester still sanitizes returned & persisted last_error_message
    db_conn = _InMemoryPostgresConnection()

    class _LeakyRestTester:
        connector_type: ConnectorType = "REST API"

        async def test(
            self, request: ConnectionTestRequest, _timeout: float
        ) -> ConnectionTestResult:
            token = request.credentials.bearerToken.get_secret_value()  # type: ignore[union-attr]
            return ConnectionTestResult(
                success=False,
                status="failed",
                errorCode="SERVER_ERROR",
                message=(
                    f"Upstream failed with Bearer {token} against "
                    f"https://user:secret_pass@api.example.com?api_key=leak123"
                ),
                durationMs=1,
            )

    registry = _RecordedDispatchRegistry(db_conn=db_conn)
    registry.register(_LeakyRestTester())
    settings = _persistent_settings(active_key_id="k1")
    app = create_app(
        settings=settings,
        registry=registry,
        database_manager=_InMemoryDatabaseManager(settings, db_conn),
    )

    with caplog.at_level(logging.DEBUG, logger="databridge.api"):
        with TestClient(app, raise_server_exceptions=False) as client:
            cid = client.post(
                "/api/v1/connections",
                json={
                    "name": "leaky-rest-test",
                    "connectorType": "REST API",
                    "config": {"baseUrl": "https://api.example.com", "authType": "bearer"},
                    "credentials": {"bearerToken": "unique_bearer_secret_42"},
                },
            ).json()["id"]

            res = client.post(f"/api/v1/connections/{cid}/test")
            assert res.status_code == 200
            body = res.json()
            assert body["success"] is False
            assert "unique_bearer_secret_42" not in body["message"]
            assert "secret_pass" not in body["message"]
            assert "leak123" not in body["message"]

            detail = client.get(f"/api/v1/connections/{cid}").json()
            assert detail["status"] == "Disconnected"
            assert detail["lastTestStatus"] == "failed"
            assert detail["errorMessage"]
            assert "unique_bearer_secret_42" not in detail["errorMessage"]
            assert "secret_pass" not in detail["errorMessage"]
            assert "leak123" not in detail["errorMessage"]

    assert "unique_bearer_secret_42" not in caplog.text


def test_decryption_failure_and_partial_rollback_fail_closed() -> None:
    client, db_conn, registry = _make_test_client()

    with client:
        cid = client.post(
            "/api/v1/connections",
            json={
                "name": "decryption-guard-pg",
                "connectorType": "PostgreSQL",
                "config": {"host": "pg.internal", "port": 5432, "database": "orders"},
                "credentials": {"username": "ro_user", "password": "pg_pw_v1"},
            },
        ).json()["id"]

        # Tamper ciphertext -> fails closed without dispatching tester
        tampered = bytearray(db_conn.credentials[cid]["ciphertext"])
        tampered[0] ^= 0xFF
        db_conn.credentials[cid]["ciphertext"] = bytes(tampered)
        before_dispatch = len(registry.dispatched_requests)

        fail_closed = client.post(f"/api/v1/connections/{cid}/test")
        assert fail_closed.status_code == 500
        assert fail_closed.json()["success"] is False
        assert "pg_pw_v1" not in fail_closed.text
        assert "ro_user" not in fail_closed.text
        assert len(registry.dispatched_requests) == before_dispatch

        # Verify partial rollback when credential write fails
        db_conn.fail_on_credential_upsert = True
        rollback_resp = client.post(
            "/api/v1/connections",
            json={
                "name": "rollback-pg",
                "connectorType": "PostgreSQL",
                "config": {"host": "pg2.internal", "port": 5432, "database": "orders"},
                "credentials": {"username": "ro_user", "password": "pg_pw_v1"},
            },
        )
        assert rollback_resp.status_code == 503
        assert db_conn.rollbacks >= 1
        assert len(db_conn.connections) == 1


def test_legacy_precorrection_credential_row_remains_readable_without_leaking_username() -> None:
    """Confirm pre-correction password-only encrypted rows + legacy config deserialize safely."""
    db_conn = _InMemoryPostgresConnection()
    settings = _persistent_settings(active_key_id="k1")
    enc_svc = CredentialEncryptionService.from_settings(settings)
    legacy_id = str(uuid.uuid4())
    now = datetime.now(UTC)

    # Simulate a pre-correction DB row where username resided in config JSONB
    db_conn.connections[legacy_id] = {
        "id": legacy_id,
        "name": "legacy-pg",
        "connector_type": "PostgreSQL",
        "description": None,
        "endpoint": "pg.legacy.internal:5432/orders",
        "config": {
            "host": "pg.legacy.internal",
            "port": 5432,
            "database": "orders",
            "username": "ro_user",
        },
        "auth_configured": True,
        "status": "Disconnected",
        "last_tested_at": None,
        "last_test_status": None,
        "last_error_message": None,
        "last_successful_sync": None,
        "sync_frequency": None,
        "created_at": now,
        "updated_at": now,
    }
    legacy_enc = enc_svc.encrypt(
        connection_id=legacy_id,
        connector_type="PostgreSQL",
        credentials=SqlConnectionTestCredentials(password="pg_pw_v1"),
    )
    db_conn.credentials[legacy_id] = {
        "connection_id": legacy_id,
        "ciphertext": legacy_enc.ciphertext,
        "nonce": legacy_enc.nonce,
        "key_id": legacy_enc.key_id,
        "algorithm": legacy_enc.algorithm,
        "format_version": legacy_enc.format_version,
        "created_at": now,
        "updated_at": now,
    }

    client, _, registry = _make_test_client(db_conn=db_conn, settings=settings)
    with client:
        # GET does not leak legacy username in config or details
        detail = client.get(f"/api/v1/connections/{legacy_id}")
        assert detail.status_code == 200
        assert "username" not in detail.json()["config"]
        assert "username" not in detail.json()["details"]
        assert "ro_user" not in detail.text

        # Existing encrypted record still tests cleanly!
        test_res = client.post(f"/api/v1/connections/{legacy_id}/test")
        assert test_res.status_code == 200
        assert test_res.json()["success"] is True
        assert registry.dispatched_requests[-1].config.username == "ro_user"  # type: ignore[union-attr]

        # Legacy SQL row without encrypted username refuses silent keep overwrite
        keep_err = client.put(
            f"/api/v1/connections/{legacy_id}",
            json={"name": "legacy-pg-renamed", "credentialAction": "keep"},
        )
        assert keep_err.status_code == 422
        assert db_conn.connections[legacy_id]["config"]["username"] == "ro_user"

        # Explicit replace migrates credentials and cleans config JSONB
        migrated = client.put(
            f"/api/v1/connections/{legacy_id}",
            json={
                "name": "legacy-pg-migrated",
                "credentialAction": "replace",
                "credentials": {"username": "ro_user", "password": "pg_pw_v2"},
            },
        )
        assert migrated.status_code == 200
        assert "username" not in db_conn.connections[legacy_id]["config"]


def test_stateless_mode_remains_operational_for_task03() -> None:
    app = create_app(
        settings=Settings(database_url=None),
        registry=_RecordedDispatchRegistry(),
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        stateless_test = client.post(
            "/api/v1/connections/test",
            json={
                "connectorType": "PostgreSQL",
                "config": {
                    "host": "db.example.com",
                    "port": 5432,
                    "database": "app",
                    "username": "ro_user",
                },
                "credentials": {"password": "pg_pw_v1"},
            },
        )
        assert stateless_test.status_code == 200
        assert stateless_test.json()["success"] is True
        assert client.get("/api/v1/connections").status_code == 503


@pytest.mark.anyio
@pytest.mark.skipif(
    not os.environ.get("DATABRIDGE_TEST_DATABASE_URL"),
    reason="DATABRIDGE_TEST_DATABASE_URL not set; skipping live PostgreSQL CRUD integration test",
)
async def test_live_postgresql_crud_encryption_and_saved_test() -> None:
    """Real PostgreSQL integration test executed when DATABRIDGE_TEST_DATABASE_URL is set."""
    db_url = os.environ["DATABRIDGE_TEST_DATABASE_URL"]
    settings = Settings(
        database_url=db_url,  # type: ignore[arg-type]
        credential_encryption_keys=f'{{"k1":"{_KEY_V1_B64}","k2":"{_KEY_V2_B64}"}}',  # type: ignore[arg-type]
        credential_active_key_id="k1",
    )
    manager = DatabaseManager(settings)
    await manager.startup()
    try:
        runner = MigrationRunner(manager)
        await runner.apply_pending()

        registry = _RecordedDispatchRegistry()
        enc_svc = CredentialEncryptionService.from_settings(settings)
        app = create_app(
            settings=settings,
            registry=registry,
            database_manager=manager,
            credential_encryption_service=enc_svc,
        )

        unique_suffix = uuid.uuid4().hex[:8]
        with TestClient(app, raise_server_exceptions=False) as client:
            create_resp = client.post(
                "/api/v1/connections",
                json={
                    "name": f"live-pg-{unique_suffix}",
                    "connectorType": "PostgreSQL",
                    "config": {
                        "host": "db.live.internal",
                        "port": 5432,
                        "database": "analytics",
                    },
                    "credentials": {"username": "ro_live", "password": "pg_pw_v1"},
                },
            )
            assert create_resp.status_code == 201, create_resp.text
            conn_id = create_resp.json()["id"]
            assert "username" not in create_resp.json()["config"]
            assert "username" not in create_resp.json()["details"]
            assert "ro_live" not in create_resp.text

            cred_repo = CredentialRepository(manager)
            conn_repo = ConnectionRepository(manager)
            stored_conn = await conn_repo.get_by_id(conn_id)
            assert stored_conn is not None
            assert "username" not in stored_conn.config

            stored_cred = await cred_repo.get_by_connection_id(conn_id)
            assert stored_cred is not None
            assert stored_cred.encrypted.key_id == "k1"
            assert b"ro_live" not in stored_cred.encrypted.ciphertext
            assert b"pg_pw_v1" not in stored_cred.encrypted.ciphertext

            test_resp = client.post(f"/api/v1/connections/{conn_id}/test")
            assert test_resp.status_code == 200
            assert test_resp.json()["success"] is True

            saved = await conn_repo.get_by_id(conn_id)
            assert saved is not None and saved.status == "Connected"

            replace_resp = client.put(
                f"/api/v1/connections/{conn_id}",
                json={
                    "credentialAction": "replace",
                    "credentials": {"username": "ro_replaced", "password": "pg_pw_v2"},
                },
            )
            assert replace_resp.status_code == 200
            assert replace_resp.json()["status"] == "Disconnected"

            del_resp = client.delete(f"/api/v1/connections/{conn_id}")
            assert del_resp.status_code == 204
            assert await conn_repo.get_by_id(conn_id) is None
            assert await cred_repo.get_by_connection_id(conn_id) is None
    finally:
        await manager.shutdown()
