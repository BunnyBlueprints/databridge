"""Versioned, forward-only SQL migration runner backed by schema_migrations."""

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.db.errors import MigrationError
from app.db.pool import DatabaseManager

logger = logging.getLogger("databridge.api")

_MIGRATION_FILENAME_RE = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")

DEFAULT_MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "migrations"


@dataclass(frozen=True)
class MigrationFile:
    """Represents a discovered SQL migration file."""

    version: str
    name: str
    path: Path

    @property
    def label(self) -> str:
        return f"{self.version}_{self.name}"


@dataclass(frozen=True)
class AppliedMigration:
    """Represents a migration recorded in schema_migrations."""

    version: str
    name: str
    applied_at: str


@dataclass(frozen=True)
class MigrationStatus:
    """Snapshot of applied and pending schema migrations."""

    current_version: str | None
    applied: list[AppliedMigration]
    pending: list[MigrationFile]


def discover_migrations(migrations_dir: Path | None = None) -> list[MigrationFile]:
    """Discover and validate ordered `.sql` migration files."""
    target_dir = migrations_dir or DEFAULT_MIGRATIONS_DIR
    if not target_dir.exists() or not target_dir.is_dir():
        raise MigrationError("Migrations directory does not exist.")

    discovered: list[MigrationFile] = []
    seen_versions: set[str] = set()

    for entry in sorted(target_dir.iterdir(), key=lambda p: p.name):
        if not entry.is_file() or entry.suffix != ".sql":
            continue
        match = _MIGRATION_FILENAME_RE.match(entry.name)
        if not match:
            raise MigrationError(
                f"Invalid migration filename '{entry.name}'. Expected format NNNN_description.sql."
            )
        version, name = match.group(1), match.group(2)
        if version in seen_versions:
            raise MigrationError(f"Duplicate migration version '{version}' detected.")
        seen_versions.add(version)
        discovered.append(MigrationFile(version=version, name=name, path=entry))

    return discovered


class MigrationRunner:
    """Applies and inspects forward-only SQL migrations explicitly."""

    def __init__(
        self,
        db_manager: DatabaseManager,
        *,
        migrations_dir: Path | None = None,
    ) -> None:
        self._db = db_manager
        self._migrations_dir = migrations_dir or DEFAULT_MIGRATIONS_DIR

    async def _ensure_tracking_table(self, conn: Any) -> None:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version VARCHAR(64) PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )

    async def _fetch_applied(self, conn: Any) -> list[AppliedMigration]:
        cursor = await conn.execute(
            """
            SELECT version, name, applied_at::text
            FROM schema_migrations
            ORDER BY version ASC
            """
        )
        rows = await cursor.fetchall()
        return [
            AppliedMigration(
                version=str(row[0]),
                name=str(row[1]),
                applied_at=str(row[2]),
            )
            for row in rows
        ]

    async def status(self) -> MigrationStatus:
        """Inspect applied and pending migrations and return current schema version."""
        all_migrations = discover_migrations(self._migrations_dir)
        async with self._db.connection() as conn:
            async with conn.transaction():
                await self._ensure_tracking_table(conn)
                applied = await self._fetch_applied(conn)

        applied_versions = {item.version for item in applied}
        pending = [m for m in all_migrations if m.version not in applied_versions]
        current_version = applied[-1].version if applied else None
        return MigrationStatus(
            current_version=current_version,
            applied=applied,
            pending=pending,
        )

    async def apply_pending(self) -> list[MigrationFile]:
        """Apply all pending migrations in deterministic version order."""
        all_migrations = discover_migrations(self._migrations_dir)
        applied_now: list[MigrationFile] = []

        async with self._db.connection() as conn:
            async with conn.transaction():
                await self._ensure_tracking_table(conn)

            async with conn.transaction():
                existing = await self._fetch_applied(conn)

            applied_versions = {item.version for item in existing}

            for migration in all_migrations:
                if migration.version in applied_versions:
                    continue

                sql_text = migration.path.read_text(encoding="utf-8").strip()
                if not sql_text:
                    raise MigrationError(f"Migration '{migration.label}' is empty.")

                try:
                    async with conn.transaction():
                        await conn.execute(sql_text)
                        await conn.execute(
                            """
                            INSERT INTO schema_migrations (version, name)
                            VALUES (%s, %s)
                            """,
                            (migration.version, migration.name),
                        )
                    applied_now.append(migration)
                    applied_versions.add(migration.version)
                    logger.info("Applied database migration: %s", migration.label)
                except Exception as exc:
                    logger.error(
                        "Database migration failed (%s): %s",
                        migration.label,
                        type(exc).__name__,
                    )
                    raise MigrationError(
                        f"Migration '{migration.label}' failed and was rolled back."
                    ) from exc

        return applied_now
