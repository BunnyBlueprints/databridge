"""Unit tests for ConnectionTestService dispatcher, timeout, and sanitization."""

import asyncio
import logging

import pytest

from app.schemas.connection_test import (
    ConnectionTestRequest,
    ConnectionTestResult,
    PostgresConnectionTestRequest,
    SqlConnectionTestConfig,
    SqlConnectionTestCredentials,
)
from app.services.connection_test_service import ConnectionTestService
from app.services.testers.registry import (
    ConnectorTesterRegistry,
    create_default_tester_registry,
)


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


@pytest.mark.anyio
async def test_default_registry_returns_tester_not_implemented() -> None:
    service = ConnectionTestService(create_default_tester_registry(), timeout_seconds=5.0)
    result, http_status = await service.test_connection(_sample_pg_request())

    assert http_status == 501
    assert result.success is False
    assert result.status == "failed"
    assert result.errorCode == "TESTER_NOT_IMPLEMENTED"
    assert result.testedAt is not None


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
    service = ConnectionTestService(create_default_tester_registry(), timeout_seconds=5.0)
    with caplog.at_level(logging.DEBUG, logger="databridge.api"):
        await service.test_connection(_sample_pg_request())

    assert "top_secret_pw" not in caplog.text
