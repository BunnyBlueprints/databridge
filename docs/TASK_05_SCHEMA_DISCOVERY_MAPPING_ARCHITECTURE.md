# DataBridge — Task 05: Schema Discovery & Field Mapping Architecture

**Status:** Architecture & API Contract (Step 01)  
**Date:** 2026-11-27  
**Scope:** System architecture, connector discovery strategies, normalized schema and mapping data models, REST API contracts, PostgreSQL persistence schema, and frontend UX workflow. **No application code, migrations, or dependencies are added in this step.**

---

## 1. Existing Connector Capability Audit

Inspection of `/Users/vishal/Downloads/databridge` establishes what the current Task 01–04 codebase actually supports today versus what Task 05 must add.

### 1.1 Current Backend Baseline (`apps/api`)
- **Stack (`apps/api/pyproject.toml`):** Python `>=3.12`, FastAPI, Pydantic v2, `psycopg[binary]>=3.2.0`, `psycopg-pool>=3.2.0`, `pymysql>=1.1.0`, `pymongo>=4.10.0`, `dnspython>=2.6.0`, `httpx`, `cryptography>=43.0.0`, `uvicorn`, `pytest`, `ruff`. Note: **no YAML parser (`pyyaml`) is installed.**
- **Existing Persistence (`apps/api/migrations/`):**
  - `0001_baseline.sql`: `schema_migrations` forward-only migration table (`MigrationRunner` in `apps/api/src/app/db/migrations.py`).
  - `0002_create_connections_and_credentials.sql`: `connections` (`id`, `name`, `connector_type`, `description`, `endpoint`, `config` JSONB, `auth_configured`, `status`, `last_tested_at`, `last_test_status`, `last_error_message`, `last_successful_sync`, `sync_frequency`, timestamps) and `connection_credentials` (`connection_id` FK `ON DELETE CASCADE`, `ciphertext`, `nonce`, `key_id`, `algorithm`, `format_version`).
- **Existing Credential & Error Boundaries:**
  - `apps/api/src/app/schemas/connections.py`: Usernames, passwords, API keys, bearer tokens, and raw credential-bearing MongoDB URIs are strictly stored in `connection_credentials` (AES-256-GCM) and never returned in `connections.config`, `details`, logs, or errors.
  - `apps/api/src/app/core/errors.py` & `logging.py`: `ApiErrorResponse` (`errorCode`, `message`, `timestamp`), `sanitize_error_message`, and `SensitiveDataFilter`.
  - `apps/api/src/app/services/testers/ssrf.py`: `validate_http_target_url`, `resolve_and_validate_host`, `SsrfSafeHttpTransport` (DNS-rebinding protection, private/loopback/link-local/CGNAT/metadata IP blocklist, no automatic redirects).
  - `apps/api/src/app/services/testers/file_validation.py`: `decode_utf8_file_content` (UTF-8 + single BOM strip, null-byte rejection, size limit `262_144` bytes).

### 1.2 Connector-by-Connector Audit & Realistic Discovery Strategy

| Connector Type | Current Task 03–04 Capability | Realistic Task 05 Discovery Strategy | Extracted Metadata | Limitations & Constraints |
| --- | --- | --- | --- | --- |
| **PostgreSQL** (`PostgresConnectionTester`) | `psycopg.AsyncConnection.connect` with `SELECT 1`, `connect_timeout` & `statement_timeout`. Config stores `{ host, port, database }`; `{ username, password? }` encrypted in vault. | Read-only SQL catalog queries against `information_schema.tables`, `information_schema.columns`, `information_schema.table_constraints`, `information_schema.key_column_usage`, and `information_schema.referential_constraints` (plus optional `pg_catalog.pg_class` / `pg_attribute` / `pg_type` enrichment for array/enum fidelity) inside a read-only transaction with `statement_timeout`. | Schemas (`public`, user schemas; excluding `pg_catalog`/`information_schema` by default), tables & views, column names, ordinal positions, native SQL types (`character varying(255)`, `numeric(12,2)`, `uuid`, `timestamptz`), nullability, defaults, primary keys, unique constraints, foreign key references. | Scoped to `config.database` configured on the connection (PostgreSQL connections bind to a single database). Privileges may hide tables or constraints (`discoveryStatus = "partial"` with warning). |
| **MySQL** (`MysqlConnectionTester`) | `pymysql.connect` in `asyncio.to_thread` with `SELECT 1`, `connect_timeout`, `read_timeout`, `write_timeout`. Config stores `{ host, port, database }`; `{ username, password? }` encrypted. | Read-only SQL catalog queries against `information_schema.TABLES`, `information_schema.COLUMNS`, `information_schema.TABLE_CONSTRAINTS`, `information_schema.KEY_COLUMN_USAGE`, and `information_schema.REFERENTIAL_CONSTRAINTS` filtered by `TABLE_SCHEMA = %s` (`config.database`). Executed via `asyncio.to_thread` with driver timeouts. | Database/schema (`config.database`), tables & views, column names, ordinal positions, `DATA_TYPE` & `COLUMN_TYPE` (e.g., `tinyint(1)`, `enum(...)`, `varchar(120)`), `IS_NULLABLE`, `COLUMN_DEFAULT`, `PRIMARY KEY`, `UNIQUE` indexes, foreign keys. | In MySQL, catalog schema equals `config.database`. `tinyint(1)` vs integer boolean semantics are preserved via `nativeType` without guessing silently. |
| **MongoDB** (`MongoDbConnectionTester`) | `pymongo.AsyncMongoClient` executing `admin.command("ping")`. Config stores `{ uri, database }` (no embedded credentials); `{ username?, password? }` encrypted. | Enumerate collections in `config.database` via `db.list_collections(filter={"type": "collection"})` (skipping `system.*`). Sample up to `sampleLimit` (default `100`, max `500`) documents per collection using bounded `aggregate([{"$sample": {"size": N}}])` or `find().limit(N)` with `maxTimeMS` and a server-side byte budget. | Collection names, inferred dot-notation paths (`customer.address.zip`), array paths (`items[]`, `items[].sku`), BSON types (`ObjectId`, `Date`, `Decimal128`, `int`, `long`, `double`, `bool`, `string`, `object`, `array`, `null`, `binData`), observed presence/nullability, `_id` marker. | **Not relational:** no SQL columns, SQL constraints, or guaranteed fixed schema. Schema is **probabilistic** (`discoverySource = "inferred_sample"`, `confidence < 1.0`). Polymorphic collections can produce mixed field types and fields omitted from sampled documents. |
| **CSV** (`CsvConnectionTester`) | Validates bare `fileName` (`.csv`) and logical/remote `endpoint`; checks transient UTF-8 `fileContent` via `csv.reader(..., strict=True)`. Never stores `fileContent` or local OS paths. | Reuse **transient upload** (`fileContent` sent only in the `POST /discover` request and never saved to PostgreSQL or logs). Validate headers via `csv.reader(..., strict=True)`, scan up to `sampleLimit` rows (default `500` rows within payload limit), infer column types and nullability across rows. | Single logical object (`fileName`), ordered header columns, inferred `canonicalType` (`integer`, `number`, `boolean`, `date`, `datetime`, `string`, `null`, `mixed`), nullable flag (empty cell / `NULL` / `N/A`), inconsistent-type warnings per column. | Persisted CSV connections do **not** retain `fileContent`; discovery requires uploading the file transiently (or providing a sample file in the UI). Unquoted numeric strings vs numbers can be ambiguous (`mixed` warnings emitted). |
| **JSON** (`JsonConnectionTester`) | Validates bare `fileName` (`.json`) and `fileContent` via strict `json.loads` (`_reject_non_standard_constant`). Never stores `fileContent` or local OS paths. | Reuse **transient upload** (`fileContent` sent only in `POST /discover`). Parse strict JSON; extract objects from a top-level array of objects, a single top-level object, or an optional user-specified `recordPath` (e.g., `data.records`). Walk objects up to bounded depth (`max_depth=8`) and sample count (`max_items=200`). | Flattened nested paths (`user.profile.email`), array markers (`tags[]`, `lineItems[].price`), inferred JSON types (`string`, `integer`, `number`, `boolean`, `date`/`datetime` heuristic, `object`, `array`, `null`, `mixed`), observed nullability, inconsistency warnings. | Persisted JSON connections do **not** retain `fileContent`. Heterogeneous arrays or deeply nested structures beyond `max_depth` trigger truncation warnings and fall back to `json` / `unknown`. |
| **REST API** (`RestApiConnectionTester`) | Performs bounded `GET` against `config.baseUrl` with `authType` (`none`, `api_key`, `bearer`) via `SsrfSafeHttpTransport` (`DEFAULT_MAX_RESPONSE_BYTES = 65_536`). | Three-tier discovery strategy:<br>1. **OpenAPI JSON Spec (`discoverySource = "openapi"`):** Fetch an explicit `openApiPath` (same safe host via `SsrfSafeHttpTransport`) or parse a user-supplied OpenAPI 3.x / Swagger 2.0 JSON payload.<br>2. **Sample Response Inference (`discoverySource = "inferred_sample"`):** Execute a bounded SSRF-safe `GET` to `baseUrl` + optional `resourcePath` and optional `recordPath` envelope pointer, feeding JSON into the shared JSON schema inferrer.<br>3. **Manual / Sample Declaration (`discoverySource = "manual"`):** Allow operators to paste a sample JSON payload or explicitly declare fields (crucial for `POST`-only destination REST endpoints or authenticated endpoints returning `204`/paginated envelopes). | OpenAPI: endpoint/resource objects (`GET /v1/orders`, `components.schemas.Order`), fields, `format`, `required`, `nullable`, `enum`.<br>Sampled GET: inferred paths & types.<br>Manual: user-declared object & field definitions. | Arbitrary REST APIs often **do not expose OpenAPI specs**, may require query parameters, enforce pagination/rate limits (`429`), or reject `GET` on write-only destination routes (`405`/`401`/`403`). Automatic vs. inferred vs. manual provenance must remain explicit. |

### 1.3 Existing Frontend Baseline (`apps/web`)
- Routes already declared in `apps/web/src/App.tsx` and `apps/web/src/components/layout/Sidebar.tsx`:
  - `/connections` (`ConnectionsPage.tsx`) — fully integrated with Task 04 backend persistence.
  - `/data-explorer` (`PlaceholderPage` today) — planned for schema introspection and connector inspection.
  - `/mapping-studio` (`PlaceholderPage` today) — planned for visual field-to-field mapping editor, nullability/default/drift rules, and dry-run mapping preview.
- Shared utilities in `apps/web/src/services/apiClientUtils.ts` and `apps/web/src/services/connectionService.ts` already standardize timeout/abort handling, error code mapping, and secret redaction.

---

## 2. Proposed System Architecture

Task 05 extends the Task 04 repository/service pattern without altering `connections` security guarantees or adding data-movement runtime workers.

```text
┌─────────────────────────────┐      HTTPS /api/v1       ┌──────────────────────────────────────┐
│      Frontend (apps/web)    │ ───────────────────────► │      FastAPI API Layer (apps/api)     │
│  - /data-explorer           │                          │  - routes/schemas.py                 │
│  - /mapping-studio          │ ◄─────────────────────── │  - routes/mappings.py                │
└─────────────────────────────┘   JSON (zero secrets)    └──────────────┬───────────────────────┘
                                                                        │
                       ┌────────────────────────────────────────────────┴───────────────────────┐
                       ▼                                                                        ▼
        ┌──────────────────────────────┐                                         ┌──────────────────────────────┐
        │   SchemaDiscoveryService     │                                         │        MappingService        │
        │  1. Load connection record   │                                         │  1. Load connections/schemas │
        │  2. Decrypt creds in memory  │                                         │  2. Persist mapping & rules  │
        │  3. Dispatch to registry     │                                         │  3. Structural/type/required │
        │  4. Normalize & fingerprint  │                                         │     & schema-drift validation│
        │  5. Save metadata (NO data!) │                                         │     (no transform execution) │
        └──────────────┬───────────────┘                                         └──────────────┬───────────────┘
                       │                                                                        │
                       ▼                                                                        ▼
        ┌──────────────────────────────┐                                         ┌──────────────────────────────┐
        │ ConnectorDiscovererRegistry  │                                         │ PostgreSQL Repositories      │
        │  - PostgresDiscoverer        │                                         │  - SchemaRepository          │
        │  - MysqlDiscoverer           │                                         │  - MappingRepository         │
        │  - MongoDbDiscoverer         │                                         │  (psycopg3 AsyncConnectionPool│
        │  - RestApiDiscoverer         │                                         │   + explicit transactions)   │
        │  - CsvDiscoverer             │                                         └──────────────────────────────┘
        │  - JsonDiscoverer            │
        │  Shared: SchemaInferer, SSRF │
        └──────────────────────────────┘
```

### Architectural Principles
1. **Strict Reuse of Task 04 Security Primitives:** Discovery fetches the saved connection via `ConnectionRepository`, decrypts credentials transiently via `CredentialEncryptionService` only inside `SchemaDiscoveryService`, passes `config + credentials` to a `ConnectorDiscoverer` instance, and never returns credentials or raw connection URIs.
2. **Metadata Only in Persistence:** `discovered_schemas`, `schema_objects`, and `schema_fields` store **structural metadata only** (names, paths, types, nullability, constraints, warnings). Sample document values, REST response bodies, and CSV/JSON `fileContent` are **never** stored in PostgreSQL or emitted in logs.
3. **Fail-Closed Persistent Mode:** Discovery and mapping persistence endpoints require `DATABASE_URL` and `CREDENTIAL_ENCRYPTION_KEYS` (`503 DATABASE_UNAVAILABLE` otherwise). Existing stateless `POST /api/v1/connections/test` remains unaffected.
4. **Shared Type-Inference Kernel:** MongoDB, CSV, JSON, and sampled REST API responses all produce semi-structured or tabular records; a single deterministic inference module (`schema_inference.py`) normalizes nested paths, arrays, nullability, and multi-type conflicts uniformly.

---

## 3. Normalized Schema Model

All six connectors map into a single connector-independent schema hierarchy so `Mapping Studio`, `Data Explorer`, and the future synchronization engine operate on uniform contracts while preserving connector-specific precision.

### 3.1 Canonical Data Types (`CanonicalFieldType`)

| `canonicalType` | Examples Across Connectors |
| --- | --- |
| `string` | PG `varchar`/`text`/`uuid`, MySQL `char`/`varchar`, Mongo `string`, CSV/JSON strings |
| `integer` | PG `smallint`/`integer`/`bigint`, MySQL `int`/`bigint`, Mongo `int`/`long`, whole JSON/CSV numbers |
| `number` | PG `numeric`/`decimal`/`real`/`double precision`, MySQL `decimal`/`float`/`double`, Mongo `double`/`decimal128`, decimal CSV/JSON numbers |
| `boolean` | PG `boolean`, MySQL `boolean` (or `tinyint(1)` note), Mongo `bool`, JSON `true`/`false`, CSV `true`/`false` |
| `date` | PG `date`, MySQL `date`, ISO-8601 `YYYY-MM-DD` strings (OpenAPI `format: date`, CSV/JSON date heuristic) |
| `datetime` | PG `timestamp`/`timestamptz`, MySQL `datetime`/`timestamp`, Mongo `date` (ISODate), OpenAPI `format: date-time`, ISO-8601 timestamps |
| `object` | PG `json`/`jsonb` sub-structures (when expanded) or Mongo/JSON/OpenAPI nested object |
| `array` | PG `ARRAY` (`text[]`), Mongo `array`, JSON `array`, OpenAPI `type: array` |
| `binary` | PG `bytea`, MySQL `blob`/`varbinary`, Mongo `binData`, OpenAPI `format: byte`/`binary` |
| `json` | PG `json`/`jsonb`, MySQL `json`, unexpanded arbitrary JSON/BSON object |
| `null` | Fields observed exclusively as `null` / empty in sampled records |
| `mixed` | Fields with conflicting observed types across records (e.g., `integer` and `string`); `inferredTypes` lists observed candidates |
| `unknown` | Unrecognized native type or max-depth-truncated node |

### 3.2 Normalized Entity Structures

- **`DiscoveredSchemaSnapshot`**
  - `id`: `UUID`
  - `connectionId`: `UUID`
  - `connectorType`: `PostgreSQL | MySQL | MongoDB | REST API | CSV | JSON`
  - `schemaVersion`: `int` (monotonic counter per connection, starting at `1`)
  - `schemaFingerprint`: `str` (64-char SHA-256 hex digest computed deterministically over sorted `objectKey` + sorted `(fieldPath, canonicalType, nativeType, isRequired, isPrimaryKey)` tuples for schema drift detection)
  - `discoveryStatus`: `"completed" | "partial"`
  - `discoveredAt`: ISO-8601 UTC timestamp
  - `warnings`: `list[SchemaWarning]` (`{ code, message, objectKey?, fieldPath? }`)
  - `objects`: `list[SchemaObject]` (omitted or summarized in lightweight list responses)

- **`SchemaObject`**
  - `id`: `UUID`
  - `objectKey`: canonical unique object key within the snapshot (e.g., `"public.orders"`, `"orders"`, `"GET /v1/orders"`, `"customers.csv"`)
  - `catalogName`: `str | None` (e.g., database name or host label)
  - `namespace`: `str | None` (e.g., `"public"` in PostgreSQL, OpenAPI tag, `null` for CSV/JSON/MongoDB)
  - `name`: `str` (`"orders"`)
  - `objectType`: `"table" | "view" | "collection" | "endpoint" | "file" | "record_set"`
  - `discoverySource`: `"catalog" | "openapi" | "inferred_sample" | "manual"`
  - `confidence`: `float` (`0.0`–`1.0`; `1.0` for SQL catalogs & OpenAPI `components.schemas`; e.g., `0.85` for 100-doc samples, lower for sparse/conflicted samples; `1.0` user-asserted for `manual` or flagged separately by `discoverySource`)
  - `sampleSize`: `int | None` (rows/documents sampled; `null` for catalog/OpenAPI/manual)
  - `primaryKeyFields`: `list[str]` (ordered key `fieldPath`s; `["_id"]` by convention for MongoDB collections when `_id` is observed)
  - `fieldCount`: `int`
  - `metadata`: `dict[str, Any]` (non-secret connector traits: e.g., `{ "httpMethod": "GET", "relativePath": "/v1/orders", "recordPath": "data" }` for REST API or `{ "hasHeaderRow": true, "delimiter": "," }` for CSV)
  - `fields`: `list[SchemaField]`

- **`SchemaField`**
  - `id`: `UUID`
  - `fieldPath`: canonical path (e.g., `"order_id"`, `"customer.address.city"`, `"lineItems[].sku"`)
  - `name`: leaf segment name (`"city"`, `"sku"`)
  - `parentPath`: `str | None` (`"customer.address"`, `"lineItems[]"`)
  - `depth`: `int` (`0` for top-level fields)
  - `ordinalPosition`: `int`
  - `canonicalType`: `CanonicalFieldType`
  - `nativeType`: `str` (lossless raw type string: e.g., `"character varying(255)"`, `"numeric(10,2)"`, `"bson:decimal128"`, `"openapi:string(uuid)"`, `"csv:inferred"`)
  - `isNullable`: `bool`
  - `isRequired`: `bool` (computed accurately for destination validation: SQL `NOT NULL` **and** no server default/identity/auto-increment; OpenAPI `required: true`; Mongo `_id` if destination requires explicit key or optional if auto-generated)
  - `isPrimaryKey`: `bool`
  - `isUnique`: `bool`
  - `isArray`: `bool`
  - `defaultValue`: `str | None` (catalog default expression summary, e.g., `"now()"`, `"nextval(...)"`, `"0"`)
  - `foreignKey`: `{ targetObjectKey: str, targetFieldPath: str } | None`
  - `inferredTypes`: `list[CanonicalFieldType]` (populated when `canonicalType == "mixed"` or sampled across variants)
  - `warnings`: `list[str]`

### 3.3 Lossless Normalization Strategy
Connector semantics are **never flattened destructively**:
- Relational constraints (`isPrimaryKey`, `isUnique`, `foreignKey`, `defaultValue`) populate first-class columns for SQL connectors and remain `false`/`null` for schemaless connectors without inventing fake constraints.
- `canonicalType` powers cross-connector UI display and compatibility validation (`integer` → `number` is safe; `string` → `integer` warns/errors), while `nativeType` retains exact precision/scale/encoding (`numeric(18,4)`, `timestamptz`, `ObjectId`) needed by future synchronization writers.
- Nested structures (MongoDB, JSON, OpenAPI) use deterministic `fieldPath` dot/bracket notation paired with `parentPath`, `depth`, and `isArray` so UI tree views and flat mapping rows render from the same model.

---

## 4. Field Mapping Model

Mappings link a persisted source `(connection, object)` to a persisted destination `(connection, object)` with ordered field-level rules and validation state.

### 4.1 Mapping Entities

- **`Mapping` (`MappingConfiguration`)**
  - `id`: `UUID`
  - `name`: `str` (1–120 chars, unique case-insensitively)
  - `description`: `str | None` (max 500 chars)
  - `status`: `"draft" | "valid" | "invalid"`
  - `mappingVersion`: `int` (starts at `1`, increments on every `PUT`)
  - `sourceConnectionId`: `UUID`
  - `sourceObjectKey`: `str`
  - `sourceSchemaFingerprint`: `str | None` (recorded from source schema snapshot at save/validate time)
  - `destinationConnectionId`: `UUID`
  - `destinationObjectKey`: `str`
  - `destinationSchemaFingerprint`: `str | None`
  - `rules`: `list[MappingFieldRule]`
  - `validationSummary`: `MappingValidationSummary | None`
  - `createdAt`, `updatedAt`: ISO-8601 UTC timestamps

- **`MappingFieldRule`**
  - `id`: `UUID`
  - `orderIndex`: `int`
  - `ruleType`: `"direct" | "constant" | "transform"`
    - `direct`: copies `sourceFieldPath` → `destinationFieldPath` (with optional null fallback `defaultValue`).
    - `constant`: assigns literal `constantValue` → `destinationFieldPath` (`sourceFieldPath` must be `null`).
    - `transform`: **contract placeholder only** — references a future transformation definition via `transformRef` taking `sourceFieldPath` (+ optional `defaultValue`) → `destinationFieldPath`. **No runtime transformation engine is implemented in Task 05.**
  - `sourceFieldPath`: `str | None`
  - `destinationFieldPath`: `str` (unique per mapping; one rule defines each mapped destination field)
  - `constantValue`: `str | int | float | bool | None` (primitive JSON scalar when `ruleType == "constant"`)
  - `defaultValue`: `str | int | float | bool | None` (optional fallback if source value is `null`/missing at runtime)
  - `transformRef`: `{ transformId?: str, presetName?: str, params?: dict[str, Any] } | None` (validated structurally only when `ruleType == "transform"`)
  - `notes`: `str | None`

- **`MappingValidationSummary`**
  - `isValid`: `bool` (`errorCount == 0`)
  - `errorCount`: `int`
  - `warningCount`: `int`
  - `missingRequiredDestinationFields`: `list[str]` (destination fields where `isRequired == true` and no rule targets `destinationFieldPath`)
  - `unmappedSourceFields`: `list[str]` (informational — valid mappings may intentionally drop source fields)
  - `unmappedDestinationFields`: `list[str]` (optional/nullable/defaulted destination fields without rules)
  - `schemaDriftDetected`: `bool` (`true` if latest discovered `schemaFingerprint` for source or destination differs from mapping's stored fingerprint, or a referenced field no longer exists)
  - `issues`: `list[MappingValidationIssue]` (`{ severity: "error" | "warning", code, message, destinationFieldPath?, sourceFieldPath? }`)
  - `validatedAt`: ISO-8601 UTC timestamp

### 4.2 Deterministic Validation Rules
1. **Referential Integrity:** Source and destination connections must exist; selected `sourceObjectKey` and `destinationObjectKey` must exist in the connections' discovered (or manually declared) schemas.
2. **Duplicate Target Guard:** At most one rule may target a given `destinationFieldPath`.
3. **Rule Shape Checks:** `direct`/`transform` require `sourceFieldPath` present in source schema; `constant` forbids `sourceFieldPath` and requires `constantValue` (non-null unless destination `isNullable == true`). `transform` requires `transformRef` with `transformId` or `presetName`.
4. **Required Destination Coverage:** Every destination field with `isRequired == true` must be mapped (or explicitly noted as missing error).
5. **Type Compatibility Matrix:**
   - **Exact / Widening (OK):** identical types, `integer -> number`, `date -> datetime`, any primitive `-> string`, any `-> json`.
   - **Precision / Coercion Risk (Warning):** `number -> integer`, `datetime -> date`, `string -> integer/number/boolean/date/datetime` (may fail on dirty data at sync time), `mixed -> concrete type`, `constant` string matching target format.
   - **Incompatible (Error):** `object/array -> integer/number/boolean/date`, `binary -> numeric/boolean/date`, `null constant -> NOT NULL destination without default`.
6. **Schema Drift Check:** Compares mapping's `sourceSchemaFingerprint` / `destinationSchemaFingerprint` against current latest `discovered_schemas.schema_fingerprint` and emits a drift warning/error if stale.

---

## 5. API Contract (`/api/v1`)

All endpoints use camelCase JSON payloads consistent with `ConnectionResponse` (`apps/api/src/app/schemas/connections.py`), enforce strict request validation (`extra="forbid"`, `hide_input_in_errors=True`), return `ApiErrorResponse` (`errorCode`, `message`, `timestamp`) on failure, and **never expose credentials or raw connection URIs with userinfo**.

### 5.1 Endpoints Overview

| Method | Path | Purpose | Timeout / Mode |
| --- | --- | --- | --- |
| `POST` | `/api/v1/connections/{connectionId}/discover` | Trigger schema discovery for a saved connection (supports transient `fileContent` for `CSV`/`JSON` and scoped hints/manual schema for `REST API`) | Bounded (`SCHEMA_DISCOVERY_TIMEOUT_SECONDS`, default `15.0s`, max `60.0s`) |
| `GET` | `/api/v1/connections/{connectionId}/schema` | Retrieve latest discovered schema metadata & object summaries (`404 SCHEMA_NOT_DISCOVERED` if none) | Fast DB read |
| `GET` | `/api/v1/connections/{connectionId}/objects` | List/filter discovered objects (`?q=`, `?namespace=`, `?limit=`, `?offset=`) | Fast DB read |
| `GET` | `/api/v1/connections/{connectionId}/objects/{objectId}/fields` | Retrieve ordered fields (`SchemaField[]`) for one discovered object (`?q=` optional filter) | Fast DB read |
| `POST` | `/api/v1/mappings` | Create a persistent field mapping configuration | DB transaction (`201`) |
| `GET` | `/api/v1/mappings` | List/filter saved mappings (`?sourceConnectionId=`, `?destinationConnectionId=`, `?status=`, `?q=`, `?limit=`, `?offset=`) | Fast DB read |
| `GET` | `/api/v1/mappings/{mappingId}` | Retrieve full mapping, rules, and last validation summary | Fast DB read |
| `PUT` | `/api/v1/mappings/{mappingId}` | Update mapping metadata, bindings, and rules atomically; increments `mappingVersion` | DB transaction (`200`) |
| `DELETE` | `/api/v1/mappings/{mappingId}` | Delete mapping and cascade its rules | DB transaction (`204`) |
| `POST` | `/api/v1/mappings/{mappingId}/validate` | Validate a saved mapping against latest discovered schemas and persist its `validationSummary` | Fast DB/service compute |
| `POST` | `/api/v1/mappings/validate` | Stateless/draft validation of unsaved UI mapping edits before persisting | Fast DB/service compute |

> **Why `objectId` in `/objects/{objectId}/fields`:** Object keys such as `GET /v1/orders` or `analytics.user/events` contain slashes and dots that create path-routing ambiguity if placed directly in a URL segment. Addressing fields by `objectId` (`UUID`) while supporting `?objectKey=` filtering on `/objects` keeps URLs unambiguous and RESTful.

### 5.2 Representative Request & Response Examples

#### A. Discover Schema (`POST /api/v1/connections/{connectionId}/discover`)

**Request (PostgreSQL / MongoDB):**
```json
{
  "options": {
    "includeSchemas": ["public"],
    "includeViews": true,
    "sampleLimit": 100
  }
}
```

**Request (CSV / JSON transient discovery):**
```json
{
  "fileName": "customers.csv",
  "fileContent": "id,email,signup_date,is_active\n1,ada@example.com,2026-11-01,true\n2,grace@example.com,2026-11-02,false\n",
  "options": {
    "sampleLimit": 500
  }
}
```

**Request (REST API OpenAPI / Sample / Manual):**
```json
{
  "options": {
    "mode": "openapi",
    "openApiPath": "/openapi.json",
    "sampleLimit": 25
  }
}
```
*(Or `"mode": "sample_get", "resourcePath": "/v1/orders", "recordPath": "data"`, or `"mode": "manual", "manualObject": { "objectKey": "POST /v1/leads", "name": "leads", "fields": [...] }`.)*

**Response (`200 OK`):**
```json
{
  "id": "8c54f9a8-2d35-4d9d-9f4e-771b99e1a101",
  "connectionId": "4f8d22b1-7f43-4f16-b65a-5e2212bc9f10",
  "connectorType": "PostgreSQL",
  "schemaVersion": 3,
  "schemaFingerprint": "9c21d4a7180ab12e...",
  "discoveryStatus": "completed",
  "discoveredAt": "2026-11-27T16:20:00+00:00",
  "warnings": [],
  "objectCount": 2,
  "objects": [
    {
      "id": "1d4c3b9f-4e65-43b8-90b7-67f001122334",
      "objectKey": "public.customers",
      "catalogName": "app_prod",
      "namespace": "public",
      "name": "customers",
      "objectType": "table",
      "discoverySource": "catalog",
      "confidence": 1.0,
      "sampleSize": null,
      "primaryKeyFields": ["id"],
      "fieldCount": 4,
      "metadata": {}
    }
  ]
}
```

#### B. Retrieve Object Fields (`GET /api/v1/connections/{connectionId}/objects/{objectId}/fields`)

**Response (`200 OK`):**
```json
{
  "connectionId": "4f8d22b1-7f43-4f16-b65a-5e2212bc9f10",
  "schemaId": "8c54f9a8-2d35-4d9d-9f4e-771b99e1a101",
  "object": {
    "id": "1d4c3b9f-4e65-43b8-90b7-67f001122334",
    "objectKey": "public.customers",
    "name": "customers",
    "objectType": "table",
    "discoverySource": "catalog",
    "confidence": 1.0,
    "primaryKeyFields": ["id"]
  },
  "fields": [
    {
      "id": "b192a4cc-0c50-45af-a1e0-101010101010",
      "fieldPath": "id",
      "name": "id",
      "parentPath": null,
      "depth": 0,
      "ordinalPosition": 1,
      "canonicalType": "integer",
      "nativeType": "bigint",
      "isNullable": false,
      "isRequired": false,
      "isPrimaryKey": true,
      "isUnique": true,
      "isArray": false,
      "defaultValue": "nextval('customers_id_seq'::regclass)",
      "foreignKey": null,
      "inferredTypes": [],
      "warnings": []
    },
    {
      "id": "b192a4cc-0c50-45af-a1e0-101010101011",
      "fieldPath": "email",
      "name": "email",
      "parentPath": null,
      "depth": 0,
      "ordinalPosition": 2,
      "canonicalType": "string",
      "nativeType": "character varying(255)",
      "isNullable": false,
      "isRequired": true,
      "isPrimaryKey": false,
      "isUnique": true,
      "isArray": false,
      "defaultValue": null,
      "foreignKey": null,
      "inferredTypes": [],
      "warnings": []
    }
  ]
}
```
*(Note: `id` has `isNullable: false` and `defaultValue` from sequence, so `isRequired` for destination insert is `false`, whereas `email` has `isNullable: false` and no default, making `isRequired: true`.)*

#### C. Create Mapping (`POST /api/v1/mappings`)

**Request (`201 Created`):**
```json
{
  "name": "Mongo Users to Postgres Customers",
  "description": "Maps customer identity and signup metadata.",
  "sourceConnectionId": "2e6a77e1-1111-4000-8000-000000000001",
  "sourceObjectKey": "users",
  "destinationConnectionId": "4f8d22b1-7f43-4f16-b65a-5e2212bc9f10",
  "destinationObjectKey": "public.customers",
  "rules": [
    {
      "orderIndex": 0,
      "ruleType": "direct",
      "sourceFieldPath": "profile.email",
      "destinationFieldPath": "email",
      "defaultValue": null
    },
    {
      "orderIndex": 1,
      "ruleType": "constant",
      "destinationFieldPath": "source_system",
      "constantValue": "mongodb_prod"
    },
    {
      "orderIndex": 2,
      "ruleType": "transform",
      "sourceFieldPath": "createdAt",
      "destinationFieldPath": "created_at",
      "transformRef": {
        "presetName": "iso8601_to_timestamptz",
        "params": { "timezone": "UTC" }
      }
    }
  ]
}
```

#### D. Validate Mapping (`POST /api/v1/mappings/{mappingId}/validate` or `/mappings/validate`)

**Response (`200 OK`):**
```json
{
  "mappingId": "c8810a12-5b2e-4f11-8899-aabbccddeeff",
  "isValid": false,
  "status": "invalid",
  "errorCount": 1,
  "warningCount": 1,
  "missingRequiredDestinationFields": ["account_status"],
  "unmappedSourceFields": ["_id", "legacyTags[]"],
  "unmappedDestinationFields": ["id", "account_status"],
  "schemaDriftDetected": false,
  "issues": [
    {
      "severity": "error",
      "code": "MISSING_REQUIRED_FIELD",
      "message": "Required destination field 'account_status' (NOT NULL without default) has no mapping rule.",
      "destinationFieldPath": "account_status",
      "sourceFieldPath": null
    },
    {
      "severity": "warning",
      "code": "TYPE_COERCION_RISK",
      "message": "Mapping inferred MongoDB field 'profile.email' (mixed) to PostgreSQL 'character varying(255)' may require normalization.",
      "destinationFieldPath": "email",
      "sourceFieldPath": "profile.email"
    }
  ],
  "validatedAt": "2026-11-27T16:25:00+00:00"
}
```

### 5.3 Long-Running Discovery & Timeouts
- Configurable settings in `Settings` (`apps/api/src/app/core/config.py`):
  - `SCHEMA_DISCOVERY_TIMEOUT_SECONDS` (default `15.0`, range `1.0–60.0`)
  - `SCHEMA_DISCOVERY_MAX_OBJECTS` (default `250`)
  - `SCHEMA_DISCOVERY_MAX_FIELDS_PER_OBJECT` (default `500`)
  - `SCHEMA_DISCOVERY_DEFAULT_SAMPLE_LIMIT` (default `100`, max `500`)
- All discovery endpoints are **read-only**, enforce driver-level + `asyncio.timeout(...)` caps, truncate cleanly with `discoveryStatus: "partial"` and explicit warnings when object/field budgets are hit, and avoid holding PostgreSQL pool connections open while querying external source systems (load connection & decrypt credentials -> release DB connection -> run external discovery -> acquire DB transaction to persist snapshot).

---

## 6. PostgreSQL Persistence Design

Implemented via forward-only SQL migrations (`0003_create_discovered_schemas.sql` and `0004_create_mappings.sql`) following `apps/api/migrations/`.

### 6.1 Tables & Relationships

#### A. `discovered_schemas`
| Column | Type | Constraints | Notes |
| --- | --- | --- | --- |
| `id` | `UUID` | `PRIMARY KEY` | Snapshot UUID |
| `connection_id` | `UUID` | `NOT NULL REFERENCES connections(id) ON DELETE CASCADE` | Automatic cleanup when connection is deleted |
| `schema_version` | `INTEGER` | `NOT NULL CHECK (schema_version >= 1)` | Incremented monotonically per `connection_id` |
| `schema_fingerprint` | `VARCHAR(64)` | `NOT NULL` | SHA-256 hex digest of structural schema |
| `discovery_status` | `VARCHAR(24)` | `NOT NULL CHECK (discovery_status IN ('completed', 'partial'))` | Flags truncated/partial introspection |
| `warnings` | `JSONB` | `NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(warnings) = 'array')` | Structured `SchemaWarning[]` |
| `discovery_metadata` | `JSONB` | `NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(discovery_metadata) = 'object')` | Non-secret options used (e.g., `sampleLimit`, `mode`) |
| `discovered_at` | `TIMESTAMPTZ` | `NOT NULL DEFAULT NOW()` | Discovery execution timestamp (UTC) |

- Indexes: `UNIQUE (connection_id, schema_version)`, `(connection_id, discovered_at DESC)` (or `UNIQUE (connection_id)` if Decision #2 selects latest-only overwrite).

#### B. `schema_objects`
| Column | Type | Constraints | Notes |
| --- | --- | --- | --- |
| `id` | `UUID` | `PRIMARY KEY` | Stable within snapshot |
| `schema_id` | `UUID` | `NOT NULL REFERENCES discovered_schemas(id) ON DELETE CASCADE` | Cascades with snapshot/connection |
| `object_key` | `VARCHAR(512)` | `NOT NULL` | Canonical identifier (`public.orders`, `GET /v1/orders`, etc.) |
| `catalog_name` | `VARCHAR(255)` | `NULL` | Database/catalog label |
| `namespace` | `VARCHAR(255)` | `NULL` | SQL schema or API tag |
| `name` | `VARCHAR(255)` | `NOT NULL` | Object short name |
| `object_type` | `VARCHAR(32)` | `NOT NULL CHECK (object_type IN ('table', 'view', 'collection', 'endpoint', 'file', 'record_set'))` | Normalized object kind |
| `discovery_source` | `VARCHAR(32)` | `NOT NULL CHECK (discovery_source IN ('catalog', 'openapi', 'inferred_sample', 'manual'))` | Provenance |
| `confidence` | `NUMERIC(3,2)` | `NOT NULL CHECK (confidence >= 0.00 AND confidence <= 1.00)` | Confidence indicator |
| `sample_size` | `INTEGER` | `NULL CHECK (sample_size IS NULL OR sample_size >= 0)` | Sampled records count |
| `primary_key_fields` | `JSONB` | `NOT NULL DEFAULT '[]'::jsonb` | Ordered key `field_path` list |
| `metadata` | `JSONB` | `NOT NULL DEFAULT '{}'::jsonb` | Non-secret object traits (`httpMethod`, `recordPath`) |

- Indexes: `UNIQUE (schema_id, object_key)`, `(schema_id, namespace, name)`.

#### C. `schema_fields`
| Column | Type | Constraints | Notes |
| --- | --- | --- | --- |
| `id` | `UUID` | `PRIMARY KEY` | Field UUID |
| `object_id` | `UUID` | `NOT NULL REFERENCES schema_objects(id) ON DELETE CASCADE` | Cascades with object |
| `field_path` | `VARCHAR(512)` | `NOT NULL` | Canonical path (`customer.email`, `items[].sku`) |
| `name` | `VARCHAR(255)` | `NOT NULL` | Leaf name |
| `parent_path` | `VARCHAR(512)` | `NULL` | Parent node path |
| `depth` | `INTEGER` | `NOT NULL DEFAULT 0 CHECK (depth >= 0)` | Nesting depth |
| `ordinal_position` | `INTEGER` | `NOT NULL CHECK (ordinal_position >= 0)` | Display order |
| `canonical_type` | `VARCHAR(32)` | `NOT NULL` | Normalized `CanonicalFieldType` |
| `native_type` | `VARCHAR(128)` | `NOT NULL` | Lossless connector native type |
| `is_nullable` | `BOOLEAN` | `NOT NULL DEFAULT TRUE` | Nullability |
| `is_required` | `BOOLEAN` | `NOT NULL DEFAULT FALSE` | Destination requirement |
| `is_primary_key` | `BOOLEAN` | `NOT NULL DEFAULT FALSE` | PK marker |
| `is_unique` | `BOOLEAN` | `NOT NULL DEFAULT FALSE` | Unique constraint marker |
| `is_array` | `BOOLEAN` | `NOT NULL DEFAULT FALSE` | Array indicator |
| `default_value` | `TEXT` | `NULL` | Non-secret default metadata |
| `foreign_key` | `JSONB` | `NULL` | `{ targetObjectKey, targetFieldPath }` |
| `inferred_types` | `JSONB` | `NOT NULL DEFAULT '[]'::jsonb` | Variant candidate list |
| `warnings` | `JSONB` | `NOT NULL DEFAULT '[]'::jsonb` | Field-level warnings |

- Indexes: `UNIQUE (object_id, field_path)`, `(object_id, ordinal_position)`.

#### D. `mappings`
| Column | Type | Constraints | Notes |
| --- | --- | --- | --- |
| `id` | `UUID` | `PRIMARY KEY` | Mapping UUID |
| `name` | `VARCHAR(120)` | `NOT NULL` | Case-insensitively unique (`uq_mappings_lower_name`) |
| `description` | `TEXT` | `NULL` | Optional description |
| `status` | `VARCHAR(24)` | `NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'valid', 'invalid'))` | Mapping readiness |
| `mapping_version` | `INTEGER` | `NOT NULL DEFAULT 1 CHECK (mapping_version >= 1)` | Incremented on `PUT` |
| `source_connection_id` | `UUID` | `NOT NULL REFERENCES connections(id) ON DELETE RESTRICT` | Recommended default; see Decision #1 |
| `source_object_key` | `VARCHAR(512)` | `NOT NULL` | Canonical source object key |
| `source_schema_fingerprint` | `VARCHAR(64)` | `NULL` | Snapshot fingerprint at save/validate |
| `destination_connection_id` | `UUID` | `NOT NULL REFERENCES connections(id) ON DELETE RESTRICT` | Recommended default; see Decision #1 |
| `destination_object_key` | `VARCHAR(512)` | `NOT NULL` | Canonical destination object key |
| `destination_schema_fingerprint` | `VARCHAR(64)` | `NULL` | Snapshot fingerprint at save/validate |
| `validation_summary` | `JSONB` | `NULL` | Latest `MappingValidationSummary` |
| `created_at` | `TIMESTAMPTZ` | `NOT NULL DEFAULT NOW()` | UTC creation |
| `updated_at` | `TIMESTAMPTZ` | `NOT NULL DEFAULT NOW()` | UTC update |

#### E. `mapping_rules`
| Column | Type | Constraints | Notes |
| --- | --- | --- | --- |
| `id` | `UUID` | `PRIMARY KEY` | Rule UUID |
| `mapping_id` | `UUID` | `NOT NULL REFERENCES mappings(id) ON DELETE CASCADE` | Cascades on mapping delete |
| `order_index` | `INTEGER` | `NOT NULL CHECK (order_index >= 0)` | Deterministic display/execution order |
| `rule_type` | `VARCHAR(24)` | `NOT NULL CHECK (rule_type IN ('direct', 'constant', 'transform'))` | Rule classification |
| `source_field_path` | `VARCHAR(512)` | `NULL` | Required for `direct`/`transform`; `NULL` for `constant` |
| `destination_field_path` | `VARCHAR(512)` | `NOT NULL` | Target field path |
| `constant_value` | `JSONB` | `NULL` | Scalar JSON value when `rule_type = 'constant'` |
| `default_value` | `JSONB` | `NULL` | Fallback scalar value |
| `transform_ref` | `JSONB` | `NULL` | Future transformation reference contract |
| `notes` | `TEXT` | `NULL` | Operator notes |

- Indexes: `UNIQUE (mapping_id, destination_field_path)`, `(mapping_id, order_index)`.

### 6.2 Normalization vs. JSONB & Avoiding Redundancy
- **Normalized:** Connection references, `object_key`, `field_path`, `canonical_type`, `native_type`, `is_required`, `is_primary_key`, `rule_type`, `source_field_path`, `destination_field_path`, `mapping_version`, and `status`. This allows paginated object lookup, indexed searching, SQL constraint checking, and loading mapping rules without hydrating full schema trees.
- **JSONB:** Variable/optional metadata (`warnings`, `discovery_metadata`, `primary_key_fields`, `metadata`, `foreign_key`, `inferred_types`, `constant_value`, `default_value`, `transform_ref`, `validation_summary`) avoids proliferation of tiny EAV or sub-tables.
- **No Redundant Copying:** `mappings` and `mapping_rules` do **not** embed copies of `SchemaObject` or `SchemaField` definitions; they reference `(connection_id, object_key, field_path)` plus `schema_fingerprint` for drift checks.

### 6.3 Deletion Behavior When a Connection Is Deleted
- `discovered_schemas` → `schema_objects` → `schema_fields` use `ON DELETE CASCADE`, automatically removing ephemeral structural snapshots when a connection is deleted.
- `mappings`: Recommended `ON DELETE RESTRICT` (`409 CONNECTION_IN_USE` with a clear list/count error message when deleting a connection referenced by active mappings, protecting users from silent pipeline loss; requires deleting or updating referencing mappings first). Alternative (`ON DELETE CASCADE`) is logged in Section 12 for approval.

---

## 7. Frontend Workflow (`apps/web`)

Integrate directly into existing sidebar routes (`App.tsx`) using the established Zinc + Emerald design system, without altering `ConnectionsPage` or `DashboardPage`:

1. **`/data-explorer` (Schema Discovery Inspector):**
   - **Select Connection:** Dropdown populated from `GET /api/v1/connections`.
   - **Discover / Re-discover Action:**
     - SQL / MongoDB: one-click `Discover Schema` (`POST /discover`).
     - CSV / JSON: inline transient file chooser (`<= 256 KB`) reminding users file content is never persisted, then `POST /discover`.
     - REST API: clear mode selector (`OpenAPI URL/Path`, `Sample GET Request`, or `Declare Manual Schema / Paste JSON`) with SSRF-protected hints.
   - **Inspect Structure:** Left panel searchable `SchemaObject` list (`namespace`, `objectType`, confidence badge); right panel `SchemaField` table (`fieldPath`, `canonicalType`, `nativeType`, `Nullable`/`Required`, `PK`/`FK`/`Unique` badges, warnings).
   - **States:** Loading skeletons, explicit "Not discovered yet" empty state with CTA, partial-discovery warning banner, sanitized error banner with `Retry`, and `AbortController` cleanup on connection change.

2. **`/mapping-studio` (Guided 7-Step Field Mapping Workspace):**
   - **Steps 1–2 (Source):** Choose source connection -> load/discover source objects -> select `sourceObjectKey` (triggers `Discover` modal/drawer if schema missing).
   - **Steps 3–4 (Destination):** Choose destination connection -> load/discover destination objects (or define destination fields manually for write-only REST APIs) -> select `destinationObjectKey`.
   - **Step 5 (Configure Field Mappings):**
     - Interactive mapping table showing destination fields (required fields pinned/badged), source field selector dropdown, rule type selector (`Direct`, `Constant`, `Transform Ref (Contract)`), optional fallback `Default`, and `Auto-match by Name` convenience button.
     - Displays `canonicalType` + `nativeType` side by side with inline compatibility cues.
   - **Step 6 (Validate):** Runs `POST /api/v1/mappings/validate` (or `/{id}/validate`), listing errors (`missingRequiredDestinationFields`, incompatible types) and warnings (`typeCoercionRisk`, `schemaDriftDetected`, `unmappedSourceFields`) with click-to-focus on affected rows.
   - **Step 7 (Structural Preview & Save):** Renders a **Structural Shape Preview** (example JSON/row field projection derived purely from schema types, constants, and rule links — clearly labeled *"Structural preview only — live data extraction and transformation execution run in Sync Jobs"*), plus `Save Mapping` (`POST`/`PUT /api/v1/mappings`).

---

## 8. Security Considerations

1. **Zero Credential Exposure:** Discovery and mapping endpoints never return passwords, usernames, API keys, bearer tokens, Mongo credential URIs, or encrypted vault fields (`ciphertext`, `nonce`, `key_id`).
2. **Scoped Decryption Lifetime:** `SchemaDiscoveryService` loads credentials transiently only inside `discover()` for SQL/Mongo/REST connections, passes them to the driver/HTTP header builder, and drops references immediately after the operation (noting Python does not guarantee deterministic memory zeroization of strings).
3. **SSRF Protection on REST Discovery:** All REST API OpenAPI fetches and sample `GET` requests must execute exclusively through `SsrfSafeHttpTransport` + `validate_http_target_url` with `follow_redirects=False` and `trust_env=False`.
4. **No Raw Data or PII Persistence:** File uploads (`CSV`, `JSON`), sampled MongoDB documents, and REST response payloads are inspected strictly in memory and discarded. Only structural field names, paths, inferred types, and warning messages are written to PostgreSQL. Warning messages must describe type/structure anomalies without echoing sample data values.
5. **Read-Only & Bounded Execution:** SQL discoverers must restrict queries to catalog tables (`SELECT` only, `statement_timeout`, read-only transaction mode); MongoDB discovery must use read-only listing/sampling with `maxTimeMS`; all discoverers enforce object/field/byte limits to prevent resource exhaustion.
6. **Auth Deployment Boundary:** DataBridge does not yet have user login/RBAC (out of scope for Task 05); API endpoints rely on deployment-level access control until user authentication is introduced.

---

## 9. Error Handling

Reuse `ApiErrorResponse` (`errorCode`, `message`, `timestamp`), `ConnectionApiError`, `sanitize_error_message` (`apps/api/src/app/core/errors.py`), and driver-specific classifiers from Task 03–04:

- **Reuse Existing Error Codes:** `CONNECTION_NOT_FOUND` (`404`), `DATABASE_UNAVAILABLE` (`503`), `INVALID_CONFIGURATION` (`422`), `AUTHENTICATION_FAILED` (`422`/`401` upstream), `DNS_RESOLUTION_FAILED`, `CONNECTION_REFUSED`, `NETWORK_ERROR`, `TIMEOUT` (`504`), `SERVER_ERROR` (`500`).
- **New Task 05 Error Codes:**
  - `SCHEMA_NOT_DISCOVERED` (`404`) — schema/object requested before discovery has run.
  - `OBJECT_NOT_FOUND` (`404`) — `objectId` or `objectKey` not present in connection's schema.
  - `FILE_REQUIRED_FOR_DISCOVERY` (`422`) — `POST /discover` called on `CSV`/`JSON` connection without transient `fileContent`.
  - `DISCOVERY_NOT_SUPPORTED` (`422`) — requested discovery mode unsupported for the connector type (e.g., `openapi` mode on `PostgreSQL`).
  - `MAPPING_NOT_FOUND` (`404`) — mapping ID does not exist.
  - `DUPLICATE_MAPPING` (`409`) — case-insensitive mapping name collision.
  - `CONNECTION_IN_USE` (`409`) — connection cannot be deleted while referenced by saved mappings (if `ON DELETE RESTRICT` is approved).
  - `MAPPING_VALIDATION_FAILED` (`422`) — malformed rule structure or invalid target connection/object bindings on save.
- **Partial Discovery Handling:** Non-fatal per-object issues (e.g., permission denied inspecting one collection or truncated fields past `MAX_FIELDS_PER_OBJECT`) return HTTP `200` with `discoveryStatus: "partial"` and structured `warnings` rather than discarding the entire discovered catalog.

---

## 10. Future Sync Engine Dependency & Integration Boundaries

Task 05 explicitly **must not** implement:
- Live source data extraction or destination writes/loading.
- Transformation code/expression runtime execution.
- Sync jobs, background workers, task queues, cron scheduling, retry loops, checkpoints, incremental cursors, or Change Data Capture (CDC).

**Integration Contract for Future Sync Engine (Task 06+):**
1. When a future Sync Job executes, it loads `Mapping` (`sourceConnectionId`, `sourceObjectKey`, `destinationConnectionId`, `destinationObjectKey`, `mappingVersion`) and its ordered `MappingFieldRule[]`.
2. It verifies `sourceSchemaFingerprint` and `destinationSchemaFingerprint` against current schemas (halting or flagging drift before corrupting destination data).
3. It uses `SchemaField.nativeType` + `canonicalType` + `isRequired` to configure typed extract readers, type coercions, `constant`/`default` injection, and destination DDL/writers, and resolves `transformRef` against the future Transformations registry.

---

## 11. Implementation Sequence

1. **Step 02 — Persistence Foundation & Repositories:** Add forward-only SQL migrations (`0003_create_discovered_schemas.sql`, `0004_create_mappings.sql`), Pydantic models (`schemas/schemas.py`, `schemas/mappings.py`), and async psycopg3 repositories (`SchemaRepository`, `MappingRepository`).
2. **Step 03 — Backend Schema Discovery Engine:** Implement `schema_inference.py`, `ConnectorDiscoverer` protocol + `ConnectorDiscovererRegistry`, all 6 discoverers (`Postgres`, `Mysql`, `MongoDb`, `RestApi`, `Csv`, `Json`), `SchemaDiscoveryService`, and `/api/v1/connections/{id}/discover|schema|objects` routes.
3. **Step 04 — Backend Mapping CRUD & Validation Service:** Implement `MappingService`, deterministic type-compatibility and drift validation engine, and `/api/v1/mappings*` endpoints.
4. **Step 05 — Frontend Schema Explorer & Mapping Studio:** Implement `schemaService.ts`, `mappingService.ts`, `/data-explorer`, and `/mapping-studio` guided 7-step UI with loading/empty/error/retry states.
5. **Step 06 — Cross-Layer Hardening & Verification:** Live PostgreSQL migrations, full backend/frontend test suites, SSRF/credential leak audit, and headless Chrome CDP end-to-end verification.

---

## 12. Testing Strategy

- **Backend Unit & Service Tests (`pytest`):**
  - Mocked driver/transport tests for each discoverer: PostgreSQL/MySQL `information_schema` row parsing & `isRequired` calculation; MongoDB `$sample` path/array/BSON inference & mixed-type warnings; CSV/JSON transient inference, header errors, BOM/size bounds, deep-nesting truncation; REST OpenAPI `$ref` cycle guard, SSRF blocking, sample inference, and manual schema declaration.
  - Mapping validator tests covering every rule type (`direct`, `constant`, `transform`), compatibility matrix edge cases, missing required destination fields, duplicate targets, and schema drift detection.
  - Credential non-exposure and error sanitization assertions across all new endpoints.
- **Backend PostgreSQL Integration Tests:**
  - Migration forward application & idempotency (`0003`, `0004`).
  - Repository transactions, fingerprint computation, cascade/restrict foreign key behavior, and concurrent discovery snapshot updates.
- **Frontend Unit & Build Verification (`apps/web`):**
  - `node --test` unit tests for `schemaService`, `mappingService`, validation/mapping form helpers, and zero secret/mock leakage (`tsc -b`, `eslint`, `vite build`).
- **Live End-to-End Browser CDP Tests:**
  - Discover source & destination schemas in `/data-explorer`, build/validate/preview/save/edit/reload/delete mappings in `/mapping-studio`, confirm zero secrets in DOM/storage/network responses.

---

## 13. Open Architectural Decisions Requiring Approval

1. **Connection Deletion Policy (`ON DELETE RESTRICT` vs. `ON DELETE CASCADE` on `mappings`):**
   - *Recommendation:* `ON DELETE RESTRICT` (return `409 CONNECTION_IN_USE` if saved mappings reference the connection) so users cannot silently destroy pipeline definitions. Confirm if `ON DELETE CASCADE` is preferred instead.
2. **Schema History Retention Policy:**
   - *Recommendation:* Retain only the **latest** snapshot per connection (replacing previous `discovered_schemas` rows inside a single transaction on re-discover) to keep `schema_objects`/`schema_fields` storage compact, while still updating `schema_version` and `schema_fingerprint` for drift detection. Confirm whether multi-version schema history is needed now.
3. **OpenAPI YAML Support:**
   - *Recommendation:* Support OpenAPI JSON specs in Task 05 without adding dependencies; defer `.yaml`/`.yml` parsing unless adding `PyYAML` is explicitly approved.
4. **CSV / JSON Discovery Persistence Model:**
   - Confirm storing only inferred `SchemaObject`/`SchemaField` metadata after transient upload during `POST /discover` (never storing `fileContent` or filesystem paths), requiring re-upload only when re-discovering changed file layouts.
5. **REST API Write/Destination Schema Declaration:**
   - Confirm supporting manual field/sample JSON schema declaration inside `POST /discover` (`mode: "manual"`) so REST API connections serving as `POST`/`PUT` destinations can participate in mappings even when no OpenAPI spec exists.
6. **Preview Scope in Mapping Studio (Step 7):**
   - Confirm whether Step 05 UI Preview should remain strictly a **structural payload preview** (computed from schemas, constants, and rule mappings with zero live data fetch), or optionally support an explicit opt-in bounded in-memory sample read (`<= 5` records, zero destination writes).

---

## 14. Implemented System Reference & Practical Usage Guide (As-Built in Steps 02–08)

This section records the exact implemented backend, persistence, API, and frontend behavior completed in Task 05. Where earlier planning sections outlined exploratory options (e.g., draft field names or dry-run sample preview), the contracts below reflect the authoritative implementation.

### 14.1 Approved Decisions & Scope Implemented
- **Persistence (`0003_create_discovered_schemas.sql`, `0004_create_mappings.sql`):**
  - Only the latest discovered schema snapshot is retained per connection (`discovered_schemas.connection_id UNIQUE`, `ON DELETE CASCADE`).
  - `mappings` uses `ON DELETE RESTRICT` on `source_connection_id` and `destination_connection_id` (`409 CONNECTION_IN_USE`), while `mapping_rules` cascades on mapping deletion.
- **Implemented Discovery Modes:**
  - **PostgreSQL & MySQL:** Read-only SQL catalog discovery (`information_schema` tables/views/columns/constraints). Live integration tests cover **PostgreSQL 16**; MySQL and MongoDB discoverers are verified via mocked driver/unit tests in this repository environment.
  - **MongoDB:** Collection enumeration (`system.*` excluded) and bounded sampling (`sampleLimit`), with per-collection permission handling (`discoveryStatus: "partial"`).
  - **CSV & JSON:** Transient upload via `fileContent` / `fileName` inside `POST /api/v1/connections/{connectionId}/discover` (max `262_144` bytes; file bytes and sample rows are never persisted or logged).
  - **REST API:** OpenAPI **JSON** (`mode: "openapi"`, URL via `openApiPath` or inline `openApiSpec` JSON; YAML is intentionally unsupported), bounded `GET` sample inference (`mode: "sample_get"`, optional `resourcePath` and `recordPath`), and explicit manual schema declaration (`mode: "manual"`, `manualObjects`). All outbound REST requests use `SsrfSafeHttpTransport` with DNS validation and disabled redirects.
- **Mapping Rules & Non-Implemented Scope:**
  - Supported rule `mappingType` values: `direct`, `constant` (any safe JSON: `null`, string, int, finite float, boolean, array, safe object), and `transform` (`TransformReference` with `presetName`/`transformId` and bounded scalar `params`; executable keys such as `code`, `expression`, `sql`, `lambda` are rejected).
  - Not implemented in Task 05: transformation execution runtime, sample-row preview / data extraction, destination writes, sync jobs, scheduling, or user authentication/RBAC.

### 14.2 Implemented API Contracts

| Endpoint | Request / Params | Response & Key Fields |
| --- | --- | --- |
| `POST /api/v1/connections/{connectionId}/discover` | `DiscoverConnectionSchemaRequest` (`fileName?`, `fileContent?`, `options?`) | `200 DiscoverSchemaResponse` (`snapshotId`, `discoveryStatus`, `discoverySource`, `schemaFingerprint`, `objectCount`, `warnings`, `objects`) |
| `GET /api/v1/connections/{connectionId}/schema` | — | `200 DiscoveredSchemaResponse` or `404 SCHEMA_NOT_DISCOVERED` |
| `GET /api/v1/connections/{connectionId}/objects` | `?q=&objectType=&objectKey=&limit=&offset=` | `200 SchemaObjectListResponse` |
| `GET /api/v1/connections/{connectionId}/objects/{objectId}/fields` | `?q=&limit=&offset=` | `200 SchemaObjectFieldsResponse` |
| `POST /api/v1/mappings` | `MappingCreate` (`name`, `description?`, `sourceConnectionId`, `sourceObjectKey`, `destinationConnectionId`, `destinationObjectKey`, `sourceSchemaFingerprint?`, `destinationSchemaFingerprint?`, `rules[]`) | `201 MappingResponse` (always initializes `status: "draft"`, `mappingVersion: 1`, captures current fingerprints) |
| `GET /api/v1/mappings` | `?sourceConnectionId=&destinationConnectionId=&status=&q=&limit=&offset=` | `200 MappingListResponse` |
| `GET /api/v1/mappings/{mappingId}` | — | `200 MappingResponse` or `404 MAPPING_NOT_FOUND` |
| `PUT /api/v1/mappings/{mappingId}` | `MappingUpdate` (all fields optional; `expectedVersion?` enables optimistic concurrency check) | `200 MappingResponse` (increments `mappingVersion`; omitted fields & rules preserved; rule replacement atomic) |
| `DELETE /api/v1/mappings/{mappingId}` | — | `204 No Content` |
| `POST /api/v1/mappings/{mappingId}/validate` | — | `200 MappingValidationResponse` (`mappingId`, `mappingVersion`, `status`, `validationSummary`), persisted atomically with `expected_mapping_version` check |
| `POST /api/v1/mappings/validate` | `MappingCreate` | `200 MappingValidationResponse` (`mappingId: null`, non-persisting preview validation) |

**Authoritative Validation & Drift Behavior:**
- CRUD operations never self-certify a mapping as `"valid"`. Only `POST /api/v1/mappings/{mappingId}/validate` transitions `status` to `"valid"` (`errorCount == 0`) or `"invalid"` (`errorCount > 0`).
- Validation compares stored `sourceSchemaFingerprint` and `destinationSchemaFingerprint` against the latest snapshots cleanly without mutating stored fingerprints or rules, reporting `sourceDriftDetected`, `destinationDriftDetected`, and `schemaDriftDetected`.
- Concurrent `PUT` or `POST /validate` against a modified mapping returns `409 MAPPING_VERSION_CONFLICT` without overwriting newer state.

### 14.3 Operational Limits & Environment Settings (`apps/api/src/app/core/config.py`)
- `API_MAX_REQUEST_BYTES`: default `524288` (`512 KB`, max `4 MB`)
- `CONNECTION_TEST_MAX_FILE_BYTES`: default `262144` (`256 KB`)
- `REST_API_MAX_RESPONSE_BYTES`: default `65536` (`64 KB`)
- `SCHEMA_DISCOVERY_TIMEOUT_SECONDS`: default `15.0` (range `> 0.0` to `60.0`)
- `SCHEMA_DISCOVERY_MAX_OBJECTS`: default `250` (max `2000`)
- `SCHEMA_DISCOVERY_MAX_FIELDS_PER_OBJECT`: default `500` (max `2000`)
- `SCHEMA_DISCOVERY_DEFAULT_SAMPLE_LIMIT` / `MAX_SAMPLE_LIMIT`: default `100`, max `500` (setting upper bound `1000`)

### 14.4 Security & Authentication Boundaries
- Credentials stay encrypted at rest in `connection_credentials` (`AES-256-GCM`) and are decrypted only inside `SchemaDiscoveryService.discover_connection_schema`; `MappingService` and `MappingRepository` never read `connection_credentials`.
- Frontend modules (`apps/web/src/pages/DataExplorerPage.tsx`, `MappingStudioPage.tsx`, `schemaService.ts`, `mappingService.ts`) never use browser storage (`localStorage`, `sessionStorage`, `indexedDB`) or mock fallback datasets.
- **Known Boundary:** User authentication, RBAC, and multi-tenant workspace isolation are not yet implemented and must be enforced at the deployment/network layer until added in a future phase.

### 14.5 Practical User Workflow
1. **Create Connections (`/connections`):** Configure and test your source and destination connections (e.g., PostgreSQL, REST API, CSV, JSON).
2. **Discover Schemas (`/data-explorer`):** Select each connection, click **Discover Schema** (upload a transient `.csv`/`.json` file or pick a REST discovery mode if applicable), and inspect objects, canonical/native types, keys, defaults, and required indicators.
3. **Author Mapping (`/mapping-studio`):** Click **New Mapping**, select source and destination connections and discovered objects, add ordered `Direct`, `Constant` (JSON literal), or `Structural Transform Reference` rules, reorder with Up/Down controls if needed, and click **Create Mapping** / **Save Mapping** (saved as `Draft`).
4. **Validate & Resolve Drift (`/mapping-studio`):** Click **Validate Mapping** to run backend validation. Inspect any `MISSING_REQUIRED_FIELD`, canonical type warnings/errors, or `Schema Drift Detected` alerts. After rediscovering changed schemas in Data Explorer, open the mapping in Mapping Studio, update affected rules or click **Update Snapshot References**, click **Save Mapping**, and re-run **Validate Mapping** to reach `Valid`.
