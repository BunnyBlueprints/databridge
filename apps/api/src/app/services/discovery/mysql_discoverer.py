"""Read-only MySQL catalog schema discoverer using PyMySQL in a bounded worker thread."""

import asyncio
import logging
from collections import defaultdict
from collections.abc import Callable
from typing import Any

import pymysql
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
from app.services.testers.mysql_tester import _classify_mysql_error

logger = logging.getLogger("databridge.api")

MysqlConnectFn = Callable[..., Any]

_LIST_MYSQL_TABLES_SQL = """
    SELECT
        TABLE_NAME,
        TABLE_TYPE
    FROM information_schema.TABLES
    WHERE TABLE_SCHEMA = %s
      AND TABLE_TYPE IN (%s, %s)
    ORDER BY TABLE_NAME ASC
    LIMIT %s
"""

_LIST_MYSQL_COLUMNS_SQL = """
    SELECT
        TABLE_NAME,
        COLUMN_NAME,
        ORDINAL_POSITION,
        DATA_TYPE,
        COLUMN_TYPE,
        IS_NULLABLE,
        COLUMN_DEFAULT,
        EXTRA,
        COLUMN_KEY
    FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = %s
    ORDER BY TABLE_NAME ASC, ORDINAL_POSITION ASC
"""

_LIST_MYSQL_CONSTRAINTS_SQL = """
    SELECT
        kcu.TABLE_NAME,
        tc.CONSTRAINT_TYPE,
        kcu.COLUMN_NAME,
        kcu.REFERENCED_TABLE_SCHEMA,
        kcu.REFERENCED_TABLE_NAME,
        kcu.REFERENCED_COLUMN_NAME
    FROM information_schema.KEY_COLUMN_USAGE kcu
    JOIN information_schema.TABLE_CONSTRAINTS tc
      ON kcu.CONSTRAINT_SCHEMA = tc.CONSTRAINT_SCHEMA
     AND kcu.TABLE_NAME = tc.TABLE_NAME
     AND kcu.CONSTRAINT_NAME = tc.CONSTRAINT_NAME
    WHERE kcu.TABLE_SCHEMA = %s
      AND tc.CONSTRAINT_TYPE IN ('PRIMARY KEY', 'UNIQUE', 'FOREIGN KEY')
    ORDER BY kcu.TABLE_NAME ASC, kcu.ORDINAL_POSITION ASC
"""


def _map_mysql_canonical_type(data_type: str, column_type: str) -> CanonicalFieldType:
    dt = data_type.strip().lower()
    ct = column_type.strip().lower()
    if ct == "tinyint(1)" or dt in ("boolean", "bool"):
        return "boolean"
    if dt in ("tinyint", "smallint", "mediumint", "int", "integer", "bigint", "year"):
        return "integer"
    if dt in ("decimal", "numeric", "float", "double", "real"):
        return "number"
    if dt == "bit":
        return "boolean" if ct == "bit(1)" else "binary"
    if dt == "date":
        return "date"
    if dt in ("datetime", "timestamp"):
        return "datetime"
    if dt == "json":
        return "json"
    if dt in ("binary", "varbinary", "tinyblob", "blob", "mediumblob", "longblob"):
        return "binary"
    if dt in (
        "char",
        "varchar",
        "tinytext",
        "text",
        "mediumtext",
        "longtext",
        "enum",
        "set",
        "time",
    ):
        return "string"
    return "unknown"


def _execute_mysql_catalog_queries(
    connect_fn: MysqlConnectFn,
    connect_kwargs: dict[str, Any],
    database: str,
    include_views: bool,
    max_objects: int,
) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    conn: Any = None
    cursor: Any = None
    second_type = "VIEW" if include_views else "BASE TABLE"
    try:
        conn = connect_fn(**connect_kwargs)
        cursor = conn.cursor()
        cursor.execute(
            _LIST_MYSQL_TABLES_SQL,
            (database, "BASE TABLE", second_type, max_objects + 1),
        )
        tables = list(cursor.fetchall())
        if not tables:
            return [], [], []

        cursor.execute(_LIST_MYSQL_COLUMNS_SQL, (database,))
        columns = list(cursor.fetchall())

        cursor.execute(_LIST_MYSQL_CONSTRAINTS_SQL, (database,))
        constraints = list(cursor.fetchall())
        return tables, columns, constraints
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception as cleanup_exc:
                logger.debug(
                    "MySQL discovery cursor cleanup failed: %s",
                    type(cleanup_exc).__name__,
                )
        if conn is not None:
            try:
                conn.close()
            except Exception as cleanup_exc:
                logger.debug(
                    "MySQL discovery connection cleanup failed: %s",
                    type(cleanup_exc).__name__,
                )


def _http_status_for_mysql_error(error_code: str, is_timeout: bool) -> int:
    if is_timeout or error_code == "TIMEOUT":
        return status.HTTP_504_GATEWAY_TIMEOUT
    if error_code in ("AUTHENTICATION_FAILED", "INVALID_CONFIGURATION"):
        return status.HTTP_422_UNPROCESSABLE_CONTENT
    return status.HTTP_502_BAD_GATEWAY


class MysqlSchemaDiscoverer:
    """Discovers MySQL tables, views, columns, defaults, and key constraints."""

    connector_type: ConnectorType = "MySQL"

    def __init__(self, connect_fn: MysqlConnectFn | None = None) -> None:
        self._connect_fn: MysqlConnectFn = connect_fn or pymysql.connect

    async def discover(self, ctx: DiscoveryContext) -> DiscoveryExecutionResult:
        if not isinstance(ctx.config, SqlPersistentConfig):
            raise DiscoveryExecutionError(
                error_code="INVALID_CONFIGURATION",
                message="Invalid configuration dispatched to MySQL discoverer.",
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
                message="MySQL username credential is required for schema discovery.",
                http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
            )

        driver_timeout = max(0.5, float(ctx.timeout_seconds))
        connect_kwargs: dict[str, Any] = {
            "host": ctx.config.host,
            "port": ctx.config.port,
            "user": username,
            "database": ctx.config.database,
            "password": (
                ctx.credentials.password.get_secret_value()
                if isinstance(ctx.credentials, SqlStoredCredentials)
                and ctx.credentials.password is not None
                else ""
            ),
            "connect_timeout": driver_timeout,
            "read_timeout": driver_timeout,
            "write_timeout": driver_timeout,
            "autocommit": True,
        }

        try:
            async with asyncio.timeout(ctx.timeout_seconds):
                raw_tables, raw_columns, raw_constraints = await asyncio.to_thread(
                    _execute_mysql_catalog_queries,
                    self._connect_fn,
                    connect_kwargs,
                    ctx.config.database,
                    ctx.options.includeViews,
                    ctx.max_objects,
                )
        except DiscoveryExecutionError:
            raise
        except Exception as exc:
            error_code, safe_message, is_timeout = _classify_mysql_error(exc)
            raise DiscoveryExecutionError(
                error_code=error_code,
                message=safe_message,
                http_status=_http_status_for_mysql_error(error_code, is_timeout),
                is_timeout=is_timeout,
            ) from exc

        warnings: list[SchemaWarning] = []
        discovery_status = "completed"
        if len(raw_tables) > ctx.max_objects:
            raw_tables = raw_tables[: ctx.max_objects]
            discovery_status = "partial"
            warnings.append(
                SchemaWarning(
                    code="OBJECT_LIMIT_REACHED",
                    message=f"Discovered objects truncated at limit of {ctx.max_objects}.",
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
                        message=f"No tables or views found in database '{ctx.config.database}'.",
                    )
                ],
                metadata={"database": ctx.config.database},
            )

        table_pairs = [(str(r[0]), str(r[1])) for r in raw_tables]
        selected_tables = {name for name, _ in table_pairs}

        pks_by_table: dict[str, list[str]] = defaultdict(list)
        uniques_by_table: dict[str, set[str]] = defaultdict(set)
        fks_by_col: dict[tuple[str, str], dict[str, str]] = {}

        for row in raw_constraints:
            t_name = str(row[0])
            if t_name not in selected_tables:
                continue
            ctype = str(row[1])
            col_name = str(row[2])
            if ctype == "PRIMARY KEY" and col_name not in pks_by_table[t_name]:
                pks_by_table[t_name].append(col_name)
            elif ctype == "UNIQUE":
                uniques_by_table[t_name].add(col_name)
            elif ctype == "FOREIGN KEY" and row[4] and row[5]:
                ref_table = str(row[4])
                fks_by_col[(t_name, col_name)] = {
                    "targetObjectKey": ref_table,
                    "targetFieldPath": str(row[5]),
                }

        cols_by_table: dict[str, list[SchemaFieldInput]] = defaultdict(list)
        for row in raw_columns:
            t_name = str(row[0])
            if t_name not in selected_tables:
                continue
            if len(cols_by_table[t_name]) >= ctx.max_fields_per_object:
                if not any(
                    w.code == "FIELD_LIMIT_REACHED" and w.objectKey == t_name for w in warnings
                ):
                    discovery_status = "partial"
                    warnings.append(
                        SchemaWarning(
                            code="FIELD_LIMIT_REACHED",
                            message=(
                                f"Object '{t_name}' exceeded {ctx.max_fields_per_object} "
                                "fields and was truncated."
                            ),
                            objectKey=t_name,
                        )
                    )
                continue

            col_name = str(row[1])
            ordinal = max(0, int(row[2]) - 1)
            data_type = str(row[3])
            column_type = str(row[4] or data_type)
            nullable = str(row[5]).upper() == "YES"
            col_default = str(row[6]) if row[6] is not None else None
            extra = str(row[7] or "").lower()
            col_key = str(row[8] or "").upper()

            is_generated = ("auto_increment" in extra) or ("generated" in extra)
            default_meta = (
                SchemaDefaultMetadata(
                    hasDefault=col_default is not None or is_generated,
                    expression=col_default[:512] if col_default is not None else None,
                    isGenerated=is_generated,
                )
                if (col_default is not None or is_generated)
                else None
            )

            is_pk = col_name in pks_by_table[t_name] or col_key == "PRI"
            if is_pk and col_name not in pks_by_table[t_name]:
                pks_by_table[t_name].append(col_name)
            is_unique = is_pk or (col_name in uniques_by_table[t_name]) or col_key == "UNI"
            is_required = not nullable and col_default is None and not is_generated

            canonical_type = _map_mysql_canonical_type(data_type, column_type)
            field_meta: dict[str, Any] = {"depth": 0, "isArray": False}
            fk_info = fks_by_col.get((t_name, col_name))
            if fk_info is not None:
                field_meta["foreignKey"] = fk_info

            cols_by_table[t_name].append(
                SchemaFieldInput(
                    fieldPath=col_name,
                    fieldName=col_name,
                    nativeType=column_type[:128],
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
        for t_name, raw_type in table_pairs:
            obj_type: SchemaObjectType = "view" if raw_type == "VIEW" else "table"
            objects.append(
                SchemaObjectInput(
                    objectKey=t_name,
                    objectName=t_name,
                    objectType=obj_type,
                    discoverySource="catalog",
                    nativeMetadata={
                        "catalogName": ctx.config.database,
                        "tableType": raw_type,
                    },
                    canonicalMetadata={
                        "confidence": 1.0,
                        "primaryKeyFields": pks_by_table.get(t_name, []),
                    },
                    fields=cols_by_table.get(t_name, []),
                )
            )

        return DiscoveryExecutionResult(
            discovery_status="partial" if discovery_status == "partial" else "completed",
            discovery_source="catalog",
            objects=objects,
            warnings=warnings,
            metadata={"database": ctx.config.database},
        )
