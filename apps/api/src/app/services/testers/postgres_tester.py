"""PostgreSQL read-only connectivity tester using psycopg 3 async connections."""

import logging
import math
import socket
from collections.abc import Awaitable, Callable
from typing import Any

import psycopg
import psycopg.errors

from app.schemas.connection_test import (
    ConnectionTestErrorCode,
    ConnectionTestRequest,
    ConnectionTestResult,
    ConnectorType,
    PostgresConnectionTestRequest,
)
from app.services.testers.common import (
    build_success_result,
    build_tester_failure,
    elapsed_ms,
    iter_exception_chain,
)

logger = logging.getLogger("databridge.api")

ConnectFn = Callable[..., Awaitable[Any]]


def _classify_postgres_error(exc: BaseException) -> tuple[ConnectionTestErrorCode, str, bool]:
    for cause in iter_exception_chain(exc):
        if isinstance(cause, (TimeoutError, psycopg.errors.QueryCanceled)):
            return (
                "TIMEOUT",
                "PostgreSQL connection test timed out before the server responded.",
                True,
            )
        if isinstance(
            cause,
            (
                psycopg.errors.InvalidPassword,
                psycopg.errors.InvalidAuthorizationSpecification,
            ),
        ):
            return (
                "AUTHENTICATION_FAILED",
                "PostgreSQL authentication failed. Verify the username and password.",
                False,
            )
        if isinstance(cause, psycopg.errors.InvalidCatalogName):
            return (
                "INVALID_CONFIGURATION",
                "Target PostgreSQL database was not found or is inaccessible.",
                False,
            )
        if isinstance(cause, ConnectionRefusedError):
            return (
                "CONNECTION_REFUSED",
                "Unable to connect to PostgreSQL. Connection was refused by the target host.",
                False,
            )
        if isinstance(cause, socket.gaierror):
            return (
                "DNS_RESOLUTION_FAILED",
                "Unable to resolve the PostgreSQL hostname. Verify the host address.",
                False,
            )

    if isinstance(exc, psycopg.OperationalError):
        detail = str(exc).lower()
        if any(
            token in detail
            for token in (
                "password authentication failed",
                "authentication failed",
                "no password supplied",
                "fe_sendauth",
            )
        ):
            return (
                "AUTHENTICATION_FAILED",
                "PostgreSQL authentication failed. Verify the username and password.",
                False,
            )
        if "timeout expired" in detail or "timed out" in detail:
            return (
                "TIMEOUT",
                "PostgreSQL connection test timed out before the server responded.",
                True,
            )
        if "connection refused" in detail:
            return (
                "CONNECTION_REFUSED",
                "Unable to connect to PostgreSQL. Connection was refused by the target host.",
                False,
            )
        if any(
            token in detail
            for token in (
                "could not translate host name",
                "name or service not known",
                "nodename nor servname",
                "temporary failure in name resolution",
            )
        ):
            return (
                "DNS_RESOLUTION_FAILED",
                "Unable to resolve the PostgreSQL hostname. Verify the host address.",
                False,
            )
        if any(
            token in detail
            for token in (
                "network is unreachable",
                "no route to host",
                "could not connect to server",
            )
        ):
            return (
                "NETWORK_ERROR",
                "Unable to reach the PostgreSQL server over the network.",
                False,
            )
        return (
            "SERVER_ERROR",
            "PostgreSQL connection check failed.",
            False,
        )

    if isinstance(exc, psycopg.Error):
        return (
            "SERVER_ERROR",
            "PostgreSQL query verification failed.",
            False,
        )

    return (
        "SERVER_ERROR",
        "An unexpected error occurred while testing the PostgreSQL connection.",
        False,
    )


class PostgresConnectionTester:
    """Performs a read-only SELECT 1 connectivity check against PostgreSQL."""

    connector_type: ConnectorType = "PostgreSQL"

    def __init__(self, connect_fn: ConnectFn | None = None) -> None:
        self._connect_fn: ConnectFn = connect_fn or psycopg.AsyncConnection.connect

    async def test(
        self,
        request: ConnectionTestRequest,
        timeout_seconds: float,
    ) -> ConnectionTestResult:
        if not isinstance(request, PostgresConnectionTestRequest):
            return build_tester_failure(
                message="Invalid request type dispatched to PostgreSQL tester.",
                error_code="INVALID_CONFIGURATION",
                duration_ms=0,
            )

        import time

        started = time.monotonic()
        connect_timeout = max(1, math.ceil(timeout_seconds))
        statement_timeout_ms = max(1, int(timeout_seconds * 1000))

        connect_kwargs: dict[str, Any] = {
            "host": request.config.host,
            "port": request.config.port,
            "dbname": request.config.database,
            "user": request.config.username,
            "connect_timeout": connect_timeout,
            "options": f"-c statement_timeout={statement_timeout_ms}",
            "autocommit": True,
        }
        if request.credentials and request.credentials.password is not None:
            connect_kwargs["password"] = request.credentials.password.get_secret_value()

        conn: Any = None
        cursor: Any = None
        try:
            conn = await self._connect_fn(**connect_kwargs)
            cursor = conn.cursor()
            await cursor.execute("SELECT 1")
            row = await cursor.fetchone()
            if row is None or row[0] != 1:
                return build_tester_failure(
                    message="PostgreSQL connectivity verification returned an unexpected result.",
                    error_code="SERVER_ERROR",
                    duration_ms=elapsed_ms(started),
                )
            return build_success_result(
                "PostgreSQL connection established and verified with SELECT 1.",
                elapsed_ms(started),
            )
        except Exception as exc:
            error_code, safe_message, is_timeout = _classify_postgres_error(exc)
            return build_tester_failure(
                message=safe_message,
                error_code=error_code,
                duration_ms=elapsed_ms(started),
                is_timeout=is_timeout,
            )
        finally:
            if cursor is not None:
                try:
                    await cursor.close()
                except Exception as cleanup_exc:
                    logger.debug(
                        "PostgreSQL cursor cleanup failed: %s",
                        type(cleanup_exc).__name__,
                    )
            if conn is not None:
                try:
                    await conn.close()
                except Exception as cleanup_exc:
                    logger.debug(
                        "PostgreSQL connection cleanup failed: %s",
                        type(cleanup_exc).__name__,
                    )
