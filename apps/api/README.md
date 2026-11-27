# DataBridge API

FastAPI backend service for DataBridge connection testing and integration workflows.

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)

## Setup

```bash
cd apps/api
cp .env.example .env
uv sync --dev
```

## Installed Dependencies

- **Web / Validation**: `fastapi`, `pydantic`, `pydantic-settings`, `uvicorn[standard]`
- **PostgreSQL Persistence Foundation**: `psycopg[binary]` + `psycopg-pool` (async connection pool & forward-only SQL migrations)
- **Connector Drivers**:
  - **PostgreSQL**: `psycopg[binary]` (psycopg 3 async driver with `connect_timeout` and `statement_timeout`)
  - **MySQL**: `pymysql` (pure-Python driver executed via `asyncio.to_thread` with `connect_timeout`, `read_timeout`, and `write_timeout`)
  - **MongoDB**: `pymongo` + `dnspython` (`AsyncMongoClient` supporting both `mongodb://` and `mongodb+srv://` URIs)
  - **REST API**: `httpx` (`AsyncClient` with custom `SsrfSafeHttpTransport` / `httpcore` network backend pinning)
  - **CSV / JSON**: Python standard library `csv` and `json` modules

## Database Persistence & Migrations

DataBridge supports two modes:

- **Stateless mode (default when `DATABASE_URL` is unset)**: Existing Task 03 connection testing (`POST /api/v1/connections/test`) works without a PostgreSQL database; `GET /health/db` returns `200` with `{"status": "disabled", "persistenceEnabled": false}`.
- **Persistent mode (when `DATABASE_URL` is configured)**: Initializes an async `psycopg_pool.AsyncConnectionPool` via FastAPI lifespan, validates DB connectivity on startup, exposes readiness via `GET /health/db`, and enables persistent connection CRUD (`POST/GET/PUT/DELETE /api/v1/connections`) and saved testing (`POST /api/v1/connections/{connection_id}/test`).

> **Deployment Security Boundary:** DataBridge does not yet implement user authentication or workspace RBAC. Never expose `/api/v1/connections*` publicly without an authenticated access-control boundary—UUIDs identify connections and do not provide authorization.

### Database Environment Variables

- `DATABASE_URL`: PostgreSQL connection string (`postgresql://user:password@host:5432/dbname`). Leave unset for stateless mode.
- `DATABASE_POOL_MIN_SIZE`: Minimum pool size (default `1`).
- `DATABASE_POOL_MAX_SIZE`: Maximum pool size (default `10`).
- `DATABASE_POOL_TIMEOUT_SECONDS`: Connection acquisition timeout in seconds (default `5.0`).
- `DATABASE_CONNECT_TIMEOUT_SECONDS`: TCP/libpq connect timeout in seconds (default `5`).
- `DATABASE_HEALTH_CHECK_ENABLED`: Enable pool connection check callback (`true` by default).

### Running Migrations

Migrations are forward-only `.sql` files in `apps/api/migrations/` tracked in `schema_migrations`, and are **never** run automatically on app startup:

```bash
cd apps/api
# Inspect current schema version and pending migrations
uv run python -m app.db.cli status

# Apply pending migrations
uv run python -m app.db.cli migrate
```

## Credential Encryption (AES-256-GCM)

When `DATABASE_URL` is configured, DataBridge requires backend credential encryption configuration at startup (`CREDENTIAL_ENCRYPTION_KEYS` and `CREDENTIAL_ACTIVE_KEY_ID`). Stateless connection testing (`DATABASE_URL` unset) works without encryption keys.

### Key Encoding & Initial Generation

Each key in `CREDENTIAL_ENCRYPTION_KEYS` must be a Base64-encoded 32-byte (256-bit) random key stored as a JSON object mapping key IDs to keys:

```bash
python3 -c 'import base64, json, os; print(json.dumps({"k1": base64.b64encode(os.urandom(32)).decode()}))'
```

Configure in `.env` (or inject via a secret manager):

```env
CREDENTIAL_ENCRYPTION_KEYS={"k1":"<generated-32-byte-base64-key>"}
CREDENTIAL_ACTIVE_KEY_ID=k1
```

### Encryption Format & Key Rotation

- `CredentialEncryptionService` encrypts validated connector credentials with **AES-256-GCM**, a fresh 12-byte CSPRNG nonce (`os.urandom(12)`) per operation, and Associated Authenticated Data (AAD) `{connection_id}:{connector_type}:{key_version}`.
- New encryptions always use `CREDENTIAL_ACTIVE_KEY_ID`; decryption selects the key matching the stored record's `key_id`.
- **Rotation procedure:**
  1. Generate a new 32-byte Base64 key (e.g., `k2`) and add it to `CREDENTIAL_ENCRYPTION_KEYS` while keeping `k1` present.
  2. Switch `CREDENTIAL_ACTIVE_KEY_ID=k2` and deploy; new or updated credentials encrypt under `k2`, while existing records continue decrypting with `k1`.
  3. Changing `CREDENTIAL_ACTIVE_KEY_ID` does **not** automatically re-encrypt existing stored records; keep retired keys in `CREDENTIAL_ENCRYPTION_KEYS` until all rows referencing them have been updated or rotated.
- **Backup & key loss:** Database backups do not contain encryption keys. Back up `CREDENTIAL_ENCRYPTION_KEYS` in a secure secret manager independently of DB snapshots—losing a key renders credentials encrypted under that key permanently unrecoverable.

## Running the Server

```bash
cd apps/api
uv run uvicorn app.main:app --app-dir src --host 127.0.0.1 --port 8000 --reload
```

Or run the module entry point:

```bash
cd apps/api
uv run python -m app.main
```

## API Documentation

When `ENABLE_API_DOCS=true` (default):

- Swagger UI: `http://127.0.0.1:8000/docs`
- ReDoc: `http://127.0.0.1:8000/redoc`
- OpenAPI JSON: `http://127.0.0.1:8000/openapi.json`

## Connector Support Matrix

| Connector | Verification Performed | Required Configuration | Transient Inputs |
| --- | --- | --- | --- |
| **PostgreSQL** | Async connect + `SELECT 1` | `host`, `port`, `database`, `username` | Optional `credentials.password` |
| **MySQL** | Bounded thread connect + `SELECT 1` | `host`, `port`, `database`, `username` | Optional `credentials.password` |
| **MongoDB** | Async connect + `admin` `ping` | `uri`, `database`, optional `username` | Optional `credentials.password` / `credentials.connectionUri` |
| **REST API** | SSRF-protected `GET` + bounded stream | `baseUrl`, `authType` (`none`, `api_key`, `bearer`) | `credentials.apiKey` or `credentials.bearerToken` |
| **CSV** | UTF-8/BOM decode + `csv.reader` header/column check | `fileName` (`*.csv`), optional `endpoint` | Required `fileContent` (no `credentials`) |
| **JSON** | UTF-8/BOM decode + strict `json.loads` syntax check | `fileName` (`*.json`), optional `endpoint` | Required `fileContent` (no `credentials`) |

## REST API Testing, Authentication & SSRF Protections

- Sends an HTTP `GET` request to `config.baseUrl` with `follow_redirects=False` and `trust_env=False` (ignoring proxy env variables).
- **Authentication**:
  - `none`: no auth headers sent.
  - `bearer`: sends `Authorization: Bearer <token>`.
  - `api_key`: sends `X-API-Key: <apiKey>`.
  - Credentials are never placed in query parameters or URL strings.
- **SSRF & Anti-DNS-Rebinding**:
  - Allows only `http://` and `https://` URLs with public FQDNs or public global IP addresses.
  - Blocks `localhost`, loopback (`127.0.0.0/8`, `::1`), private networks (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `fc00::/7`), link-local / metadata ranges (`169.254.0.0/16`, `fe80::/10`, `metadata.google.internal`), CGNAT (`100.64.0.0/10`), IPv4-mapped IPv6 (`::ffff:127.0.0.1`), single-label internal hostnames, and Unix domain sockets.
  - `SsrfSafeNetworkBackend` resolves the hostname once, validates every returned IP, and connects the TCP socket directly to the pinned public IP so DNS rebinding cannot occur while preserving TLS SNI and certificate validation.
  - Redirects (`3xx`) are never followed automatically; redirects to blocked destinations are explicitly flagged.
- **HTTP Status Interpretation**:
  - `2xx`: `connected`
  - `401` / `403`: `AUTHENTICATION_FAILED`
  - Other `4xx` or `3xx`: `INVALID_CONFIGURATION` (endpoint reached but rejected/redirected)
  - `5xx`: `SERVER_ERROR`
  - Timeouts: `TIMEOUT` (`status: "timeout"`)
- **Response Limits**: Streams response chunks up to `REST_API_MAX_RESPONSE_BYTES` (default `65536` bytes / 64 KB) and closes immediately without buffering or storing response bodies.

## CSV & JSON Validation & Transient File Handling

- **Transient `fileContent`**: CSV and JSON test requests include `fileContent` strictly for the duration of the `POST /api/v1/connections/test` call. Contents are never persisted to disk, databases, or caches, and are never logged or returned in errors.
- **CSV Rules**: Requires `.csv` extension, UTF-8 text (strips a single leading BOM `\ufeff`), non-empty content, valid non-empty unique header row, and consistent column counts across non-blank rows. Valid header-only CSVs with 0 data rows pass.
- **JSON Rules**: Requires `.json` extension, UTF-8 text (strips a single leading BOM `\ufeff`), non-empty content, and strict `json.loads` parsing (rejects non-standard `NaN`/`Infinity`). Accepts valid JSON objects, arrays, strings, numbers, booleans, and `null`, reporting a safe top-level structural summary.

## Request & File Size Limits

- `API_MAX_REQUEST_BYTES`: `524288` bytes (512 KB default) enforced by `RequestSizeLimitMiddleware` on both `Content-Length` and streamed request bodies (`413`).
- `CONNECTION_TEST_MAX_FILE_BYTES`: `262144` bytes (256 KB default) enforced on UTF-8 byte length of `fileContent`, leaving headroom for JSON encoding overhead within the 512 KB request limit.
- `CONNECTION_TEST_TIMEOUT_SECONDS`: `10.0` seconds default.

## Current Limitations

- Connectivity tests validate reachability, authentication, and transient file syntax/structure only.
- Data synchronization, schema discovery, file ingestion/import pipelines, and credential vault persistence are not implemented yet.

## Tests & Lint

```bash
cd apps/api
uv run pytest -v
uv run ruff check src tests
uv run ruff format --check src tests
```
