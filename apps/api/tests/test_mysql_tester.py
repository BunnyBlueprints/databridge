"""Unit tests for the MySQL connection tester."""

import socket
from typing import Any

import pymysql.err
import pytest

from app.schemas.connection_test import (
    MysqlConnectionTestRequest,
    PostgresConnectionTestRequest,
    SqlConnectionTestConfig,
    SqlConnectionTestCredentials,
)
from app.services.testers.mysql_tester import MysqlConnectionTester


def _mysql_request(*, password: str | None = "mysql_secret_pw") -> MysqlConnectionTestRequest:
    return MysqlConnectionTestRequest(
        connectionId="conn-mysql-1",
        connectorType="MySQL",
        config=SqlConnectionTestConfig(
            host="mysql.example.com",
            port=3306,
            database="crm",
            username="crm_reader",
        ),
        credentials=SqlConnectionTestCredentials(password=password)
        if password is not None
        else None,
    )


class _FakeSyncCursor:
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

    def execute(self, query: str) -> None:
        self.executed_queries.append(query)
        if self._execute_error is not None:
            raise self._execute_error

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._row

    def close(self) -> None:
        self.closed = True
        if self._close_error is not None:
            raise self._close_error


class _FakeSyncConnection:
    def __init__(
        self,
        cursor: _FakeSyncCursor,
        *,
        close_error: BaseException | None = None,
    ) -> None:
        self._cursor = cursor
        self._close_error = close_error
        self.closed = False

    def cursor(self) -> _FakeSyncCursor:
        return self._cursor

    def close(self) -> None:
        self.closed = True
        if self._close_error is not None:
            raise self._close_error


@pytest.mark.anyio
async def test_mysql_successful_connection_and_select_1() -> None:
    captured_kwargs: dict[str, Any] = {}
    fake_cursor = _FakeSyncCursor(row=(1,))
    fake_conn = _FakeSyncConnection(fake_cursor)

    def fake_connect(**kwargs: Any) -> _FakeSyncConnection:
        captured_kwargs.update(kwargs)
        return fake_conn

    tester = MysqlConnectionTester(connect_fn=fake_connect)
    result = await tester.test(_mysql_request(), timeout_seconds=6.0)

    assert result.success is True
    assert result.status == "connected"
    assert result.errorCode is None
    assert fake_cursor.executed_queries == ["SELECT 1"]
    assert fake_cursor.closed is True
    assert fake_conn.closed is True
    assert captured_kwargs["host"] == "mysql.example.com"
    assert captured_kwargs["port"] == 3306
    assert captured_kwargs["database"] == "crm"
    assert captured_kwargs["user"] == "crm_reader"
    assert captured_kwargs["password"] == "mysql_secret_pw"
    assert captured_kwargs["connect_timeout"] == 6.0
    assert captured_kwargs["read_timeout"] == 6.0
    assert captured_kwargs["write_timeout"] == 6.0


@pytest.mark.anyio
async def test_mysql_authentication_failure() -> None:
    def fake_connect(**_kwargs: Any) -> Any:
        raise pymysql.err.OperationalError(1045, "Access denied for user 'crm_reader'")

    tester = MysqlConnectionTester(connect_fn=fake_connect)
    result = await tester.test(_mysql_request(), timeout_seconds=5.0)

    assert result.success is False
    assert result.status == "failed"
    assert result.errorCode == "AUTHENTICATION_FAILED"
    assert "mysql_secret_pw" not in result.message


@pytest.mark.anyio
async def test_mysql_connection_failure_refused_and_dns() -> None:
    def refused_connect(**_kwargs: Any) -> Any:
        try:
            raise ConnectionRefusedError(61, "Connection refused")
        except ConnectionRefusedError as err:
            raise pymysql.err.OperationalError(2003, "Can't connect to MySQL server") from err

    tester = MysqlConnectionTester(connect_fn=refused_connect)
    refused_result = await tester.test(_mysql_request(), timeout_seconds=5.0)
    assert refused_result.success is False
    assert refused_result.errorCode == "CONNECTION_REFUSED"

    def dns_connect(**_kwargs: Any) -> Any:
        try:
            raise socket.gaierror(8, "nodename nor servname provided")
        except socket.gaierror as err:
            raise pymysql.err.OperationalError(2005, "Unknown MySQL server host") from err

    dns_tester = MysqlConnectionTester(connect_fn=dns_connect)
    dns_result = await dns_tester.test(_mysql_request(), timeout_seconds=5.0)
    assert dns_result.success is False
    assert dns_result.errorCode == "DNS_RESOLUTION_FAILED"


@pytest.mark.anyio
async def test_mysql_timeout() -> None:
    def timeout_connect(**_kwargs: Any) -> Any:
        raise TimeoutError("timed out")

    tester = MysqlConnectionTester(connect_fn=timeout_connect)
    result = await tester.test(_mysql_request(), timeout_seconds=5.0)

    assert result.success is False
    assert result.status == "timeout"
    assert result.errorCode == "TIMEOUT"


@pytest.mark.anyio
async def test_mysql_query_failure_cleans_up_cursor_and_connection() -> None:
    fake_cursor = _FakeSyncCursor(
        execute_error=pymysql.err.ProgrammingError(1146, "Query execution error"),
        close_error=RuntimeError("cursor close failure"),
    )
    fake_conn = _FakeSyncConnection(
        fake_cursor,
        close_error=RuntimeError("conn close failure"),
    )

    def fake_connect(**_kwargs: Any) -> _FakeSyncConnection:
        return fake_conn

    tester = MysqlConnectionTester(connect_fn=fake_connect)
    result = await tester.test(_mysql_request(), timeout_seconds=5.0)

    assert result.success is False
    assert result.status == "failed"
    assert result.errorCode == "SERVER_ERROR"
    assert fake_cursor.closed is True
    assert fake_conn.closed is True


@pytest.mark.anyio
async def test_mysql_unexpected_query_result_fails() -> None:
    fake_cursor = _FakeSyncCursor(row=None)
    fake_conn = _FakeSyncConnection(fake_cursor)

    def fake_connect(**_kwargs: Any) -> _FakeSyncConnection:
        return fake_conn

    tester = MysqlConnectionTester(connect_fn=fake_connect)
    result = await tester.test(_mysql_request(password=None), timeout_seconds=5.0)

    assert result.success is False
    assert result.status == "failed"
    assert result.errorCode == "SERVER_ERROR"
    assert fake_cursor.closed is True
    assert fake_conn.closed is True


@pytest.mark.anyio
async def test_mysql_rejects_mismatched_request_type() -> None:
    tester = MysqlConnectionTester()
    pg_req = PostgresConnectionTestRequest(
        connectorType="PostgreSQL",
        config=SqlConnectionTestConfig(
            host="pg.example.com",
            port=5432,
            database="orders",
            username="ro",
        ),
    )
    result = await tester.test(pg_req, timeout_seconds=5.0)
    assert result.success is False
    assert result.errorCode == "INVALID_CONFIGURATION"
