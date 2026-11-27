"""Base repository conventions for parameterized async PostgreSQL access."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import psycopg
import psycopg.errors
import psycopg_pool

from app.db.errors import (
    DatabaseConflictError,
    DatabaseError,
    DatabaseTimeoutError,
    DatabaseUnavailableError,
)
from app.db.pool import DatabaseManager


def translate_db_error(exc: Exception) -> DatabaseError:
    """Translate psycopg driver errors into safe application database errors."""
    if isinstance(exc, DatabaseError):
        return exc
    if isinstance(exc, (psycopg_pool.PoolTimeout, TimeoutError, psycopg.errors.QueryCanceled)):
        return DatabaseTimeoutError("Database operation timed out.")
    if isinstance(exc, psycopg.errors.UniqueViolation):
        return DatabaseConflictError("A connection with this name already exists.")
    if isinstance(exc, psycopg.errors.ForeignKeyViolation):
        return DatabaseConflictError("Database integrity constraint violated.")
    if isinstance(exc, (psycopg.OperationalError, psycopg.InterfaceError)):
        return DatabaseUnavailableError("PostgreSQL database is unavailable.")
    if isinstance(exc, psycopg.Error):
        return DatabaseError("Database operation failed.")
    return DatabaseError("Unexpected database error occurred.")


class BaseRepository:
    """Base repository providing pooled connections, transactions, and safe error translation."""

    def __init__(self, db_manager: DatabaseManager) -> None:
        self._db = db_manager

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[psycopg.AsyncConnection[Any]]:
        """Acquire a pooled connection with consistent error translation."""
        try:
            async with self._db.connection() as conn:
                yield conn
        except DatabaseError:
            raise
        except (psycopg.Error, psycopg_pool.PoolTimeout, TimeoutError) as exc:
            raise translate_db_error(exc) from exc

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[psycopg.AsyncConnection[Any]]:
        """Acquire a pooled connection inside an explicit atomic psycopg transaction."""
        try:
            async with self._db.connection() as conn:
                async with conn.transaction():
                    yield conn
        except DatabaseError:
            raise
        except (psycopg.Error, psycopg_pool.PoolTimeout, TimeoutError) as exc:
            raise translate_db_error(exc) from exc
