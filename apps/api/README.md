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

## Installed Connector Dependencies

- **PostgreSQL**: `psycopg[binary]` (psycopg 3 async driver with `connect_timeout` and `statement_timeout`)
- **MySQL**: `pymysql` (pure-Python driver executed via `asyncio.to_thread` with `connect_timeout`, `read_timeout`, and `write_timeout`)
- **MongoDB**: `pymongo` + `dnspython` (`AsyncMongoClient` supporting both `mongodb://` and `mongodb+srv://` URIs)
- **REST API**: `httpx` (`AsyncClient` with custom `SsrfSafeHttpTransport` / `httpcore` network backend pinning)
- **CSV / JSON**: Python standard library `csv` and `json` modules

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
