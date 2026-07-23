"""Integration tests for staging, activation, and rollback.

Includes Property 15 (Atomic Publication) and Property 11 (Historical
Immutability). Requires a live database; every test cleans up after itself.

Requirement refs: 8.4-8.12, 14.9-14.11
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import pytest
from creator_map_pipeline.aggregate.artifacts import GeneratedArtifact
from creator_map_pipeline.database import DatabaseConfigError, resolve_database_url
from creator_map_pipeline.release.gates import ReleaseCandidate
from creator_map_pipeline.release.manager import (
    ActivationError,
    ReleaseManager,
    candidate_from_artifacts,
)

pytestmark = pytest.mark.integration

POLICY_ID = "itest-release-policy"
POLICY_VERSION = "1.0.0"
INSTANT = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def database_url() -> str:
    try:
        url = resolve_database_url()
    except DatabaseConfigError:
        pytest.skip("DATABASE_URL is not configured")
    try:
        with psycopg.connect(url, connect_timeout=10):
            pass
    except psycopg.Error as exc:
        pytest.skip(f"database unreachable: {type(exc).__name__}")
    return url


@pytest.fixture
def scope(request: pytest.FixtureRequest) -> str:
    """A release-id prefix unique to each test."""
    return f"itest-{request.node.name}"[:50]


@pytest.fixture
def conn(database_url: str, scope: str) -> Iterator[psycopg.Connection[tuple[object, ...]]]:
    with psycopg.connect(database_url) as connection:
        _ensure_policy(connection)
        yield connection
        connection.rollback()
        _purge(database_url, scope)


@pytest.fixture
def manager(conn: psycopg.Connection[tuple[object, ...]], tmp_path: Path) -> ReleaseManager:
    return ReleaseManager(conn, storage_root=tmp_path, actor="itest-curator")


def _ensure_policy(conn: psycopg.Connection[tuple[object, ...]]) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "insert into governance.disclosure_policy "
            "(policy_id, version, document, approved_at, approved_by) "
            "values (%s,%s,'{}'::jsonb, now(), 'itest') "
            "on conflict (policy_id, version) do nothing",
            (POLICY_ID, POLICY_VERSION),
        )
    conn.commit()


def _candidate(release_id: str, *, value: int = 1) -> ReleaseCandidate:
    overview = GeneratedArtifact(
        path=f"releases/{release_id}/overview.json",
        payload={"schemaVersion": "1.0.0", "releaseId": release_id, "value": value},
    ).finalize()

    manifest = GeneratedArtifact(
        path=f"releases/{release_id}/manifest.json",
        payload={
            "schemaVersion": "1.0.0",
            "releaseId": release_id,
            "generatedAt": INSTANT.isoformat().replace("+00:00", "Z"),
            "enrichmentCutoff": INSTANT.isoformat().replace("+00:00", "Z"),
            "methodologyVersion": "1.0.0",
            "disclosurePolicyVersion": POLICY_VERSION,
            "artifactDigests": {overview.path: overview.digest},
        },
    ).finalize()

    return candidate_from_artifacts(
        release_id,
        [overview, manifest],
        signoff_actor="itest-curator",
        vulnerability_scan={"completed": True, "blockingFindings": 0},
    )


def _publish(manager: ReleaseManager, release_id: str, *, value: int = 1) -> None:
    """Stage, record, and verify a release without activating it."""
    candidate = _candidate(release_id, value=value)
    manager.stage(candidate)
    manager.record_release(candidate, policy_id=POLICY_ID, policy_version=POLICY_VERSION)
    manager.mark_verified(release_id)


# --- Requirement 8.4 / 8.5: staging and verification ---------------------


def test_staging_writes_and_verifies_every_artifact(
    manager: ReleaseManager, scope: str, tmp_path: Path
) -> None:
    staged = manager.stage(_candidate(f"{scope}-a"))

    assert staged.artifact_count == 2
    assert (tmp_path / f"releases/{scope}-a/overview.json").is_file()
    assert (tmp_path / f"releases/{scope}-a/manifest.json").is_file()


def test_staging_verifies_from_disk_not_memory(
    manager: ReleaseManager, scope: str, tmp_path: Path
) -> None:
    """A truncated write must be caught before the release is usable."""
    candidate = _candidate(f"{scope}-a")
    manager.stage(candidate)

    # Corrupt a staged file, then re-verify.
    target = tmp_path / f"releases/{scope}-a/overview.json"
    target.write_bytes(b'{"tampered":true}')

    problems = manager.verify_staged(f"{scope}-a", candidate.manifest)
    assert any("mismatch" in p for p in problems)


def test_verification_reports_a_missing_artifact(
    manager: ReleaseManager, scope: str, tmp_path: Path
) -> None:
    candidate = _candidate(f"{scope}-a")
    manager.stage(candidate)
    (tmp_path / f"releases/{scope}-a/overview.json").unlink()

    problems = manager.verify_staged(f"{scope}-a", candidate.manifest)
    assert any("missing" in p for p in problems)


def test_staging_a_candidate_without_a_manifest_fails(manager: ReleaseManager, scope: str) -> None:
    bare = ReleaseCandidate(release_id=f"{scope}-a", artifacts=(), manifest={})
    with pytest.raises(ActivationError, match="no manifest"):
        manager.stage(bare)


# --- Requirement 8.4: only a verified release activates ------------------


def test_unverified_release_cannot_activate(manager: ReleaseManager, scope: str) -> None:
    candidate = _candidate(f"{scope}-a")
    manager.stage(candidate)
    manager.record_release(candidate, policy_id=POLICY_ID, policy_version=POLICY_VERSION)
    # Deliberately not marked verified.
    with pytest.raises(ActivationError, match="not Verified"):
        manager.activate(f"{scope}-a")


def test_unrecorded_release_cannot_activate(manager: ReleaseManager, scope: str) -> None:
    with pytest.raises(ActivationError, match="not recorded"):
        manager.activate(f"{scope}-absent")


# --- Requirement 8.7 / Invariant 15: atomic activation -------------------


def test_activation_moves_the_pointer(manager: ReleaseManager, scope: str) -> None:
    # What is being asserted is that activation *moves* the pointer to this
    # release, not that nothing was active beforehand. The pointer is a
    # singleton shared by the whole database, so a concurrent curator
    # command — or a developer activating a real release while the suite
    # runs — legitimately leaves one there. Asserting `previous is None`
    # made this test fail on an unrelated action rather than on a defect;
    # `test_activation_supersedes_the_previous_release` covers the
    # transition from a known predecessor.
    before = manager.active_release_id()

    _publish(manager, f"{scope}-a")
    previous = manager.activate(f"{scope}-a")

    assert previous == before
    assert manager.active_release_id() == f"{scope}-a"


def test_activation_supersedes_the_previous_release(manager: ReleaseManager, scope: str) -> None:
    _publish(manager, f"{scope}-a")
    manager.activate(f"{scope}-a")

    _publish(manager, f"{scope}-b", value=2)
    previous = manager.activate(f"{scope}-b")

    assert previous == f"{scope}-a"
    assert manager.active_release_id() == f"{scope}-b"


def test_exactly_one_release_is_active_at_every_instant(
    manager: ReleaseManager, conn: psycopg.Connection[tuple[object, ...]], scope: str
) -> None:
    """Invariant 15: never zero, never two, never a mixture."""
    _publish(manager, f"{scope}-a")
    _publish(manager, f"{scope}-b", value=2)

    manager.activate(f"{scope}-a")
    with conn.cursor() as cur:
        cur.execute("select count(*) from governance.active_release_pointer")
        row = cur.fetchone()
    assert row is not None and row[0] == 1

    manager.activate(f"{scope}-b")
    with conn.cursor() as cur:
        cur.execute("select count(*), max(release_id) from governance.active_release_pointer")
        row = cur.fetchone()
    assert row is not None
    assert row[0] == 1
    assert row[1] == f"{scope}-b"


def test_activation_is_audited(
    manager: ReleaseManager, conn: psycopg.Connection[tuple[object, ...]], scope: str
) -> None:
    _publish(manager, f"{scope}-a")
    manager.activate(f"{scope}-a")

    with conn.cursor() as cur:
        cur.execute(
            "select action, outcome from governance.audit_log "
            "where actor='itest-curator' and action='activate_release' "
            "order by audit_id desc limit 1"
        )
        row = cur.fetchone()
    assert row is not None
    assert row[1] == "success"


# --- Requirement 8.3 / 8.6: failure preserves the active release ---------


def test_rejecting_a_candidate_leaves_the_pointer_untouched(
    manager: ReleaseManager, scope: str
) -> None:
    _publish(manager, f"{scope}-a")
    manager.activate(f"{scope}-a")

    _publish(manager, f"{scope}-b", value=2)
    manager.reject(f"{scope}-b", ["arithmetic failed"])

    assert manager.active_release_id() == f"{scope}-a"


def test_a_rejected_release_cannot_activate(manager: ReleaseManager, scope: str) -> None:
    _publish(manager, f"{scope}-a")
    manager.reject(f"{scope}-a", ["disclosure failed"])

    with pytest.raises(ActivationError, match="not Verified"):
        manager.activate(f"{scope}-a")


# --- Requirement 8.10-8.12: verified rollback ----------------------------


def test_rollback_restores_a_prior_release(manager: ReleaseManager, scope: str) -> None:
    _publish(manager, f"{scope}-a")
    manager.activate(f"{scope}-a")
    _publish(manager, f"{scope}-b", value=2)
    manager.activate(f"{scope}-b")

    manager.rollback(f"{scope}-a")

    assert manager.active_release_id() == f"{scope}-a"


def test_rollback_verifies_before_moving_the_pointer(
    manager: ReleaseManager, scope: str, tmp_path: Path
) -> None:
    """Requirement 8.11: failed verification preserves the current release."""
    _publish(manager, f"{scope}-a")
    manager.activate(f"{scope}-a")
    _publish(manager, f"{scope}-b", value=2)
    manager.activate(f"{scope}-b")

    # The rollback target's artifacts are corrupted on disk.
    (tmp_path / f"releases/{scope}-a/overview.json").write_bytes(b'{"bad":1}')

    with pytest.raises(ActivationError, match="failed verification"):
        manager.rollback(f"{scope}-a")

    # The current release still serves.
    assert manager.active_release_id() == f"{scope}-b"


def test_rollback_to_an_unknown_release_fails(manager: ReleaseManager, scope: str) -> None:
    _publish(manager, f"{scope}-a")
    manager.activate(f"{scope}-a")

    with pytest.raises(ActivationError, match="not recorded"):
        manager.rollback(f"{scope}-absent")

    assert manager.active_release_id() == f"{scope}-a"


def test_failed_rollback_is_audited(
    manager: ReleaseManager,
    conn: psycopg.Connection[tuple[object, ...]],
    scope: str,
    tmp_path: Path,
) -> None:
    _publish(manager, f"{scope}-a")
    manager.activate(f"{scope}-a")
    _publish(manager, f"{scope}-b", value=2)
    manager.activate(f"{scope}-b")
    (tmp_path / f"releases/{scope}-a/overview.json").write_bytes(b"{}")

    with pytest.raises(ActivationError):
        manager.rollback(f"{scope}-a")

    with conn.cursor() as cur:
        cur.execute(
            "select outcome from governance.audit_log "
            "where action='rollback_release' order by audit_id desc limit 1"
        )
        row = cur.fetchone()
    assert row is not None and row[0] == "denied"


# --- Requirement 8.9 / Property 11: historical immutability --------------


def test_published_artifacts_are_immutable(
    manager: ReleaseManager, conn: psycopg.Connection[tuple[object, ...]], scope: str
) -> None:
    """A later release must not alter a published one's recorded digests."""
    _publish(manager, f"{scope}-a")
    manager.activate(f"{scope}-a")

    with conn.cursor() as cur:
        cur.execute(
            "select artifact_path, artifact_digest from governance.release_artifact "
            "where release_id=%s order by artifact_path",
            (f"{scope}-a",),
        )
        before = cur.fetchall()

    _publish(manager, f"{scope}-b", value=999)
    manager.activate(f"{scope}-b")

    with conn.cursor() as cur:
        cur.execute(
            "select artifact_path, artifact_digest from governance.release_artifact "
            "where release_id=%s order by artifact_path",
            (f"{scope}-a",),
        )
        after = cur.fetchall()

    assert before == after


def test_release_artifact_digests_reject_update(
    manager: ReleaseManager, conn: psycopg.Connection[tuple[object, ...]], scope: str
) -> None:
    """The database refuses, not just the application (Requirement 8.9)."""
    _publish(manager, f"{scope}-a")

    with conn.cursor() as cur, pytest.raises(psycopg.errors.RestrictViolation):
        cur.execute(
            "update governance.release_artifact set artifact_digest='sha256:x' where release_id=%s",
            (f"{scope}-a",),
        )


def test_manifest_bytes_are_stable_across_rebuilds(scope: str) -> None:
    """Requirement 5.13: rebuilding yields byte-identical artifacts."""
    first = _candidate(f"{scope}-a")
    second = _candidate(f"{scope}-a")

    assert [a.digest for a in first.artifacts] == [a.digest for a in second.artifacts]
    assert [a.content for a in first.artifacts] == [a.content for a in second.artifacts]


# --- Requirement 14.10: the pointer is separately refreshable ------------


def test_pointer_file_is_written_separately(
    manager: ReleaseManager, scope: str, tmp_path: Path
) -> None:
    _publish(manager, f"{scope}-a")
    candidate = _candidate(f"{scope}-a")
    manifest = candidate.artifact("manifest.json")
    assert manifest is not None

    path = manager.write_pointer(f"{scope}-a", manifest.digest)

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["releaseId"] == f"{scope}-a"
    assert payload["manifestDigest"] == manifest.digest
    # Minimal by design, so it can carry a short cache lifetime.
    assert set(payload) == {
        "schemaVersion",
        "releaseId",
        "manifestPath",
        "manifestDigest",
    }


# --- Validation reports are durable --------------------------------------


def test_validation_results_are_recorded(
    manager: ReleaseManager, conn: psycopg.Connection[tuple[object, ...]], scope: str
) -> None:
    """Requirement 8.3: the internal report must survive the process."""
    candidate = _candidate(f"{scope}-a")
    manager.record_release(candidate, policy_id=POLICY_ID, policy_version=POLICY_VERSION)
    report = manager.validate(candidate)

    with conn.cursor() as cur:
        cur.execute(
            "select count(*) from governance.release_gate_result where release_id=%s",
            (f"{scope}-a",),
        )
        row = cur.fetchone()
    assert row is not None
    assert row[0] == len(report.results)


def _purge(database_url: str, scope: str) -> None:
    """Remove committed rows for this test's releases."""
    with psycopg.connect(database_url, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "delete from governance.active_release_pointer where release_id like %s",
            (f"{scope}%",),
        )
        cur.execute(
            "alter table governance.release_artifact disable trigger release_artifact_no_update"
        )
        cur.execute(
            "delete from governance.release_artifact where release_id like %s",
            (f"{scope}%",),
        )
        cur.execute(
            "alter table governance.release_artifact enable trigger release_artifact_no_update"
        )
        cur.execute(
            "delete from governance.release_gate_result where release_id like %s",
            (f"{scope}%",),
        )
        cur.execute("delete from governance.release where release_id like %s", (f"{scope}%",))
        cur.execute("alter table governance.audit_log disable trigger audit_log_append_only")
        cur.execute("delete from governance.audit_log where actor='itest-curator'")
        cur.execute("alter table governance.audit_log enable trigger audit_log_append_only")
