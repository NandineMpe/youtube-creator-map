"""Enrichment execution: claim, resolve, checkpoint, repeat.

Composes planning, leasing, resolution, and checkpointing into one resumable
loop. Interruption at any point leaves committed work committed and
uncommitted work reclaimable, which is what Requirement 4.17 and Invariant
10 demand.

Requirement refs: 3.5-3.9, 4.2-4.18
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime

import psycopg
from creator_map_schemas import (
    ChannelResolution,
    EnrichmentPolicy,
    EntityKind,
    FailureDisposition,
    VideoResolution,
)

from creator_map_pipeline.enrichment.leases import (
    ClaimResult,
    claim_batch,
    commit_checkpoint,
    enqueue_work,
    ensure_job,
    is_job_halted,
    release_lease,
    remaining_quota,
)
from creator_map_pipeline.enrichment.resolver import (
    ChannelObservationResult,
    VideoObservationResult,
)
from creator_map_pipeline.enrichment.youtube import ApiError


@dataclass(slots=True)
class RunSummary:
    """What one enrichment run accomplished."""

    entity_kind: EntityKind
    batches_committed: int = 0
    batches_replayed: int = 0
    observations_written: int = 0
    resolved: int = 0
    unavailable: int = 0
    quota_units_used: int = 0
    halted: bool = False
    halt_reason: str | None = None
    stopped_for_quota: bool = False

    def describe(self) -> str:
        parts = [
            f"{self.entity_kind.value.lower()}s:",
            f"batches={self.batches_committed}",
            f"observations={self.observations_written}",
            f"resolved={self.resolved}",
            f"unavailable={self.unavailable}",
            f"quota={self.quota_units_used}",
        ]
        if self.batches_replayed:
            parts.append(f"replayed={self.batches_replayed}")
        if self.stopped_for_quota:
            parts.append("STOPPED: quota reserve reached")
        if self.halted:
            parts.append(f"HALTED: {self.halt_reason}")
        return " ".join(parts)


def batch_key(entity_ids: tuple[str, ...]) -> str:
    """Derive a deterministic key identifying one batch's content.

    Requirement 4.7 needs replay detection to survive a crash, so the key
    must depend only on what the batch contains — not on a counter or a
    timestamp that a restarted worker would generate differently.
    """
    # A separator keeps the key unambiguous: without one, ("ab","c") and
    # ("a","bc") would hash identically.
    joined = "\x1f".join(sorted(entity_ids)).encode("utf-8")
    return hashlib.sha256(joined).hexdigest()


def _write_video_observations(
    cur: psycopg.Cursor[tuple[object, ...]],
    observations: tuple[VideoResolution, ...],
    *,
    policy_version: str,
) -> None:
    """Append video observations. Never updates an existing row."""
    if not observations:
        return
    cur.executemany(
        "insert into enrichment.video_observation "
        "(video_id, status, channel_id, observed_at, response_digest, policy_version) "
        "values (%s,%s,%s,%s,%s,%s)",
        [
            (
                o.video_id,
                o.status.value,
                o.channel_id,
                o.observed_at,
                o.response_digest,
                policy_version,
            )
            for o in observations
        ],
    )


def _write_channel_observations(
    cur: psycopg.Cursor[tuple[object, ...]],
    observations: tuple[ChannelResolution, ...],
    *,
    policy_version: str,
) -> None:
    """Append channel observations. Never updates an existing row."""
    if not observations:
        return
    cur.executemany(
        "insert into enrichment.channel_observation "
        "(channel_id, status, display_name, declared_country, observed_at, "
        " response_digest, policy_version) values (%s,%s,%s,%s,%s,%s,%s)",
        [
            (
                o.channel_id,
                o.status.value,
                o.display_name,
                o.declared_country,
                o.observed_at,
                o.response_digest,
                policy_version,
            )
            for o in observations
        ],
    )


def _halt_job(cur: psycopg.Cursor[tuple[object, ...]], *, job_id: str, reason_class: str) -> None:
    """Place a job in Operator_Halt (Requirement 4.11)."""
    cur.execute(
        "update enrichment.job set state = 'OperatorHalt', halt_reason_class = %s, "
        "updated_at = now() where job_id = %s",
        (reason_class, job_id),
    )


def _apply_failure(
    cur: psycopg.Cursor[tuple[object, ...]],
    *,
    claim: ClaimResult,
    error: ApiError,
    policy: EnrichmentPolicy,
    now: datetime,
) -> bool:
    """Apply a classified failure to the claimed items.

    Returns True when the job must halt. Retryable failures schedule the
    next attempt with bounded backoff; a run of attempts reaching the policy
    bound becomes terminal (Requirements 4.8-4.10).
    """
    disposition = policy.retry_policy.disposition_for(error.error_class)
    item_ids = [item.work_item_id for item in claim.items]

    if disposition is FailureDisposition.OPERATOR_HALT:
        release_lease(cur, work_item_ids=tuple(item_ids), now=now)
        return True

    if disposition is FailureDisposition.NON_RETRYABLE:
        cur.execute(
            "update enrichment.work_item set state = 'TerminalFailure', "
            "last_error_class = %s, lease_expires_at = null, lease_owner = null, "
            "attempts = attempts + 1, updated_at = %s where work_item_id = any(%s)",
            (error.error_class.value, now, item_ids),
        )
        return False

    # Retryable: schedule the next attempt, or go terminal at the bound.
    for item in claim.items:
        attempts = item.attempts + 1
        if attempts >= policy.retry_policy.max_attempts:
            cur.execute(
                "update enrichment.work_item set state = 'TerminalFailure', "
                "last_error_class = %s, attempts = %s, lease_expires_at = null, "
                "lease_owner = null, updated_at = %s where work_item_id = %s",
                (error.error_class.value, attempts, now, item.work_item_id),
            )
        else:
            delay = policy.retry_policy.delay_for_attempt(attempts)
            cur.execute(
                "update enrichment.work_item set state = 'RetryableFailure', "
                "last_error_class = %s, attempts = %s, "
                "next_attempt_at = %s + make_interval(secs => %s), "
                "lease_expires_at = null, lease_owner = null, updated_at = %s "
                "where work_item_id = %s",
                (
                    error.error_class.value,
                    attempts,
                    now,
                    delay,
                    now,
                    item.work_item_id,
                ),
            )
    return False


def run_video_enrichment(
    connection: psycopg.Connection[tuple[object, ...]],
    *,
    job_id: str,
    video_ids: tuple[str, ...],
    resolver: object,
    policy: EnrichmentPolicy,
    daily_quota_limit: int,
    operation: str = "videos.list",
    worker_id: str = "worker-1",
    max_batches: int | None = None,
    now: datetime | None = None,
) -> RunSummary:
    """Resolve videos to channels, resumably.

    Each batch commits in its own transaction so an interruption loses at
    most the in-flight batch, and that batch's items return to the queue
    when their lease expires.
    """
    summary = RunSummary(entity_kind=EntityKind.VIDEO)
    instant = now or datetime.now(UTC)

    with connection.cursor() as cur:
        ensure_job(
            cur,
            job_id=job_id,
            entity_kind=EntityKind.VIDEO,
            policy_version=policy.version,
        )
        enqueue_work(
            cur,
            job_id=job_id,
            entity_kind=EntityKind.VIDEO,
            entity_ids=video_ids,
            policy_version=policy.version,
            # Same clock the claim uses, so freshly enqueued work is due
            # immediately rather than a few microseconds in the future.
            now=instant,
        )
    connection.commit()

    batches = 0
    while max_batches is None or batches < max_batches:
        with connection.cursor() as cur:
            if is_job_halted(cur, job_id):
                summary.halted = True
                summary.halt_reason = "job is in Operator_Halt"
                connection.rollback()
                break

            # Requirement 4.14/4.16: a positive-cost batch stops at the
            # reserve; a zero-cost batch proceeds regardless.
            projected = getattr(resolver, "quota_units_per_batch", 1)
            if projected > 0:
                available = remaining_quota(cur, daily_limit=daily_quota_limit, operation=operation)
                if available - projected <= policy.quota_reserve:
                    summary.stopped_for_quota = True
                    connection.rollback()
                    break

            claim = claim_batch(
                cur,
                job_id=job_id,
                entity_kind=EntityKind.VIDEO,
                policy_version=policy.version,
                worker_id=worker_id,
                batch_size=policy.max_batch_size,
                now=instant,
            )
            if not claim:
                connection.commit()
                break
            connection.commit()

        try:
            result: VideoObservationResult = resolver.resolve_videos(  # type: ignore[attr-defined]
                claim.entity_ids, observed_at=instant
            )
        except ApiError as error:
            with connection.cursor() as cur:
                must_halt = _apply_failure(
                    cur, claim=claim, error=error, policy=policy, now=instant
                )
                if must_halt:
                    _halt_job(cur, job_id=job_id, reason_class=error.error_class.value)
                    summary.halted = True
                    summary.halt_reason = error.error_class.value
            connection.commit()
            if summary.halted:
                break
            batches += 1
            continue

        with connection.cursor() as cur:
            key = batch_key(claim.entity_ids)
            committed = commit_checkpoint(
                cur,
                job_id=job_id,
                batch_key=key,
                succeeded=tuple(i.work_item_id for i in claim.items),
                quota_units=result.quota_units,
                operation=operation,
                now=instant,
            )
            if committed:
                _write_video_observations(cur, result.observations, policy_version=policy.version)
                summary.batches_committed += 1
                summary.observations_written += len(result.observations)
                summary.quota_units_used += result.quota_units
                summary.resolved += sum(1 for o in result.observations if o.channel_id)
                summary.unavailable += sum(1 for o in result.observations if not o.channel_id)
            else:
                summary.batches_replayed += 1
        connection.commit()
        batches += 1

    return summary


def run_channel_enrichment(
    connection: psycopg.Connection[tuple[object, ...]],
    *,
    job_id: str,
    channel_ids: tuple[str, ...],
    resolver: object,
    policy: EnrichmentPolicy,
    daily_quota_limit: int,
    operation: str = "channels.list",
    worker_id: str = "worker-1",
    max_batches: int | None = None,
    now: datetime | None = None,
) -> RunSummary:
    """Resolve channels to display metadata and Declared_Country."""
    summary = RunSummary(entity_kind=EntityKind.CHANNEL)
    instant = now or datetime.now(UTC)

    with connection.cursor() as cur:
        ensure_job(
            cur,
            job_id=job_id,
            entity_kind=EntityKind.CHANNEL,
            policy_version=policy.version,
        )
        enqueue_work(
            cur,
            job_id=job_id,
            entity_kind=EntityKind.CHANNEL,
            entity_ids=channel_ids,
            policy_version=policy.version,
            now=instant,
        )
    connection.commit()

    batches = 0
    while max_batches is None or batches < max_batches:
        with connection.cursor() as cur:
            if is_job_halted(cur, job_id):
                summary.halted = True
                summary.halt_reason = "job is in Operator_Halt"
                connection.rollback()
                break

            projected = getattr(resolver, "quota_units_per_batch", 1)
            if projected > 0:
                available = remaining_quota(cur, daily_limit=daily_quota_limit, operation=operation)
                if available - projected <= policy.quota_reserve:
                    summary.stopped_for_quota = True
                    connection.rollback()
                    break

            claim = claim_batch(
                cur,
                job_id=job_id,
                entity_kind=EntityKind.CHANNEL,
                policy_version=policy.version,
                worker_id=worker_id,
                batch_size=policy.max_batch_size,
                now=instant,
            )
            if not claim:
                connection.commit()
                break
            connection.commit()

        try:
            result: ChannelObservationResult = resolver.resolve_channels(  # type: ignore[attr-defined]
                claim.entity_ids, observed_at=instant
            )
        except ApiError as error:
            with connection.cursor() as cur:
                must_halt = _apply_failure(
                    cur, claim=claim, error=error, policy=policy, now=instant
                )
                if must_halt:
                    _halt_job(cur, job_id=job_id, reason_class=error.error_class.value)
                    summary.halted = True
                    summary.halt_reason = error.error_class.value
            connection.commit()
            if summary.halted:
                break
            batches += 1
            continue

        with connection.cursor() as cur:
            key = batch_key(claim.entity_ids)
            committed = commit_checkpoint(
                cur,
                job_id=job_id,
                batch_key=key,
                succeeded=tuple(i.work_item_id for i in claim.items),
                quota_units=result.quota_units,
                operation=operation,
                now=instant,
            )
            if committed:
                _write_channel_observations(cur, result.observations, policy_version=policy.version)
                summary.batches_committed += 1
                summary.observations_written += len(result.observations)
                summary.quota_units_used += result.quota_units
                summary.resolved += sum(
                    1 for o in result.observations if o.display_name is not None
                )
                summary.unavailable += sum(1 for o in result.observations if o.display_name is None)
            else:
                summary.batches_replayed += 1
        connection.commit()
        batches += 1

    return summary
