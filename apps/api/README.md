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

## Current Limitations

- `POST /api/v1/connections/test` validates connector payloads and dispatches to the tester registry, returning `501 Not Implemented` (`TESTER_NOT_IMPLEMENTED`) until connector-specific testers are added in subsequent steps.
- Credentials passed in test requests are transient and are never persisted or logged.

## Tests & Lint

```bash
cd apps/api
uv run pytest
uv run ruff check src tests
```
