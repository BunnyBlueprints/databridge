"""Base repository conventions for parameterized async PostgreSQL access."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import psycopg
import psycopg.errors
import psycopg_pool

from app.db.errors import (
    DatabaseConflictError,
    DatabaseConstraintError,
    DatabaseError,
    DatabaseTimeoutError,
    DatabaseUnavailableError,
)
from app.db.pool import DatabaseManager

_UNIQUE_CONSTRAINT_MESSAGES: dict[str, tuple[str, str]] = {
    "uq_connections_lower_name": (
        "DUPLICATE_CONNECTION",
        "A connection with this name already exists.",
    ),
    "uq_mappings_lower_name": (
        "DUPLICATE_MAPPING",
        "A mapping with this name already exists.",
    ),
    "uq_mapping_rules_destination": (
        "DUPLICATE_DESTINATION_FIELD",
        "Duplicate mapping rule for destination field.",
    ),
    "uq_mapping_rules_ordinal": (
        "INVALID_MAPPING_RULE",
        "Duplicate ordinal position in mapping rules.",
    ),
    "uq_schema_objects_schema_object_key": (
        "INVALID_CONFIGURATION",
        "Duplicate object key in discovered schema snapshot.",
    ),
    "uq_schema_fields_object_path": (
        "INVALID_CONFIGURATION",
        "Duplicate field path in schema object.",
    ),
    "discovered_schemas_connection_id_key": (
        "INVALID_CONFIGURATION",
        "A discovered schema snapshot already exists for this connection.",
    ),
}

_CONNECTION_IN_USE_CONSTRAINTS = frozenset(
    {
        "mappings_source_connection_id_fkey",
        "mappings_destination_connection_id_fkey",
    }
)


def _extract_constraint_name(exc: psycopg.Error) -> str | None:
    diag = getattr(exc, "diag", None)
    raw = getattr(diag, "constraint_name", None) if diag is not None else None
    return str(raw) if raw else None


def translate_db_error(exc: Exception) -> DatabaseError:
    """Translate psycopg driver errors into safe application database errors."""
    if isinstance(exc, DatabaseError):
        return exc
    if isinstance(exc, (psycopg_pool.PoolTimeout, TimeoutError, psycopg.errors.QueryCanceled)):
        return DatabaseTimeoutError("Database operation timed out.")
    if isinstance(exc, psycopg.errors.UniqueViolation):
        constraint = _extract_constraint_name(exc)
        if constraint and constraint in _UNIQUE_CONSTRAINT_MESSAGES:
            error_code, message = _UNIQUE_CONSTRAINT_MESSAGES[constraint]
            return DatabaseConflictError(message, error_code=error_code)
        return DatabaseConflictError(
            "A connection or record with these unique values already exists."
        )
    if isinstance(exc, (psycopg.errors.ForeignKeyViolation, psycopg.errors.RestrictViolation)):
        constraint = _extract_constraint_name(exc)
        if constraint in _CONNECTION_IN_USE_CONSTRAINTS:
            return DatabaseConflictError(
                "Connection is referenced by one or more saved mappings and cannot be deleted.",
                error_code="CONNECTION_IN_USE",
            )
        return DatabaseConflictError(
            "Database integrity constraint violated.",
            error_code="INVALID_CONFIGURATION",
        )
    if isinstance(exc, (psycopg.errors.CheckViolation, psycopg.errors.NotNullViolation)):
        return DatabaseConstraintError("Database validation constraint violated.")
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
