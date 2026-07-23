"""Curator command for applying database migrations.

Usage:
    python -m creator_map_pipeline.cli_migrate --status
    python -m creator_map_pipeline.cli_migrate --apply

Requirement refs: 1.3, 15.2, 15.3
"""

from __future__ import annotations

import argparse
import sys

import psycopg

from creator_map_pipeline.database import (
    DatabaseConfigError,
    redacted_target,
    resolve_database_url,
)
from creator_map_pipeline.migrations import (
    MigrationError,
    applied_versions,
    load_migrations,
    migrate,
    pending_migrations,
    verify_no_drift,
)


def _status(url: str) -> int:
    migrations = load_migrations()
    with psycopg.connect(url) as connection:
        applied = applied_versions(connection)
        connection.commit()

    try:
        verify_no_drift(migrations, applied)
        drift = "none"
    except MigrationError as exc:
        drift = str(exc)

    pending = pending_migrations(migrations, applied)
    print(f"target:  {redacted_target(url)}")
    print(f"known:   {len(migrations)}")
    print(f"applied: {len(applied)}")
    print(f"pending: {len(pending)}")
    print(f"drift:   {drift}")
    for migration in migrations:
        mark = "applied" if migration.version in applied else "PENDING"
        print(f"  [{mark}] {migration.version}_{migration.name}")
    return 0 if drift == "none" else 1


def _apply(url: str) -> int:
    print(f"target: {redacted_target(url)}")
    # autocommit lets each migration manage its own BEGIN/COMMIT, matching
    # the transaction blocks written inside the .sql files.
    with psycopg.connect(url, autocommit=True) as connection:
        applied = migrate(connection)

    if not applied:
        print("no pending migrations")
        return 0
    for migration in applied:
        print(f"applied {migration.version}_{migration.name}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cli_migrate", description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--status", action="store_true", help="Report migration state")
    group.add_argument("--apply", action="store_true", help="Apply pending migrations")
    args = parser.parse_args(argv)

    try:
        url = resolve_database_url()
    except DatabaseConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        return _status(url) if args.status else _apply(url)
    except MigrationError as exc:
        print(f"migration error: {exc}", file=sys.stderr)
        return 1
    except psycopg.Error as exc:
        # psycopg errors can carry connection context; report the class and
        # message only, never the connection string.
        print(f"database error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
