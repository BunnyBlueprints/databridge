"""Unit and live PostgreSQL 16 tests for Task 05 Step 02 schema and mapping persistence."""

import inspect
import os
import re
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import psycopg
import psycopg.errors
import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.db.errors import DatabaseConflictError, DatabaseConstraintError, DatabaseError
from app.db.migrations import DEFAULT_MIGRATIONS_DIR, MigrationRunner, discover_migrations
from app.db.pool import DatabaseManager
from app.repositories import (
    ConnectionRepository,
    CredentialRepository,
    MappingRepository,
    SchemaRepository,
    translate_db_error,
)
from app.repositories import (
    mappings as mappings_repo_module,
)
from app.repositories import (
    schemas as schemas_repo_module,
)
from app.schemas.mappings import (
    MappingCreate,
    MappingRuleInput,
    MappingUpdate,
    MappingValidationIssue,
    MappingValidationSummary,
)
from app.schemas.schemas import (
    DiscoveredSchemaCreate,
    SchemaDefaultMetadata,
    SchemaFieldInput,
    SchemaObjectInput,
    compute_schema_fingerprint,
)
from app.services.connection_service import ConnectionService
from app.services.credential_encryption import CredentialEncryptionService, SqlStoredCredentials


def _sample_settings(db_url: str = "postgresql://user:pw@localhost:5432/databridge") -> Settings:
    return Settings(
        database_url=db_url,  # type: ignore[arg-type]
        credential_encryption_keys='{"k1":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="}',  # type: ignore[arg-type]
        credential_active_key_id="k1",
    )


def _sample_schema_objects() -> list[SchemaObjectInput]:
    return [
        SchemaObjectInput(
            objectKey="public.customers",
            objectName="customers",
            objectType="table",
            discoverySource="catalog",
            nativeMetadata={"schema": "public", "tableType": "BASE TABLE"},
            canonicalMetadata={"confidence": 1.0, "primaryKeyFields": ["id"]},
            fields=[
                SchemaFieldInput(
                    fieldPath="id",
                    fieldName="id",
                    nativeType="bigint",
                    canonicalType="integer",
                    nullable=False,
                    isRequired=False,
                    primaryKey=True,
                    uniqueKey=True,
                    defaultMetadata=SchemaDefaultMetadata(
                        hasDefault=True,
                        expression="nextval('customers_id_seq')",
                        isGenerated=True,
                    ),
                    ordinalPosition=0,
                    discoverySource="catalog",
                    metadata={"depth": 0, "isArray": False},
                ),
                SchemaFieldInput(
                    fieldPath="email",
                    fieldName="email",
                    nativeType="character varying(255)",
                    canonicalType="string",
                    nullable=False,
                    isRequired=True,
                    primaryKey=False,
                    uniqueKey=True,
                    ordinalPosition=1,
                    discoverySource="catalog",
                    metadata={"depth": 0, "isArray": False},
                ),
            ],
        ),
        SchemaObjectInput(
            objectKey="public.orders",
            objectName="orders",
            objectType="table",
            discoverySource="catalog",
            nativeMetadata={"schema": "public"},
            canonicalMetadata={"confidence": 1.0, "primaryKeyFields": ["order_id"]},
            fields=[
                SchemaFieldInput(
                    fieldPath="order_id",
                    fieldName="order_id",
                    nativeType="uuid",
                    canonicalType="string",
                    nullable=False,
                    isRequired=True,
                    primaryKey=True,
                    uniqueKey=True,
                    ordinalPosition=0,
                    discoverySource="catalog",
                ),
            ],
        ),
    ]


class _FakeCursor:
    def __init__(self, rows: list[tuple[Any, ...]] | None = None) -> None:
        self._rows = rows or []

    async def fetchone(self) -> tuple[Any, ...] | None:
        return self._rows[0] if self._rows else None

    async def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self._rows)


class _FakeTransaction:
    def __init__(self, conn: "_ScriptedConnection") -> None:
        self._conn = conn

    async def __aenter__(self) -> "_FakeTransaction":
        return self

    async def __aexit__(self, exc_type: Any, _exc: Any, _tb: Any) -> None:
        if exc_type is not None:
            self._conn.rollbacks += 1
        else:
            self._conn.commits += 1


class _ScriptedConnection:
    def __init__(self, *, fail_on_substring: str | None = None) -> None:
        self.executed: list[tuple[str, Any]] = []
        self.applied_rows: dict[str, tuple[str, str, str]] = {}
        self.rollbacks = 0
        self.commits = 0
        self._fail_on_substring = fail_on_substring

    def transaction(self) -> _FakeTransaction:
        return _FakeTransaction(self)

    async def execute(self, sql: str, params: Any = None) -> _FakeCursor:
        normalized = " ".join(sql.split())
        self.executed.append((normalized, params))
        if self._fail_on_substring and self._fail_on_substring in normalized:
            raise psycopg.errors.OperationalError("simulated mid-transaction failure")
        if normalized.startswith("SELECT version, name, applied_at::text FROM schema_migrations"):
            return _FakeCursor([self.applied_rows[k] for k in sorted(self.applied_rows)])
        if normalized.startswith("INSERT INTO schema_migrations"):
            version, name = params
            self.applied_rows[version] = (version, name, "2026-11-27T12:00:00+00:00")
        return _FakeCursor()

    async def executemany(self, sql: str, params_seq: list[Any]) -> None:
        normalized = " ".join(sql.split())
        self.executed.append((normalized, params_seq))
        if self._fail_on_substring and self._fail_on_substring in normalized:
            raise psycopg.errors.OperationalError("simulated batch insert failure")


class _ScriptedPool:
    def __init__(self, conn: _ScriptedConnection) -> None:
        self._conn = conn

    async def open(self, *, wait: bool = True, timeout: float = 30.0) -> None:
        del wait, timeout

    async def close(self) -> None:
        return None

    @asynccontextmanager
    async def connection(self, *, timeout: float = 30.0) -> AsyncIterator[_ScriptedConnection]:
        del timeout
        yield self._conn


def test_migrations_discover_0001_through_0004_in_order() -> None:
    migrations = discover_migrations()
    labels = [m.label for m in migrations]
    assert labels[:4] == [
        "0001_baseline",
        "0002_create_connections_and_credentials",
        "0003_create_discovered_schemas",
        "0004_create_mappings",
    ]


@pytest.mark.anyio
async def test_migration_runner_upgrades_from_0002_to_0003_and_0004_idempotently() -> None:
    conn = _ScriptedConnection()
    conn.applied_rows = {
        "0001": ("0001", "baseline", "2026-11-27T10:00:00+00:00"),
        "0002": ("0002", "create_connections_and_credentials", "2026-11-27T10:00:01+00:00"),
    }
    manager = DatabaseManager(_sample_settings(), pool_factory=lambda **_kw: _ScriptedPool(conn))
    await manager.startup()

    runner = MigrationRunner(manager)
    applied = await runner.apply_pending()
    assert [m.label for m in applied] == [
        "0003_create_discovered_schemas",
        "0004_create_mappings",
    ]
    assert await runner.apply_pending() == []
    status = await runner.status()
    assert status.current_version == "0004"
    assert status.pending == []


def test_schema_fingerprint_is_deterministic_and_sensitive_to_changes() -> None:
    objects_a = _sample_schema_objects()
    objects_b = list(reversed(_sample_schema_objects()))
    # Reverse fields inside first object too.
    objects_b[1] = objects_b[1].model_copy(update={"fields": list(reversed(objects_b[1].fields))})

    fp_a = compute_schema_fingerprint(objects_a)
    fp_b = compute_schema_fingerprint(objects_b)
    assert fp_a == fp_b
    assert re.fullmatch(r"[0-9a-f]{64}", fp_a)

    mutated = _sample_schema_objects()
    mutated[0].fields[1] = mutated[0].fields[1].model_copy(update={"isRequired": False})
    assert compute_schema_fingerprint(mutated) != fp_a


def test_pydantic_schema_and_mapping_validation_rejects_malformed_or_unsafe_inputs() -> None:
    conn_id = uuid.uuid4()
    dest_id = uuid.uuid4()

    # Invalid canonical type rejected.
    with pytest.raises(ValidationError):
        SchemaFieldInput(
            fieldPath="col",
            fieldName="col",
            nativeType="text",
            canonicalType="varchar_arbitrary",  # type: ignore[arg-type]
            ordinalPosition=0,
            discoverySource="catalog",
        )

    # Credential or raw file content keys inside metadata rejected.
    with pytest.raises(ValidationError):
        SchemaObjectInput(
            objectKey="public.bad",
            objectName="bad",
            objectType="table",
            discoverySource="catalog",
            nativeMetadata={"password": "leak"},
        )

    with pytest.raises(ValidationError):
        SchemaFieldInput(
            fieldPath="bad",
            fieldName="bad",
            nativeType="text",
            canonicalType="string",
            ordinalPosition=0,
            discoverySource="inferred_sample",
            metadata={"fileContent": "a,b\n1,2"},
        )

    with pytest.raises(ValidationError):
        DiscoveredSchemaCreate(
            connectionId=conn_id,
            discoveryStatus="completed",
            schemaFingerprint="not-a-sha256",
            discoverySource="catalog",
        )

    # Invalid mapping rule shapes rejected.
    with pytest.raises(ValidationError):
        MappingRuleInput(
            mappingType="direct",
            destinationFieldPath="email",
            sourceFieldPath=None,
            ordinalPosition=0,
        )

    with pytest.raises(ValidationError):
        MappingRuleInput(
            mappingType="constant",
            destinationFieldPath="source",
            sourceFieldPath="should_not_exist",
            constantValue="prod",
            ordinalPosition=0,
        )

    with pytest.raises(ValidationError):
        MappingRuleInput(
            mappingType="constant",
            destinationFieldPath="source",
            ordinalPosition=0,
        )

    with pytest.raises(ValidationError):
        MappingRuleInput(
            mappingType="transform",
            sourceFieldPath="created_at",
            destinationFieldPath="created_at",
            transformRef={"presetName": "trim", "params": {"expression": "x.strip()"}},  # type: ignore[arg-type]
            ordinalPosition=0,
        )

    # Duplicate destination fields & duplicate ordinals rejected.
    with pytest.raises(ValidationError):
        MappingCreate(
            name="dup-dest",
            sourceConnectionId=conn_id,
            sourceObjectKey="src",
            destinationConnectionId=dest_id,
            destinationObjectKey="dst",
            rules=[
                MappingRuleInput(
                    mappingType="direct",
                    sourceFieldPath="a",
                    destinationFieldPath="target",
                    ordinalPosition=0,
                ),
                MappingRuleInput(
                    mappingType="direct",
                    sourceFieldPath="b",
                    destinationFieldPath="target",
                    ordinalPosition=1,
                ),
            ],
        )

    with pytest.raises(ValidationError):
        MappingCreate(
            name="dup-ord",
            sourceConnectionId=conn_id,
            sourceObjectKey="src",
            destinationConnectionId=dest_id,
            destinationObjectKey="dst",
            rules=[
                MappingRuleInput(
                    mappingType="direct",
                    sourceFieldPath="a",
                    destinationFieldPath="t1",
                    ordinalPosition=0,
                ),
                MappingRuleInput(
                    mappingType="direct",
                    sourceFieldPath="b",
                    destinationFieldPath="t2",
                    ordinalPosition=0,
                ),
            ],
        )


def test_repositories_never_reference_credentials_and_translate_db_errors() -> None:
    schemas_src = inspect.getsource(schemas_repo_module)
    mappings_src = inspect.getsource(mappings_repo_module)
    assert "connection_credentials" not in schemas_src
    assert "connection_credentials" not in mappings_src

    assert isinstance(
        translate_db_error(psycopg.errors.CheckViolation("check failed")),
        DatabaseConstraintError,
    )
    restrict_err = translate_db_error(psycopg.errors.RestrictViolation("restrict"))
    assert isinstance(restrict_err, DatabaseConflictError)


@pytest.mark.anyio
async def test_repository_transaction_rollback_when_nested_insert_fails() -> None:
    conn = _ScriptedConnection(fail_on_substring="INSERT INTO schema_fields")
    manager = DatabaseManager(_sample_settings(), pool_factory=lambda **_kw: _ScriptedPool(conn))
    await manager.startup()

    schema_repo = SchemaRepository(manager)
    # Fail on INSERT INTO schema_objects / schema_fields should roll back the whole transaction.
    payload = DiscoveredSchemaCreate(
        connectionId=uuid.uuid4(),
        discoveryStatus="completed",
        discoverySource="catalog",
        objects=_sample_schema_objects(),
    )
    # _ScriptedConnection returns no row for INSERT INTO schema_objects -> raises DatabaseError.
    with pytest.raises(DatabaseError):
        await schema_repo.replace_snapshot(payload)
    assert conn.rollbacks == 1
    assert conn.commits == 0


@pytest.mark.anyio
@pytest.mark.skipif(
    not os.environ.get("DATABRIDGE_TEST_DATABASE_URL"),
    reason="DATABRIDGE_TEST_DATABASE_URL not set; skipping live PostgreSQL Step 02 test",
)
async def test_live_postgresql_step02_migrations_schemas_and_mappings() -> None:
    """Comprehensive live PostgreSQL 16 verification for migrations 0003/0004 and repositories."""
    db_url = os.environ["DATABRIDGE_TEST_DATABASE_URL"]
    settings = Settings(
        database_url=db_url,  # type: ignore[arg-type]
        credential_encryption_keys='{"k1":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="}',  # type: ignore[arg-type]
        credential_active_key_id="k1",
    )

    # 1. Verify clean upgrade from 0002 -> 0003 & 0004 inside an isolated PostgreSQL schema.
    isolated_schema = f"step02_upgrade_{uuid.uuid4().hex[:10]}"
    async with await psycopg.AsyncConnection.connect(db_url) as setup_conn:
        await setup_conn.execute(f"CREATE SCHEMA {isolated_schema}")
        await setup_conn.execute(f"SET search_path TO {isolated_schema}")
        await setup_conn.execute(
            """
            CREATE TABLE schema_migrations (
                version VARCHAR(64) PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        all_migrations = discover_migrations(DEFAULT_MIGRATIONS_DIR)
        for mig in all_migrations[:2]:
            await setup_conn.execute(mig.path.read_text(encoding="utf-8"))
            await setup_conn.execute(
                "INSERT INTO schema_migrations (version, name) VALUES (%s, %s)",
                (mig.version, mig.name),
            )
        # Apply 0003 and 0004 from clean 0002 state.
        for mig in all_migrations[2:]:
            await setup_conn.execute(mig.path.read_text(encoding="utf-8"))
            await setup_conn.execute(
                "INSERT INTO schema_migrations (version, name) VALUES (%s, %s)",
                (mig.version, mig.name),
            )
        # Verify re-applying 0003 and 0004 DDL statements is idempotent (`IF NOT EXISTS`).
        for mig in all_migrations[2:]:
            await setup_conn.execute(mig.path.read_text(encoding="utf-8"))
        await setup_conn.execute(f"DROP SCHEMA {isolated_schema} CASCADE")
        await setup_conn.commit()

    # 2. Run MigrationRunner idempotently on main DB and test repositories end-to-end.
    manager = DatabaseManager(settings)
    await manager.startup()
    src_conn_id = str(uuid.uuid4())
    dst_conn_id = str(uuid.uuid4())
    mapping_id: str | None = None

    try:
        runner = MigrationRunner(manager)
        await runner.apply_pending()
        assert await runner.apply_pending() == []
        status = await runner.status()
        assert int(status.current_version or "0") >= 4

        conn_repo = ConnectionRepository(manager)
        cred_repo = CredentialRepository(manager)
        schema_repo = SchemaRepository(manager)
        mapping_repo = MappingRepository(manager)
        enc_svc = CredentialEncryptionService.from_settings(settings)
        conn_svc = ConnectionService(
            connection_repo=conn_repo,
            credential_repo=cred_repo,
            encryption_service=enc_svc,
            connection_test_service=None,  # type: ignore[arg-type]
        )

        suffix = uuid.uuid4().hex[:8]
        await conn_repo.create(
            connection_id=src_conn_id,
            name=f"step02-src-{suffix}",
            connector_type="PostgreSQL",
            description="Source test DB",
            endpoint="db.src.internal:5432/app",
            config={"host": "db.src.internal", "port": 5432, "database": "app"},
            auth_configured=True,
            status="Disconnected",
        )
        await cred_repo.upsert(
            connection_id=src_conn_id,
            encrypted=enc_svc.encrypt(
                connection_id=src_conn_id,
                connector_type="PostgreSQL",
                credentials=SqlStoredCredentials(
                    username="src_user",
                    password="src_secret_pw",  # type: ignore[arg-type]
                ),
            ),
        )
        await conn_repo.create(
            connection_id=dst_conn_id,
            name=f"step02-dst-{suffix}",
            connector_type="MySQL",
            description="Dest test DB",
            endpoint="db.dst.internal:3306/dw",
            config={"host": "db.dst.internal", "port": 3306, "database": "dw"},
            auth_configured=False,
            status="Disconnected",
        )

        # 3. Create initial schema snapshot + verify object & field persistence.
        v1_objects = _sample_schema_objects()
        snap_v1 = await schema_repo.replace_snapshot(
            DiscoveredSchemaCreate(
                connectionId=uuid.UUID(src_conn_id),
                discoveryStatus="completed",
                discoverySource="catalog",
                metadata={"schemaVersion": 1},
                objects=v1_objects,
            )
        )
        assert snap_v1.schemaFingerprint == compute_schema_fingerprint(v1_objects)
        assert len(snap_v1.objects) == 2

        listed_objs, total_objs = await schema_repo.list_objects(
            src_conn_id,
            search_query="cust",
        )
        assert total_objs == 1
        assert listed_objs[0].objectKey == "public.customers"
        assert listed_objs[0].fieldCount == 2

        obj_with_fields = await schema_repo.get_object_with_fields(
            src_conn_id,
            str(listed_objs[0].id),
        )
        assert obj_with_fields is not None
        assert [f.fieldPath for f in obj_with_fields.fields] == ["id", "email"]
        assert obj_with_fields.fields[0].primaryKey is True
        assert obj_with_fields.fields[0].defaultMetadata is not None
        assert obj_with_fields.fields[1].isRequired is True

        # 4. Verify latest snapshot replacement keeps strictly one snapshot & cascades stale rows.
        v2_objects = [v1_objects[0]]
        snap_v2 = await schema_repo.replace_snapshot(
            DiscoveredSchemaCreate(
                connectionId=uuid.UUID(src_conn_id),
                discoveryStatus="partial",
                discoverySource="catalog",
                metadata={"schemaVersion": 2},
                objects=v2_objects,
            )
        )
        assert snap_v2.id != snap_v1.id
        latest = await schema_repo.get_latest_by_connection_id(src_conn_id)
        assert latest is not None
        assert len(latest.objects) == 1
        assert await schema_repo.get_object_by_key(src_conn_id, "public.orders") is None

        # Verify failed snapshot replacement inside DB transaction preserves snap_v2.
        with pytest.raises(DatabaseConstraintError):
            async with schema_repo.transaction() as tx_conn:
                await tx_conn.execute(
                    "DELETE FROM discovered_schemas WHERE connection_id = %s",
                    (src_conn_id,),
                )
                await tx_conn.execute(
                    """
                    INSERT INTO discovered_schemas (
                        id, connection_id, discovery_status, schema_fingerprint, discovery_source
                    ) VALUES (%s, %s, %s, %s, %s)
                    """,
                    (str(uuid.uuid4()), src_conn_id, "bad_status", "a" * 64, "catalog"),
                )
        still_v2 = await schema_repo.get_latest_by_connection_id(src_conn_id)
        assert still_v2 is not None
        assert still_v2.id == snap_v2.id

        # 5. Mapping creation with unsorted rules + direct/constant(null)/transform shapes.
        created_mapping = await mapping_repo.create(
            MappingCreate(
                name=f"Customers Sync {suffix}",
                description="Live mapping persistence check",
                sourceConnectionId=uuid.UUID(src_conn_id),
                sourceObjectKey="public.customers",
                destinationConnectionId=uuid.UUID(dst_conn_id),
                destinationObjectKey="customers",
                status="draft",
                sourceSchemaFingerprint=snap_v2.schemaFingerprint,
                rules=[
                    MappingRuleInput(
                        mappingType="constant",
                        destinationFieldPath="deleted_reason",
                        constantValue=None,
                        ordinalPosition=2,
                    ),
                    MappingRuleInput(
                        mappingType="direct",
                        sourceFieldPath="email",
                        destinationFieldPath="email_addr",
                        ordinalPosition=0,
                    ),
                    MappingRuleInput(
                        mappingType="transform",
                        sourceFieldPath="id",
                        destinationFieldPath="external_id",
                        transformRef={
                            "presetName": "to_string",
                            "params": {"prefix": "pg_"},
                        },  # type: ignore[arg-type]
                        ordinalPosition=1,
                    ),
                ],
            )
        )
        mapping_id = str(created_mapping.id)
        assert created_mapping.mappingVersion == 1
        assert [r.ordinalPosition for r in created_mapping.rules] == [0, 1, 2]
        assert [r.destinationFieldPath for r in created_mapping.rules] == [
            "email_addr",
            "external_id",
            "deleted_reason",
        ]
        assert created_mapping.rules[2].constantValue is None
        assert created_mapping.rules[1].transformRef is not None
        assert created_mapping.rules[1].transformRef.presetName == "to_string"

        # 6. Duplicate destination assignment constraint in PostgreSQL -> DatabaseConflictError.
        with pytest.raises(DatabaseConflictError) as dup_rule_exc:
            async with mapping_repo.transaction() as tx_conn:
                await tx_conn.execute(
                    """
                    INSERT INTO mapping_rules (
                        id, mapping_id, source_field_path, destination_field_path,
                        mapping_type, ordinal_position
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (str(uuid.uuid4()), mapping_id, "id", "email_addr", "direct", 99),
                )
        assert dup_rule_exc.value.error_code == "DUPLICATE_DESTINATION_FIELD"

        # 7. Update increments mappingVersion and replaces rules atomically.
        updated_mapping = await mapping_repo.update(
            mapping_id,
            MappingUpdate(
                name=f"Customers Sync Updated {suffix}",
                sourceConnectionId=uuid.UUID(src_conn_id),
                sourceObjectKey="public.customers",
                destinationConnectionId=uuid.UUID(dst_conn_id),
                destinationObjectKey="customers",
                status="valid",
                sourceSchemaFingerprint=snap_v2.schemaFingerprint,
                validationSummary=MappingValidationSummary(
                    isValid=True,
                    errorCount=0,
                    warningCount=0,
                    validatedAt=datetime.now(UTC),
                    issues=[
                        MappingValidationIssue(
                            severity="warning",
                            code="INFO",
                            message="Check complete.",
                        )
                    ],
                ),
                rules=[
                    MappingRuleInput(
                        mappingType="direct",
                        sourceFieldPath="email",
                        destinationFieldPath="email_addr",
                        ordinalPosition=0,
                    )
                ],
            ),
        )
        assert updated_mapping is not None
        assert updated_mapping.mappingVersion == 2
        assert len(updated_mapping.rules) == 1

        # 8. Connection deletion restricted while referenced by mappings (preserves credentials!).
        assert await mapping_repo.count_by_connection_id(src_conn_id) == 1
        with pytest.raises(DatabaseConflictError) as restrict_exc:
            await conn_svc.delete_connection(src_conn_id)
        assert restrict_exc.value.error_code == "CONNECTION_IN_USE"
        # Transaction rolled back so encrypted credential row remains intact!
        assert await cred_repo.get_by_connection_id(src_conn_id) is not None

        # 9. Deleting mapping cascades mapping_rules cleanly, unblocking connection delete.
        assert await mapping_repo.delete(mapping_id) is True
        mapping_id = None
        async with manager.connection() as check_conn:
            cur = await check_conn.execute(
                "SELECT COUNT(*) FROM mapping_rules WHERE mapping_id = %s",
                (str(updated_mapping.id),),
            )
            assert (await cur.fetchone()) == (0,)

        # Deleting connections now succeeds and cascades discovered_schemas -> objects -> fields.
        await conn_svc.delete_connection(src_conn_id)
        await conn_svc.delete_connection(dst_conn_id)
        assert await schema_repo.get_latest_by_connection_id(src_conn_id) is None
    finally:
        # Defensive cleanup if an assertion failed mid-test.
        try:
            async with manager.connection() as cleanup_conn:
                if mapping_id is not None:
                    await cleanup_conn.execute("DELETE FROM mappings WHERE id = %s", (mapping_id,))
                await cleanup_conn.execute(
                    "DELETE FROM connections WHERE id = ANY(%s::uuid[])",
                    ([src_conn_id, dst_conn_id],),
                )
                await cleanup_conn.commit()
        finally:
            await manager.shutdown()
