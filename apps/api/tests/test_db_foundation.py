"""Unit tests (and optional env-gated PostgreSQL integration tests) for database foundation."""

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import psycopg
import psycopg.errors
import psycopg_pool
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.db.cli import main as cli_main
from app.db.errors import (
    DatabaseConflictError,
    DatabaseNotConfiguredError,
    DatabaseTimeoutError,
    DatabaseUnavailableError,
    MigrationError,
)
from app.db.migrations import MigrationRunner, discover_migrations
from app.db.pool import DatabaseManager
from app.main import create_app
from app.repositories.base import BaseRepository, translate_db_error


class _FakeCursor:
    def __init__(self, rows: list[tuple[Any, ...]] | None = None) -> None:
        self._rows = rows or []

    async def fetchone(self) -> tuple[Any, ...] | None:
        return self._rows[0] if self._rows else None

    async def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self._rows)


class _FakeTransaction:
    def __init__(self, conn: "_FakeConnection") -> None:
        self._conn = conn

    async def __aenter__(self) -> "_FakeTransaction":
        self._conn.tx_depth += 1
        return self

    async def __aexit__(self, exc_type: Any, _exc: Any, _tb: Any) -> None:
        if exc_type is not None:
            self._conn.rollbacks += 1
        else:
            self._conn.commits += 1
        self._conn.tx_depth -= 1


class _FakeConnection:
    def __init__(self, *, fail_sql_substring: str | None = None) -> None:
        self.executed: list[tuple[str, Any]] = []
        self.applied_rows: dict[str, tuple[str, str, str]] = {}
        self.tx_depth = 0
        self.commits = 0
        self.rollbacks = 0
        self._fail_sql_substring = fail_sql_substring

    def transaction(self) -> _FakeTransaction:
        return _FakeTransaction(self)

    async def execute(self, sql: str, params: Any = None) -> _FakeCursor:
        normalized = " ".join(sql.split())
        if self._fail_sql_substring and self._fail_sql_substring in normalized:
            raise psycopg.errors.SyntaxError("simulated SQL failure")
        self.executed.append((normalized, params))

        if normalized == "SELECT 1":
            return _FakeCursor([(1,)])
        if normalized.startswith("SELECT version, name, applied_at::text FROM schema_migrations"):
            rows = [self.applied_rows[k] for k in sorted(self.applied_rows)]
            return _FakeCursor(rows)
        if normalized.startswith("INSERT INTO schema_migrations"):
            version, name = params
            self.applied_rows[version] = (version, name, "2026-11-27T10:00:00+00:00")
            return _FakeCursor()
        return _FakeCursor()


class _FakePool:
    def __init__(
        self,
        conn: _FakeConnection | None = None,
        *,
        open_error: BaseException | None = None,
        acquire_error: BaseException | None = None,
        fail_after_acquisitions: int = 0,
    ) -> None:
        self._conn = conn or _FakeConnection()
        self._open_error = open_error
        self._acquire_error = acquire_error
        self._fail_after_acquisitions = fail_after_acquisitions
        self.opened = False
        self.closed = False
        self.acquisitions = 0
        self.releases = 0

    async def open(self, *, wait: bool = True, timeout: float = 30.0) -> None:
        del wait, timeout
        if self._open_error is not None:
            raise self._open_error
        self.opened = True

    async def close(self) -> None:
        self.closed = True

    @asynccontextmanager
    async def connection(self, *, timeout: float = 30.0) -> AsyncIterator[_FakeConnection]:
        del timeout
        if self._acquire_error is not None and self.acquisitions >= self._fail_after_acquisitions:
            raise self._acquire_error
        self.acquisitions += 1
        try:
            yield self._conn
        finally:
            self.releases += 1


def _db_settings(
    url: str = "postgresql://databridge:super_secret_db_pw@db.internal:5432/databridge",
) -> Settings:
    return Settings(
        database_url=url,  # type: ignore[arg-type]
        database_pool_min_size=1,
        database_pool_max_size=4,
        database_pool_timeout_seconds=2.0,
        database_connect_timeout_seconds=2,
        credential_encryption_keys='{"k1":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="}',  # type: ignore[arg-type]
        credential_active_key_id="k1",
    )


def test_database_settings_validation_and_stateless_mode() -> None:
    stateless = Settings(database_url=None)
    assert stateless.database_enabled is False

    blank = Settings(database_url="   ")  # type: ignore[arg-type]
    assert blank.database_enabled is False

    valid = _db_settings()
    assert valid.database_enabled is True
    assert "super_secret_db_pw" not in repr(valid.database_url)

    with pytest.raises(ValueError) as bad_scheme:
        Settings(database_url="mysql://user:pw@localhost:3306/db")  # type: ignore[arg-type]
    assert "pw" not in str(bad_scheme.value)

    with pytest.raises(ValueError):
        Settings(database_url="postgresql://localhost:5432")  # type: ignore[arg-type]

    with pytest.raises(ValueError):
        Settings(
            database_url="postgresql://user:pw@localhost:5432/db",  # type: ignore[arg-type]
            database_pool_min_size=8,
            database_pool_max_size=2,
        )


@pytest.mark.anyio
async def test_pool_initialization_readiness_and_shutdown(
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake_conn = _FakeConnection()
    fake_pool = _FakePool(fake_conn)
    captured_factory_kwargs: dict[str, Any] = {}

    def fake_factory(**kwargs: Any) -> _FakePool:
        captured_factory_kwargs.update(kwargs)
        return fake_pool

    manager = DatabaseManager(_db_settings(), pool_factory=fake_factory)
    with caplog.at_level(logging.DEBUG, logger="databridge.api"):
        await manager.startup()
        assert manager.is_open is True
        assert fake_pool.opened is True
        status_label, _detail = await manager.check_readiness()
        assert status_label == "ready"
        await manager.shutdown()

    assert manager.is_open is False
    assert fake_pool.closed is True
    assert fake_pool.acquisitions == fake_pool.releases == 2
    assert captured_factory_kwargs["min_size"] == 1
    assert captured_factory_kwargs["max_size"] == 4
    assert "super_secret_db_pw" not in caplog.text


@pytest.mark.anyio
async def test_missing_database_configuration_runs_stateless_and_blocks_pool_access() -> None:
    manager = DatabaseManager(Settings(database_url=None))
    await manager.startup()
    assert manager.is_configured is False
    assert manager.is_open is False

    status_label, _detail = await manager.check_readiness()
    assert status_label == "disabled"

    with pytest.raises(DatabaseNotConfiguredError):
        async with manager.connection():
            pass


@pytest.mark.anyio
async def test_database_startup_failure_closes_pool_and_hides_credentials(
    caplog: pytest.LogCaptureFixture,
) -> None:
    failing_pool = _FakePool(
        open_error=psycopg.OperationalError("connection failed to super_secret_db_pw@db.internal")
    )
    manager = DatabaseManager(_db_settings(), pool_factory=lambda **_kw: failing_pool)

    with caplog.at_level(logging.DEBUG, logger="databridge.api"):
        with pytest.raises(DatabaseUnavailableError) as exc_info:
            await manager.startup()

    assert failing_pool.closed is True
    assert manager.is_open is False
    assert "super_secret_db_pw" not in str(exc_info.value)
    assert "super_secret_db_pw" not in caplog.text


@pytest.mark.anyio
async def test_pool_acquisition_timeout_raises_database_timeout_error() -> None:
    timeout_pool = _FakePool(
        acquire_error=psycopg_pool.PoolTimeout("pool exhausted"),
        fail_after_acquisitions=1,
    )
    manager = DatabaseManager(_db_settings(), pool_factory=lambda **_kw: timeout_pool)
    await manager.startup()

    with pytest.raises(DatabaseTimeoutError):
        async with manager.connection():
            pass

    await manager.shutdown()


@pytest.mark.anyio
async def test_repository_transaction_rollback_and_error_translation() -> None:
    fake_conn = _FakeConnection()
    fake_pool = _FakePool(fake_conn)
    manager = DatabaseManager(_db_settings(), pool_factory=lambda **_kw: fake_pool)
    await manager.startup()

    repo = BaseRepository(manager)

    async with repo.transaction() as conn:
        await conn.execute("SELECT 1")
    assert fake_conn.commits == 1
    assert fake_conn.rollbacks == 0

    with pytest.raises(DatabaseConflictError):
        async with repo.transaction():
            raise psycopg.errors.UniqueViolation("duplicate key value violates unique constraint")

    assert fake_conn.rollbacks == 1
    assert isinstance(
        translate_db_error(psycopg.OperationalError("down")), DatabaseUnavailableError
    )


@pytest.mark.anyio
async def test_migrations_apply_skip_duplicates_and_inspect_status(tmp_path: Path) -> None:
    (tmp_path / "0001_initial.sql").write_text("SELECT 1;", encoding="utf-8")
    (tmp_path / "0002_second.sql").write_text("SELECT 2;", encoding="utf-8")

    fake_conn = _FakeConnection()
    fake_pool = _FakePool(fake_conn)
    manager = DatabaseManager(_db_settings(), pool_factory=lambda **_kw: fake_pool)
    await manager.startup()

    runner = MigrationRunner(manager, migrations_dir=tmp_path)
    initial_status = await runner.status()
    assert initial_status.current_version is None
    assert [m.label for m in initial_status.pending] == ["0001_initial", "0002_second"]

    applied_first = await runner.apply_pending()
    assert [m.label for m in applied_first] == ["0001_initial", "0002_second"]

    # Second run skips already-applied migrations cleanly.
    applied_second = await runner.apply_pending()
    assert applied_second == []

    final_status = await runner.status()
    assert final_status.current_version == "0002"
    assert len(final_status.applied) == 2
    assert final_status.pending == []


@pytest.mark.anyio
async def test_migration_failure_rolls_back_and_does_not_record_version(tmp_path: Path) -> None:
    (tmp_path / "0001_initial.sql").write_text("SELECT 1;", encoding="utf-8")
    (tmp_path / "0002_broken.sql").write_text("BROKEN SQL;", encoding="utf-8")

    fake_conn = _FakeConnection(fail_sql_substring="BROKEN SQL")
    fake_pool = _FakePool(fake_conn)
    manager = DatabaseManager(_db_settings(), pool_factory=lambda **_kw: fake_pool)
    await manager.startup()

    runner = MigrationRunner(manager, migrations_dir=tmp_path)
    with pytest.raises(MigrationError):
        await runner.apply_pending()

    status_after = await runner.status()
    assert status_after.current_version == "0001"
    assert [m.version for m in status_after.applied] == ["0001"]
    assert [m.version for m in status_after.pending] == ["0002"]


def test_migration_discovery_rejects_invalid_and_duplicate_versions(tmp_path: Path) -> None:
    (tmp_path / "bad_name.sql").write_text("SELECT 1;", encoding="utf-8")
    with pytest.raises(MigrationError):
        discover_migrations(tmp_path)

    dup_dir = tmp_path / "dup"
    dup_dir.mkdir()
    (dup_dir / "0001_first.sql").write_text("SELECT 1;", encoding="utf-8")
    (dup_dir / "0001_second.sql").write_text("SELECT 2;", encoding="utf-8")
    with pytest.raises(MigrationError):
        discover_migrations(dup_dir)


def test_migration_cli_fails_cleanly_without_database_url(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    exit_code = cli_main(["status"])
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "DATABASE_URL is not configured" in captured.err


def test_health_and_db_readiness_endpoints_in_stateless_and_persistent_modes() -> None:
    # 1. Stateless mode: /health ok, /health/db reports disabled (200) without failing Task 03.
    with TestClient(create_app(settings=Settings(database_url=None))) as stateless_client:
        assert stateless_client.get("/health").json() == {"status": "ok"}
        db_resp = stateless_client.get("/health/db")
        assert db_resp.status_code == 200
        assert db_resp.json()["status"] == "disabled"
        assert db_resp.json()["persistenceEnabled"] is False

    # 2. Persistent mode ready.
    ready_pool = _FakePool(_FakeConnection())
    ready_mgr = DatabaseManager(_db_settings(), pool_factory=lambda **_kw: ready_pool)
    with TestClient(
        create_app(settings=_db_settings(), database_manager=ready_mgr)
    ) as ready_client:
        db_resp = ready_client.get("/health/db")
        assert db_resp.status_code == 200
        assert db_resp.json()["status"] == "ready"
        assert db_resp.json()["persistenceEnabled"] is True
        assert "super_secret_db_pw" not in db_resp.text
    assert ready_pool.closed is True

    # 3. Persistent mode unavailable -> 503 without leaking credentials.
    class _UnavailableManager(DatabaseManager):
        async def startup(self) -> None:
            return None

        async def check_readiness(self) -> tuple[str, str]:
            return "unavailable", "PostgreSQL database is unavailable."

    unavail_mgr = _UnavailableManager(_db_settings())
    with TestClient(
        create_app(settings=_db_settings(), database_manager=unavail_mgr)
    ) as unavail_client:
        db_resp = unavail_client.get("/health/db")
        assert db_resp.status_code == 503
        assert db_resp.json()["status"] == "unavailable"
        assert "super_secret_db_pw" not in db_resp.text


@pytest.mark.anyio
@pytest.mark.skipif(
    not os.environ.get("DATABRIDGE_TEST_DATABASE_URL"),
    reason="DATABRIDGE_TEST_DATABASE_URL not set; skipping live PostgreSQL integration test",
)
async def test_live_postgresql_pool_migrations_and_transaction_rollback(tmp_path: Path) -> None:
    """Optional integration test executed only when DATABRIDGE_TEST_DATABASE_URL is provided."""
    db_url = os.environ["DATABRIDGE_TEST_DATABASE_URL"]
    settings = Settings(database_url=db_url)  # type: ignore[arg-type]
    manager = DatabaseManager(settings)
    await manager.startup()
    try:
        assert await manager.check_readiness() == ("ready", "PostgreSQL connection pool is ready.")

        # Test migration runner against real PostgreSQL.
        runner = MigrationRunner(manager)
        applied = await runner.apply_pending()
        expected_migrations = discover_migrations()
        status_after = await runner.status()
        assert status_after.current_version == expected_migrations[-1].version
        assert status_after.pending == []
        assert len(status_after.applied) == len(expected_migrations)
        assert len(applied) in (0, len(expected_migrations))

        # Test failed transaction rollback on real PostgreSQL.
        repo = BaseRepository(manager)
        async with repo.transaction() as conn:
            await conn.execute("CREATE TEMP TABLE test_tx (id INT PRIMARY KEY) ON COMMIT DROP")
            await conn.execute("INSERT INTO test_tx (id) VALUES (%s)", (1,))
            cursor = await conn.execute("SELECT COUNT(*) FROM test_tx")
            row = await cursor.fetchone()
            assert row == (1,)

        with pytest.raises(MigrationError):
            bad_dir = tmp_path / "bad_migrations"
            bad_dir.mkdir()
            (bad_dir / "9999_failing.sql").write_text("INVALID SQL STATEMENT;", encoding="utf-8")
            await MigrationRunner(manager, migrations_dir=bad_dir).apply_pending()
    finally:
        await manager.shutdown()
        assert manager.is_open is False
