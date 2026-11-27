"""Unit tests for the PostgreSQL connection tester."""

import socket
from typing import Any

import psycopg
import psycopg.errors
import pytest

from app.schemas.connection_test import (
    MysqlConnectionTestRequest,
    PostgresConnectionTestRequest,
    SqlConnectionTestConfig,
    SqlConnectionTestCredentials,
)
from app.services.testers.postgres_tester import PostgresConnectionTester


def _pg_request(*, password: str | None = "pg_secret_pw") -> PostgresConnectionTestRequest:
    return PostgresConnectionTestRequest(
        connectionId="conn-pg-1",
        connectorType="PostgreSQL",
        config=SqlConnectionTestConfig(
            host="pg.example.com",
            port=5432,
            database="orders",
            username="ro_user",
        ),
        credentials=SqlConnectionTestCredentials(password=password)
        if password is not None
        else None,
    )


class _FakeAsyncCursor:
    def __init__(
        self,
        *,
        row: tuple[Any, ...] | None = (1,),
        execute_error: BaseException | None = None,
        close_error: BaseException | None = None,
    ) -> None:
        self._row = row
        self._execute_error = execute_error
        self._close_error = close_error
        self.executed_queries: list[str] = []
        self.closed = False

    async def execute(self, query: str) -> None:
        self.executed_queries.append(query)
        if self._execute_error is not None:
            raise self._execute_error

    async def fetchone(self) -> tuple[Any, ...] | None:
        return self._row

    async def close(self) -> None:
        self.closed = True
        if self._close_error is not None:
            raise self._close_error


class _FakeAsyncConnection:
    def __init__(
        self,
        cursor: _FakeAsyncCursor,
        *,
        close_error: BaseException | None = None,
    ) -> None:
        self._cursor = cursor
        self._close_error = close_error
        self.closed = False

    def cursor(self) -> _FakeAsyncCursor:
        return self._cursor

    async def close(self) -> None:
        self.closed = True
        if self._close_error is not None:
            raise self._close_error


@pytest.mark.anyio
async def test_postgres_successful_connection_and_select_1() -> None:
    captured_kwargs: dict[str, Any] = {}
    fake_cursor = _FakeAsyncCursor(row=(1,))
    fake_conn = _FakeAsyncConnection(fake_cursor)

    async def fake_connect(**kwargs: Any) -> _FakeAsyncConnection:
        captured_kwargs.update(kwargs)
        return fake_conn

    tester = PostgresConnectionTester(connect_fn=fake_connect)
    result = await tester.test(_pg_request(), timeout_seconds=7.5)

    assert result.success is True
    assert result.status == "connected"
    assert result.errorCode is None
    assert fake_cursor.executed_queries == ["SELECT 1"]
    assert fake_cursor.closed is True
    assert fake_conn.closed is True
    assert captured_kwargs["host"] == "pg.example.com"
    assert captured_kwargs["port"] == 5432
    assert captured_kwargs["dbname"] == "orders"
    assert captured_kwargs["user"] == "ro_user"
    assert captured_kwargs["password"] == "pg_secret_pw"
    assert captured_kwargs["connect_timeout"] == 8
    assert "statement_timeout=7500" in captured_kwargs["options"]


@pytest.mark.anyio
async def test_postgres_passwordless_connection_omits_password_kwarg() -> None:
    captured_kwargs: dict[str, Any] = {}
    fake_conn = _FakeAsyncConnection(_FakeAsyncCursor(row=(1,)))

    async def fake_connect(**kwargs: Any) -> _FakeAsyncConnection:
        captured_kwargs.update(kwargs)
        return fake_conn

    tester = PostgresConnectionTester(connect_fn=fake_connect)
    result = await tester.test(_pg_request(password=None), timeout_seconds=5.0)

    assert result.success is True
    assert "password" not in captured_kwargs


@pytest.mark.anyio
async def test_postgres_authentication_failure() -> None:
    async def fake_connect(**_kwargs: Any) -> Any:
        raise psycopg.errors.InvalidPassword("password authentication failed for user ro_user")

    tester = PostgresConnectionTester(connect_fn=fake_connect)
    result = await tester.test(_pg_request(), timeout_seconds=5.0)

    assert result.success is False
    assert result.status == "failed"
    assert result.errorCode == "AUTHENTICATION_FAILED"
    assert "pg_secret_pw" not in result.message


@pytest.mark.anyio
async def test_postgres_connection_refused() -> None:
    async def fake_connect(**_kwargs: Any) -> Any:
        try:
            raise ConnectionRefusedError(61, "Connection refused")
        except ConnectionRefusedError as err:
            raise psycopg.OperationalError("connection refused") from err

    tester = PostgresConnectionTester(connect_fn=fake_connect)
    result = await tester.test(_pg_request(), timeout_seconds=5.0)

    assert result.success is False
    assert result.status == "failed"
    assert result.errorCode == "CONNECTION_REFUSED"


@pytest.mark.anyio
async def test_postgres_dns_failure() -> None:
    async def fake_connect(**_kwargs: Any) -> Any:
        try:
            raise socket.gaierror(8, "nodename nor servname provided")
        except socket.gaierror as err:
            raise psycopg.OperationalError("could not translate host name") from err

    tester = PostgresConnectionTester(connect_fn=fake_connect)
    result = await tester.test(_pg_request(), timeout_seconds=5.0)

    assert result.success is False
    assert result.status == "failed"
    assert result.errorCode == "DNS_RESOLUTION_FAILED"


@pytest.mark.anyio
async def test_postgres_timeout() -> None:
    async def fake_connect(**_kwargs: Any) -> Any:
        raise TimeoutError("connection timeout expired")

    tester = PostgresConnectionTester(connect_fn=fake_connect)
    result = await tester.test(_pg_request(), timeout_seconds=5.0)

    assert result.success is False
    assert result.status == "timeout"
    assert result.errorCode == "TIMEOUT"


@pytest.mark.anyio
async def test_postgres_query_failure_and_resource_cleanup() -> None:
    fake_cursor = _FakeAsyncCursor(
        execute_error=psycopg.errors.QueryCanceled("canceling statement due to statement timeout"),
        close_error=RuntimeError("cleanup cursor failed"),
    )
    fake_conn = _FakeAsyncConnection(
        fake_cursor,
        close_error=RuntimeError("cleanup conn failed"),
    )

    async def fake_connect(**_kwargs: Any) -> _FakeAsyncConnection:
        return fake_conn

    tester = PostgresConnectionTester(connect_fn=fake_connect)
    result = await tester.test(_pg_request(), timeout_seconds=5.0)

    assert result.success is False
    assert result.status == "timeout"
    assert result.errorCode == "TIMEOUT"
    assert fake_cursor.closed is True
    assert fake_conn.closed is True


@pytest.mark.anyio
async def test_postgres_unexpected_query_result_fails() -> None:
    fake_conn = _FakeAsyncConnection(_FakeAsyncCursor(row=(0,)))

    async def fake_connect(**_kwargs: Any) -> _FakeAsyncConnection:
        return fake_conn

    tester = PostgresConnectionTester(connect_fn=fake_connect)
    result = await tester.test(_pg_request(), timeout_seconds=5.0)

    assert result.success is False
    assert result.status == "failed"
    assert result.errorCode == "SERVER_ERROR"
    assert fake_conn.closed is True


@pytest.mark.anyio
async def test_postgres_rejects_mismatched_request_type() -> None:
    tester = PostgresConnectionTester()
    mysql_req = MysqlConnectionTestRequest(
        connectorType="MySQL",
        config=SqlConnectionTestConfig(
            host="mysql.example.com",
            port=3306,
            database="crm",
            username="ro",
        ),
    )
    result = await tester.test(mysql_req, timeout_seconds=5.0)
    assert result.success is False
    assert result.errorCode == "INVALID_CONFIGURATION"
