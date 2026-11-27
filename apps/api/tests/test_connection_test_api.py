"""API endpoint integration tests for POST /api/v1/connections/test."""

import logging
from datetime import datetime
from typing import Any

import httpx
import psycopg.errors
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.schemas.connection_test import (
    SUPPORTED_CONNECTOR_TYPES,
    ConnectionTestRequest,
    ConnectionTestResult,
)
from app.services.testers.csv_tester import CsvConnectionTester
from app.services.testers.json_tester import JsonConnectionTester
from app.services.testers.mongo_tester import MongoDbConnectionTester
from app.services.testers.mysql_tester import MysqlConnectionTester
from app.services.testers.postgres_tester import PostgresConnectionTester
from app.services.testers.registry import (
    ConnectorTesterRegistry,
    create_default_tester_registry,
)
from app.services.testers.rest_tester import RestApiConnectionTester


def _make_client(
    *,
    settings: Settings | None = None,
    registry: ConnectorTesterRegistry | None = None,
) -> TestClient:
    app = create_app(settings=settings or Settings(), registry=registry)
    return TestClient(app, raise_server_exceptions=False)


def _assert_normalized_result_shape(body: dict[str, object]) -> None:
    expected_allowed_keys = {"success", "status", "message", "errorCode", "durationMs", "testedAt"}
    assert set(body.keys()).issubset(expected_allowed_keys)
    assert isinstance(body["success"], bool)
    assert body["status"] in ("connected", "failed", "timeout")
    assert isinstance(body["message"], str) and len(body["message"]) > 0
    if "durationMs" in body:
        assert isinstance(body["durationMs"], int) and body["durationMs"] >= 0
    if "testedAt" in body:
        assert isinstance(body["testedAt"], str)
        datetime.fromisoformat(body["testedAt"])


def _make_mocked_full_registry() -> ConnectorTesterRegistry:
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

    async def fake_pg_connect(**_kwargs: Any) -> _PgConn:
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

    def fake_my_connect(**_kwargs: Any) -> _MyConn:
        return _MyConn()

    class _MongoAdmin:
        async def command(self, _cmd: str) -> dict[str, Any]:
            return {"ok": 1.0}

    class _MongoClient:
        admin = _MongoAdmin()

        async def close(self) -> None:
            return None

    async def fake_public_resolver(_host: str, _port: int) -> list[str]:
        return ["93.184.216.34"]

    registry = ConnectorTesterRegistry()
    registry.register(PostgresConnectionTester(connect_fn=fake_pg_connect))
    registry.register(MysqlConnectionTester(connect_fn=fake_my_connect))
    registry.register(MongoDbConnectionTester(client_factory=lambda **_kw: _MongoClient()))
    registry.register(
        RestApiConnectionTester(
            transport=httpx.MockTransport(lambda _req: httpx.Response(200, json={"ok": True})),
            resolver=fake_public_resolver,
        )
    )
    registry.register(CsvConnectionTester())
    registry.register(JsonConnectionTester())
    return registry


def test_default_registry_registers_all_six_connectors() -> None:
    registry = create_default_tester_registry()
    assert set(registry.registered_connectors()) == set(SUPPORTED_CONNECTOR_TYPES)
    assert isinstance(registry.get("PostgreSQL"), PostgresConnectionTester)
    assert isinstance(registry.get("MySQL"), MysqlConnectionTester)
    assert isinstance(registry.get("MongoDB"), MongoDbConnectionTester)
    assert isinstance(registry.get("REST API"), RestApiConnectionTester)
    assert isinstance(registry.get("CSV"), CsvConnectionTester)
    assert isinstance(registry.get("JSON"), JsonConnectionTester)


@pytest.mark.parametrize(
    "payload",
    [
        {
            "connectionId": "conn-pg-1",
            "connectorType": "PostgreSQL",
            "config": {
                "host": "db.internal.example.com",
                "port": 5432,
                "database": "orders",
                "username": "databridge_ro",
            },
            "credentials": {"password": "transient-pg-secret"},
        },
        {
            "connectorType": "MySQL",
            "config": {
                "host": "mysql.internal.example.com",
                "port": 3306,
                "database": "crm",
                "username": "crm_reader",
            },
        },
        {
            "connectorType": "MongoDB",
            "config": {
                "uri": "mongodb+srv://cluster01.mongodb.net",
                "database": "telemetry",
                "username": "reader",
            },
            "credentials": {
                "password": "transient-mongo-secret",
                "connectionUri": "mongodb+srv://reader:transient-mongo-secret@cluster01.mongodb.net",
            },
        },
        {
            "connectorType": "REST API",
            "config": {
                "baseUrl": "https://api.public-service.example/v2",
                "authType": "bearer",
            },
            "credentials": {"bearerToken": "transient-bearer-token-xyz"},
        },
        {
            "connectorType": "CSV",
            "config": {
                "fileName": "catalog-export.csv",
                "endpoint": "s3://partner-drops/catalog/catalog-export.csv",
            },
            "fileContent": "id,sku\n1,WIDGET-01\n",
        },
        {
            "connectorType": "JSON",
            "config": {
                "fileName": "edge-events.json",
                "endpoint": "local://edge-events.json",
            },
            "fileContent": '[{"event":"boot","ok":true}]',
        },
    ],
)
def test_all_six_connectors_dispatch_and_succeed_with_verified_testers(
    payload: dict[str, object],
) -> None:
    client = _make_client(registry=_make_mocked_full_registry())
    response = client.post("/api/v1/connections/test", json=payload)

    assert response.status_code == 200
    body = response.json()
    _assert_normalized_result_shape(body)
    assert body["success"] is True
    assert body["status"] == "connected"
    assert "errorCode" not in body


def test_empty_registry_never_returns_fake_success() -> None:
    client = _make_client(registry=ConnectorTesterRegistry())
    response = client.post(
        "/api/v1/connections/test",
        json={
            "connectorType": "REST API",
            "config": {"baseUrl": "https://api.public-service.example/v2", "authType": "none"},
        },
    )

    assert response.status_code == 501
    body = response.json()
    _assert_normalized_result_shape(body)
    assert body["success"] is False
    assert body["errorCode"] == "TESTER_NOT_IMPLEMENTED"


def test_invalid_connector_type_is_rejected() -> None:
    client = _make_client()
    response = client.post(
        "/api/v1/connections/test",
        json={
            "connectorType": "Snowflake",
            "config": {"host": "acct.snowflakecomputing.com"},
        },
    )

    assert response.status_code == 422
    body = response.json()
    _assert_normalized_result_shape(body)
    assert body["success"] is False
    assert body["status"] == "failed"
    assert body["errorCode"] == "UNSUPPORTED_CONNECTOR"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"connectorType": "PostgreSQL"},
        {"connectorType": "PostgreSQL", "config": {"host": "db.example.com", "port": 5432}},
        {"connectorType": "REST API", "config": {"authType": "none"}},
        {"connectorType": "CSV", "config": {}},
        {"connectorType": "CSV", "config": {"fileName": "orders.csv"}},
        {"connectorType": "JSON", "config": {"fileName": "events.json"}},
    ],
)
def test_missing_required_fields_are_rejected(payload: dict[str, object]) -> None:
    client = _make_client()
    response = client.post("/api/v1/connections/test", json=payload)

    assert response.status_code == 422
    body = response.json()
    _assert_normalized_result_shape(body)
    assert body["success"] is False
    assert body["errorCode"] == "INVALID_CONFIGURATION"


@pytest.mark.parametrize("bad_port", [0, -1, 65536, "not-a-port", True, 12.5])
def test_invalid_ports_are_rejected(bad_port: object) -> None:
    client = _make_client()
    response = client.post(
        "/api/v1/connections/test",
        json={
            "connectorType": "PostgreSQL",
            "config": {
                "host": "db.example.com",
                "port": bad_port,
                "database": "app",
                "username": "ro",
            },
        },
    )

    assert response.status_code == 422
    body = response.json()
    _assert_normalized_result_shape(body)
    assert body["success"] is False
    assert body["errorCode"] == "INVALID_CONFIGURATION"


@pytest.mark.parametrize(
    "bad_url",
    [
        "ftp://files.example.com",
        "not-a-url",
        "https://user:supersecret@api.example.com/v1",
        "http://",
    ],
)
def test_invalid_rest_urls_are_rejected_without_leaking_credentials(bad_url: str) -> None:
    client = _make_client()
    response = client.post(
        "/api/v1/connections/test",
        json={
            "connectorType": "REST API",
            "config": {
                "baseUrl": bad_url,
                "authType": "api_key",
            },
            "credentials": {"apiKey": "sk_live_never_echo_this"},
        },
    )

    assert response.status_code == 422
    body = response.json()
    _assert_normalized_result_shape(body)
    assert body["success"] is False
    assert body["errorCode"] == "INVALID_CONFIGURATION"
    assert "supersecret" not in response.text
    assert "sk_live_never_echo_this" not in response.text


def test_mongo_config_uri_rejects_embedded_credentials_without_leaking_them() -> None:
    client = _make_client()
    response = client.post(
        "/api/v1/connections/test",
        json={
            "connectorType": "MongoDB",
            "config": {
                "uri": "mongodb+srv://admin:hidden_mongo_pw@cluster01.mongodb.net",
                "database": "telemetry",
            },
        },
    )

    assert response.status_code == 422
    body = response.json()
    _assert_normalized_result_shape(body)
    assert body["errorCode"] == "INVALID_CONFIGURATION"
    assert "hidden_mongo_pw" not in response.text


def test_unknown_config_fields_and_file_credentials_are_rejected() -> None:
    client = _make_client()

    extra_field_resp = client.post(
        "/api/v1/connections/test",
        json={
            "connectorType": "PostgreSQL",
            "config": {
                "host": "db.example.com",
                "port": 5432,
                "database": "app",
                "username": "ro",
                "arbitrarySql": "DROP TABLE users;",
            },
        },
    )
    assert extra_field_resp.status_code == 422
    assert extra_field_resp.json()["errorCode"] == "INVALID_CONFIGURATION"

    csv_creds_resp = client.post(
        "/api/v1/connections/test",
        json={
            "connectorType": "CSV",
            "config": {"fileName": "orders.csv"},
            "fileContent": "id\n1\n",
            "credentials": {"password": "should-not-be-allowed"},
        },
    )
    assert csv_creds_resp.status_code == 422
    assert "should-not-be-allowed" not in csv_creds_resp.text


def test_malformed_json_payload_returns_normalized_400() -> None:
    client = _make_client()
    response = client.post(
        "/api/v1/connections/test",
        content=b"{not valid json",
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 400
    body = response.json()
    _assert_normalized_result_shape(body)
    assert body["success"] is False
    assert body["errorCode"] == "INVALID_CONFIGURATION"


def test_oversized_payload_returns_413() -> None:
    client = _make_client(settings=Settings(api_max_request_bytes=1024))
    oversized_name = "a" * 2048
    response = client.post(
        "/api/v1/connections/test",
        json={
            "connectorType": "REST API",
            "config": {"baseUrl": f"https://example.com/{oversized_name}", "authType": "none"},
        },
    )

    assert response.status_code == 413
    body = response.json()
    _assert_normalized_result_shape(body)
    assert body["success"] is False
    assert body["errorCode"] == "INVALID_CONFIGURATION"


def test_credentials_and_file_content_do_not_appear_in_logs_or_validation_errors(
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret_password = "ultra_secret_db_password_98765"
    secret_token = "bearer_secret_token_443322"
    secret_csv_value = "confidential_csv_cell_998877"

    async def failing_pg_connect(**_kwargs: Any) -> Any:
        raise psycopg.errors.InvalidPassword(
            f"password authentication failed for password={secret_password}"
        )

    registry = ConnectorTesterRegistry()
    registry.register(PostgresConnectionTester(connect_fn=failing_pg_connect))
    registry.register(CsvConnectionTester())

    with caplog.at_level(logging.DEBUG, logger="databridge.api"):
        client = _make_client(registry=registry)
        pg_resp = client.post(
            "/api/v1/connections/test",
            json={
                "connectorType": "PostgreSQL",
                "config": {
                    "host": "db.example.com",
                    "port": 5432,
                    "database": "app",
                    "username": "ro",
                },
                "credentials": {"password": secret_password},
            },
        )
        bad_resp = client.post(
            "/api/v1/connections/test",
            json={
                "connectorType": "REST API",
                "config": {"baseUrl": "invalid-url", "authType": "bearer"},
                "credentials": {"bearerToken": secret_token},
            },
        )
        csv_resp = client.post(
            "/api/v1/connections/test",
            json={
                "connectorType": "CSV",
                "config": {"fileName": "orders.csv"},
                "fileContent": f"id,name\n1,{secret_csv_value},extra_col\n",
            },
        )

    assert pg_resp.status_code == 200
    assert pg_resp.json()["errorCode"] == "AUTHENTICATION_FAILED"
    assert secret_password not in pg_resp.text
    assert secret_token not in bad_resp.text
    assert secret_csv_value not in csv_resp.text
    assert secret_password not in caplog.text
    assert secret_token not in caplog.text
    assert secret_csv_value not in caplog.text


def test_unhandled_tester_exception_is_sanitized_without_stack_trace() -> None:
    class ExplodingTester:
        connector_type = "PostgreSQL"

        async def test(
            self,
            _request: ConnectionTestRequest,
            _timeout_seconds: float,
        ) -> ConnectionTestResult:
            raise RuntimeError(
                "Traceback: /Users/dev/secret/db.py failed at 10.0.1.25 password=leaked_secret"
            )

    registry = ConnectorTesterRegistry()
    registry.register(ExplodingTester())  # type: ignore[arg-type]
    client = _make_client(registry=registry)

    response = client.post(
        "/api/v1/connections/test",
        json={
            "connectorType": "PostgreSQL",
            "config": {
                "host": "db.example.com",
                "port": 5432,
                "database": "app",
                "username": "ro",
            },
            "credentials": {"password": "leaked_secret"},
        },
    )

    assert response.status_code == 500
    body = response.json()
    _assert_normalized_result_shape(body)
    assert body["success"] is False
    assert body["errorCode"] == "SERVER_ERROR"
    assert "leaked_secret" not in response.text
    assert "10.0.1.25" not in response.text
    assert "/Users/dev" not in response.text
    assert "Traceback" not in response.text


def test_docs_and_health_endpoints_are_accessible() -> None:
    client = _make_client()
    assert client.get("/health").status_code == 200
    assert client.get("/docs").status_code == 200
    openapi = client.get("/openapi.json")
    assert openapi.status_code == 200
    assert "/api/v1/connections/test" in openapi.json()["paths"]


def test_cors_allows_configured_origin_and_blocks_wildcard() -> None:
    client = _make_client(settings=Settings(cors_allowed_origins=["https://app.databridge.local"]))

    allowed = client.options(
        "/api/v1/connections/test",
        headers={
            "Origin": "https://app.databridge.local",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert allowed.headers.get("access-control-allow-origin") == "https://app.databridge.local"

    disallowed = client.options(
        "/api/v1/connections/test",
        headers={
            "Origin": "https://evil.example.com",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert "access-control-allow-origin" not in disallowed.headers

    with pytest.raises(ValueError):
        Settings(cors_allowed_origins=["*"])
