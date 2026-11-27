"""Explicit CLI entrypoint for inspecting and applying database migrations."""

import argparse
import asyncio
import sys

from app.core.config import Settings
from app.core.logging import configure_logging
from app.db.errors import DatabaseError
from app.db.migrations import MigrationRunner
from app.db.pool import DatabaseManager


async def _run_cli(command: str) -> int:
    settings = Settings()
    configure_logging(settings.log_level)

    if not settings.database_enabled:
        print(
            "Error: DATABASE_URL is not configured. Set DATABASE_URL to run migrations.",
            file=sys.stderr,
        )
        return 1

    db_manager = DatabaseManager(settings)
    try:
        await db_manager.startup()
        runner = MigrationRunner(db_manager)

        if command in ("status", "version"):
            state = await runner.status()
            print(f"Current schema version: {state.current_version or 'none'}")
            print(f"Applied migrations ({len(state.applied)}):")
            for applied in state.applied:
                print(f"  - {applied.version}_{applied.name} ({applied.applied_at})")
            print(f"Pending migrations ({len(state.pending)}):")
            for pending in state.pending:
                print(f"  - {pending.label}")
            return 0

        if command in ("migrate", "upgrade"):
            applied_now = await runner.apply_pending()
            state = await runner.status()
            if applied_now:
                print(f"Applied {len(applied_now)} migration(s):")
                for item in applied_now:
                    print(f"  + {item.label}")
            else:
                print("No pending migrations.")
            print(f"Current schema version: {state.current_version or 'none'}")
            return 0

        print(f"Unknown command: {command}", file=sys.stderr)
        return 2
    except DatabaseError as exc:
        print(f"Database migration error: {exc}", file=sys.stderr)
        return 1
    finally:
        await db_manager.shutdown()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.db.cli",
        description="DataBridge database migration CLI",
    )
    parser.add_argument(
        "command",
        choices=("status", "version", "migrate", "upgrade"),
        help="Inspect schema version/status or apply pending SQL migrations.",
    )
    args = parser.parse_args(argv)
    return asyncio.run(_run_cli(args.command))


if __name__ == "__main__":
    raise SystemExit(main())
