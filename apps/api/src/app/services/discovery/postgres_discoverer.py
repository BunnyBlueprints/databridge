"""Read-only PostgreSQL catalog schema discoverer using psycopg 3 async connections."""

import asyncio
import logging
import math
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any

import psycopg
from fastapi import status

from app.schemas.connection_test import ConnectorType
from app.schemas.connections import SqlPersistentConfig
from app.schemas.schemas import (
    CanonicalFieldType,
    SchemaDefaultMetadata,
    SchemaFieldInput,
    SchemaObjectInput,
    SchemaObjectType,
    SchemaWarning,
)
from app.services.credential_encryption import SqlStoredCredentials
from app.services.discovery.base import (
    DiscoveryContext,
    DiscoveryExecutionError,
    DiscoveryExecutionResult,
)
from app.services.testers.postgres_tester import _classify_postgres_error

logger = logging.getLogger("databridge.api")

ConnectFn = Callable[..., Awaitable[Any]]

_LIST_TABLES_SQL = """
    SELECT
        table_schema,
        table_name,
        table_type
    FROM information_schema.tables
    WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
      AND table_schema NOT LIKE 'pg\\_%%' ESCAPE '\\'
      AND (%s::text[] IS NULL OR table_schema = ANY(%s::text[]))
      AND table_type = ANY(%s::text[])
    ORDER BY table_schema ASC, table_name ASC
    LIMIT %s
"""

_LIST_COLUMNS_SQL = """
    SELECT
        table_schema,
        table_name,
        column_name,
        ordinal_position,
        data_type,
        udt_name,
        character_maximum_length,
        numeric_precision,
        numeric_scale,
        is_nullable,
        column_default,
        is_identity,
        is_generated
    FROM information_schema.columns
    WHERE table_schema = ANY(%s::text[])
      AND table_name = ANY(%s::text[])
    ORDER BY table_schema ASC, table_name ASC, ordinal_position ASC
"""

_LIST_CONSTRAINTS_SQL = """
    SELECT
        tc.table_schema,
        tc.table_name,
        tc.constraint_type,
        kcu.column_name,
        r_kcu.table_schema AS referenced_schema,
        r_kcu.table_name AS referenced_table,
        r_kcu.column_name AS referenced_column
    FROM information_schema.table_constraints tc
    JOIN information_schema.key_column_usage kcu
      ON tc.constraint_catalog = kcu.constraint_catalog
     AND tc.constraint_schema = kcu.constraint_schema
     AND tc.constraint_name = kcu.constraint_name
    LEFT JOIN information_schema.referential_constraints rc
      ON tc.constraint_type = 'FOREIGN KEY'
     AND tc.constraint_catalog = rc.constraint_catalog
     AND tc.constraint_schema = rc.constraint_schema
     AND tc.constraint_name = rc.constraint_name
    LEFT JOIN information_schema.key_column_usage r_kcu
      ON rc.unique_constraint_catalog = r_kcu.constraint_catalog
     AND rc.unique_constraint_schema = r_kcu.constraint_schema
     AND rc.unique_constraint_name = r_kcu.constraint_name
     AND kcu.position_in_unique_constraint = r_kcu.ordinal_position
    WHERE tc.table_schema = ANY(%s::text[])
      AND tc.table_name = ANY(%s::text[])
      AND tc.constraint_type IN ('PRIMARY KEY', 'UNIQUE', 'FOREIGN KEY')
    ORDER BY tc.table_schema ASC, tc.table_name ASC, kcu.ordinal_position ASC
"""


def _map_postgres_canonical_type(data_type: str, udt_name: str) -> CanonicalFieldType:
    dt = data_type.strip().lower()
    udt = udt_name.strip().lower()

    if dt == "array" or udt.startswith("_"):
        return "array"
    if dt in ("smallint", "integer", "bigint", "smallserial", "serial", "bigserial") or udt in (
        "int2",
        "int4",
        "int8",
    ):
        return "integer"
    if dt in ("numeric", "decimal", "real", "double precision", "money") or udt in (
        "numeric",
        "float4",
        "float8",
    ):
        return "number"
    if dt == "boolean" or udt == "bool":
        return "boolean"
    if dt == "date" or udt == "date":
        return "date"
    if dt.startswith("timestamp") or udt in ("timestamp", "timestamptz"):
        return "datetime"
    if dt == "bytea" or udt == "bytea":
        return "binary"
    if dt in ("json", "jsonb") or udt in ("json", "jsonb"):
        return "json"
    if dt in (
        "character varying",
        "varchar",
        "character",
        "char",
        "text",
        "uuid",
        "inet",
        "cidr",
        "macaddr",
        "citext",
        "xml",
    ) or udt in ("varchar", "bpchar", "text", "uuid", "citext"):
        return "string"
    if dt.startswith("time"):
        return "string"
    if dt == "user-defined":
        return "string"
    return "unknown"


def _format_postgres_native_type(
    *,
    data_type: str,
    udt_name: str,
    char_max_len: int | None,
    numeric_precision: int | None,
    numeric_scale: int | None,
) -> str:
    dt = data_type.strip().lower()
    udt = udt_name.strip().lower()
    if dt == "array" and udt.startswith("_"):
        return f"{udt[1:]}[]"[:128]
    if dt in ("character varying", "character") and char_max_len is not None:
        return f"{dt}({char_max_len})"[:128]
    if dt in ("numeric", "decimal") and numeric_precision is not None:
        if numeric_scale is not None:
            return f"{dt}({numeric_precision},{numeric_scale})"[:128]
        return f"{dt}({numeric_precision})"[:128]
    if dt == "user-defined" and udt:
        return udt[:128]
    return (data_type.strip() or udt or "unknown")[:128]


def _http_status_for_postgres_error(error_code: str, is_timeout: bool) -> int:
    if is_timeout or error_code == "TIMEOUT":
        return status.HTTP_504_GATEWAY_TIMEOUT
    if error_code in ("AUTHENTICATION_FAILED", "INVALID_CONFIGURATION"):
        return status.HTTP_422_UNPROCESSABLE_CONTENT
    return status.HTTP_502_BAD_GATEWAY


class PostgresSchemaDiscoverer:
    """Discovers PostgreSQL tables, views, columns, defaults, and key constraints."""

    connector_type: ConnectorType = "PostgreSQL"

    def __init__(self, connect_fn: ConnectFn | None = None) -> None:
        self._connect_fn: ConnectFn = connect_fn or psycopg.AsyncConnection.connect

    async def discover(self, ctx: DiscoveryContext) -> DiscoveryExecutionResult:
        if not isinstance(ctx.config, SqlPersistentConfig):
            raise DiscoveryExecutionError(
                error_code="INVALID_CONFIGURATION",
                message="Invalid configuration dispatched to PostgreSQL discoverer.",
                http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
            )

        username = (
            ctx.credentials.username.get_secret_value()
            if isinstance(ctx.credentials, SqlStoredCredentials)
            and ctx.credentials.username is not None
            else ctx.legacy_username
        )
        if not username:
            raise DiscoveryExecutionError(
                error_code="INVALID_CONFIGURATION",
                message="PostgreSQL username credential is required for schema discovery.",
                http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
            )

        connect_timeout = max(1, math.ceil(ctx.timeout_seconds))
        statement_timeout_ms = max(100, int(ctx.timeout_seconds * 1000))
        connect_kwargs: dict[str, Any] = {
            "host": ctx.config.host,
            "port": ctx.config.port,
            "dbname": ctx.config.database,
            "user": username,
            "connect_timeout": connect_timeout,
            "options": f"-c statement_timeout={statement_timeout_ms}",
            "autocommit": False,
        }
        if isinstance(ctx.credentials, SqlStoredCredentials) and ctx.credentials.password:
            connect_kwargs["password"] = ctx.credentials.password.get_secret_value()

        table_types = ["BASE TABLE", "VIEW"] if ctx.options.includeViews else ["BASE TABLE"]
        schema_filter = ctx.options.includeSchemas

        conn: Any = None
        try:
            async with asyncio.timeout(ctx.timeout_seconds):
                conn = await self._connect_fn(**connect_kwargs)
                async with conn.transaction():
                    async with conn.cursor() as cur:
                        await cur.execute("SET TRANSACTION READ ONLY")
                        await cur.execute(
                            _LIST_TABLES_SQL,
                            (
                                schema_filter,
                                schema_filter,
                                table_types,
                                ctx.max_objects + 1,
                            ),
                        )
                        raw_tables = await cur.fetchall()

                        warnings: list[SchemaWarning] = []
                        discovery_status = "completed"
                        if len(raw_tables) > ctx.max_objects:
                            raw_tables = raw_tables[: ctx.max_objects]
                            discovery_status = "partial"
                            warnings.append(
                                SchemaWarning(
                                    code="OBJECT_LIMIT_REACHED",
                                    message=(
                                        f"Discovered objects truncated at limit of "
                                        f"{ctx.max_objects}."
                                    ),
                                )
                            )

                        if not raw_tables:
                            return DiscoveryExecutionResult(
                                discovery_status="completed",
                                discovery_source="catalog",
                                objects=[],
                                warnings=[
                                    SchemaWarning(
                                        code="NO_OBJECTS_FOUND",
                                        message=(
                                            f"No tables or views found in database "
                                            f"'{ctx.config.database}'."
                                        ),
                                    )
                                ],
                                metadata={"database": ctx.config.database},
                            )

                        table_pairs = [(str(r[0]), str(r[1]), str(r[2])) for r in raw_tables]
                        selected_pair_set = {(s, t) for s, t, _ in table_pairs}
                        schema_names = sorted({s for s, _, _ in table_pairs})
                        table_names = sorted({t for _, t, _ in table_pairs})

                        await cur.execute(_LIST_COLUMNS_SQL, (schema_names, table_names))
                        raw_columns = await cur.fetchall()

                        await cur.execute(_LIST_CONSTRAINTS_SQL, (schema_names, table_names))
                        raw_constraints = await cur.fetchall()
        except DiscoveryExecutionError:
            raise
        except Exception as exc:
            error_code, safe_message, is_timeout = _classify_postgres_error(exc)
            raise DiscoveryExecutionError(
                error_code=error_code,
                message=safe_message,
                http_status=_http_status_for_postgres_error(error_code, is_timeout),
                is_timeout=is_timeout,
            ) from exc
        finally:
            if conn is not None:
                try:
                    await conn.close()
                except Exception as cleanup_exc:
                    logger.debug(
                        "PostgreSQL discovery connection cleanup failed: %s",
                        type(cleanup_exc).__name__,
                    )

        pks_by_table: dict[tuple[str, str], list[str]] = defaultdict(list)
        uniques_by_table: dict[tuple[str, str], set[str]] = defaultdict(set)
        fks_by_col: dict[tuple[str, str, str], dict[str, str]] = {}

        for row in raw_constraints:
            pair = (str(row[0]), str(row[1]))
            if pair not in selected_pair_set:
                continue
            ctype = str(row[2])
            col_name = str(row[3])
            if ctype == "PRIMARY KEY" and col_name not in pks_by_table[pair]:
                pks_by_table[pair].append(col_name)
            elif ctype == "UNIQUE":
                uniques_by_table[pair].add(col_name)
            elif ctype == "FOREIGN KEY" and row[4] and row[5] and row[6]:
                fks_by_col[(pair[0], pair[1], col_name)] = {
                    "targetObjectKey": f"{row[4]}.{row[5]}",
                    "targetFieldPath": str(row[6]),
                }

        cols_by_table: dict[tuple[str, str], list[SchemaFieldInput]] = defaultdict(list)
        for row in raw_columns:
            schema_name = str(row[0])
            table_name = str(row[1])
            pair = (schema_name, table_name)
            if pair not in selected_pair_set:
                continue

            object_key = f"{schema_name}.{table_name}"
            if len(cols_by_table[pair]) >= ctx.max_fields_per_object:
                if not any(
                    w.code == "FIELD_LIMIT_REACHED" and w.objectKey == object_key for w in warnings
                ):
                    discovery_status = "partial"
                    warnings.append(
                        SchemaWarning(
                            code="FIELD_LIMIT_REACHED",
                            message=(
                                f"Object '{object_key}' exceeded {ctx.max_fields_per_object} "
                                "fields and was truncated."
                            ),
                            objectKey=object_key,
                        )
                    )
                continue

            col_name = str(row[2])
            ordinal = max(0, int(row[3]) - 1)
            data_type = str(row[4])
            udt_name = str(row[5])
            nullable = str(row[9]).upper() == "YES"
            col_default = str(row[10]) if row[10] is not None else None
            is_identity = str(row[11] or "").upper() == "YES"
            is_generated = str(row[12] or "").upper() == "ALWAYS"

            default_meta = (
                SchemaDefaultMetadata(
                    hasDefault=col_default is not None or is_identity or is_generated,
                    expression=col_default[:512] if col_default is not None else None,
                    isGenerated=is_identity or is_generated,
                )
                if (col_default is not None or is_identity or is_generated)
                else None
            )

            is_pk = col_name in pks_by_table[pair]
            is_unique = is_pk or (col_name in uniques_by_table[pair])
            is_required = (
                not nullable and col_default is None and not is_identity and not is_generated
            )
            canonical_type = _map_postgres_canonical_type(data_type, udt_name)
            native_type = _format_postgres_native_type(
                data_type=data_type,
                udt_name=udt_name,
                char_max_len=int(row[6]) if row[6] is not None else None,
                numeric_precision=int(row[7]) if row[7] is not None else None,
                numeric_scale=int(row[8]) if row[8] is not None else None,
            )

            field_meta: dict[str, Any] = {
                "depth": 0,
                "isArray": canonical_type == "array",
            }
            fk_info = fks_by_col.get((schema_name, table_name, col_name))
            if fk_info is not None:
                field_meta["foreignKey"] = fk_info

            cols_by_table[pair].append(
                SchemaFieldInput(
                    fieldPath=col_name,
                    fieldName=col_name,
                    nativeType=native_type,
                    canonicalType=canonical_type,
                    nullable=nullable,
                    isRequired=is_required,
                    primaryKey=is_pk,
                    uniqueKey=is_unique,
                    defaultMetadata=default_meta,
                    ordinalPosition=ordinal,
                    discoverySource="catalog",
                    metadata=field_meta,
                )
            )

        objects: list[SchemaObjectInput] = []
        for schema_name, table_name, raw_type in table_pairs:
            pair = (schema_name, table_name)
            obj_type: SchemaObjectType = "view" if raw_type == "VIEW" else "table"
            object_key = f"{schema_name}.{table_name}"
            objects.append(
                SchemaObjectInput(
                    objectKey=object_key,
                    objectName=table_name,
                    objectType=obj_type,
                    discoverySource="catalog",
                    nativeMetadata={
                        "catalogName": ctx.config.database,
                        "namespace": schema_name,
                        "tableType": raw_type,
                    },
                    canonicalMetadata={
                        "confidence": 1.0,
                        "primaryKeyFields": pks_by_table.get(pair, []),
                    },
                    fields=cols_by_table.get(pair, []),
                )
            )

        return DiscoveryExecutionResult(
            discovery_status="partial" if discovery_status == "partial" else "completed",
            discovery_source="catalog",
            objects=objects,
            warnings=warnings,
            metadata={"database": ctx.config.database},
        )
