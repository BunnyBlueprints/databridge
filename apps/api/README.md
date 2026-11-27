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

## Installed Database Driver Dependencies

- **PostgreSQL**: `psycopg[binary]` (psycopg 3 async driver with `connect_timeout` and `statement_timeout`)
- **MySQL**: `pymysql` (pure-Python driver executed via `asyncio.to_thread` with `connect_timeout`, `read_timeout`, and `write_timeout`)
- **MongoDB**: `pymongo` + `dnspython` (`AsyncMongoClient` supporting both `mongodb://` and `mongodb+srv://` URIs)

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

## Supported Database Connection Tests

`POST /api/v1/connections/test` performs read-only connectivity verification for:

- **PostgreSQL**: Opens an async `psycopg` connection, runs `SELECT 1`, verifies the returned row, and closes the cursor and connection. Passwordless connections are supported if permitted by the target PostgreSQL server.
- **MySQL**: Opens a `pymysql` connection in a bounded worker thread, runs `SELECT 1`, verifies the returned row, and closes the cursor and connection.
- **MongoDB**: Opens an `AsyncMongoClient` (supporting `mongodb://` and `mongodb+srv://`), runs `client.admin.command("ping")`, verifies `ok`, and closes the client.

No writes, schema changes, or collection reads are performed by any tester.

## Required Configuration Fields

- **PostgreSQL / MySQL**: `config.host`, `config.port` (`1–65535`), `config.database`, `config.username` (optional `credentials.password`).
- **MongoDB**: `config.uri` (`mongodb://` or `mongodb+srv://` without embedded userinfo), `config.database`, optional `config.username` (optional `credentials.password` and/or `credentials.connectionUri`).

## Timeout Behavior

- Configured globally via `CONNECTION_TEST_TIMEOUT_SECONDS` (default `10.0` seconds).
- Enforced both at the driver level (`connect_timeout` / `statement_timeout` for PostgreSQL; `connect_timeout` / `read_timeout` / `write_timeout` for MySQL; `connectTimeoutMS` / `serverSelectionTimeoutMS` / `socketTimeoutMS` for MongoDB) and at the service layer via `asyncio.wait_for`.

## Credential Behavior

- Credentials are transient for the duration of a single `POST /api/v1/connections/test` request.
- Credentials are wrapped in Pydantic `SecretStr`, never logged, never stored in any database/file/cache, and never returned in responses or validation errors.
- For MongoDB, if `credentials.connectionUri` already contains embedded userinfo, credentials are not duplicated into client kwargs.

## Error Classification

Driver and network errors are mapped into sanitized, normalized `errorCode` values without exposing stack traces, driver messages, URIs, or internal host details:

- `AUTHENTICATION_FAILED`
- `CONNECTION_REFUSED`
- `DNS_RESOLUTION_FAILED`
- `TIMEOUT`
- `INVALID_CONFIGURATION`
- `NETWORK_ERROR`
- `SERVER_ERROR`
- `TESTER_NOT_IMPLEMENTED` (for connectors not yet implemented)

## Current Limitations

- `REST API`, `CSV`, and `JSON` connection testers are not implemented yet and return `501 Not Implemented` (`TESTER_NOT_IMPLEMENTED`).
- Database synchronization, schema inspection, and data extraction are not implemented.
- Credentials are not persisted; testing saved connections without stored backend credentials requires passing transient credentials in the request.

## Tests & Lint

```bash
cd apps/api
uv run pytest -v
uv run ruff check src tests
uv run ruff format --check src tests
```
