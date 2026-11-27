# DataBridge — Task 04: Connection Persistence & Secure Credential Management Architecture

**Status:** Design & API Contract (Step 01)  
**Date:** 2026-11-27  
**Scope:** Architectural design and API contracts for persisting connections and encrypted credentials. No implementation code is included in this step.

---

## 1. Existing Architecture Findings

Inspection of the current repository confirms the following baseline from Tasks 01–03:

### 1.1 Frontend (`apps/web`)
- **Stack:** React `19.3.1`, TypeScript (`~6.0.3`), Vite (`8.3.6`), Tailwind CSS v4 (`4.3.5`), `react-router-dom` (`7.18.7`), `lucide-react`.
- **Connection state:** `ConnectionsPage.tsx` initializes `connections` in React `useState` from `MOCK_CONNECTIONS` (`apps/web/src/data/mockConnections.ts`). Create, edit, and delete operations mutate local session state only.
- **Data types (`apps/web/src/types/connection.ts` & `dashboard.ts`):**
  - `ConnectorType`: `'PostgreSQL' | 'MySQL' | 'MongoDB' | 'REST API' | 'CSV' | 'JSON'`
  - `ConnectionStatus`: `'Connected' | 'Disconnected' | 'Error'`
  - `ConnectionAuthType`: `'none' | 'api_key' | 'bearer'`
  - `Connection`: `{ id, name, type, status, endpoint, description?, lastSuccessfulSync?, syncFrequency?, errorMessage?, details?: ConnectionSafeDetails }`
  - `ConnectionSafeDetails`: `{ host?, port?, database?, username?, sanitizedUri?, baseUrl?, authType?, authConfigured?, fileName? }`
- **Form & test workflows:**
  - `connectorForm.ts` maps `AddConnectionModal` form fields into non-secret `Connection` records and strips embedded URI userinfo (`://user:pass@`).
  - `TestConnectionModal.tsx` and `connectionTestForm.ts` prefill safe configuration, request transient credentials or transient CSV/JSON `fileContent`, and call `testConnection()` in `apps/web/src/services/connectionTestService.ts`.
  - `apps/web/src/config/env.ts` resolves `VITE_API_BASE_URL` via `buildApiUrl(path)`.
- **Workspace & auth:** `WorkspaceContext.tsx` holds mock workspace metadata for the dashboard; there is no user authentication, session token, or multi-tenant isolation.

### 1.2 Backend (`apps/api`)
- **Stack:** Python `>=3.12` (running `3.14.7`), FastAPI (`0.165.0`), Pydantic v2 (`2.14.4`), `pydantic-settings` (`2.16.3`), Uvicorn (`0.54.0`), `httpx` (`0.29.0`), `psycopg[binary]` (`3.3.9`), `pymysql` (`2.2.8`), `pymongo` (`4.20.2`), `dnspython` (`2.9.2`), `pytest`, `ruff`.
- **Existing structure:**
  - `core/config.py`: `Settings` (`app_env`, `api_host`, `api_port`, `log_level`, `cors_allowed_origins`, `api_max_request_bytes`, `connection_test_timeout_seconds`, `connection_test_max_file_bytes`, `rest_api_max_response_bytes`, `enable_api_docs`).
  - `core/errors.py`: Normalized `ConnectionTestResult` exception handlers (`RequestValidationError` with `hide_input_in_errors=True`, `StarletteHTTPException`, unhandled `Exception`) and `sanitize_error_message`.
  - `core/logging.py`: `databridge.api` logger with `SensitiveDataFilter`.
  - `schemas/connection_test.py`: Discriminated union `ConnectionTestRequest` and `ConnectionTestResult`.
  - `services/connection_test_service.py` & `services/testers/`: `ConnectorTester` protocol, `ConnectorTesterRegistry` with all six testers registered (`PostgresConnectionTester`, `MysqlConnectionTester`, `MongoDbConnectionTester`, `RestApiConnectionTester`, `CsvConnectionTester`, `JsonConnectionTester`).
  - `api/routes/connections.py`: `POST /api/v1/connections/test`.
  - `api/routes/health.py`: `GET /health`.
- **Gaps identified:** No application database, no connection repository, no migration framework, no credential encryption/vault service, and no authentication/authorization layer.

---

## 2. Selected Database Strategy

### 2.1 Primary Database: PostgreSQL
DataBridge will use **PostgreSQL** as its primary operational metadata database:
- Fits relational integrity requirements (unique connection names, foreign-key references to encrypted credentials, atomic deletes, `TIMESTAMPTZ` audit fields, and `JSONB` for validated non-secret connector configuration).
- `psycopg[binary]>=3.2.0` is already an installed runtime dependency in `apps/api/pyproject.toml`, avoiding driver fragmentation.

### 2.2 Access Layer & Repository Pattern
- **Selected approach:** Direct async `psycopg` (`psycopg.AsyncConnection`) with `psycopg_pool.AsyncConnectionPool` and explicit repository classes (`ConnectionRepository`, `CredentialRepository`) returning typed Pydantic domain models.
- **Why not SQLAlchemy:** Adding SQLAlchemy for two persistence tables introduces duplicate model definitions alongside Pydantic v2 schemas, async session/detachment complexity, and unnecessary dependency weight when `psycopg` 3 natively provides async parameterized SQL, `JSONB` serialization, row factories, and explicit transaction blocks.

### 2.3 Connection Pooling & Lifespan
- Manage a single `AsyncConnectionPool` in FastAPI's `lifespan` context manager (`open=False` on creation, `await pool.open()` at startup, `await pool.close()` at shutdown).
- Configurable pool bounds (`DATABASE_POOL_MIN_SIZE`, `DATABASE_POOL_MAX_SIZE`, `DATABASE_CONNECT_TIMEOUT_SECONDS`).
- Expose a dependency provider `get_db_connection()` or pass the pool to services via `app.state`.

### 2.4 Transaction Handling
- Multi-table writes (creating a connection + encrypted credential, updating config + replacing/clearing credential, deleting a connection + credential) execute inside `async with conn.transaction():` so partial writes or orphaned credential rows cannot occur.

### 2.5 Migration Strategy
- Versioned, forward-only SQL migration files stored in `apps/api/migrations/` (e.g., `0001_create_connections_and_credentials.sql`) tracked in a `schema_migrations` table (`version TEXT PRIMARY KEY`, `applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()`).
- Executed via an explicit CLI command / startup migration helper using `psycopg` inside a transaction (`SET lock_timeout` + advisory lock) so schema changes stay deterministic, SQL-reviewable, and ORM-free.

### 2.6 Development vs. Production Configuration
- **Development:** Local PostgreSQL via `DATABASE_URL` in `apps/api/.env` (excluded from git); optional `sslmode=prefer` or `disable` for local loopback DBs only.
- **Production:** Managed PostgreSQL via `DATABASE_URL` injected from a secret manager, enforcing `sslmode=require` (or `verify-full`), strict pool limits, and `GET /health` / readiness checks that verify DB connectivity without exposing connection strings.

---

## 3. Connection Entity Design

Connections and secrets are separated into two PostgreSQL tables so standard connection queries never load encrypted ciphertext or touch decryption keys.

### 3.1 `connections` Table

| Column | Type | Constraints | Notes |
| --- | --- | --- | --- |
| `id` | `UUID` | `PRIMARY KEY` | Generated server-side (`uuid4`), serialized as UUID string |
| `name` | `VARCHAR(120)` | `NOT NULL` | Display name; unique case-insensitively (`UNIQUE INDEX uq_connections_lower_name ON connections (LOWER(name))`) |
| `connector_type` | `VARCHAR(32)` | `NOT NULL`, `CHECK (connector_type IN ('PostgreSQL', 'MySQL', 'MongoDB', 'REST API', 'CSV', 'JSON'))` | Matches `ConnectorType` |
| `description` | `TEXT` | `NULL` | Optional user description (max 500 chars in API validation) |
| `status` | `VARCHAR(24)` | `NOT NULL DEFAULT 'Disconnected'`, `CHECK (status IN ('Connected', 'Disconnected', 'Error'))` | Matches `ConnectionStatus` |
| `endpoint` | `VARCHAR(512)` | `NOT NULL` | Sanitized display endpoint (`host:port/db`, `mongodb+srv://host/db`, `https://...`, `local://file.csv`); never contains credentials |
| `config` | `JSONB` | `NOT NULL` | Validated non-secret connector configuration (see Section 4) |
| `auth_configured` | `BOOLEAN` | `NOT NULL DEFAULT FALSE` | Indicates whether an encrypted credential record is attached |
| `last_tested_at` | `TIMESTAMPTZ` | `NULL` | Timestamp of most recent test execution |
| `last_test_status` | `VARCHAR(24)` | `NULL`, `CHECK (last_test_status IN ('connected', 'failed', 'timeout'))` | Terminal status from last test |
| `last_error_message` | `TEXT` | `NULL` | Sanitized error message from last failed/timed-out test (maps to `errorMessage`) |
| `last_successful_sync` | `TIMESTAMPTZ` | `NULL` | Reserved for future sync metadata |
| `sync_frequency` | `VARCHAR(64)` | `NULL` | Reserved for future sync schedule metadata |
| `created_at` | `TIMESTAMPTZ` | `NOT NULL DEFAULT NOW()` | Creation timestamp (UTC) |
| `updated_at` | `TIMESTAMPTZ` | `NOT NULL DEFAULT NOW()` | Last update timestamp (UTC) |

### 3.2 `connection_credentials` Table (Backend Vault Only)

| Column | Type | Constraints | Notes |
| --- | --- | --- | --- |
| `connection_id` | `UUID` | `PRIMARY KEY REFERENCES connections(id) ON DELETE CASCADE` | One encrypted record per connection; cascading cleanup on delete |
| `ciphertext` | `BYTEA` | `NOT NULL` | AES-256-GCM ciphertext + 16-byte authentication tag |
| `nonce` | `BYTEA` | `NOT NULL` | 12-byte cryptographically random nonce (`os.urandom(12)`) |
| `key_id` | `VARCHAR(32)` | `NOT NULL` | Identifier of the encryption key used (e.g. `k1`) |
| `algorithm` | `VARCHAR(32)` | `NOT NULL DEFAULT 'AES-256-GCM'` | AEAD cipher identifier |
| `format_version` | `INTEGER` | `NOT NULL DEFAULT 1` | Encrypted payload envelope version |
| `created_at` | `TIMESTAMPTZ` | `NOT NULL DEFAULT NOW()` | Creation timestamp (UTC) |
| `updated_at` | `TIMESTAMPTZ` | `NOT NULL DEFAULT NOW()` | Last credential rotation/replacement timestamp (UTC) |

**Invariant:** `connection_credentials` is never joined or returned by list, get, create, or update API serializers.

---

## 4. Connector-Specific Configuration & Credential Schemas

Reuse the existing Pydantic validation rules in `apps/api/src/app/schemas/connection_test.py` (`extra="forbid"`, `hide_input_in_errors=True`, `SecretStr`) so persisted and tested schemas cannot drift.

| Connector Type | Non-Secret `config` (`JSONB`) | Encrypted `credentials` Payload | Computed `endpoint` |
| --- | --- | --- | --- |
| **PostgreSQL** | `{ host, port, database }` | `{ username, password? }` | `{host}:{port}/{database}` |
| **MySQL** | `{ host, port, database }` | `{ username, password? }` | `{host}:{port}/{database}` |
| **MongoDB** | `{ uri, database }` (`uri` must NOT contain `@` userinfo or secret query params) | `{ username?, password? }` (single authoritative URI in `config.uri`) | `{sanitized_uri}/{database}` |
| **REST API** | `{ baseUrl, authType }` (`none \| api_key \| bearer`) | `{ apiKey?, bearerToken? }` | `{origin}{path}` |
| **CSV** | `{ fileName, endpoint? }` (`*.csv`; no local/browser paths) | Not permitted (`null` / omitted; `authConfigured = false`) | `endpoint` or `local://{fileName}` |
| **JSON** | `{ fileName, endpoint? }` (`*.json`; no local/browser paths) | Not permitted (`null` / omitted; `authConfigured = false`) | `endpoint` or `local://{fileName}` |

### Validation Rules
- **PostgreSQL / MySQL:** Valid host (no scheme/path/userinfo), integer port `1–65535`, non-empty `database`; `username` and optional `password` are stored strictly in encrypted `connection_credentials` (rejected in `config` JSONB).
- **MongoDB:** `config.uri` is the single authoritative URI and must use `mongodb://` or `mongodb+srv://` with no embedded userinfo or credential query parameters; optional `username` and `password` are stored strictly in `connection_credentials`.
- **REST API:** `baseUrl` must use `http://` or `https://` with no embedded credentials; `authType: "api_key"` requires `credentials.apiKey` on create (or `replace`); `authType: "bearer"` requires `credentials.bearerToken` on create (or `replace`); `authType: "none"` forbids credentials and clears any existing secret on update; switching auth modes rejects `keep` so incompatible secrets cannot be reused.
- **CSV / JSON:** `fileName` must be a bare filename ending with `.csv`/`.json` without paths or traversal; `endpoint` forbids local OS/browser filesystem paths (`file:`, `/...`, `~/...`, `./...`, `C:\...`); file contents (`fileContent`) are **never persisted** in PostgreSQL.

---

## 5. Credential Encryption Architecture

### 5.1 Cryptographic Design
- **Library:** Python `cryptography` (`cryptography.hazmat.primitives.ciphers.aead.AESGCM`).
- **Cipher:** **AES-256-GCM** (authenticated encryption with associated data).
  - Provides 256-bit confidentiality and built-in integrity/authenticity (authentication tag verification prevents silent tampering or wrong-key corruption).
  - Uses a fresh 96-bit (`12`-byte) CSPRNG nonce (`os.urandom(12)`) on every encryption operation.
  - Binds Associated Authenticated Data (`AAD`) to prevent ciphertext swapping across records:
    ```text
    aad = f"{connection_id}:{connector_type}:{key_version}".encode("utf-8")
    ```
- **Plaintext envelope before encryption:** Canonical UTF-8 JSON (`{"v":1,"connectorType":...,"secrets":{...}}`) of the validated connector secret dictionary (stripped of `None`/empty values).
- **Typed encrypted payload (`EncryptedCredential`):** `{ ciphertext: bytes, nonce: bytes, key_id: str, algorithm: "AES-256-GCM", format_version: 1 }`.

### 5.2 Key Management & Configuration
- Encryption keys are provided strictly via environment configuration (or external secret manager injecting environment variables):
  - `CREDENTIAL_ENCRYPTION_KEYS`: JSON mapping of `{ "<key_version>": "<base64-encoded-32-byte-key>" }` (wrapped in `SecretStr`).
  - `CREDENTIAL_ACTIVE_KEY_ID`: Active key version ID used for all new encryptions (must exist in `CREDENTIAL_ENCRYPTION_KEYS`).
- **Initial key generation:**
  ```bash
  python3 -c 'import base64, json, os; print(json.dumps({"k1": base64.b64encode(os.urandom(32)).decode()}))'
  ```
- **Strict startup / fail-closed policy:**
  - Never hardcode a default or fallback encryption key in source code or `.env.example`.
  - Validate key decoding (exact 32-byte length) and active key existence at configuration load, and block application startup when `DATABASE_URL` is configured without valid encryption keys (while preserving stateless Task 03 mode when `DATABASE_URL` is unset).

### 5.3 Decryption & Failure Handling
- `CredentialEncryptionService.decrypt(encrypted, connection_id, connector_type)` validates envelope metadata, selects the key matching `encrypted.key_id`, verifies `AAD` and GCM tag, and returns the typed `SecretStr`-backed credential model strictly inside backend operations.
- **Failure modes:**
  - **Unknown `key_id`, invalid nonce/version, `InvalidTag`, AAD mismatch, or malformed JSON:** Raises `CredentialDecryptionError`; API returns sanitized `500 SERVER_ERROR` without exposing plaintext, ciphertext, nonces, or key material, and never falls back to empty credentials.
  - **Missing credential row when `auth_configured=True`:** Treated as inconsistent vault state; returns `422` / `500` prompting credential replacement rather than silently succeeding.

### 5.4 Key Rotation & Backup Implications
- **Rotation:** Add a new key version (e.g., `"k2"`) to `CREDENTIAL_ENCRYPTION_KEYS` while keeping `"k1"`, switch `CREDENTIAL_ACTIVE_KEY_ID="k2"`, and deploy. New/updated records encrypt under `"k2"` while older records continue decrypting with `"k1"`. Changing the active key does **not** automatically re-encrypt existing rows; retain retired keys until all rows referencing them have been replaced or rotated.
- **Backups:** Database backups contain `ciphertext`, `nonce`, and `key_version`, which are useless without `CREDENTIAL_ENCRYPTION_KEYS`. Operators must back up key versions in a secure secret manager independently of database snapshots. Losing a key version renders affected credentials permanently unrecoverable.

---

## 6. API Endpoint Contracts

All endpoints live under `/api/v1/connections`. All error responses follow the normalized error envelope (Section 7).

### 6.1 Shared Safe Response Model (`ConnectionResponse`)

Matches the frontend `Connection` type in `apps/web/src/types/connection.ts`:

```json
{
  "id": "8e3d9c52-4a91-4f8f-9e42-1a2b3c4d5e6f",
  "name": "prod-orders-pg",
  "type": "PostgreSQL",
  "status": "Connected",
  "endpoint": "db-orders.internal:5432/orders",
  "description": "Primary transactional orders read replica",
  "lastSuccessfulSync": null,
  "syncFrequency": null,
  "errorMessage": null,
  "lastTestedAt": "2026-11-27T09:40:00+00:00",
  "lastTestStatus": "connected",
  "createdAt": "2026-11-27T09:30:00+00:00",
  "updatedAt": "2026-11-27T09:40:00+00:00",
  "details": {
    "host": "db-orders.internal",
    "port": 5432,
    "database": "orders",
    "authConfigured": true
  }
}
```

### 6.2 Create Connection — `POST /api/v1/connections`
- **Request:** Discriminated union `CreateConnectionRequest` keyed on `connectorType`, carrying `name`, `connectorType`, `description?`, `config`, and optional `credentials` (PostgreSQL/MySQL `username` is supplied inside `credentials` and encrypted).
- **Example Request:**
  ```json
  {
    "name": "prod-orders-pg",
    "connectorType": "PostgreSQL",
    "description": "Primary transactional orders read replica",
    "config": {
      "host": "db-orders.internal",
      "port": 5432,
      "database": "orders"
    },
    "credentials": {
      "username": "databridge_ro",
      "password": "secret-password"
    }
  }
  ```
- **Responses:**
  - `201 Created`: `ConnectionResponse` (`status: "Disconnected"`, `details.authConfigured: true`, no secrets).
  - `409 Conflict`: Duplicate connection name (`DUPLICATE_CONNECTION`).
  - `422 Unprocessable Content`: Schema validation failure (`INVALID_CONFIGURATION` / `UNSUPPORTED_CONNECTOR`).

### 6.3 List Connections — `GET /api/v1/connections`
- **Query parameters (optional):**
  - `status`: `'Connected' | 'Disconnected' | 'Error'`
  - `type`: `ConnectorType`
  - `q`: case-insensitive substring filter on `name` or `endpoint`
- **Response (`200 OK`):**
  ```json
  {
    "items": [ /* ConnectionResponse[] */ ],
    "total": 6
  }
  ```

### 6.4 Get Connection Details — `GET /api/v1/connections/{connection_id}`
- **Responses:**
  - `200 OK`: `ConnectionResponse`
  - `404 Not Found`: `CONNECTION_NOT_FOUND`

### 6.5 Update Connection — `PUT /api/v1/connections/{connection_id}`
- **Request:** `UpdateConnectionRequest` (`connectorType` immutable on existing connection; rejects type changes with `422 INVALID_CONFIGURATION`):
  - `name`: `string`
  - `description?`: `string | null`
  - `config`: connector-specific non-secret config
  - `credentialAction?`: `'keep' | 'replace' | 'clear'` (defaults to `'keep'`)
  - `credentials?`: connector-specific secret object (required when `credentialAction === 'replace'`, forbidden when `credentialAction === 'keep'` or `'clear'`).
- **Credential update semantics:**
  1. **Omitted (`credentialAction: "keep"`, `credentials` omitted):** Updates metadata/config only; preserves existing `connection_credentials` row and `authConfigured`.
  2. **Replace (`credentialAction: "replace"`, `credentials: { ... }`):** Encrypts new secrets with `CREDENTIAL_ACTIVE_KEY_ID`, upserts `connection_credentials`, sets `authConfigured = true`.
  3. **Clear (`credentialAction: "clear"`, `credentials` omitted):** Deletes any linked `connection_credentials` row inside the transaction and sets `authConfigured = false`.
- **Responses:**
  - `200 OK`: Updated `ConnectionResponse` (never returns secrets).
  - `404 Not Found`: `CONNECTION_NOT_FOUND`.
  - `409 Conflict`: Updated `name` conflicts with another connection (`DUPLICATE_CONNECTION`).
  - `422 Unprocessable Content`: Invalid config, immutable connector type mismatch, or invalid `credentialAction` / `credentials` combination.

### 6.6 Delete Connection — `DELETE /api/v1/connections/{connection_id}`
- **Behavior:** Atomically deletes the `connections` row and associated `connection_credentials` row inside a single DB transaction (`ON DELETE CASCADE` + explicit delete).
- **Responses:**
  - `204 No Content`: Empty body.
  - `404 Not Found`: `CONNECTION_NOT_FOUND`.

### 6.7 Test Saved Connection — `POST /api/v1/connections/{connection_id}/test`
- **Request body:**
  - For `PostgreSQL`, `MySQL`, `MongoDB`, `REST API`: empty body `{}` or omitted. **Must reject** requests containing `credentials` (`extra="forbid"`), ensuring saved-connection tests use backend-stored credentials exclusively.
  - For `CSV` / `JSON`: `{ "fileContent": "..." }` (transient file content required because file contents are never stored in the database).
- **Execution flow:**
  1. Load `connections` row by `connection_id` (`404` if missing).
  2. If `auth_configured` is `true`, load `connection_credentials` row and decrypt inside backend memory via `CredentialService`.
  3. Assemble transient `ConnectionTestRequest` in memory and dispatch to `ConnectionTestService` (reusing existing testers, SSRF protections, and timeouts).
  4. Persist `last_tested_at`, `last_test_status`, `last_error_message` (sanitized), and update `status` (`'Connected'` if `success` else `'Disconnected'`) in `connections`.
  5. Return existing normalized `ConnectionTestResult` (`{ success, status, message, errorCode?, durationMs?, testedAt? }`).
- **Note on existing endpoint:** `POST /api/v1/connections/test` remains intact for testing unsaved draft configurations or explicit transient overrides in `AddConnectionModal` / `TestConnectionModal`.

---

## 7. Error Response Contract

All endpoints share a consistent, secret-free error contract:

- **`POST /api/v1/connections/{connection_id}/test`** (and `/api/v1/connections/test`) returns `ConnectionTestResult`:
  ```json
  {
    "success": false,
    "status": "failed",
    "message": "Connection not found.",
    "errorCode": "CONNECTION_NOT_FOUND",
    "durationMs": 2,
    "testedAt": "2026-11-27T09:45:00+00:00"
  }
  ```
- **CRUD endpoints** (`/api/v1/connections`, `/api/v1/connections/{id}`) return a normalized `ApiErrorResponse`:
  ```json
  {
    "errorCode": "DUPLICATE_CONNECTION",
    "message": "A connection with this name already exists.",
    "timestamp": "2026-11-27T09:45:00+00:00"
  }
  ```

### Standard Error Codes & HTTP Mapping

| Condition | HTTP Status | `errorCode` |
| --- | --- | --- |
| Invalid input / schema / `credentialAction` mismatch | `422` (or `400` malformed JSON) | `INVALID_CONFIGURATION` |
| Unsupported connector type | `422` | `UNSUPPORTED_CONNECTOR` |
| Connection ID not found | `404` | `CONNECTION_NOT_FOUND` |
| Duplicate connection name | `409` | `DUPLICATE_CONNECTION` |
| Payload size exceeded | `413` | `INVALID_CONFIGURATION` |
| Database unavailable / unexpected failure | `503` / `500` | `DATABASE_UNAVAILABLE` / `SERVER_ERROR` |
| Credential encryption / decryption failure | `500` | `CREDENTIAL_ERROR` |

All validation and exception handlers must keep `hide_input_in_errors=True` and run messages through `sanitize_error_message` so passwords, API keys, bearer tokens, MongoDB URIs, SQL statements, and DB connection strings never reach the client.

---

## 8. Frontend Integration Strategy

- **API Service (`apps/web/src/services/connectionsService.ts`):**
  - Reuse `buildApiUrl()` from `apps/web/src/config/env.ts` and the error-sanitization patterns in `connectionTestService.ts`.
  - Expose `listConnections`, `getConnection`, `createConnection`, `updateConnection`, `deleteConnection`, and `testSavedConnection`.
- **`ConnectionsPage.tsx`:**
  - Replace `MOCK_CONNECTIONS` initial state with async API fetch on mount (with loading, empty, and error states, plus explicit retry—never falling back to mock data when the API fails).
  - Keep existing `ConnectionCard`, `ConnectionDetailsModal`, `AddConnectionModal`, `DeleteConnectionDialog`, `TestConnectionModal`, search bar, and status filter bar intact.
- **Create/Edit (`AddConnectionModal.tsx` & `connectorForm.ts`):**
  - Create sends `credentials` once to `POST /api/v1/connections` and clears local form secrets on close.
  - Edit defaults `credentialAction: "keep"` unless the user enters a replacement secret (`"replace"`) or clears auth (`"clear"` / `authType: "none"`).
  - `Connection` state in React stores only `ConnectionResponse` metadata (`details.authConfigured`, never secrets or ciphertext).
- **Saved-Connection Testing (`TestConnectionModal.tsx`):**
  - For database/REST connections with `details.authConfigured === true` where the user does not supply a transient override, call `POST /api/v1/connections/{id}/test` so the backend tests using encrypted vault credentials without re-entering secrets.
  - When the user supplies transient credentials/overrides or tests an unsaved connection or CSV/JSON `fileContent`, continue using `POST /api/v1/connections/test` (or `POST /api/v1/connections/{id}/test` with `{ fileContent }` for saved CSV/JSON).

---

## 9. Security & Authorization Considerations

- **No plaintext secrets in `connections`:** Passwords, tokens, API keys, and credential-bearing MongoDB URIs are strictly stored in `connection_credentials.ciphertext` and excluded from `connections.config` and `connections.endpoint`.
- **No secret logging:** `SensitiveDataFilter`, `access_log=False`, and `hide_input_in_errors=True` remain enforced across all new routes and repository errors.
- **No browser secret storage:** `localStorage`, `sessionStorage`, and React `Connection` state never store credentials, encrypted blobs, or CSV/JSON `fileContent`.
- **Authorization gap (explicitly documented):** The repository currently has **no authentication or authorization system** (`WorkspaceContext` is mock UI state only). Until user authentication and workspace-scoped RBAC are implemented, any caller able to reach the API can read, create, update, delete, or test saved connections. Production deployment must gate `/api/v1/*` behind authenticated access control and add `workspace_id` / ownership filtering to every SQL query to prevent IDOR.

---

## 10. Implementation Sequence (Steps 02–06)

- **Step 02 — Database Layer & Migrations:** Add `psycopg_pool`, configure `DATABASE_URL` and pool settings in `Settings`, implement migration runner and `0001_create_connections_and_credentials.sql`, wire pool lifecycle into `main.py`, and add unit/integration DB setup tests.
- **Step 03 — Credential Encryption Service:** Add `cryptography`, implement `CredentialService` (AES-256-GCM + AAD + key version keyring), `CredentialRepository`, and tests for encrypt/decrypt, AAD tampering rejection, key rotation, and malformed/missing key handling.
- **Step 04 — Connection Repository & CRUD + Saved-Test Endpoints:** Implement `ConnectionRepository`, `ConnectionService`, routes (`POST/GET/PUT/DELETE /api/v1/connections`, `POST /api/v1/connections/{id}/test`), normalized error codes, and full API test suite.
- **Step 05 — Frontend Persistence Integration:** Implement `connectionsService.ts`, migrate `ConnectionsPage.tsx`, `AddConnectionModal.tsx`, `ConnectionDetailsModal.tsx`, and `TestConnectionModal.tsx` from mock state to API persistence while preserving existing UI/UX.
- **Step 06 — Full-Stack Integration & Security Verification:** Verify end-to-end CRUD, vault encryption/decryption, saved-connection testing, key-rotation behavior, and browser/regression checks.

---

## 11. Dependencies & Environment Variables Required

### New Backend Dependencies (for Steps 02–03, not installed in Step 01)
- `psycopg-pool>=3.2.0` (async PostgreSQL connection pooling matching existing `psycopg[binary]`)
- `cryptography>=43.0.0` (AES-256-GCM authenticated encryption)

### New Environment Variables (for Steps 02–03)
- `DATABASE_URL` — PostgreSQL connection string (`SecretStr`, e.g. `postgresql://databridge:password@localhost:5432/databridge`)
- `DATABASE_POOL_MIN_SIZE` — Minimum pool connections (default `1`)
- `DATABASE_POOL_MAX_SIZE` — Maximum pool connections (default `10`)
- `DATABASE_CONNECT_TIMEOUT_SECONDS` — Pool/DB connect timeout (default `5.0`)
- `CREDENTIAL_ENCRYPTION_KEYS` — JSON map of key version to base64 32-byte key (`SecretStr`, no default)
- `CREDENTIAL_ACTIVE_KEY_ID` — Active key version ID for new encryptions (e.g. `k1`, no hardcoded key)
