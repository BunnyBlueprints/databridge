"""Asynchronous PostgreSQL repository for persistent connection records."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from psycopg.types.json import Jsonb

from app.db.errors import DatabaseError
from app.repositories.base import BaseRepository, translate_db_error
from app.schemas.connection_test import ConnectionTestTerminalStatus, ConnectorType
from app.schemas.connections import ConnectionStatus

_INSERT_CONNECTION_SQL = """
    INSERT INTO connections (
        id,
        name,
        connector_type,
        description,
        endpoint,
        config,
        auth_configured,
        status
    )
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    RETURNING
        id, name, connector_type, description, endpoint, config, auth_configured,
        status, last_tested_at, last_test_status, last_error_message,
        last_successful_sync, sync_frequency, created_at, updated_at
"""

_SELECT_BY_ID_SQL = """
    SELECT
        id, name, connector_type, description, endpoint, config, auth_configured,
        status, last_tested_at, last_test_status, last_error_message,
        last_successful_sync, sync_frequency, created_at, updated_at
    FROM connections
    WHERE id = %s
"""

_SELECT_BY_ID_FOR_UPDATE_SQL = """
    SELECT
        id, name, connector_type, description, endpoint, config, auth_configured,
        status, last_tested_at, last_test_status, last_error_message,
        last_successful_sync, sync_frequency, created_at, updated_at
    FROM connections
    WHERE id = %s
    FOR UPDATE
"""

_COUNT_CONNECTIONS_SQL = """
    SELECT COUNT(*)
    FROM connections
    WHERE (%s::text IS NULL OR status = %s)
      AND (%s::text IS NULL OR connector_type = %s)
      AND (
          %s::text IS NULL
          OR name ILIKE %s ESCAPE '\\'
          OR endpoint ILIKE %s ESCAPE '\\'
          OR COALESCE(description, '') ILIKE %s ESCAPE '\\'
      )
"""

_LIST_CONNECTIONS_SQL = """
    SELECT
        id, name, connector_type, description, endpoint, config, auth_configured,
        status, last_tested_at, last_test_status, last_error_message,
        last_successful_sync, sync_frequency, created_at, updated_at
    FROM connections
    WHERE (%s::text IS NULL OR status = %s)
      AND (%s::text IS NULL OR connector_type = %s)
      AND (
          %s::text IS NULL
          OR name ILIKE %s ESCAPE '\\'
          OR endpoint ILIKE %s ESCAPE '\\'
          OR COALESCE(description, '') ILIKE %s ESCAPE '\\'
      )
    ORDER BY created_at DESC, id DESC
    LIMIT %s OFFSET %s
"""

_UPDATE_CONNECTION_SQL = """
    UPDATE connections
    SET
        name = %s,
        description = %s,
        endpoint = %s,
        config = %s,
        auth_configured = %s,
        status = %s,
        last_error_message = CASE WHEN %s THEN NULL ELSE last_error_message END,
        updated_at = NOW()
    WHERE id = %s
    RETURNING
        id, name, connector_type, description, endpoint, config, auth_configured,
        status, last_tested_at, last_test_status, last_error_message,
        last_successful_sync, sync_frequency, created_at, updated_at
"""

_UPDATE_TEST_OUTCOME_SQL = """
    UPDATE connections
    SET
        status = %s,
        last_tested_at = %s,
        last_test_status = %s,
        last_error_message = %s,
        updated_at = NOW()
    WHERE id = %s
    RETURNING
        id, name, connector_type, description, endpoint, config, auth_configured,
        status, last_tested_at, last_test_status, last_error_message,
        last_successful_sync, sync_frequency, created_at, updated_at
"""

_DELETE_CONNECTION_SQL = """
    DELETE FROM connections
    WHERE id = %s
    RETURNING id
"""


@dataclass(frozen=True)
class ConnectionRecord:
    """Internal database domain representation for a row in `connections`."""

    id: str
    name: str
    connector_type: ConnectorType
    description: str | None
    endpoint: str
    config: dict[str, Any]
    auth_configured: bool
    status: ConnectionStatus
    last_tested_at: str | None
    last_test_status: ConnectionTestTerminalStatus | None
    last_error_message: str | None
    last_successful_sync: str | None
    sync_frequency: str | None
    created_at: str
    updated_at: str


def _to_iso_string(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return dt.astimezone(UTC).isoformat()
    return str(value)


def _escape_like_pattern(query_text: str) -> str:
    return query_text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _row_to_connection_record(row: tuple[Any, ...]) -> ConnectionRecord:
    return ConnectionRecord(
        id=str(row[0]),
        name=str(row[1]),
        connector_type=cast(ConnectorType, str(row[2])),
        description=str(row[3]) if row[3] is not None else None,
        endpoint=str(row[4]),
        config=dict(row[5]) if isinstance(row[5], dict) else {},
        auth_configured=bool(row[6]),
        status=cast(ConnectionStatus, str(row[7])),
        last_tested_at=_to_iso_string(row[8]),
        last_test_status=(
            cast(ConnectionTestTerminalStatus, str(row[9])) if row[9] is not None else None
        ),
        last_error_message=str(row[10]) if row[10] is not None else None,
        last_successful_sync=_to_iso_string(row[11]),
        sync_frequency=str(row[12]) if row[12] is not None else None,
        created_at=_to_iso_string(row[13]) or "",
        updated_at=_to_iso_string(row[14]) or "",
    )


class ConnectionRepository(BaseRepository):
    """SQL repository managing persistent rows in `connections` (never handles secrets)."""

    async def create(
        self,
        *,
        connection_id: str,
        name: str,
        connector_type: ConnectorType,
        description: str | None,
        endpoint: str,
        config: dict[str, Any],
        auth_configured: bool,
        status: ConnectionStatus = "Disconnected",
        conn: Any | None = None,
    ) -> ConnectionRecord:
        """Insert a new connection record and return the stored domain row."""
        params = (
            connection_id,
            name,
            connector_type,
            description,
            endpoint,
            Jsonb(config),
            auth_configured,
            status,
        )
        return await self._execute_single_row(_INSERT_CONNECTION_SQL, params, conn=conn)

    async def get_by_id(
        self,
        connection_id: str,
        *,
        for_update: bool = False,
        conn: Any | None = None,
    ) -> ConnectionRecord | None:
        """Load one connection record by UUID, or None if not found."""
        query = _SELECT_BY_ID_FOR_UPDATE_SQL if for_update else _SELECT_BY_ID_SQL
        if conn is not None:
            return await self._fetch_optional_row(conn, query, (connection_id,))
        async with self.connection() as pooled_conn:
            return await self._fetch_optional_row(pooled_conn, query, (connection_id,))

    async def list_connections(
        self,
        *,
        status_filter: ConnectionStatus | None = None,
        type_filter: ConnectorType | None = None,
        search_query: str | None = None,
        limit: int = 50,
        offset: int = 0,
        conn: Any | None = None,
    ) -> tuple[list[ConnectionRecord], int]:
        """Return filtered connections in deterministic order alongside total match count."""
        cleaned_search = (
            f"%{_escape_like_pattern(search_query.strip())}%"
            if search_query and search_query.strip()
            else None
        )
        filter_params = (
            status_filter,
            status_filter,
            type_filter,
            type_filter,
            cleaned_search,
            cleaned_search,
            cleaned_search,
            cleaned_search,
        )

        async def _run(active_conn: Any) -> tuple[list[ConnectionRecord], int]:
            try:
                count_cur = await active_conn.execute(_COUNT_CONNECTIONS_SQL, filter_params)
                count_row = await count_cur.fetchone()
                total = int(count_row[0]) if count_row is not None else 0

                list_cur = await active_conn.execute(
                    _LIST_CONNECTIONS_SQL,
                    (*filter_params, limit, offset),
                )
                rows = await list_cur.fetchall()
                return [_row_to_connection_record(row) for row in rows], total
            except DatabaseError:
                raise
            except Exception as exc:
                raise translate_db_error(exc) from exc

        if conn is not None:
            return await _run(conn)
        async with self.connection() as pooled_conn:
            return await _run(pooled_conn)

    async def update(
        self,
        *,
        connection_id: str,
        name: str,
        description: str | None,
        endpoint: str,
        config: dict[str, Any],
        auth_configured: bool,
        status: ConnectionStatus,
        clear_error_message: bool,
        conn: Any | None = None,
    ) -> ConnectionRecord | None:
        """Update connection fields and return the updated record, or None if missing."""
        params = (
            name,
            description,
            endpoint,
            Jsonb(config),
            auth_configured,
            status,
            clear_error_message,
            connection_id,
        )
        if conn is not None:
            return await self._fetch_optional_row(conn, _UPDATE_CONNECTION_SQL, params)
        async with self.connection() as pooled_conn:
            return await self._fetch_optional_row(pooled_conn, _UPDATE_CONNECTION_SQL, params)

    async def update_test_outcome(
        self,
        *,
        connection_id: str,
        status: ConnectionStatus,
        last_tested_at: datetime,
        last_test_status: ConnectionTestTerminalStatus,
        last_error_message: str | None,
        conn: Any | None = None,
    ) -> ConnectionRecord | None:
        """Persist connectivity test outcome and status transition on a connection row."""
        params = (
            status,
            last_tested_at,
            last_test_status,
            last_error_message,
            connection_id,
        )
        if conn is not None:
            return await self._fetch_optional_row(conn, _UPDATE_TEST_OUTCOME_SQL, params)
        async with self.connection() as pooled_conn:
            return await self._fetch_optional_row(pooled_conn, _UPDATE_TEST_OUTCOME_SQL, params)

    async def delete(
        self,
        connection_id: str,
        *,
        conn: Any | None = None,
    ) -> bool:
        """Delete one connection record (cascade-deleting credentials). Return True if deleted."""

        async def _run(active_conn: Any) -> bool:
            try:
                cur = await active_conn.execute(_DELETE_CONNECTION_SQL, (connection_id,))
                row = await cur.fetchone()
                return row is not None
            except DatabaseError:
                raise
            except Exception as exc:
                raise translate_db_error(exc) from exc

        if conn is not None:
            return await _run(conn)
        async with self.connection() as pooled_conn:
            return await _run(pooled_conn)

    async def _execute_single_row(
        self,
        query: str,
        params: tuple[Any, ...],
        *,
        conn: Any | None,
    ) -> ConnectionRecord:
        async def _run(active_conn: Any) -> ConnectionRecord:
            row = await self._fetch_optional_row(active_conn, query, params)
            if row is None:
                raise DatabaseError("Expected persisted connection row was not returned.")
            return row

        if conn is not None:
            return await _run(conn)
        async with self.connection() as pooled_conn:
            return await _run(pooled_conn)

    async def _fetch_optional_row(
        self,
        active_conn: Any,
        query: str,
        params: tuple[Any, ...],
    ) -> ConnectionRecord | None:
        try:
            cur = await active_conn.execute(query, params)
            row = await cur.fetchone()
            return _row_to_connection_record(row) if row is not None else None
        except DatabaseError:
            raise
        except Exception as exc:
            raise translate_db_error(exc) from exc
