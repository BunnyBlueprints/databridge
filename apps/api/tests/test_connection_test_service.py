"""Unit tests for ConnectionTestService dispatcher, timeout, and sanitization."""

import asyncio
import logging

import httpx
import pytest

from app.schemas.connection_test import (
    SUPPORTED_CONNECTOR_TYPES,
    ConnectionTestRequest,
    ConnectionTestResult,
    CsvConnectionTestConfig,
    CsvConnectionTestRequest,
    JsonConnectionTestConfig,
    JsonConnectionTestRequest,
    PostgresConnectionTestRequest,
    RestApiConnectionTestConfig,
    RestApiConnectionTestRequest,
    SqlConnectionTestConfig,
    SqlConnectionTestCredentials,
)
from app.services.connection_test_service import ConnectionTestService
from app.services.testers.registry import (
    ConnectorTesterRegistry,
    create_default_tester_registry,
)
from app.services.testers.rest_tester import RestApiConnectionTester


def _sample_pg_request() -> PostgresConnectionTestRequest:
    return PostgresConnectionTestRequest(
        connectionId="conn-pg-1",
        connectorType="PostgreSQL",
        config=SqlConnectionTestConfig(
            host="db.example.com",
            port=5432,
            database="orders",
            username="ro_user",
        ),
        credentials=SqlConnectionTestCredentials(password="top_secret_pw"),
    )


def _sample_rest_request() -> RestApiConnectionTestRequest:
    return RestApiConnectionTestRequest(
        connectionId="conn-rest-1",
        connectorType="REST API",
        config=RestApiConnectionTestConfig(
            baseUrl="https://api.public-service.example/v2",
            authType="none",
        ),
    )


@pytest.mark.anyio
async def test_default_registry_contains_all_six_supported_connectors() -> None:
    registry = create_default_tester_registry()
    assert set(registry.registered_connectors()) == set(SUPPORTED_CONNECTOR_TYPES)


@pytest.mark.anyio
async def test_empty_registry_returns_tester_not_implemented_for_all_types() -> None:
    service = ConnectionTestService(ConnectorTesterRegistry(), timeout_seconds=5.0)
    result, http_status = await service.test_connection(_sample_pg_request())

    assert http_status == 501
    assert result.success is False
    assert result.errorCode == "TESTER_NOT_IMPLEMENTED"
    assert result.testedAt is not None


@pytest.mark.anyio
async def test_service_dispatches_rest_csv_and_json_testers() -> None:
    async def public_resolver(_host: str, _port: int) -> list[str]:
        return ["93.184.216.34"]

    registry = create_default_tester_registry()
    registry.register(
        RestApiConnectionTester(
            transport=httpx.MockTransport(lambda _req: httpx.Response(200)),
            resolver=public_resolver,
        )
    )
    service = ConnectionTestService(registry, timeout_seconds=5.0)

    rest_res, rest_status = await service.test_connection(_sample_rest_request())
    assert rest_status == 200
    assert rest_res.success is True and rest_res.status == "connected"

    csv_res, csv_status = await service.test_connection(
        CsvConnectionTestRequest(
            connectorType="CSV",
            config=CsvConnectionTestConfig(fileName="orders.csv"),
            fileContent="id,total\n1,100\n",
        )
    )
    assert csv_status == 200
    assert csv_res.success is True and csv_res.status == "connected"

    json_res, json_status = await service.test_connection(
        JsonConnectionTestRequest(
            connectorType="JSON",
            config=JsonConnectionTestConfig(fileName="events.json"),
            fileContent='{"ok": true}',
        )
    )
    assert json_status == 200
    assert json_res.success is True and json_res.status == "connected"


@pytest.mark.anyio
async def test_registered_tester_is_dispatched_and_sanitized() -> None:
    class StubTester:
        connector_type = "PostgreSQL"

        async def test(
            self,
            _request: ConnectionTestRequest,
            _timeout_seconds: float,
        ) -> ConnectionTestResult:
            return ConnectionTestResult(
                success=False,
                status="failed",
                message="Auth failed at 192.168.10.5 with Bearer my_secret_jwt_123",
                errorCode="AUTHENTICATION_FAILED",
                durationMs=42,
            )

    registry = ConnectorTesterRegistry()
    registry.register(StubTester())  # type: ignore[arg-type]
    service = ConnectionTestService(registry, timeout_seconds=5.0)

    result, http_status = await service.test_connection(_sample_pg_request())

    assert http_status == 200
    assert result.success is False
    assert result.errorCode == "AUTHENTICATION_FAILED"
    assert "192.168.10.5" not in result.message
    assert "my_secret_jwt_123" not in result.message
    assert "[internal-host]" in result.message
    assert "[REDACTED]" in result.message


@pytest.mark.anyio
async def test_server_side_timeout_returns_504_timeout_result() -> None:
    class SlowTester:
        connector_type = "PostgreSQL"

        async def test(
            self,
            _request: ConnectionTestRequest,
            _timeout_seconds: float,
        ) -> ConnectionTestResult:
            await asyncio.sleep(1.0)
            return ConnectionTestResult(
                success=True,
                status="connected",
                message="Should never reach here",
            )

    registry = ConnectorTesterRegistry()
    registry.register(SlowTester())  # type: ignore[arg-type]
    service = ConnectionTestService(registry, timeout_seconds=0.05)

    result, http_status = await service.test_connection(_sample_pg_request())

    assert http_status == 504
    assert result.success is False
    assert result.status == "timeout"
    assert result.errorCode == "TIMEOUT"


@pytest.mark.anyio
async def test_service_never_logs_secret_credentials(
    caplog: pytest.LogCaptureFixture,
) -> None:
    service = ConnectionTestService(ConnectorTesterRegistry(), timeout_seconds=5.0)
    with caplog.at_level(logging.DEBUG, logger="databridge.api"):
        await service.test_connection(_sample_pg_request())

    assert "top_secret_pw" not in caplog.text
