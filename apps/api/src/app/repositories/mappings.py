"""Asynchronous PostgreSQL repository for persistent field mappings and ordered rules."""

from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from app.db.errors import DatabaseConflictError, DatabaseError
from app.repositories.base import BaseRepository, translate_db_error
from app.schemas.mappings import (
    ConstantJsonValue,
    MappingCreate,
    MappingResponse,
    MappingRuleInput,
    MappingRuleResponse,
    MappingRuleType,
    MappingStatus,
    MappingUpdate,
    MappingValidationSummary,
    TransformReference,
)

_INSERT_MAPPING_SQL = """
    INSERT INTO mappings (
        id,
        name,
        description,
        source_connection_id,
        source_object_key,
        destination_connection_id,
        destination_object_key,
        status,
        mapping_version,
        source_schema_fingerprint,
        destination_schema_fingerprint,
        validation_summary
    )
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    RETURNING
        id, name, description, source_connection_id, source_object_key,
        destination_connection_id, destination_object_key, status, mapping_version,
        source_schema_fingerprint, destination_schema_fingerprint, validation_summary,
        created_at, updated_at
"""

_SELECT_MAPPING_BY_ID_SQL = """
    SELECT
        id, name, description, source_connection_id, source_object_key,
        destination_connection_id, destination_object_key, status, mapping_version,
        source_schema_fingerprint, destination_schema_fingerprint, validation_summary,
        created_at, updated_at
    FROM mappings
    WHERE id = %s
"""

_SELECT_MAPPING_BY_ID_FOR_UPDATE_SQL = """
    SELECT
        id, name, description, source_connection_id, source_object_key,
        destination_connection_id, destination_object_key, status, mapping_version,
        source_schema_fingerprint, destination_schema_fingerprint, validation_summary,
        created_at, updated_at
    FROM mappings
    WHERE id = %s
    FOR UPDATE
"""

_COUNT_MAPPINGS_SQL = """
    SELECT COUNT(*)
    FROM mappings
    WHERE (%s::uuid IS NULL OR source_connection_id = %s::uuid)
      AND (%s::uuid IS NULL OR destination_connection_id = %s::uuid)
      AND (%s::text IS NULL OR status = %s)
      AND (
          %s::text IS NULL
          OR name ILIKE %s ESCAPE '\\'
          OR source_object_key ILIKE %s ESCAPE '\\'
          OR destination_object_key ILIKE %s ESCAPE '\\'
          OR COALESCE(description, '') ILIKE %s ESCAPE '\\'
      )
"""

_LIST_MAPPINGS_SQL = """
    SELECT
        id, name, description, source_connection_id, source_object_key,
        destination_connection_id, destination_object_key, status, mapping_version,
        source_schema_fingerprint, destination_schema_fingerprint, validation_summary,
        created_at, updated_at
    FROM mappings
    WHERE (%s::uuid IS NULL OR source_connection_id = %s::uuid)
      AND (%s::uuid IS NULL OR destination_connection_id = %s::uuid)
      AND (%s::text IS NULL OR status = %s)
      AND (
          %s::text IS NULL
          OR name ILIKE %s ESCAPE '\\'
          OR source_object_key ILIKE %s ESCAPE '\\'
          OR destination_object_key ILIKE %s ESCAPE '\\'
          OR COALESCE(description, '') ILIKE %s ESCAPE '\\'
      )
    ORDER BY created_at DESC, id DESC
    LIMIT %s OFFSET %s
"""

_UPDATE_MAPPING_SQL = """
    UPDATE mappings
    SET
        name = %s,
        description = %s,
        source_connection_id = %s,
        source_object_key = %s,
        destination_connection_id = %s,
        destination_object_key = %s,
        status = %s,
        mapping_version = mapping_version + 1,
        source_schema_fingerprint = %s,
        destination_schema_fingerprint = %s,
        validation_summary = %s,
        updated_at = NOW()
    WHERE id = %s
    RETURNING
        id, name, description, source_connection_id, source_object_key,
        destination_connection_id, destination_object_key, status, mapping_version,
        source_schema_fingerprint, destination_schema_fingerprint, validation_summary,
        created_at, updated_at
"""

_UPDATE_MAPPING_VALIDATION_SQL = """
    UPDATE mappings
    SET
        status = %s,
        validation_summary = %s,
        source_schema_fingerprint = COALESCE(%s, source_schema_fingerprint),
        destination_schema_fingerprint = COALESCE(%s, destination_schema_fingerprint),
        updated_at = NOW()
    WHERE id = %s
      AND (%s::integer IS NULL OR mapping_version = %s::integer)
    RETURNING
        id, name, description, source_connection_id, source_object_key,
        destination_connection_id, destination_object_key, status, mapping_version,
        source_schema_fingerprint, destination_schema_fingerprint, validation_summary,
        created_at, updated_at
"""

_DELETE_MAPPING_SQL = """
    DELETE FROM mappings
    WHERE id = %s
    RETURNING id
"""

_COUNT_BY_CONNECTION_SQL = """
    SELECT COUNT(*)
    FROM mappings
    WHERE source_connection_id = %s OR destination_connection_id = %s
"""

_DELETE_RULES_BY_MAPPING_SQL = """
    DELETE FROM mapping_rules
    WHERE mapping_id = %s
"""

_INSERT_RULE_SQL = """
    INSERT INTO mapping_rules (
        id,
        mapping_id,
        source_field_path,
        destination_field_path,
        mapping_type,
        constant_value,
        transform_ref,
        ordinal_position,
        metadata
    )
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    RETURNING
        id, mapping_id, source_field_path, destination_field_path, mapping_type,
        constant_value, transform_ref, ordinal_position, metadata, created_at, updated_at
"""

_SELECT_RULES_FOR_MAPPINGS_SQL = """
    SELECT
        id, mapping_id, source_field_path, destination_field_path, mapping_type,
        constant_value, transform_ref, ordinal_position, metadata, created_at, updated_at
    FROM mapping_rules
    WHERE mapping_id = ANY(%s::uuid[])
    ORDER BY mapping_id ASC, ordinal_position ASC, destination_field_path ASC
"""


def _as_utc_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return datetime.fromisoformat(str(value))


def _escape_like_pattern(query_text: str) -> str:
    return query_text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _row_to_rule(row: tuple[Any, ...]) -> MappingRuleResponse:
    transform_ref = TransformReference.model_validate(row[6]) if isinstance(row[6], dict) else None
    return MappingRuleResponse(
        id=UUID(str(row[0])),
        mappingId=UUID(str(row[1])),
        sourceFieldPath=str(row[2]) if row[2] is not None else None,
        destinationFieldPath=str(row[3]),
        mappingType=cast(MappingRuleType, str(row[4])),
        constantValue=cast(ConstantJsonValue, row[5]),
        transformRef=transform_ref,
        ordinalPosition=int(row[7]),
        metadata=dict(row[8]) if isinstance(row[8], dict) else {},
        createdAt=_as_utc_datetime(row[9]),
        updatedAt=_as_utc_datetime(row[10]),
    )


def _row_to_mapping(
    row: tuple[Any, ...],
    rules: list[MappingRuleResponse],
) -> MappingResponse:
    summary = (
        MappingValidationSummary.model_validate(row[11]) if isinstance(row[11], dict) else None
    )
    return MappingResponse(
        id=UUID(str(row[0])),
        name=str(row[1]),
        description=str(row[2]) if row[2] is not None else None,
        sourceConnectionId=UUID(str(row[3])),
        sourceObjectKey=str(row[4]),
        destinationConnectionId=UUID(str(row[5])),
        destinationObjectKey=str(row[6]),
        status=cast(MappingStatus, str(row[7])),
        mappingVersion=int(row[8]),
        sourceSchemaFingerprint=str(row[9]) if row[9] is not None else None,
        destinationSchemaFingerprint=str(row[10]) if row[10] is not None else None,
        validationSummary=summary,
        rules=rules,
        createdAt=_as_utc_datetime(row[12]),
        updatedAt=_as_utc_datetime(row[13]),
    )


def _rule_insert_params(mapping_id: str, rule: MappingRuleInput) -> tuple[Any, ...]:
    # Constant rules serialize even Python None as JSONB 'null' so SQL CHECK distinguishes
    # an explicit JSON null constant from direct/transform rules where constant_value is SQL NULL.
    constant_param = Jsonb(rule.constantValue) if rule.mappingType == "constant" else None
    transform_param = (
        Jsonb(rule.transformRef.model_dump(exclude_none=True))
        if rule.transformRef is not None
        else None
    )
    return (
        str(rule.id or uuid4()),
        mapping_id,
        rule.sourceFieldPath,
        rule.destinationFieldPath,
        rule.mappingType,
        constant_param,
        transform_param,
        rule.ordinalPosition,
        Jsonb(rule.metadata),
    )


class MappingRepository(BaseRepository):
    """SQL repository managing `mappings` and ordered `mapping_rules`."""

    async def create(
        self,
        payload: MappingCreate,
        *,
        conn: Any | None = None,
    ) -> MappingResponse:
        """Insert a mapping and all ordered rules atomically inside one transaction."""

        async def _run(active_conn: Any) -> MappingResponse:
            try:
                mapping_id = str(payload.id or uuid4())
                summary_param = (
                    Jsonb(payload.validationSummary.model_dump(mode="json"))
                    if payload.validationSummary is not None
                    else None
                )
                cur = await active_conn.execute(
                    _INSERT_MAPPING_SQL,
                    (
                        mapping_id,
                        payload.name,
                        payload.description,
                        str(payload.sourceConnectionId),
                        payload.sourceObjectKey,
                        str(payload.destinationConnectionId),
                        payload.destinationObjectKey,
                        payload.status,
                        1,
                        payload.sourceSchemaFingerprint,
                        payload.destinationSchemaFingerprint,
                        summary_param,
                    ),
                )
                mapping_row = await cur.fetchone()
                if mapping_row is None:
                    raise DatabaseError("Expected persisted mapping row was not returned.")

                rules = await self._insert_ordered_rules(
                    active_conn=active_conn,
                    mapping_id=mapping_id,
                    rules=payload.rules,
                )
                return _row_to_mapping(mapping_row, rules)
            except DatabaseError:
                raise
            except Exception as exc:
                raise translate_db_error(exc) from exc

        if conn is not None:
            return await _run(conn)
        async with self.transaction() as tx_conn:
            return await _run(tx_conn)

    async def get_by_id(
        self,
        mapping_id: str,
        *,
        for_update: bool = False,
        conn: Any | None = None,
    ) -> MappingResponse | None:
        """Load one mapping configuration and its ordered rules by UUID."""
        query = _SELECT_MAPPING_BY_ID_FOR_UPDATE_SQL if for_update else _SELECT_MAPPING_BY_ID_SQL

        async def _run(active_conn: Any) -> MappingResponse | None:
            try:
                cur = await active_conn.execute(query, (mapping_id,))
                row = await cur.fetchone()
                if row is None:
                    return None
                rules_by_id = await self._load_rules_for_mappings(active_conn, [str(row[0])])
                return _row_to_mapping(row, rules_by_id.get(str(row[0]), []))
            except DatabaseError:
                raise
            except Exception as exc:
                raise translate_db_error(exc) from exc

        if conn is not None:
            return await _run(conn)
        async with self.connection() as pooled_conn:
            return await _run(pooled_conn)

    async def list_mappings(
        self,
        *,
        source_connection_id: str | None = None,
        destination_connection_id: str | None = None,
        status_filter: MappingStatus | None = None,
        search_query: str | None = None,
        limit: int = 50,
        offset: int = 0,
        conn: Any | None = None,
    ) -> tuple[list[MappingResponse], int]:
        """List and filter mappings with their ordered rules and total match count."""
        cleaned_search = (
            f"%{_escape_like_pattern(search_query.strip())}%"
            if search_query and search_query.strip()
            else None
        )
        filter_params = (
            source_connection_id,
            source_connection_id,
            destination_connection_id,
            destination_connection_id,
            status_filter,
            status_filter,
            cleaned_search,
            cleaned_search,
            cleaned_search,
            cleaned_search,
            cleaned_search,
        )

        async def _run(active_conn: Any) -> tuple[list[MappingResponse], int]:
            try:
                count_cur = await active_conn.execute(_COUNT_MAPPINGS_SQL, filter_params)
                count_row = await count_cur.fetchone()
                total = int(count_row[0]) if count_row is not None else 0

                list_cur = await active_conn.execute(
                    _LIST_MAPPINGS_SQL,
                    (*filter_params, limit, offset),
                )
                mapping_rows = await list_cur.fetchall()
                if not mapping_rows:
                    return [], total

                mapping_ids = [str(row[0]) for row in mapping_rows]
                rules_by_id = await self._load_rules_for_mappings(active_conn, mapping_ids)
                return (
                    [
                        _row_to_mapping(row, rules_by_id.get(str(row[0]), []))
                        for row in mapping_rows
                    ],
                    total,
                )
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
        mapping_id: str,
        payload: MappingUpdate,
        *,
        expected_mapping_version: int | None = None,
        conn: Any | None = None,
    ) -> MappingResponse | None:
        """Update mapping metadata/bindings and replace all rules atomically."""

        async def _run(active_conn: Any) -> MappingResponse | None:
            try:
                lock_cur = await active_conn.execute(
                    _SELECT_MAPPING_BY_ID_FOR_UPDATE_SQL,
                    (mapping_id,),
                )
                existing_row = await lock_cur.fetchone()
                if existing_row is None:
                    return None

                existing_mapping = _row_to_mapping(existing_row, [])
                if (
                    expected_mapping_version is not None
                    and existing_mapping.mappingVersion != expected_mapping_version
                ):
                    raise DatabaseConflictError(
                        "Mapping was modified concurrently; reload the latest version before "
                        "saving.",
                        error_code="MAPPING_VERSION_CONFLICT",
                    )
                fields_set = payload.model_fields_set

                next_name = payload.name if payload.name is not None else existing_mapping.name
                next_desc = (
                    payload.description
                    if "description" in fields_set
                    else existing_mapping.description
                )
                next_src_conn = (
                    str(payload.sourceConnectionId)
                    if payload.sourceConnectionId is not None
                    else str(existing_mapping.sourceConnectionId)
                )
                next_src_obj = (
                    payload.sourceObjectKey
                    if payload.sourceObjectKey is not None
                    else existing_mapping.sourceObjectKey
                )
                next_dst_conn = (
                    str(payload.destinationConnectionId)
                    if payload.destinationConnectionId is not None
                    else str(existing_mapping.destinationConnectionId)
                )
                next_dst_obj = (
                    payload.destinationObjectKey
                    if payload.destinationObjectKey is not None
                    else existing_mapping.destinationObjectKey
                )
                next_status = (
                    payload.status if payload.status is not None else existing_mapping.status
                )
                next_src_fp = (
                    payload.sourceSchemaFingerprint
                    if "sourceSchemaFingerprint" in fields_set
                    else existing_mapping.sourceSchemaFingerprint
                )
                next_dst_fp = (
                    payload.destinationSchemaFingerprint
                    if "destinationSchemaFingerprint" in fields_set
                    else existing_mapping.destinationSchemaFingerprint
                )
                next_summary = (
                    payload.validationSummary
                    if "validationSummary" in fields_set
                    else existing_mapping.validationSummary
                )

                summary_param = (
                    Jsonb(next_summary.model_dump(mode="json"))
                    if next_summary is not None
                    else None
                )
                upd_cur = await active_conn.execute(
                    _UPDATE_MAPPING_SQL,
                    (
                        next_name,
                        next_desc,
                        next_src_conn,
                        next_src_obj,
                        next_dst_conn,
                        next_dst_obj,
                        next_status,
                        next_src_fp,
                        next_dst_fp,
                        summary_param,
                        mapping_id,
                    ),
                )
                updated_row = await upd_cur.fetchone()
                if updated_row is None:
                    return None

                if payload.rules is not None:
                    await active_conn.execute(_DELETE_RULES_BY_MAPPING_SQL, (mapping_id,))
                    rules = await self._insert_ordered_rules(
                        active_conn=active_conn,
                        mapping_id=mapping_id,
                        rules=payload.rules,
                    )
                else:
                    rules_by_id = await self._load_rules_for_mappings(active_conn, [mapping_id])
                    rules = rules_by_id.get(mapping_id, [])
                return _row_to_mapping(updated_row, rules)
            except DatabaseError:
                raise
            except Exception as exc:
                raise translate_db_error(exc) from exc

        if conn is not None:
            return await _run(conn)
        async with self.transaction() as tx_conn:
            return await _run(tx_conn)

    async def update_validation_outcome(
        self,
        mapping_id: str,
        *,
        status: MappingStatus,
        validation_summary: MappingValidationSummary,
        expected_mapping_version: int | None = None,
        source_schema_fingerprint: str | None = None,
        destination_schema_fingerprint: str | None = None,
        conn: Any | None = None,
    ) -> MappingResponse | None:
        """Persist validation status and summary with row locking and optimistic version guard."""

        async def _run(active_conn: Any) -> MappingResponse | None:
            try:
                lock_cur = await active_conn.execute(
                    _SELECT_MAPPING_BY_ID_FOR_UPDATE_SQL,
                    (mapping_id,),
                )
                locked_row = await lock_cur.fetchone()
                if locked_row is None:
                    return None
                current_version = int(locked_row[8])
                if (
                    expected_mapping_version is not None
                    and current_version != expected_mapping_version
                ):
                    raise DatabaseConflictError(
                        "Mapping was modified concurrently during validation; revalidate on "
                        "the latest version.",
                        error_code="MAPPING_VERSION_CONFLICT",
                    )

                cur = await active_conn.execute(
                    _UPDATE_MAPPING_VALIDATION_SQL,
                    (
                        status,
                        Jsonb(validation_summary.model_dump(mode="json")),
                        source_schema_fingerprint,
                        destination_schema_fingerprint,
                        mapping_id,
                        expected_mapping_version,
                        expected_mapping_version,
                    ),
                )
                row = await cur.fetchone()
                if row is None:
                    raise DatabaseConflictError(
                        "Mapping was modified concurrently during validation; revalidate on "
                        "the latest version.",
                        error_code="MAPPING_VERSION_CONFLICT",
                    )
                rules_by_id = await self._load_rules_for_mappings(active_conn, [str(row[0])])
                return _row_to_mapping(row, rules_by_id.get(str(row[0]), []))
            except DatabaseError:
                raise
            except Exception as exc:
                raise translate_db_error(exc) from exc

        if conn is not None:
            return await _run(conn)
        async with self.transaction() as tx_conn:
            return await _run(tx_conn)

    async def delete(
        self,
        mapping_id: str,
        *,
        conn: Any | None = None,
    ) -> bool:
        """Delete one mapping configuration (cascade-deleting mapping_rules)."""

        async def _run(active_conn: Any) -> bool:
            try:
                cur = await active_conn.execute(_DELETE_MAPPING_SQL, (mapping_id,))
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

    async def count_by_connection_id(
        self,
        connection_id: str,
        *,
        conn: Any | None = None,
    ) -> int:
        """Return how many saved mappings reference `connection_id` as source or destination."""

        async def _run(active_conn: Any) -> int:
            try:
                cur = await active_conn.execute(
                    _COUNT_BY_CONNECTION_SQL,
                    (connection_id, connection_id),
                )
                row = await cur.fetchone()
                return int(row[0]) if row is not None else 0
            except DatabaseError:
                raise
            except Exception as exc:
                raise translate_db_error(exc) from exc

        if conn is not None:
            return await _run(conn)
        async with self.connection() as pooled_conn:
            return await _run(pooled_conn)

    async def _insert_ordered_rules(
        self,
        *,
        active_conn: Any,
        mapping_id: str,
        rules: list[MappingRuleInput],
    ) -> list[MappingRuleResponse]:
        inserted: list[MappingRuleResponse] = []
        for rule in sorted(rules, key=lambda item: item.ordinalPosition):
            cur = await active_conn.execute(
                _INSERT_RULE_SQL,
                _rule_insert_params(mapping_id, rule),
            )
            row = await cur.fetchone()
            if row is None:
                raise DatabaseError("Expected persisted mapping rule row was not returned.")
            inserted.append(_row_to_rule(row))
        return inserted

    async def _load_rules_for_mappings(
        self,
        active_conn: Any,
        mapping_ids: list[str],
    ) -> dict[str, list[MappingRuleResponse]]:
        cur = await active_conn.execute(_SELECT_RULES_FOR_MAPPINGS_SQL, (mapping_ids,))
        grouped: dict[str, list[MappingRuleResponse]] = {mid: [] for mid in mapping_ids}
        for row in await cur.fetchall():
            rule = _row_to_rule(row)
            grouped.setdefault(str(rule.mappingId), []).append(rule)
        return grouped
