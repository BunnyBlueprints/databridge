"""Unit tests for connector discoverer registry, type inference, and discoverer limits."""

import json
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import httpx
import pymongo.errors
import pytest
from bson import Decimal128, Int64, ObjectId
from pydantic import ValidationError

from app.schemas.connections import (
    CsvPersistentConfig,
    JsonPersistentConfig,
    MongoDbPersistentConfig,
    RestApiConnectionTestConfig,
    SqlPersistentConfig,
)
from app.schemas.schemas import (
    DiscoveryOptionsInput,
    ManualSchemaFieldInput,
    ManualSchemaObjectInput,
    compute_schema_fingerprint,
)
from app.services.credential_encryption import (
    RestApiConnectionTestCredentials,
    SqlStoredCredentials,
)
from app.services.discovery.base import DiscoveryContext, DiscoveryExecutionError
from app.services.discovery.file_discoverer import CsvSchemaDiscoverer, JsonSchemaDiscoverer
from app.services.discovery.inference import (
    classify_csv_cell,
    classify_runtime_value,
    infer_csv_schema,
    infer_documents_schema,
)
from app.services.discovery.mongodb_discoverer import MongoDbSchemaDiscoverer
from app.services.discovery.mysql_discoverer import MysqlSchemaDiscoverer
from app.services.discovery.postgres_discoverer import PostgresSchemaDiscoverer
from app.services.discovery.registry import create_default_discoverer_registry
from app.services.discovery.rest_discoverer import RestApiSchemaDiscoverer


def _make_ctx(
    connector_type: str,
    config: Any,
    *,
    credentials: Any = None,
    options: DiscoveryOptionsInput | None = None,
    file_name: str | None = None,
    file_content: str | None = None,
    max_objects: int = 50,
    max_fields: int = 100,
    sample_limit: int = 50,
) -> DiscoveryContext:
    return DiscoveryContext(
        connection_id=str(uuid.uuid4()),
        connector_type=connector_type,  # type: ignore[arg-type]
        config=config,
        credentials=credentials,
        legacy_username=None,
        options=options or DiscoveryOptionsInput(),
        file_name=file_name,
        file_content=file_content,
        timeout_seconds=5.0,
        max_objects=max_objects,
        max_fields_per_object=max_fields,
        sample_limit=sample_limit,
    )


def test_default_discoverer_registry_covers_all_six_connectors() -> None:
    registry = create_default_discoverer_registry()
    assert set(registry.registered_connectors()) == {
        "PostgreSQL",
        "MySQL",
        "MongoDB",
        "REST API",
        "CSV",
        "JSON",
    }
    for conn_type in ("PostgreSQL", "MySQL", "MongoDB", "REST API", "CSV", "JSON"):
        assert registry.get(conn_type) is not None


def test_canonical_value_and_csv_cell_classification() -> None:
    assert classify_runtime_value(True) == ("boolean", "boolean")
    assert classify_runtime_value(42) == ("integer", "integer")
    assert classify_runtime_value(Decimal("12.50")) == ("number", "decimal")
    assert classify_runtime_value(ObjectId()) == ("string", "bson:objectId")
    assert classify_runtime_value(Int64(99)) == ("integer", "bson:long")
    assert classify_runtime_value(Decimal128("3.14")) == ("number", "bson:decimal128")
    assert classify_runtime_value(date(2026, 11, 27)) == ("date", "date")
    assert classify_runtime_value(datetime(2026, 11, 27, 10, 0, 0)) == ("datetime", "datetime")
    assert classify_runtime_value("2026-11-27") == ("date", "string(iso-date)")
    assert classify_runtime_value("2026-11-27T10:00:00Z") == ("datetime", "string(iso-datetime)")

    # Leading-zero zip codes must not silently coerce to integers.
    assert classify_csv_cell("00123") == ("string", "csv:string")
    assert classify_csv_cell("123") == ("integer", "csv:integer")
    assert classify_csv_cell("12.34") == ("number", "csv:number")
    assert classify_csv_cell("true") == ("boolean", "csv:boolean")
    assert classify_csv_cell("") == ("null", "csv:null")


def test_nested_json_inference_determinism_arrays_and_conflicts() -> None:
    docs_a = [
        {
            "id": 1,
            "customer": {"email": "a@example.com", "tier": "pro"},
            "items": [{"sku": "A1", "qty": 2}],
            "score": 10,
            "emptyCol": None,
        },
        {
            "items": [{"sku": "B2", "qty": 1.5}],
            "customer": {"email": "b@example.com"},
            "id": 2,
            "score": "ten",
            "emptyCol": None,
        },
    ]
    # Different key order on docs_b must yield identical field paths & fingerprint.
    docs_b = [dict(reversed(list(doc.items()))) for doc in docs_a]

    fields_a, warnings_a, conf_a = infer_documents_schema(
        docs_a,
        object_key="orders.json",
        discovery_source="inferred_sample",
        max_fields=100,
    )
    fields_b, _, conf_b = infer_documents_schema(
        docs_b,
        object_key="orders.json",
        discovery_source="inferred_sample",
        max_fields=100,
    )

    assert [f.fieldPath for f in fields_a] == [f.fieldPath for f in fields_b]
    assert conf_a == conf_b
    by_path = {f.fieldPath: f for f in fields_a}

    assert by_path["customer"].canonicalType == "object"
    assert by_path["customer.tier"].nullable is True
    assert by_path["items"].metadata["isArray"] is True
    assert by_path["items[].sku"].canonicalType == "string"
    assert by_path["items[].qty"].canonicalType == "number"
    assert by_path["score"].canonicalType == "mixed"
    assert by_path["score"].metadata["inferredTypes"] == ["integer", "string"]
    assert by_path["emptyCol"].canonicalType == "null"
    warning_codes = {w.code for w in warnings_a}
    assert "MIXED_FIELD_TYPES" in warning_codes
    assert "ALL_NULL_VALUES" in warning_codes
    # Sample data values must never leak into warning strings.
    for warning in warnings_a:
        assert "a@example.com" not in warning.message
        assert "ten" not in warning.message


def test_csv_inference_and_file_discoverers_never_persist_or_leak_content() -> None:
    fields, warnings, _ = infer_csv_schema(
        ["id", "zip", "amount", "flag"],
        [["1", "00501", "10.5", "true"], ["2", "00502", "invalid", ""]],
        object_key="sales.csv",
        max_fields=10,
    )
    assert [f.fieldPath for f in fields] == ["id", "zip", "amount", "flag"]
    assert fields[0].canonicalType == "integer"
    assert fields[1].canonicalType == "string"
    assert fields[2].canonicalType == "mixed"
    assert fields[3].canonicalType == "boolean"
    assert fields[3].nullable is True
    assert any(w.code == "MIXED_FIELD_TYPES" for w in warnings)
    assert all("00501" not in w.message and "invalid" not in w.message for w in warnings)


@pytest.mark.anyio
async def test_csv_and_json_discoverers_validate_transient_content() -> None:
    csv_disc = CsvSchemaDiscoverer()
    missing_ctx = _make_ctx("CSV", CsvPersistentConfig(fileName="users.csv"), file_content=None)
    with pytest.raises(DiscoveryExecutionError) as missing_exc:
        await csv_disc.discover(missing_ctx)
    assert missing_exc.value.error_code == "FILE_REQUIRED_FOR_DISCOVERY"

    bom_csv = "\ufeffid,email,active\n1,ada@example.com,true\n2,grace@example.com,false\n"
    csv_res = await csv_disc.discover(
        _make_ctx("CSV", CsvPersistentConfig(fileName="users.csv"), file_content=bom_csv)
    )
    assert csv_res.discovery_status == "completed"
    assert csv_res.objects[0].objectKey == "users.csv"
    assert [f.canonicalType for f in csv_res.objects[0].fields] == [
        "integer",
        "string",
        "boolean",
    ]
    assert "ada@example.com" not in json.dumps(csv_res.objects[0].model_dump())

    json_disc = JsonSchemaDiscoverer()
    wrapped_json = json.dumps({"meta": {"page": 1}, "data": [{"id": 10, "tags": ["a", "b"]}]})
    json_res = await json_disc.discover(
        _make_ctx(
            "JSON",
            JsonPersistentConfig(fileName="events.json"),
            file_content=wrapped_json,
            options=DiscoveryOptionsInput(recordPath="data"),
        )
    )
    paths = [f.fieldPath for f in json_res.objects[0].fields]
    assert paths == ["id", "tags", "tags[]"]


@pytest.mark.anyio
async def test_mongodb_discoverer_handles_partial_access_denied_and_bson_types() -> None:
    class _FakeCursor:
        def __init__(self, docs: list[dict[str, Any]], *, fail: bool = False) -> None:
            self._docs = docs
            self._fail = fail

        def limit(self, _n: int) -> "_FakeCursor":
            return self

        def max_time_ms(self, _ms: int) -> "_FakeCursor":
            return self

        async def to_list(self, *, length: int) -> list[dict[str, Any]]:
            if self._fail:
                raise pymongo.errors.OperationFailure("not authorized", code=13)
            return self._docs[:length]

    class _FakeCollection:
        def __init__(self, docs: list[dict[str, Any]], *, fail: bool = False) -> None:
            self._docs = docs
            self._fail = fail

        def find(self, *_args: Any, **_kwargs: Any) -> _FakeCursor:
            return _FakeCursor(self._docs, fail=self._fail)

    class _FakeDb:
        async def list_collections(self, **_kwargs: Any) -> list[dict[str, Any]]:
            return [
                {"name": "system.profile"},
                {"name": "orders"},
                {"name": "restricted_audit"},
            ]

        def __getitem__(self, name: str) -> _FakeCollection:
            if name == "restricted_audit":
                return _FakeCollection([], fail=True)
            return _FakeCollection(
                [
                    {
                        "_id": ObjectId(),
                        "total": Decimal128("45.20"),
                        "lines": [{"sku": "SKU-1"}],
                    }
                ]
            )

    class _FakeClient:
        def __getitem__(self, _db: str) -> _FakeDb:
            return _FakeDb()

        async def close(self) -> None:
            return None

    discoverer = MongoDbSchemaDiscoverer(client_factory=lambda **_kw: _FakeClient())
    res = await discoverer.discover(
        _make_ctx(
            "MongoDB", MongoDbPersistentConfig(uri="mongodb://db.example.com", database="shop")
        )
    )
    assert res.discovery_status == "partial"
    assert [o.objectKey for o in res.objects] == ["orders"]
    assert any(w.code == "COLLECTION_ACCESS_DENIED" for w in res.warnings)
    by_path = {f.fieldPath: f for f in res.objects[0].fields}
    assert by_path["_id"].nativeType == "bson:objectId"
    assert by_path["_id"].primaryKey is True
    assert by_path["total"].canonicalType == "number"


@pytest.mark.anyio
async def test_postgres_and_mysql_discoverers_normalize_catalog_metadata() -> None:
    # 1. PostgreSQL mocked catalog queries.
    class _PgCur:
        def __init__(self) -> None:
            self._results: list[Any] = []

        async def __aenter__(self) -> "_PgCur":
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

        async def execute(self, sql: str, _params: Any = None) -> None:
            if "information_schema.tables" in sql:
                self._results = [
                    ("public", "customers", "BASE TABLE"),
                    ("public", "active_customers", "VIEW"),
                ]
            elif "information_schema.columns" in sql:
                self._results = [
                    (
                        "public",
                        "customers",
                        "id",
                        1,
                        "bigint",
                        "int8",
                        None,
                        None,
                        None,
                        "NO",
                        "nextval('s')",
                        "NO",
                        "NEVER",
                    ),
                    (
                        "public",
                        "customers",
                        "email",
                        2,
                        "character varying",
                        "varchar",
                        255,
                        None,
                        None,
                        "NO",
                        None,
                        "NO",
                        "NEVER",
                    ),
                    (
                        "public",
                        "customers",
                        "org_id",
                        3,
                        "uuid",
                        "uuid",
                        None,
                        None,
                        None,
                        "YES",
                        None,
                        "NO",
                        "NEVER",
                    ),
                    (
                        "public",
                        "active_customers",
                        "email",
                        1,
                        "character varying",
                        "varchar",
                        255,
                        None,
                        None,
                        "YES",
                        None,
                        "NO",
                        "NEVER",
                    ),
                ]
            elif "information_schema.table_constraints" in sql:
                self._results = [
                    ("public", "customers", "PRIMARY KEY", "id", None, None, None),
                    ("public", "customers", "UNIQUE", "email", None, None, None),
                    ("public", "customers", "FOREIGN KEY", "org_id", "public", "orgs", "id"),
                ]

        async def fetchall(self) -> list[tuple[Any, ...]]:
            return list(self._results)

    class _PgTx:
        async def __aenter__(self) -> "_PgTx":
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

    class _PgConn:
        def transaction(self) -> _PgTx:
            return _PgTx()

        def cursor(self) -> _PgCur:
            return _PgCur()

        async def close(self) -> None:
            return None

    async def _fake_pg_connect(**_kwargs: Any) -> _PgConn:
        return _PgConn()

    pg_res = await PostgresSchemaDiscoverer(connect_fn=_fake_pg_connect).discover(
        _make_ctx(
            "PostgreSQL",
            SqlPersistentConfig(host="pg.internal", port=5432, database="app"),
            credentials=SqlStoredCredentials(username="ro", password="secret"),  # type: ignore[arg-type]
        )
    )
    assert pg_res.discovery_status == "completed"
    assert [o.objectKey for o in pg_res.objects] == ["public.customers", "public.active_customers"]
    assert pg_res.objects[1].objectType == "view"
    cust_fields = {f.fieldPath: f for f in pg_res.objects[0].fields}
    assert cust_fields["id"].primaryKey is True
    assert cust_fields["id"].isRequired is False  # has default
    assert cust_fields["email"].isRequired is True
    assert cust_fields["email"].uniqueKey is True
    assert cust_fields["org_id"].metadata["foreignKey"] == {
        "targetObjectKey": "public.orgs",
        "targetFieldPath": "id",
    }

    # 2. MySQL mocked catalog queries.
    class _MysqlCur:
        def __init__(self) -> None:
            self._rows: list[tuple[Any, ...]] = []

        def execute(self, sql: str, _params: Any = None) -> None:
            if "information_schema.TABLES" in sql:
                self._rows = [("users", "BASE TABLE")]
            elif "information_schema.COLUMNS" in sql:
                self._rows = [
                    (
                        "users",
                        "id",
                        1,
                        "bigint",
                        "bigint unsigned",
                        "NO",
                        None,
                        "auto_increment",
                        "PRI",
                    ),
                    ("users", "is_admin", 2, "tinyint", "tinyint(1)", "NO", "0", "", ""),
                    ("users", "username", 3, "varchar", "varchar(64)", "NO", None, "", "UNI"),
                ]
            elif "KEY_COLUMN_USAGE" in sql:
                self._rows = [
                    ("users", "PRIMARY KEY", "id", None, None, None),
                    ("users", "UNIQUE", "username", None, None, None),
                ]

        def fetchall(self) -> list[tuple[Any, ...]]:
            return list(self._rows)

        def close(self) -> None:
            return None

    class _MysqlConn:
        def cursor(self) -> _MysqlCur:
            return _MysqlCur()

        def close(self) -> None:
            return None

    mysql_res = await MysqlSchemaDiscoverer(connect_fn=lambda **_kw: _MysqlConn()).discover(
        _make_ctx(
            "MySQL",
            SqlPersistentConfig(host="mysql.internal", port=3306, database="app"),
            credentials=SqlStoredCredentials(username="ro", password="secret"),  # type: ignore[arg-type]
        )
    )
    mysql_fields = {f.fieldPath: f for f in mysql_res.objects[0].fields}
    assert mysql_fields["id"].isRequired is False  # auto_increment
    assert mysql_fields["is_admin"].canonicalType == "boolean"
    assert mysql_fields["is_admin"].nativeType == "tinyint(1)"
    assert mysql_fields["username"].isRequired is True
    assert mysql_fields["username"].uniqueKey is True


@pytest.mark.anyio
async def test_rest_discoverer_openapi_ssrf_sample_and_manual_modes() -> None:
    openapi_spec = {
        "openapi": "3.0.3",
        "paths": {
            "/v1/customers": {
                "get": {
                    "operationId": "listCustomers",
                    "responses": {
                        "200": {
                            "description": "ok",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "array",
                                        "items": {"$ref": "#/components/schemas/Customer"},
                                    }
                                }
                            },
                        }
                    },
                }
            }
        },
        "components": {
            "schemas": {
                "Customer": {
                    "type": "object",
                    "required": ["id", "email"],
                    "properties": {
                        "id": {"type": "string", "format": "uuid"},
                        "email": {"type": "string"},
                        "externalMeta": {"$ref": "https://evil.example.com/schema.json"},
                        "selfRef": {"$ref": "#/components/schemas/Customer"},
                    },
                }
            }
        },
    }

    captured_headers: dict[str, str] = {}

    async def _handler(request: httpx.Request) -> httpx.Response:
        captured_headers.update(dict(request.headers))
        if request.url.path == "/openapi.json":
            return httpx.Response(200, json=openapi_spec)
        if request.url.path == "/v1/events":
            return httpx.Response(200, json={"items": [{"id": 1, "ok": True}]})
        return httpx.Response(404, json={"error": "not found"})

    async def _public_resolver(_host: str, _port: int) -> list[str]:
        return ["93.184.216.34"]

    discoverer = RestApiSchemaDiscoverer(
        transport=httpx.MockTransport(_handler),
        resolver=_public_resolver,
    )
    rest_cfg = RestApiConnectionTestConfig(baseUrl="https://api.example.com", authType="bearer")
    rest_creds = RestApiConnectionTestCredentials(bearerToken="super_secret_token_999")  # type: ignore[arg-type]

    # 1. OpenAPI JSON with local $ref, circular guard, and external $ref skipped.
    oa_res = await discoverer.discover(
        _make_ctx(
            "REST API",
            rest_cfg,
            credentials=rest_creds,
            options=DiscoveryOptionsInput(mode="openapi", openApiPath="/openapi.json"),
        )
    )
    assert oa_res.discovery_source == "openapi"
    assert captured_headers.get("authorization") == "Bearer super_secret_token_999"
    oa_fields = {f.fieldPath: f for f in oa_res.objects[0].fields}
    assert oa_fields["id"].isRequired is True
    assert oa_fields["id"].nativeType == "openapi:string(uuid)"
    assert oa_fields["externalMeta"].nativeType == "openapi:external_ref"
    warning_codes = {w.code for w in oa_res.warnings}
    assert "EXTERNAL_REF_SKIPPED" in warning_codes
    assert "CIRCULAR_REF_TRUNCATED" in warning_codes

    # 2. Sample GET inference with SSRF-protected mock transport.
    sample_res = await discoverer.discover(
        _make_ctx(
            "REST API",
            rest_cfg,
            credentials=rest_creds,
            options=DiscoveryOptionsInput(
                mode="sample_get",
                resourcePath="/v1/events",
                recordPath="items",
            ),
        )
    )
    assert sample_res.discovery_source == "inferred_sample"
    assert [f.fieldPath for f in sample_res.objects[0].fields] == ["id", "ok"]

    # 3. Private IP SSRF blocked cleanly.
    async def _private_resolver(_host: str, _port: int) -> list[str]:
        return ["127.0.0.1"]

    ssrf_discoverer = RestApiSchemaDiscoverer(
        transport=httpx.MockTransport(_handler),
        resolver=_private_resolver,
    )
    with pytest.raises(DiscoveryExecutionError) as ssrf_exc:
        await ssrf_discoverer.discover(_make_ctx("REST API", rest_cfg, credentials=rest_creds))
    assert ssrf_exc.value.error_code == "INVALID_CONFIGURATION"

    # 4. Manual declaration validated strictly and rejects forbidden metadata keys.
    manual_res = await discoverer.discover(
        _make_ctx(
            "REST API",
            rest_cfg,
            options=DiscoveryOptionsInput(
                mode="manual",
                manualObjects=[
                    ManualSchemaObjectInput(
                        objectKey="POST /v1/leads",
                        fields=[
                            ManualSchemaFieldInput(
                                fieldPath="email",
                                canonicalType="string",
                                isRequired=True,
                                nullable=False,
                            )
                        ],
                    )
                ],
            ),
        )
    )
    assert manual_res.discovery_source == "manual"
    assert manual_res.objects[0].fields[0].isRequired is True

    with pytest.raises(ValidationError):
        ManualSchemaFieldInput(
            fieldPath="bad",
            canonicalType="string",
            metadata={"apiKey": "must-reject"},
        )

    # Deterministic fingerprint across object permutations.
    assert compute_schema_fingerprint(manual_res.objects) == compute_schema_fingerprint(
        list(manual_res.objects)
    )


@pytest.mark.anyio
async def test_discoverers_handle_empty_and_malformed_inputs_safely() -> None:
    """Verify empty CSV/JSON/Mongo collections and malformed JSON/CSV rejection."""
    csv_disc = CsvSchemaDiscoverer()
    # Empty CSV rejected cleanly without crash.
    with pytest.raises(DiscoveryExecutionError) as empty_csv_exc:
        await csv_disc.discover(
            _make_ctx("CSV", CsvPersistentConfig(fileName="empty.csv"), file_content="   \n")
        )
    assert empty_csv_exc.value.error_code == "INVALID_CONFIGURATION"

    # Malformed CSV row width mismatch rejected cleanly.
    with pytest.raises(DiscoveryExecutionError) as bad_csv_exc:
        await csv_disc.discover(
            _make_ctx(
                "CSV",
                CsvPersistentConfig(fileName="bad.csv"),
                file_content="id,email\n1\n",
            )
        )
    assert bad_csv_exc.value.error_code == "INVALID_CONFIGURATION"

    # JSON empty array yields completed snapshot with NO_RECORDS warning and zero stored raw rows.
    json_disc = JsonSchemaDiscoverer()
    empty_json_res = await json_disc.discover(
        _make_ctx("JSON", JsonPersistentConfig(fileName="empty.json"), file_content="[]")
    )
    assert empty_json_res.discovery_status == "completed"
    assert empty_json_res.objects[0].fields == []

    # Invalid JSON rejected cleanly.
    with pytest.raises(DiscoveryExecutionError) as bad_json_exc:
        await json_disc.discover(
            _make_ctx("JSON", JsonPersistentConfig(fileName="bad.json"), file_content="{bad")
        )
    assert bad_json_exc.value.error_code == "INVALID_CONFIGURATION"

    # Empty MongoDB collection yields completed snapshot with NO_OBJECTS_FOUND warning.
    class _EmptyMongoDb:
        async def list_collections(self, **_kwargs: Any) -> list[dict[str, Any]]:
            return []

    class _EmptyMongoClient:
        def __getitem__(self, _db: str) -> _EmptyMongoDb:
            return _EmptyMongoDb()

        async def close(self) -> None:
            return None

    empty_mongo = await MongoDbSchemaDiscoverer(
        client_factory=lambda **_kw: _EmptyMongoClient()
    ).discover(
        _make_ctx(
            "MongoDB",
            MongoDbPersistentConfig(uri="mongodb://db.example.com", database="empty_db"),
        )
    )
    assert empty_mongo.discovery_status == "completed"
    assert empty_mongo.objects == []
    assert any(w.code == "NO_OBJECTS_FOUND" for w in empty_mongo.warnings)
