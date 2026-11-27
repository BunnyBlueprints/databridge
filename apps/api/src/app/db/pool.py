"""Asynchronous PostgreSQL connection pool lifecycle and safe acquisition."""

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

import psycopg
import psycopg_pool
from fastapi import Request

from app.core.config import Settings
from app.db.errors import (
    DatabaseNotConfiguredError,
    DatabaseTimeoutError,
    DatabaseUnavailableError,
)

logger = logging.getLogger("databridge.api")

PoolFactory = Callable[..., Any]


def _default_pool_factory(
    *,
    conninfo: str,
    min_size: int,
    max_size: int,
    timeout: float,
    connect_timeout: int,
    health_check_enabled: bool,
) -> psycopg_pool.AsyncConnectionPool[psycopg.AsyncConnection[Any]]:
    return psycopg_pool.AsyncConnectionPool(
        conninfo=conninfo,
        min_size=min_size,
        max_size=max_size,
        timeout=timeout,
        open=False,
        check=psycopg_pool.AsyncConnectionPool.check_connection if health_check_enabled else None,
        kwargs={"connect_timeout": connect_timeout},
    )


class DatabaseManager:
    """Manages an AsyncConnectionPool tied to the FastAPI application lifespan."""

    def __init__(
        self,
        settings: Settings,
        *,
        pool_factory: PoolFactory | None = None,
    ) -> None:
        self._settings = settings
        self._pool_factory: PoolFactory = pool_factory or _default_pool_factory
        self._pool: Any = None
        self._is_open = False

    @property
    def is_configured(self) -> bool:
        """Return True if DATABASE_URL is configured."""
        return self._settings.database_enabled

    @property
    def is_open(self) -> bool:
        """Return True if the connection pool is currently open."""
        return self._is_open and self._pool is not None

    async def startup(self) -> None:
        """Initialize and verify the PostgreSQL connection pool when configured."""
        if not self.is_configured or self._settings.database_url is None:
            logger.info("PostgreSQL persistence disabled (DATABASE_URL not configured).")
            return

        if self._is_open and self._pool is not None:
            return

        pool = self._pool_factory(
            conninfo=self._settings.database_url.get_secret_value(),
            min_size=self._settings.database_pool_min_size,
            max_size=self._settings.database_pool_max_size,
            timeout=self._settings.database_pool_timeout_seconds,
            connect_timeout=self._settings.database_connect_timeout_seconds,
            health_check_enabled=self._settings.database_health_check_enabled,
        )
        self._pool = pool

        try:
            await pool.open(
                wait=True,
                timeout=self._settings.database_pool_timeout_seconds,
            )
            self._is_open = True
            async with self.connection() as conn:
                await conn.execute("SELECT 1")
            logger.info(
                "PostgreSQL connection pool initialized (min=%d, max=%d).",
                self._settings.database_pool_min_size,
                self._settings.database_pool_max_size,
            )
        except Exception as exc:
            await self.shutdown()
            logger.error(
                "Failed to initialize PostgreSQL connection pool: %s",
                type(exc).__name__,
            )
            if isinstance(exc, DatabaseTimeoutError):
                raise
            raise DatabaseUnavailableError(
                "Unable to connect to the configured PostgreSQL database."
            ) from exc

    async def shutdown(self) -> None:
        """Close the PostgreSQL connection pool cleanly."""
        pool = self._pool
        self._pool = None
        self._is_open = False
        if pool is not None:
            try:
                await pool.close()
            except Exception as exc:
                logger.warning(
                    "Error while closing PostgreSQL connection pool: %s",
                    type(exc).__name__,
                )

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[psycopg.AsyncConnection[Any]]:
        """Acquire a pooled PostgreSQL connection and return it cleanly on exit."""
        if not self.is_configured:
            raise DatabaseNotConfiguredError("Database persistence is not configured.")
        if not self.is_open or self._pool is None:
            raise DatabaseUnavailableError("PostgreSQL connection pool is not initialized.")

        try:
            async with self._pool.connection(
                timeout=self._settings.database_pool_timeout_seconds
            ) as conn:
                yield conn
        except (psycopg_pool.PoolTimeout, TimeoutError) as exc:
            raise DatabaseTimeoutError(
                "Timed out waiting for a PostgreSQL connection from the pool."
            ) from exc
        except (psycopg.OperationalError, psycopg.InterfaceError) as exc:
            raise DatabaseUnavailableError(
                "PostgreSQL database connection is unavailable."
            ) from exc

    async def check_readiness(self) -> tuple[str, str]:
        """Verify database readiness without exposing connection strings or secrets."""
        if not self.is_configured:
            return "disabled", "Database persistence is disabled."
        if not self.is_open:
            return "unavailable", "PostgreSQL connection pool is not ready."

        try:
            async with self.connection() as conn:
                cursor = await conn.execute("SELECT 1")
                row = await cursor.fetchone()
                if row is None or row[0] != 1:
                    return "unavailable", "PostgreSQL readiness query failed."
            return "ready", "PostgreSQL connection pool is ready."
        except (DatabaseTimeoutError, DatabaseUnavailableError, psycopg.Error) as exc:
            logger.warning("PostgreSQL readiness check failed: %s", type(exc).__name__)
            return "unavailable", "PostgreSQL database is unavailable."


def get_database_manager(request: Request) -> DatabaseManager:
    """FastAPI dependency returning the application's DatabaseManager."""
    return request.app.state.database_manager
