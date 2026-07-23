"""Integration tests for leases, checkpoints, retries, and quota.

These exercise the concurrency semantics that unit tests cannot reach:
two workers racing for the same batch, an expired lease returning to the
queue, and a replayed checkpoint being refused. All require a live database.

Every test rolls back.

Requirement refs: 4.2-4.17
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from creator_map_pipeline.database import DatabaseConfigError, resolve_database_url
from creator_map_pipeline.enrichment.leases import (
    MAX_LEASE_BATCH,
    claim_batch,
    commit_checkpoint,
    enqueue_work,
    ensure_job,
    is_job_halted,
    release_lease,
    remaining_quota,
)
from creator_map_schemas import EntityKind

pytestmark = pytest.mark.integration

NOW = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
JOB = "itest-lease-job"


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
def conn(database_url: str) -> Iterator[psycopg.Connection[tuple[object, ...]]]:
    with psycopg.connect(database_url) as connection:
        yield connection
        connection.rollback()


@pytest.fixture
def policy_version(request: pytest.FixtureRequest) -> str:
    """A policy version unique to each test.

    Work identity is global across jobs (Requirement 4.1), so tests sharing
    a policy version would contend for each other's items — and for the
    real ingested work already in the development database. Scoping by test
    name keeps every test's identities disjoint.
    """
    return f"itest-{request.node.name}"


@pytest.fixture
def cur(
    conn: psycopg.Connection[tuple[object, ...]], policy_version: str
) -> Iterator[psycopg.Cursor[tuple[object, ...]]]:
    with conn.cursor() as cursor:
        ensure_job(
            cursor,
            job_id=JOB,
            entity_kind=EntityKind.VIDEO,
            policy_version=policy_version,
        )
        yield cursor


def _enqueue(cur: psycopg.Cursor[tuple[object, ...]], count: int, *, policy_version: str) -> None:
    enqueue_work(
        cur,
        job_id=JOB,
        entity_kind=EntityKind.VIDEO,
        entity_ids=tuple(f"{policy_version}-vid-{i:04d}" for i in range(count)),
        policy_version=policy_version,
        # Match the fixed clock the tests claim against, so items are due.
        now=NOW,
    )


def _claim(
    cur: psycopg.Cursor[tuple[object, ...]],
    *,
    policy_version: str,
    worker: str = "worker-1",
    size: int = 10,
    now: datetime = NOW,
    lease_seconds: int = 300,
):
    return claim_batch(
        cur,
        job_id=JOB,
        entity_kind=EntityKind.VIDEO,
        policy_version=policy_version,
        worker_id=worker,
        batch_size=size,
        lease_seconds=lease_seconds,
        now=now,
    )


# --- Requirement 4.1: idempotent enqueue ---------------------------------


def test_enqueue_is_idempotent(
    cur: psycopg.Cursor[tuple[object, ...]], policy_version: str
) -> None:
    _enqueue(cur, 5, policy_version=policy_version)
    _enqueue(cur, 5, policy_version=policy_version)

    cur.execute(
        "select count(*) from enrichment.work_item where policy_version=%s",
        (policy_version,),
    )
    row = cur.fetchone()
    assert row is not None and row[0] == 5


def test_freshly_enqueued_work_is_immediately_claimable(
    cur: psycopg.Cursor[tuple[object, ...]], policy_version: str
) -> None:
    """Enqueue and claim must share a clock.

    When enqueue left `next_attempt_at` to the server default while the
    claim filtered against a caller-supplied instant, every freshly
    enqueued item was scheduled microseconds in the future and no batch
    was ever claimable — the run reported success having done nothing.
    """
    _enqueue(cur, 5, policy_version=policy_version)
    claim = _claim(cur, policy_version=policy_version, size=5)
    assert len(claim.items) == 5


# --- Requirement 4.2: batch bounds ---------------------------------------


def test_claim_respects_batch_size(
    cur: psycopg.Cursor[tuple[object, ...]], policy_version: str
) -> None:
    _enqueue(cur, 40, policy_version=policy_version)
    claim = _claim(cur, policy_version=policy_version, size=15)
    assert len(claim.items) == 15


def test_claim_refuses_oversized_batch(
    cur: psycopg.Cursor[tuple[object, ...]], policy_version: str
) -> None:
    with pytest.raises(ValueError, match="between 1 and 50"):
        _claim(cur, policy_version=policy_version, size=MAX_LEASE_BATCH + 1)


def test_claim_returns_empty_when_no_work(
    cur: psycopg.Cursor[tuple[object, ...]], policy_version: str
) -> None:
    """Requirement: an empty claim is a normal outcome, not an error."""
    claim = _claim(cur, policy_version=policy_version)
    assert not claim
    assert claim.items == ()


# --- Requirement 4.3: exclusive leases -----------------------------------


def test_two_workers_never_claim_the_same_item(database_url: str, policy_version: str) -> None:
    """The core concurrency guarantee, exercised with real connections."""
    with (
        psycopg.connect(database_url) as first,
        psycopg.connect(database_url) as second,
    ):
        with first.cursor() as cur_a:
            ensure_job(
                cur_a,
                job_id=JOB,
                entity_kind=EntityKind.VIDEO,
                policy_version=policy_version,
            )
            _enqueue(cur_a, 20, policy_version=policy_version)
        first.commit()

        try:
            with first.cursor() as cur_a, second.cursor() as cur_b:
                claim_a = claim_batch(
                    cur_a,
                    job_id=JOB,
                    entity_kind=EntityKind.VIDEO,
                    policy_version=policy_version,
                    worker_id="worker-a",
                    batch_size=10,
                    now=NOW,
                )
                # Worker B claims while A's transaction is still open. The
                # SKIP LOCKED clause must hand B a disjoint set rather than
                # blocking or duplicating.
                claim_b = claim_batch(
                    cur_b,
                    job_id=JOB,
                    entity_kind=EntityKind.VIDEO,
                    policy_version=policy_version,
                    worker_id="worker-b",
                    batch_size=10,
                    now=NOW,
                )

                overlap = set(claim_a.entity_ids) & set(claim_b.entity_ids)
                assert overlap == set(), f"workers shared items: {overlap}"
                assert len(claim_a.items) == 10
                assert len(claim_b.items) == 10
        finally:
            first.rollback()
            second.rollback()
            _purge(database_url, policy_version)


def test_leased_item_is_not_reclaimed_before_expiry(
    cur: psycopg.Cursor[tuple[object, ...]], policy_version: str
) -> None:
    _enqueue(cur, 5, policy_version=policy_version)
    first = _claim(cur, policy_version=policy_version, size=5, lease_seconds=300)
    assert len(first.items) == 5

    # A second claim at the same instant sees nothing eligible.
    second = _claim(cur, policy_version=policy_version, worker="worker-2", size=5)
    assert second.items == ()


# --- Requirement 4.4: expired leases return -----------------------------


def test_expired_lease_becomes_claimable_again(
    cur: psycopg.Cursor[tuple[object, ...]], policy_version: str
) -> None:
    """Eligibility follows expiry, not stored state, so a dead worker's
    items return without a sweeper process."""
    _enqueue(cur, 3, policy_version=policy_version)
    first = _claim(cur, policy_version=policy_version, size=3, lease_seconds=60)
    assert len(first.items) == 3

    # The stored state still reads Leased.
    cur.execute(
        "select count(*) from enrichment.work_item where state='Leased' and policy_version=%s",
        (policy_version,),
    )
    row = cur.fetchone()
    assert row is not None and row[0] == 3

    # Past expiry, another worker reclaims them.
    later = NOW + timedelta(seconds=120)
    second = _claim(cur, policy_version=policy_version, worker="worker-2", size=3, now=later)
    assert len(second.items) == 3
    assert set(second.entity_ids) == set(first.entity_ids)


def test_release_returns_items_without_consuming_an_attempt(
    cur: psycopg.Cursor[tuple[object, ...]], policy_version: str
) -> None:
    _enqueue(cur, 4, policy_version=policy_version)
    claim = _claim(cur, policy_version=policy_version, size=4)
    release_lease(cur, work_item_ids=tuple(i.work_item_id for i in claim.items), now=NOW)

    cur.execute(
        "select state, attempts from enrichment.work_item where policy_version=%s limit 1",
        (policy_version,),
    )
    row = cur.fetchone()
    assert row is not None
    assert row[0] == "Pending"
    assert row[1] == 0


# --- Requirement 4.5-4.7: checkpoints ------------------------------------


def test_checkpoint_commits_outcomes_and_quota(
    cur: psycopg.Cursor[tuple[object, ...]], policy_version: str
) -> None:
    _enqueue(cur, 5, policy_version=policy_version)
    claim = _claim(cur, policy_version=policy_version, size=5)

    committed = commit_checkpoint(
        cur,
        job_id=JOB,
        batch_key="batch-alpha",
        succeeded=tuple(i.work_item_id for i in claim.items),
        quota_units=1,
        operation="itest.videos",
        now=NOW,
    )

    assert committed
    cur.execute(
        "select count(*) from enrichment.work_item where policy_version=%s and state='Succeeded'",
        (policy_version,),
    )
    row = cur.fetchone()
    assert row is not None and row[0] == 5

    cur.execute(
        "select requests, estimated_units from enrichment.quota_ledger "
        "where operation='itest.videos' and ledger_date = current_date"
    )
    row = cur.fetchone()
    assert row is not None
    assert row[1] >= 1


def test_replayed_checkpoint_is_refused(
    cur: psycopg.Cursor[tuple[object, ...]], policy_version: str
) -> None:
    """Requirement 4.7: a replay applies nothing twice."""
    _enqueue(cur, 3, policy_version=policy_version)
    claim = _claim(cur, policy_version=policy_version, size=3)
    ids = tuple(i.work_item_id for i in claim.items)

    assert commit_checkpoint(
        cur,
        job_id=JOB,
        batch_key="batch-replay",
        succeeded=ids,
        quota_units=1,
        operation="itest.videos",
        now=NOW,
    )

    cur.execute(
        "select estimated_units from enrichment.quota_ledger "
        "where operation='itest.videos' and ledger_date=current_date"
    )
    row = cur.fetchone()
    after_first = int(row[0]) if row else 0

    # The same key again: refused, and quota is not double-counted.
    assert not commit_checkpoint(
        cur,
        job_id=JOB,
        batch_key="batch-replay",
        succeeded=ids,
        quota_units=1,
        operation="itest.videos",
        now=NOW,
    )

    cur.execute(
        "select estimated_units from enrichment.quota_ledger "
        "where operation='itest.videos' and ledger_date=current_date"
    )
    row = cur.fetchone()
    assert row is not None and int(row[0]) == after_first


def test_succeeded_items_are_not_reclaimed(
    cur: psycopg.Cursor[tuple[object, ...]], policy_version: str
) -> None:
    _enqueue(cur, 3, policy_version=policy_version)
    claim = _claim(cur, policy_version=policy_version, size=3)
    commit_checkpoint(
        cur,
        job_id=JOB,
        batch_key="batch-done",
        succeeded=tuple(i.work_item_id for i in claim.items),
        quota_units=0,
        operation="itest.videos",
        now=NOW,
    )

    later = NOW + timedelta(hours=1)
    assert _claim(cur, policy_version=policy_version, size=3, now=later).items == ()


# --- Requirement 4.12: halted jobs issue no claims ------------------------


def test_halted_job_issues_no_claims(
    cur: psycopg.Cursor[tuple[object, ...]], policy_version: str
) -> None:
    _enqueue(cur, 5, policy_version=policy_version)
    cur.execute(
        "update enrichment.job set state='OperatorHalt', halt_reason_class='InvalidCredential' "
        "where job_id=%s",
        (JOB,),
    )

    assert is_job_halted(cur, JOB)
    assert _claim(cur, policy_version=policy_version, size=5).items == ()


# --- Requirement 4.14: quota accounting ----------------------------------


def test_remaining_quota_reflects_usage(
    cur: psycopg.Cursor[tuple[object, ...]], policy_version: str
) -> None:
    before = remaining_quota(cur, daily_limit=10_000, operation="itest.quota")

    cur.execute(
        "insert into enrichment.quota_ledger "
        "(ledger_date, operation, requests, estimated_units) "
        "values (current_date, 'itest.quota', 1, 100) "
        "on conflict (ledger_date, operation) do update set "
        "estimated_units = enrichment.quota_ledger.estimated_units + 100"
    )

    after = remaining_quota(cur, daily_limit=10_000, operation="itest.quota")
    assert after == before - 100


def _purge(database_url: str, policy_version: str) -> None:
    """Remove committed test rows from the concurrency test."""
    with psycopg.connect(database_url, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "delete from enrichment.work_item where policy_version=%s",
            (policy_version,),
        )
        cur.execute(
            "delete from enrichment.job where job_id=%s and policy_version=%s",
            (JOB, policy_version),
        )
