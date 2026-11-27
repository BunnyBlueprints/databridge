"""MySQL read-only connectivity tester using PyMySQL in a bounded worker thread."""

import asyncio
import logging
import socket
import time
from collections.abc import Callable
from typing import Any

import pymysql
import pymysql.err

from app.schemas.connection_test import (
    ConnectionTestErrorCode,
    ConnectionTestRequest,
    ConnectionTestResult,
    ConnectorType,
    MysqlConnectionTestRequest,
)
from app.services.testers.common import (
    build_success_result,
    build_tester_failure,
    elapsed_ms,
    iter_exception_chain,
)

logger = logging.getLogger("databridge.api")

MysqlConnectFn = Callable[..., Any]

_MYSQL_AUTH_ERROR_CODES = {1044, 1045, 1251, 1698}
_MYSQL_BAD_DB_ERROR_CODES = {1049}
_MYSQL_DNS_ERROR_CODES = {2005}
_MYSQL_TIMEOUT_ERROR_CODES = {1205, 3024}
_MYSQL_NETWORK_ERROR_CODES = {2002, 2003, 2006, 2013}


def _classify_mysql_error(exc: BaseException) -> tuple[ConnectionTestErrorCode, str, bool]:
    for cause in iter_exception_chain(exc):
        if isinstance(cause, TimeoutError):
            return (
                "TIMEOUT",
                "MySQL connection test timed out before the server responded.",
                True,
            )
        if isinstance(cause, ConnectionRefusedError):
            return (
                "CONNECTION_REFUSED",
                "Unable to connect to MySQL. Connection was refused by the target host.",
                False,
            )
        if isinstance(cause, socket.gaierror):
            return (
                "DNS_RESOLUTION_FAILED",
                "Unable to resolve the MySQL hostname. Verify the host address.",
                False,
            )

    if isinstance(exc, pymysql.err.MySQLError):
        code = exc.args[0] if exc.args and isinstance(exc.args[0], int) else None
        detail = str(exc.args[1]).lower() if len(exc.args) > 1 else str(exc).lower()

        if code in _MYSQL_AUTH_ERROR_CODES:
            return (
                "AUTHENTICATION_FAILED",
                "MySQL authentication failed. Verify the username and password.",
                False,
            )
        if code in _MYSQL_BAD_DB_ERROR_CODES:
            return (
                "INVALID_CONFIGURATION",
                "Target MySQL database was not found or is inaccessible.",
                False,
            )
        if code in _MYSQL_DNS_ERROR_CODES:
            return (
                "DNS_RESOLUTION_FAILED",
                "Unable to resolve the MySQL hostname. Verify the host address.",
                False,
            )
        if code in _MYSQL_TIMEOUT_ERROR_CODES or "timed out" in detail:
            return (
                "TIMEOUT",
                "MySQL connection test timed out before the server responded.",
                True,
            )
        if code in _MYSQL_NETWORK_ERROR_CODES:
            if "connection refused" in detail:
                return (
                    "CONNECTION_REFUSED",
                    "Unable to connect to MySQL. Connection was refused by the target host.",
                    False,
                )
            if "name or service not known" in detail or "nodename nor servname" in detail:
                return (
                    "DNS_RESOLUTION_FAILED",
                    "Unable to resolve the MySQL hostname. Verify the host address.",
                    False,
                )
            return (
                "NETWORK_ERROR",
                "Unable to reach the MySQL server over the network.",
                False,
            )
        return (
            "SERVER_ERROR",
            "MySQL query verification failed.",
            False,
        )

    return (
        "SERVER_ERROR",
        "An unexpected error occurred while testing the MySQL connection.",
        False,
    )


def _execute_mysql_check(
    connect_fn: MysqlConnectFn,
    connect_kwargs: dict[str, Any],
) -> bool:
    conn: Any = None
    cursor: Any = None
    try:
        conn = connect_fn(**connect_kwargs)
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        row = cursor.fetchone()
        return row is not None and row[0] == 1
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception as cleanup_exc:
                logger.debug(
                    "MySQL cursor cleanup failed: %s",
                    type(cleanup_exc).__name__,
                )
        if conn is not None:
            try:
                conn.close()
            except Exception as cleanup_exc:
                logger.debug(
                    "MySQL connection cleanup failed: %s",
                    type(cleanup_exc).__name__,
                )


class MysqlConnectionTester:
    """Performs a read-only SELECT 1 connectivity check against MySQL."""

    connector_type: ConnectorType = "MySQL"

    def __init__(self, connect_fn: MysqlConnectFn | None = None) -> None:
        self._connect_fn: MysqlConnectFn = connect_fn or pymysql.connect

    async def test(
        self,
        request: ConnectionTestRequest,
        timeout_seconds: float,
    ) -> ConnectionTestResult:
        if not isinstance(request, MysqlConnectionTestRequest):
            return build_tester_failure(
                message="Invalid request type dispatched to MySQL tester.",
                error_code="INVALID_CONFIGURATION",
                duration_ms=0,
            )

        started = time.monotonic()
        driver_timeout = max(0.1, float(timeout_seconds))
        connect_kwargs: dict[str, Any] = {
            "host": request.config.host,
            "port": request.config.port,
            "user": request.config.username,
            "database": request.config.database,
            "password": (
                request.credentials.password.get_secret_value()
                if request.credentials and request.credentials.password is not None
                else ""
            ),
            "connect_timeout": driver_timeout,
            "read_timeout": driver_timeout,
            "write_timeout": driver_timeout,
            "autocommit": True,
        }

        try:
            verified = await asyncio.to_thread(
                _execute_mysql_check,
                self._connect_fn,
                connect_kwargs,
            )
            if not verified:
                return build_tester_failure(
                    message="MySQL connectivity verification returned an unexpected result.",
                    error_code="SERVER_ERROR",
                    duration_ms=elapsed_ms(started),
                )
            return build_success_result(
                "MySQL connection established and verified with SELECT 1.",
                elapsed_ms(started),
            )
        except Exception as exc:
            error_code, safe_message, is_timeout = _classify_mysql_error(exc)
            return build_tester_failure(
                message=safe_message,
                error_code=error_code,
                duration_ms=elapsed_ms(started),
                is_timeout=is_timeout,
            )
