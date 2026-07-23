"""Transactional work leasing and idempotent batch checkpoints.

The claim query is the heart of resumability. It selects eligible work with
`FOR UPDATE SKIP LOCKED`, which lets several workers claim disjoint batches
concurrently without blocking each other and without two workers ever
holding the same item (Requirement 4.3).

Eligibility is computed from lease expiry rather than stored state
(Requirement 4.4): an item whose worker died still reads as `Leased`, but
its lease has passed, so it is reclaimable without a separate sweeper
process that could itself fail.

Requirement refs: 4.2-4.7, 4.14-4.16
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import psycopg
from creator_map_schemas import EntityKind

#: Requirement 4.2 bounds one metadata batch at 50 distinct items.
MAX_LEASE_BATCH = 50


class QuotaExhausted(RuntimeError):
    """Raised when a positive-cost claim would consume the quota reserve."""


@dataclass(frozen=True, slots=True)
class LeasedItem:
    """One work item leased to this worker."""

    work_item_id: int
    entity_id: str
    attempts: int


@dataclass(frozen=True, slots=True)
class ClaimResult:
    """The outcome of one claim attempt."""

    items: tuple[LeasedItem, ...]
    lease_expires_at: datetime

    @property
    def entity_ids(self) -> tuple[str, ...]:
        return tuple(item.entity_id for item in self.items)

    def __bool__(self) -> bool:
        return bool(self.items)


def ensure_job(
    cur: psycopg.Cursor[tuple[object, ...]],
    *,
    job_id: str,
    entity_kind: EntityKind,
    policy_version: str,
) -> None:
    """Create the job row if absent. Idempotent."""
    cur.execute(
        "insert into enrichment.job (job_id, entity_kind, policy_version) "
        "values (%s, %s, %s) on conflict (job_id) do nothing",
        (job_id, entity_kind.value, policy_version),
    )


def enqueue_work(
    cur: psycopg.Cursor[tuple[object, ...]],
    *,
    job_id: str,
    entity_kind: EntityKind,
    entity_ids: tuple[str, ...],
    policy_version: str,
    now: datetime | None = None,
) -> int:
    """Create work items for entities that do not already have one.

    Idempotent on (entity_kind, entity_id, policy_version): re-enqueuing an
    existing identity is a no-op, which is what lets a replanned run add
    only genuinely new work (Requirements 3.1, 3.2, 4.1).

    `now` sets the initial `next_attempt_at`. It is explicit rather than
    left to the column default so that a caller supplying a fixed clock —
    a test, or a backfill replaying a historical run — sees its items as
    immediately due rather than scheduled against the server's wall clock.
    """
    if not entity_ids:
        return 0

    instant = now or datetime.now(UTC)
    cur.executemany(
        "insert into enrichment.work_item "
        "(job_id, entity_kind, entity_id, policy_version, next_attempt_at) "
        "values (%s,%s,%s,%s,%s) "
        "on conflict (entity_kind, entity_id, policy_version) do nothing",
        [
            (job_id, entity_kind.value, entity_id, policy_version, instant)
            for entity_id in entity_ids
        ],
    )
    return len(entity_ids)


def is_job_halted(cur: psycopg.Cursor[tuple[object, ...]], job_id: str) -> bool:
    """Whether the job is in Operator_Halt (Requirement 4.12)."""
    cur.execute("select state from enrichment.job where job_id = %s", (job_id,))
    row = cur.fetchone()
    return row is not None and str(row[0]) == "OperatorHalt"


def remaining_quota(
    cur: psycopg.Cursor[tuple[object, ...]], *, daily_limit: int, operation: str
) -> int:
    """Return quota units remaining today for an operation."""
    cur.execute(
        "select coalesce(sum(estimated_units), 0) from enrichment.quota_ledger "
        "where ledger_date = current_date and operation = %s",
        (operation,),
    )
    row = cur.fetchone()
    used = int(str(row[0])) if row is not None else 0
    return max(daily_limit - used, 0)


def claim_batch(
    cur: psycopg.Cursor[tuple[object, ...]],
    *,
    job_id: str,
    entity_kind: EntityKind,
    policy_version: str,
    worker_id: str,
    batch_size: int,
    lease_seconds: int = 300,
    now: datetime | None = None,
) -> ClaimResult:
    """Claim up to `batch_size` eligible items for one worker.

    Eligible means: not already succeeded or terminally failed, due for its
    next attempt, and either unleased or holding an expired lease. The
    `SKIP LOCKED` clause means a concurrent worker takes a different batch
    rather than waiting.

    Work is selected by (entity_kind, policy_version) rather than by job:
    Requirement 4.1 makes work identity global across jobs, so a second job
    over the same identities must not create duplicate items and must be
    able to continue work an earlier job left pending. Filtering by job_id
    here would strand that work permanently.
    """
    if not 1 <= batch_size <= MAX_LEASE_BATCH:
        msg = f"batch size must be between 1 and {MAX_LEASE_BATCH}; got {batch_size}"
        raise ValueError(msg)

    if is_job_halted(cur, job_id):
        # Requirement 4.12: a halted job issues no claims.
        return ClaimResult(items=(), lease_expires_at=now or datetime.now(UTC))

    instant = now or datetime.now(UTC)
    expires_at = instant + timedelta(seconds=lease_seconds)

    cur.execute(
        """
        with eligible as (
            select work_item_id
            from enrichment.work_item
            where entity_kind = %(entity_kind)s
              and policy_version = %(policy_version)s
              and state in ('Pending', 'RetryableFailure', 'Leased')
              and next_attempt_at <= %(now)s
              -- Requirement 4.4: expiry, not stored state, decides
              -- eligibility, so a dead worker's item returns on its own.
              and (state <> 'Leased' or lease_expires_at <= %(now)s)
            order by next_attempt_at, work_item_id
            limit %(batch_size)s
            for update skip locked
        )
        update enrichment.work_item w
        set state = 'Leased',
            lease_expires_at = %(expires_at)s,
            lease_owner = %(worker_id)s,
            job_id = %(job_id)s,
            updated_at = %(now)s
        from eligible e
        where w.work_item_id = e.work_item_id
        returning w.work_item_id, w.entity_id, w.attempts
        """,
        {
            "job_id": job_id,
            "entity_kind": entity_kind.value,
            "policy_version": policy_version,
            "now": instant,
            "expires_at": expires_at,
            "batch_size": batch_size,
            "worker_id": worker_id,
        },
    )

    items = tuple(
        LeasedItem(
            work_item_id=int(str(row[0])),
            entity_id=str(row[1]),
            attempts=int(str(row[2])),
        )
        for row in cur.fetchall()
    )
    return ClaimResult(items=items, lease_expires_at=expires_at)


def commit_checkpoint(
    cur: psycopg.Cursor[tuple[object, ...]],
    *,
    job_id: str,
    batch_key: str,
    succeeded: tuple[int, ...],
    quota_units: int,
    operation: str,
    now: datetime | None = None,
) -> bool:
    """Commit one batch's outcomes, transitions, and quota atomically.

    Returns False when the batch key already exists, which means this is a
    replay: the prior effects stand and nothing is applied twice
    (Requirement 4.7). The unique constraint on (job_id, batch_key) is what
    makes that detection reliable rather than advisory.
    """
    instant = now or datetime.now(UTC)

    cur.execute(
        "insert into enrichment.checkpoint "
        "(job_id, batch_key, items_committed, quota_units_used) "
        "values (%s,%s,%s,%s) on conflict (job_id, batch_key) do nothing "
        "returning checkpoint_id",
        (job_id, batch_key, len(succeeded), quota_units),
    )
    if cur.fetchone() is None:
        return False

    if succeeded:
        cur.execute(
            "update enrichment.work_item "
            "set state = 'Succeeded', lease_expires_at = null, lease_owner = null, "
            "    updated_at = %s "
            "where work_item_id = any(%s)",
            (instant, list(succeeded)),
        )

    if quota_units:
        cur.execute(
            "insert into enrichment.quota_ledger "
            "(ledger_date, operation, requests, estimated_units) "
            "values (current_date, %s, 1, %s) "
            "on conflict (ledger_date, operation) do update "
            "set requests = enrichment.quota_ledger.requests + 1, "
            "    estimated_units = enrichment.quota_ledger.estimated_units + %s, "
            "    updated_at = now()",
            (operation, quota_units, quota_units),
        )

    return True


def release_lease(
    cur: psycopg.Cursor[tuple[object, ...]],
    *,
    work_item_ids: tuple[int, ...],
    now: datetime | None = None,
) -> None:
    """Return items to Pending without consuming an attempt.

    Used when a batch is abandoned for a reason that is not the item's
    fault, such as reaching the quota reserve mid-run.
    """
    if not work_item_ids:
        return
    cur.execute(
        "update enrichment.work_item "
        "set state = 'Pending', lease_expires_at = null, lease_owner = null, "
        "    updated_at = %s "
        "where work_item_id = any(%s) and state = 'Leased'",
        (now or datetime.now(UTC), list(work_item_ids)),
    )
