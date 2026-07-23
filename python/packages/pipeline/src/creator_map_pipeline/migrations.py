"""Forward-only SQL migration runner.

Migrations are plain `.sql` files applied in filename order inside a single
transaction each, with the applied set tracked in a ledger table. There is no
down-migration path: the provenance and observation tables are append-only by
design (Requirements 3.9, 8.9), so a reversal that dropped or rewrote them
would contradict the immutability the schema exists to guarantee. Schema
corrections move forward as new migrations.

Requirement refs: 1.3, 1.4, 3.9, 4.1, 8.9, 15.20
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

MIGRATIONS_DIR = Path(__file__).resolve().parents[4] / "migrations"

_FILENAME_PATTERN = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")

_LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS public.schema_migration (
    version      text        NOT NULL PRIMARY KEY,
    name         text        NOT NULL,
    checksum     text        NOT NULL,
    applied_at   timestamptz NOT NULL DEFAULT now()
)
"""


class MigrationError(RuntimeError):
    """Raised when migrations are malformed or have drifted from the ledger."""


@dataclass(frozen=True, slots=True)
class Migration:
    """One forward migration file."""

    version: str
    name: str
    sql: str

    @property
    def checksum(self) -> str:
        """Digest of the migration body.

        Recorded at apply time so that editing an already-applied migration
        is detected rather than silently diverging the deployed schema from
        the repository.
        """
        return hashlib.sha256(self.sql.encode("utf-8")).hexdigest()


class MigrationCursor(Protocol):
    """The minimal cursor surface the runner needs.

    Declared structurally so the runner can be exercised against a fake in
    tests without a live database, and so the pipeline package does not bind
    itself to one driver.
    """

    def execute(self, query: str, params: tuple[object, ...] = ..., /) -> object: ...

    def fetchall(self) -> list[tuple[object, ...]]: ...


class MigrationConnection(Protocol):
    """The minimal connection surface the runner needs."""

    def cursor(self) -> MigrationCursor: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


def load_migrations(directory: Path | None = None) -> tuple[Migration, ...]:
    """Load and order every migration file in a directory.

    Ordering is by the numeric version prefix rather than raw filename sort,
    so the sequence stays correct past nine migrations.
    """
    source = directory if directory is not None else MIGRATIONS_DIR
    if not source.is_dir():
        msg = f"migrations directory not found: {source}"
        raise MigrationError(msg)

    migrations: list[Migration] = []
    seen_versions: set[str] = set()

    for path in sorted(source.glob("*.sql")):
        match = _FILENAME_PATTERN.match(path.name)
        if match is None:
            msg = f"migration filename must match NNNN_lower_snake_case.sql; got {path.name!r}"
            raise MigrationError(msg)

        version, name = match.group(1), match.group(2)
        if version in seen_versions:
            msg = f"duplicate migration version {version!r}"
            raise MigrationError(msg)
        seen_versions.add(version)

        migrations.append(
            Migration(version=version, name=name, sql=path.read_text(encoding="utf-8"))
        )

    if not migrations:
        msg = f"no migration files found in {source}"
        raise MigrationError(msg)

    ordered = tuple(sorted(migrations, key=lambda m: int(m.version)))
    expected = [f"{index + 1:04d}" for index in range(len(ordered))]
    actual = [migration.version for migration in ordered]
    if actual != expected:
        msg = f"migration versions must be contiguous from 0001; got {actual}"
        raise MigrationError(msg)

    return ordered


def applied_versions(connection: MigrationConnection) -> dict[str, str]:
    """Return version -> checksum for every applied migration."""
    cursor = connection.cursor()
    cursor.execute(_LEDGER_DDL)
    cursor.execute("SELECT version, checksum FROM public.schema_migration")
    rows = cursor.fetchall()
    return {str(version): str(checksum) for version, checksum in rows}


def verify_no_drift(migrations: tuple[Migration, ...], applied: dict[str, str]) -> None:
    """Fail closed when an applied migration's body has since changed.

    A modified migration means the live schema no longer corresponds to the
    repository, which would make every downstream immutability guarantee
    unverifiable. This is reported rather than repaired automatically.
    """
    for migration in migrations:
        recorded = applied.get(migration.version)
        if recorded is not None and recorded != migration.checksum:
            msg = (
                f"migration {migration.version}_{migration.name} was modified "
                f"after being applied; create a new forward migration instead"
            )
            raise MigrationError(msg)

    unknown = set(applied) - {migration.version for migration in migrations}
    if unknown:
        msg = f"database reports migrations absent from this repository: {sorted(unknown)}"
        raise MigrationError(msg)


def pending_migrations(
    migrations: tuple[Migration, ...], applied: dict[str, str]
) -> tuple[Migration, ...]:
    """Return the migrations not yet applied, in order."""
    return tuple(m for m in migrations if m.version not in applied)


def apply_migration(connection: MigrationConnection, migration: Migration) -> None:
    """Apply one migration and record it, atomically.

    The migration body and its ledger row commit together. A failure leaves
    neither applied, so a partially-created schema cannot be mistaken for a
    complete one on the next run.
    """
    cursor = connection.cursor()
    try:
        cursor.execute(migration.sql)
        cursor.execute(
            "INSERT INTO public.schema_migration (version, name, checksum) VALUES (%s, %s, %s)",
            (migration.version, migration.name, migration.checksum),
        )
    except Exception:
        connection.rollback()
        raise
    connection.commit()


def migrate(
    connection: MigrationConnection, directory: Path | None = None
) -> tuple[Migration, ...]:
    """Apply every pending migration and return what was applied."""
    migrations = load_migrations(directory)
    applied = applied_versions(connection)
    verify_no_drift(migrations, applied)

    pending = pending_migrations(migrations, applied)
    for migration in pending:
        apply_migration(connection, migration)
    return pending
