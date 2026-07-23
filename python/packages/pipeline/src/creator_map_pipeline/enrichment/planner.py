"""Distinct work planning for enrichment.

Requirements 3.1 and 3.2 permit at most one Work_Item per distinct entity
per policy version. Requirement 3.3 keeps dataset membership as a
many-to-many relation rather than copying observations per dataset, so a
video appearing in five datasets is enriched once, not five times.

Requirement refs: 3.1-3.4, 4.1
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from creator_map_schemas import EnrichmentPolicy, EntityKind

from creator_map_pipeline.enrichment.observations import ObservationStore


@dataclass(frozen=True, slots=True)
class WorkPlan:
    """The distinct work an enrichment run needs to perform."""

    entity_kind: EntityKind
    policy_version: str
    #: Entities needing a metadata request, deterministically ordered.
    pending: tuple[str, ...]
    #: Entities already satisfied by a fresh cached observation.
    cached: tuple[str, ...] = field(default=())

    @property
    def total(self) -> int:
        """Distinct entities considered."""
        return len(self.pending) + len(self.cached)

    @property
    def projected_requests(self) -> int:
        """Metadata requests this plan will issue at the policy batch size."""
        return len(self.pending)

    def batches(self, size: int) -> tuple[tuple[str, ...], ...]:
        """Split pending work into batches of at most `size`.

        Requirement 4.2 bounds a batch at 50 distinct items; the policy's
        max_batch_size enforces the cap and this only partitions.
        """
        if size < 1:
            msg = f"batch size must be >= 1; got {size}"
            raise ValueError(msg)
        return tuple(
            self.pending[start : start + size] for start in range(0, len(self.pending), size)
        )


def plan_video_work(
    video_ids: set[str],
    *,
    policy: EnrichmentPolicy,
    cutoff: datetime,
    store: ObservationStore,
) -> WorkPlan:
    """Plan video enrichment for a set of distinct identities.

    The input is a set, so a video present in many datasets yields exactly
    one work item (Requirement 3.1). Ordering is sorted rather than
    set-iteration order so a replanned run produces identical batches.
    """
    pending: list[str] = []
    cached: list[str] = []

    for video_id in sorted(video_ids):
        if store.has_fresh_video(video_id, policy=policy, cutoff=cutoff):
            cached.append(video_id)
        else:
            pending.append(video_id)

    return WorkPlan(
        entity_kind=EntityKind.VIDEO,
        policy_version=policy.version,
        pending=tuple(pending),
        cached=tuple(cached),
    )


def plan_channel_work(
    channel_ids: set[str],
    *,
    policy: EnrichmentPolicy,
    cutoff: datetime,
    store: ObservationStore,
) -> WorkPlan:
    """Plan channel enrichment for a set of distinct identities."""
    pending: list[str] = []
    cached: list[str] = []

    for channel_id in sorted(channel_ids):
        if store.has_fresh_channel(channel_id, policy=policy, cutoff=cutoff):
            cached.append(channel_id)
        else:
            pending.append(channel_id)

    return WorkPlan(
        entity_kind=EntityKind.CHANNEL,
        policy_version=policy.version,
        pending=tuple(pending),
        cached=tuple(cached),
    )
