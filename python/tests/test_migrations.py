"""Tests for the forward-only migration runner and the shipped SQL.

These run without a database: the runner is exercised against a recording
fake, and the shipped migration files are checked structurally. Behavioural
verification against a live PostgreSQL instance lives in the integration
suite.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from creator_map_pipeline.migrations import (
    MIGRATIONS_DIR,
    Migration,
    MigrationError,
    apply_migration,
    load_migrations,
    migrate,
    pending_migrations,
    verify_no_drift,
)


class FakeCursor:
    """Records executed statements and answers the ledger query."""

    def __init__(self, applied: list[tuple[str, str]]) -> None:
        self.applied = applied
        self.statements: list[str] = []
        self._last_was_ledger_query = False
        self.fail_on: str | None = None

    def execute(self, query: str, params: tuple[object, ...] = (), /) -> object:
        self.statements.append(query)
        if self.fail_on is not None and self.fail_on in query:
            msg = f"simulated failure executing: {self.fail_on}"
            raise RuntimeError(msg)
        self._last_was_ledger_query = "SELECT version, checksum" in query
        if query.strip().startswith("INSERT INTO public.schema_migration"):
            version, _name, checksum = (str(param) for param in params)
            self.applied.append((version, checksum))
        return None

    def fetchall(self) -> list[tuple[object, ...]]:
        if self._last_was_ledger_query:
            return [(version, checksum) for version, checksum in self.applied]
        return []


class FakeConnection:
    """Minimal connection double tracking commit and rollback calls."""

    def __init__(self, applied: list[tuple[str, str]] | None = None) -> None:
        self.applied: list[tuple[str, str]] = list(applied or [])
        self._cursor = FakeCursor(self.applied)
        self.commits = 0
        self.rollbacks = 0

    def cursor(self) -> FakeCursor:
        return self._cursor

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def _write(directory: Path, name: str, body: str = "SELECT 1;") -> None:
    (directory / name).write_text(body, encoding="utf-8")


# --- Loading and ordering -------------------------------------------------


def test_loads_shipped_migrations_in_order() -> None:
    migrations = load_migrations()
    versions = [migration.version for migration in migrations]
    assert versions == sorted(versions)
    assert versions[0] == "0001"
    assert len(migrations) >= 3


def test_orders_numerically_past_nine(tmp_path: Path) -> None:
    """Filename sort and numeric sort diverge at ten migrations."""
    for index in range(1, 12):
        _write(tmp_path, f"{index:04d}_step.sql")
    migrations = load_migrations(tmp_path)
    assert [m.version for m in migrations] == [f"{i:04d}" for i in range(1, 12)]


def test_rejects_malformed_filename(tmp_path: Path) -> None:
    _write(tmp_path, "0001_first.sql")
    _write(tmp_path, "second-migration.sql")
    with pytest.raises(MigrationError, match="filename must match"):
        load_migrations(tmp_path)


def test_rejects_version_gap(tmp_path: Path) -> None:
    """A gap means a migration is missing from the repository."""
    _write(tmp_path, "0001_first.sql")
    _write(tmp_path, "0003_third.sql")
    with pytest.raises(MigrationError, match="contiguous"):
        load_migrations(tmp_path)


def test_rejects_empty_directory(tmp_path: Path) -> None:
    with pytest.raises(MigrationError, match="no migration files"):
        load_migrations(tmp_path)


def test_rejects_missing_directory(tmp_path: Path) -> None:
    with pytest.raises(MigrationError, match="not found"):
        load_migrations(tmp_path / "absent")


# --- Drift detection ------------------------------------------------------


def test_detects_modified_applied_migration() -> None:
    """Editing an applied migration desynchronizes schema from repository."""
    migration = Migration(version="0001", name="first", sql="SELECT 1;")
    applied = {"0001": "a-different-checksum"}
    with pytest.raises(MigrationError, match="was modified after being applied"):
        verify_no_drift((migration,), applied)


def test_accepts_unmodified_applied_migration() -> None:
    migration = Migration(version="0001", name="first", sql="SELECT 1;")
    verify_no_drift((migration,), {"0001": migration.checksum})


def test_detects_migration_present_only_in_database() -> None:
    """A database ahead of the repository is reported, not ignored."""
    migration = Migration(version="0001", name="first", sql="SELECT 1;")
    applied = {"0001": migration.checksum, "0002": "unknown"}
    with pytest.raises(MigrationError, match="absent from this repository"):
        verify_no_drift((migration,), applied)


# --- Applying -------------------------------------------------------------


def test_pending_excludes_already_applied() -> None:
    first = Migration(version="0001", name="first", sql="SELECT 1;")
    second = Migration(version="0002", name="second", sql="SELECT 2;")
    pending = pending_migrations((first, second), {"0001": first.checksum})
    assert [m.version for m in pending] == ["0002"]


def test_apply_commits_body_and_ledger_together() -> None:
    connection = FakeConnection()
    migration = Migration(version="0001", name="first", sql="CREATE TABLE t ();")

    apply_migration(connection, migration)

    assert connection.commits == 1
    assert connection.rollbacks == 0
    assert connection.applied == [("0001", migration.checksum)]
    statements = connection.cursor().statements
    assert "CREATE TABLE t ();" in statements


def test_apply_rolls_back_on_failure() -> None:
    """A failed body leaves no ledger row, so the migration stays pending."""
    connection = FakeConnection()
    connection.cursor().fail_on = "CREATE TABLE t"
    migration = Migration(version="0001", name="first", sql="CREATE TABLE t ();")

    with pytest.raises(RuntimeError, match="simulated failure"):
        apply_migration(connection, migration)

    assert connection.rollbacks == 1
    assert connection.commits == 0
    assert connection.applied == []


def test_migrate_is_idempotent() -> None:
    """Re-running applies nothing further and commits nothing further."""
    connection = FakeConnection()
    first_run = migrate(connection)
    assert len(first_run) >= 3
    commits_after_first = connection.commits

    second_run = migrate(connection)
    assert second_run == ()
    assert connection.commits == commits_after_first


# --- Shipped SQL structure ------------------------------------------------


def _shipped_sql() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(MIGRATIONS_DIR.glob("*.sql"))
    )


def test_every_migration_is_transactional() -> None:
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        body = path.read_text(encoding="utf-8")
        assert "BEGIN;" in body, f"{path.name} must open a transaction"
        assert "COMMIT;" in body, f"{path.name} must commit"


def test_append_only_tables_reject_mutation() -> None:
    """Requirements 3.9 and 8.9 depend on this enforcement existing.

    Migration 0004 replaced the original RULEs with BEFORE triggers, because
    PostgreSQL forbids ON CONFLICT on any table carrying an INSERT/UPDATE
    rule and resumable ingestion needs idempotent upserts.
    """
    sql = _shipped_sql()
    for table in (
        "normalized_occurrence",
        "video_observation",
        "channel_observation",
        "audit_log",
    ):
        assert f"{table}_append_only" in sql, f"{table} must have an append-only trigger"

    assert "reject_mutation" in sql
    assert "BEFORE UPDATE OR DELETE" in sql


def test_legacy_rules_are_dropped() -> None:
    """The rules must be removed, not merely superseded."""
    sql = _shipped_sql()
    for rule in (
        "normalized_occurrence_no_update",
        "video_observation_no_delete",
        "audit_log_no_update",
    ):
        assert f"DROP RULE IF EXISTS {rule}" in sql, f"{rule} must be dropped"


def test_work_item_identity_is_unique() -> None:
    """Requirement 4.1: one work item per entity and policy version."""
    sql = _shipped_sql()
    assert "work_item_identity_unique" in sql
    assert re.search(r"UNIQUE\s*\(\s*entity_kind,\s*entity_id,\s*policy_version\s*\)", sql)


def test_checkpoint_replay_is_constrained() -> None:
    """Requirement 4.7: a replayed checkpoint conflicts rather than duplicates."""
    sql = _shipped_sql()
    assert re.search(r"UNIQUE\s*\(\s*job_id,\s*batch_key\s*\)", sql)


def test_active_release_pointer_is_a_singleton() -> None:
    """Invariant 15: two simultaneously active releases are unrepresentable."""
    sql = _shipped_sql()
    assert "active_release_pointer_singleton" in sql
    assert "CHECK (pointer_id = true)" in sql


def test_extraction_report_enforces_conservation() -> None:
    """Requirement 2.14 / Invariant 2 is enforced in the schema."""
    sql = _shipped_sql()
    assert "extraction_report_conservation" in sql
    assert "records_accepted + records_rejected = records_examined" in sql


def test_occurrence_clip_bounds_are_constrained() -> None:
    """Requirement 2.9: 0 <= start < end, both present or both absent."""
    sql = _shipped_sql()
    assert "normalized_occurrence_clip_bounds" in sql
    assert "clip_start >= 0 AND clip_start < clip_end" in sql


def test_declared_country_has_no_inference_default() -> None:
    """Invariant 6: absent country stays absent; nothing backfills it."""
    sql = _shipped_sql()
    country_lines = [
        line for line in sql.splitlines() if "declared_country" in line and "DEFAULT" in line
    ]
    assert country_lines == [], f"declared_country must have no default: {country_lines}"


def test_occurrences_carry_no_uniqueness_over_dataset_video() -> None:
    """Requirement 2.11 forbids deduplicating retained source evidence."""
    sql = _shipped_sql()
    assert not re.search(
        r"UNIQUE\s*\(\s*dataset_id,\s*dataset_version,\s*video_id\s*\)[^;]*"
        r"normalized_occurrence",
        sql,
    )
