"""Asynchronous PostgreSQL repository for discovered schema snapshots, objects, and fields."""

import logging
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb
from pydantic import ValidationError

from app.db.errors import DatabaseError
from app.repositories.base import BaseRepository, translate_db_error
from app.schemas.schemas import (
    CanonicalFieldType,
    DiscoveredSchemaCreate,
    DiscoveredSchemaResponse,
    DiscoveryStatus,
    FieldDiscoverySource,
    SchemaDefaultMetadata,
    SchemaFieldResponse,
    SchemaObjectInput,
    SchemaObjectResponse,
    SchemaObjectSummaryResponse,
    SchemaObjectType,
    SchemaWarning,
    SnapshotDiscoverySource,
)

_DELETE_SCHEMA_BY_CONNECTION_SQL = """
    DELETE FROM discovered_schemas
    WHERE connection_id = %s
    RETURNING id
"""

_INSERT_SCHEMA_SQL = """
    INSERT INTO discovered_schemas (
        id,
        connection_id,
        discovery_status,
        discovered_at,
        schema_fingerprint,
        discovery_source,
        metadata
    )
    VALUES (%s, %s, %s, %s, %s, %s, %s)
    RETURNING
        id, connection_id, discovery_status, discovered_at, schema_fingerprint,
        discovery_source, metadata, created_at, updated_at
"""

_INSERT_OBJECT_SQL = """
    INSERT INTO schema_objects (
        id,
        discovered_schema_id,
        object_key,
        object_name,
        object_type,
        discovery_source,
        native_metadata,
        canonical_metadata
    )
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    RETURNING
        id, discovered_schema_id, object_key, object_name, object_type,
        discovery_source, native_metadata, canonical_metadata, created_at, updated_at
"""

_INSERT_FIELD_SQL = """
    INSERT INTO schema_fields (
        id,
        schema_object_id,
        field_path,
        field_name,
        native_type,
        canonical_type,
        nullable,
        is_required,
        primary_key,
        unique_key,
        default_metadata,
        ordinal_position,
        discovery_source,
        metadata
    )
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""

_SELECT_SCHEMA_BY_CONNECTION_SQL = """
    SELECT
        id, connection_id, discovery_status, discovered_at, schema_fingerprint,
        discovery_source, metadata, created_at, updated_at
    FROM discovered_schemas
    WHERE connection_id = %s
"""

_SELECT_OBJECTS_FOR_SCHEMA_SQL = """
    SELECT
        so.id,
        so.discovered_schema_id,
        so.object_key,
        so.object_name,
        so.object_type,
        so.discovery_source,
        so.native_metadata,
        so.canonical_metadata,
        COUNT(sf.id)::integer AS field_count,
        so.created_at,
        so.updated_at
    FROM schema_objects so
    LEFT JOIN schema_fields sf ON sf.schema_object_id = so.id
    WHERE so.discovered_schema_id = %s
    GROUP BY so.id
    ORDER BY so.object_key ASC
"""

_COUNT_OBJECTS_FILTERED_SQL = """
    SELECT COUNT(*)
    FROM schema_objects so
    JOIN discovered_schemas ds ON ds.id = so.discovered_schema_id
    WHERE ds.connection_id = %s
      AND (%s::text IS NULL OR so.object_type = %s)
      AND (%s::text IS NULL OR so.object_key = %s)
      AND (
          %s::text IS NULL
          OR so.object_key ILIKE %s ESCAPE '\\'
          OR so.object_name ILIKE %s ESCAPE '\\'
      )
"""

_LIST_OBJECTS_FILTERED_SQL = """
    SELECT
        so.id,
        so.discovered_schema_id,
        so.object_key,
        so.object_name,
        so.object_type,
        so.discovery_source,
        so.native_metadata,
        so.canonical_metadata,
        COUNT(sf.id)::integer AS field_count,
        so.created_at,
        so.updated_at
    FROM schema_objects so
    JOIN discovered_schemas ds ON ds.id = so.discovered_schema_id
    LEFT JOIN schema_fields sf ON sf.schema_object_id = so.id
    WHERE ds.connection_id = %s
      AND (%s::text IS NULL OR so.object_type = %s)
      AND (%s::text IS NULL OR so.object_key = %s)
      AND (
          %s::text IS NULL
          OR so.object_key ILIKE %s ESCAPE '\\'
          OR so.object_name ILIKE %s ESCAPE '\\'
      )
    GROUP BY so.id
    ORDER BY so.object_key ASC
    LIMIT %s OFFSET %s
"""

_SELECT_OBJECT_BY_ID_SQL = """
    SELECT
        so.id,
        so.discovered_schema_id,
        so.object_key,
        so.object_name,
        so.object_type,
        so.discovery_source,
        so.native_metadata,
        so.canonical_metadata,
        0::integer AS field_count,
        so.created_at,
        so.updated_at
    FROM schema_objects so
    JOIN discovered_schemas ds ON ds.id = so.discovered_schema_id
    WHERE ds.connection_id = %s
      AND so.id = %s
"""

_SELECT_OBJECT_BY_KEY_SQL = """
    SELECT
        so.id,
        so.discovered_schema_id,
        so.object_key,
        so.object_name,
        so.object_type,
        so.discovery_source,
        so.native_metadata,
        so.canonical_metadata,
        0::integer AS field_count,
        so.created_at,
        so.updated_at
    FROM schema_objects so
    JOIN discovered_schemas ds ON ds.id = so.discovered_schema_id
    WHERE ds.connection_id = %s
      AND so.object_key = %s
"""

_SELECT_FIELDS_FOR_OBJECT_SQL = """
    SELECT
        id,
        schema_object_id,
        field_path,
        field_name,
        native_type,
        canonical_type,
        nullable,
        is_required,
        primary_key,
        unique_key,
        default_metadata,
        ordinal_position,
        discovery_source,
        metadata,
        created_at,
        updated_at
    FROM schema_fields
    WHERE schema_object_id = %s
      AND (
          %s::text IS NULL
          OR field_path ILIKE %s ESCAPE '\\'
          OR field_name ILIKE %s ESCAPE '\\'
      )
    ORDER BY ordinal_position ASC, field_path ASC
"""


def _as_utc_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return datetime.fromisoformat(str(value))


def _escape_like_pattern(query_text: str) -> str:
    return query_text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _row_to_object_summary(row: tuple[Any, ...]) -> SchemaObjectSummaryResponse:
    return SchemaObjectSummaryResponse(
        id=UUID(str(row[0])),
        discoveredSchemaId=UUID(str(row[1])),
        objectKey=str(row[2]),
        objectName=str(row[3]),
        objectType=cast(SchemaObjectType, str(row[4])),
        discoverySource=cast(FieldDiscoverySource, str(row[5])),
        nativeMetadata=dict(row[6]) if isinstance(row[6], dict) else {},
        canonicalMetadata=dict(row[7]) if isinstance(row[7], dict) else {},
        fieldCount=int(row[8]),
        createdAt=_as_utc_datetime(row[9]),
        updatedAt=_as_utc_datetime(row[10]),
    )


def _row_to_field(row: tuple[Any, ...]) -> SchemaFieldResponse:
    default_meta = (
        SchemaDefaultMetadata.model_validate(row[10]) if isinstance(row[10], dict) else None
    )
    return SchemaFieldResponse(
        id=UUID(str(row[0])),
        schemaObjectId=UUID(str(row[1])),
        fieldPath=str(row[2]),
        fieldName=str(row[3]),
        nativeType=str(row[4]),
        canonicalType=cast(CanonicalFieldType, str(row[5])),
        nullable=bool(row[6]),
        isRequired=bool(row[7]),
        primaryKey=bool(row[8]),
        uniqueKey=bool(row[9]),
        defaultMetadata=default_meta,
        ordinalPosition=int(row[11]),
        discoverySource=cast(FieldDiscoverySource, str(row[12])),
        metadata=dict(row[13]) if isinstance(row[13], dict) else {},
        createdAt=_as_utc_datetime(row[14]),
        updatedAt=_as_utc_datetime(row[15]),
    )


_LOGGER = logging.getLogger("databridge.api")


def _extract_warnings_from_metadata(metadata: dict[str, Any]) -> list[SchemaWarning]:
    raw_warnings = metadata.get("warnings")
    if not isinstance(raw_warnings, list):
        return []
    parsed: list[SchemaWarning] = []
    for item in raw_warnings:
        if isinstance(item, dict):
            try:
                parsed.append(SchemaWarning.model_validate(item))
            except ValidationError as exc:
                _LOGGER.debug("Skipping malformed stored schema warning: %s", type(exc).__name__)
    return parsed


def _row_to_schema_response(
    row: tuple[Any, ...],
    objects: list[SchemaObjectSummaryResponse],
) -> DiscoveredSchemaResponse:
    metadata = dict(row[6]) if isinstance(row[6], dict) else {}
    return DiscoveredSchemaResponse(
        id=UUID(str(row[0])),
        connectionId=UUID(str(row[1])),
        discoveryStatus=cast(DiscoveryStatus, str(row[2])),
        discoveredAt=_as_utc_datetime(row[3]),
        schemaFingerprint=str(row[4]),
        discoverySource=cast(SnapshotDiscoverySource, str(row[5])),
        objectCount=len(objects),
        warnings=_extract_warnings_from_metadata(metadata),
        metadata=metadata,
        objects=objects,
        createdAt=_as_utc_datetime(row[7]),
        updatedAt=_as_utc_datetime(row[8]),
    )


class SchemaRepository(BaseRepository):
    """SQL repository managing `discovered_schemas`, `schema_objects`, and `schema_fields`."""

    async def replace_snapshot(
        self,
        payload: DiscoveredSchemaCreate,
        *,
        conn: Any | None = None,
    ) -> DiscoveredSchemaResponse:
        """Atomically replace the single latest discovered schema snapshot for a connection."""

        async def _run(active_conn: Any) -> DiscoveredSchemaResponse:
            try:
                connection_id = str(payload.connectionId)
                schema_id = str(payload.id or uuid4())

                await active_conn.execute(_DELETE_SCHEMA_BY_CONNECTION_SQL, (connection_id,))
                schema_cur = await active_conn.execute(
                    _INSERT_SCHEMA_SQL,
                    (
                        schema_id,
                        connection_id,
                        payload.discoveryStatus,
                        payload.discoveredAt,
                        payload.schemaFingerprint,
                        payload.discoverySource,
                        Jsonb(payload.metadata),
                    ),
                )
                schema_row = await schema_cur.fetchone()
                if schema_row is None:
                    raise DatabaseError("Expected persisted schema row was not returned.")

                object_summaries: list[SchemaObjectSummaryResponse] = []
                for obj in sorted(payload.objects, key=lambda item: item.objectKey):
                    summary = await self._insert_object_with_fields(
                        active_conn=active_conn,
                        schema_id=schema_id,
                        obj=obj,
                    )
                    object_summaries.append(summary)

                return _row_to_schema_response(schema_row, object_summaries)
            except DatabaseError:
                raise
            except Exception as exc:
                raise translate_db_error(exc) from exc

        if conn is not None:
            return await _run(conn)
        async with self.transaction() as tx_conn:
            return await _run(tx_conn)

    async def _insert_object_with_fields(
        self,
        *,
        active_conn: Any,
        schema_id: str,
        obj: SchemaObjectInput,
    ) -> SchemaObjectSummaryResponse:
        object_id = str(obj.id or uuid4())
        obj_cur = await active_conn.execute(
            _INSERT_OBJECT_SQL,
            (
                object_id,
                schema_id,
                obj.objectKey,
                obj.objectName,
                obj.objectType,
                obj.discoverySource,
                Jsonb(obj.nativeMetadata),
                Jsonb(obj.canonicalMetadata),
            ),
        )
        obj_row = await obj_cur.fetchone()
        if obj_row is None:
            raise DatabaseError("Expected persisted schema object row was not returned.")

        if obj.fields:
            field_params = [
                (
                    str(field.id or uuid4()),
                    object_id,
                    field.fieldPath,
                    field.fieldName,
                    field.nativeType,
                    field.canonicalType,
                    field.nullable,
                    field.isRequired,
                    field.primaryKey,
                    field.uniqueKey,
                    (
                        Jsonb(field.defaultMetadata.model_dump(exclude_none=True))
                        if field.defaultMetadata is not None
                        else None
                    ),
                    field.ordinalPosition,
                    field.discoverySource,
                    Jsonb(field.metadata),
                )
                for field in obj.fields
            ]
            async with active_conn.cursor() as field_cur:
                await field_cur.executemany(_INSERT_FIELD_SQL, field_params)

        return SchemaObjectSummaryResponse(
            id=UUID(str(obj_row[0])),
            discoveredSchemaId=UUID(str(obj_row[1])),
            objectKey=str(obj_row[2]),
            objectName=str(obj_row[3]),
            objectType=cast(SchemaObjectType, str(obj_row[4])),
            discoverySource=cast(FieldDiscoverySource, str(obj_row[5])),
            nativeMetadata=dict(obj_row[6]) if isinstance(obj_row[6], dict) else {},
            canonicalMetadata=dict(obj_row[7]) if isinstance(obj_row[7], dict) else {},
            fieldCount=len(obj.fields),
            createdAt=_as_utc_datetime(obj_row[8]),
            updatedAt=_as_utc_datetime(obj_row[9]),
        )

    async def get_latest_by_connection_id(
        self,
        connection_id: str,
        *,
        include_objects: bool = True,
        conn: Any | None = None,
    ) -> DiscoveredSchemaResponse | None:
        """Load the latest discovered schema snapshot for `connection_id`, if any."""

        async def _run(active_conn: Any) -> DiscoveredSchemaResponse | None:
            try:
                cur = await active_conn.execute(_SELECT_SCHEMA_BY_CONNECTION_SQL, (connection_id,))
                schema_row = await cur.fetchone()
                if schema_row is None:
                    return None

                objects: list[SchemaObjectSummaryResponse] = []
                if include_objects:
                    obj_cur = await active_conn.execute(
                        _SELECT_OBJECTS_FOR_SCHEMA_SQL,
                        (str(schema_row[0]),),
                    )
                    objects = [_row_to_object_summary(row) for row in await obj_cur.fetchall()]
                return _row_to_schema_response(schema_row, objects)
            except DatabaseError:
                raise
            except Exception as exc:
                raise translate_db_error(exc) from exc

        if conn is not None:
            return await _run(conn)
        async with self.connection() as pooled_conn:
            return await _run(pooled_conn)

    async def list_objects(
        self,
        connection_id: str,
        *,
        search_query: str | None = None,
        object_type: SchemaObjectType | None = None,
        object_key: str | None = None,
        limit: int = 100,
        offset: int = 0,
        conn: Any | None = None,
    ) -> tuple[list[SchemaObjectSummaryResponse], int]:
        """List and filter discovered objects for `connection_id` without hydrating fields."""
        cleaned_search = (
            f"%{_escape_like_pattern(search_query.strip())}%"
            if search_query and search_query.strip()
            else None
        )
        cleaned_key = object_key.strip() if object_key and object_key.strip() else None
        params = (
            connection_id,
            object_type,
            object_type,
            cleaned_key,
            cleaned_key,
            cleaned_search,
            cleaned_search,
            cleaned_search,
        )

        async def _run(active_conn: Any) -> tuple[list[SchemaObjectSummaryResponse], int]:
            try:
                count_cur = await active_conn.execute(_COUNT_OBJECTS_FILTERED_SQL, params)
                count_row = await count_cur.fetchone()
                total = int(count_row[0]) if count_row is not None else 0

                list_cur = await active_conn.execute(
                    _LIST_OBJECTS_FILTERED_SQL,
                    (*params, limit, offset),
                )
                rows = await list_cur.fetchall()
                return [_row_to_object_summary(row) for row in rows], total
            except DatabaseError:
                raise
            except Exception as exc:
                raise translate_db_error(exc) from exc

        if conn is not None:
            return await _run(conn)
        async with self.connection() as pooled_conn:
            return await _run(pooled_conn)

    async def get_object_with_fields(
        self,
        connection_id: str,
        object_id: str,
        *,
        field_query: str | None = None,
        conn: Any | None = None,
    ) -> SchemaObjectResponse | None:
        """Load one discovered object by `object_id` together with its ordered fields."""
        return await self._load_object_with_fields(
            query=_SELECT_OBJECT_BY_ID_SQL,
            lookup_param=object_id,
            connection_id=connection_id,
            field_query=field_query,
            conn=conn,
        )

    async def get_object_by_key(
        self,
        connection_id: str,
        object_key: str,
        *,
        field_query: str | None = None,
        conn: Any | None = None,
    ) -> SchemaObjectResponse | None:
        """Load one discovered object by canonical `object_key` with its ordered fields."""
        return await self._load_object_with_fields(
            query=_SELECT_OBJECT_BY_KEY_SQL,
            lookup_param=object_key,
            connection_id=connection_id,
            field_query=field_query,
            conn=conn,
        )

    async def _load_object_with_fields(
        self,
        *,
        query: str,
        lookup_param: str,
        connection_id: str,
        field_query: str | None,
        conn: Any | None,
    ) -> SchemaObjectResponse | None:
        cleaned_field_search = (
            f"%{_escape_like_pattern(field_query.strip())}%"
            if field_query and field_query.strip()
            else None
        )

        async def _run(active_conn: Any) -> SchemaObjectResponse | None:
            try:
                obj_cur = await active_conn.execute(query, (connection_id, lookup_param))
                obj_row = await obj_cur.fetchone()
                if obj_row is None:
                    return None

                summary = _row_to_object_summary(obj_row)
                field_cur = await active_conn.execute(
                    _SELECT_FIELDS_FOR_OBJECT_SQL,
                    (
                        str(summary.id),
                        cleaned_field_search,
                        cleaned_field_search,
                        cleaned_field_search,
                    ),
                )
                fields = [_row_to_field(row) for row in await field_cur.fetchall()]
                return SchemaObjectResponse(
                    id=summary.id,
                    discoveredSchemaId=summary.discoveredSchemaId,
                    objectKey=summary.objectKey,
                    objectName=summary.objectName,
                    objectType=summary.objectType,
                    discoverySource=summary.discoverySource,
                    nativeMetadata=summary.nativeMetadata,
                    canonicalMetadata=summary.canonicalMetadata,
                    fieldCount=len(fields),
                    createdAt=summary.createdAt,
                    updatedAt=summary.updatedAt,
                    fields=fields,
                )
            except DatabaseError:
                raise
            except Exception as exc:
                raise translate_db_error(exc) from exc

        if conn is not None:
            return await _run(conn)
        async with self.connection() as pooled_conn:
            return await _run(pooled_conn)

    async def delete_by_connection_id(
        self,
        connection_id: str,
        *,
        conn: Any | None = None,
    ) -> bool:
        """Delete the discovered schema snapshot for `connection_id` (cascading objects/fields)."""

        async def _run(active_conn: Any) -> bool:
            try:
                cur = await active_conn.execute(_DELETE_SCHEMA_BY_CONNECTION_SQL, (connection_id,))
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
