"""Append-only observation storage and deterministic selection.

Requirement 3.9 makes observations append-only: a new result is appended,
never an update. Requirement 3.10 excludes observations later than the
release-pinned cutoff, and Requirement 3.11 selects exactly one per identity
using the policy's deterministic ordering and tie-breaking.

Determinism here is what makes Requirement 3.12 hold: unchanged inputs,
policy version, and cutoff must yield the same Selected_Observation on every
build, so a release is reproducible.

Requirement refs: 3.4, 3.9-3.12
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from creator_map_schemas import (
    ChannelResolution,
    EnrichmentPolicy,
    ObservationTieBreaker,
    VideoResolution,
)

#: An observation is either a video or a channel resolution.
Observation = VideoResolution | ChannelResolution


@dataclass(frozen=True, slots=True)
class SelectedObservation:
    """The single observation chosen for one identity at a cutoff."""

    entity_id: str
    observation: Observation
    #: How many eligible observations existed before selection. Useful for
    #: operator diagnostics; never published.
    candidate_count: int


def _sort_key(observation: Observation) -> tuple[datetime, str]:
    """Return the deterministic ordering key for an observation.

    Observation time alone is not a total order: two observations can share
    an instant. The response digest breaks the tie because it is unique per
    distinct response payload, so selection never depends on insertion order
    or physical row order (Requirement 3.11).
    """
    return (observation.observed_at, observation.response_digest or "")


def select_observation(
    entity_id: str,
    candidates: list[Observation],
    *,
    policy: EnrichmentPolicy,
    cutoff: datetime,
) -> SelectedObservation | None:
    """Select exactly one eligible observation, or None if none qualify.

    Eligibility requires the observation to be at or before the cutoff
    (Requirement 3.10). Among eligible candidates, the policy's tie-breaker
    decides.
    """
    eligible = [c for c in candidates if c.observed_at <= cutoff]
    if not eligible:
        return None

    reverse = policy.tie_breaker is ObservationTieBreaker.LATEST_OBSERVED_THEN_DIGEST
    ordered = sorted(eligible, key=_sort_key, reverse=reverse)

    return SelectedObservation(
        entity_id=entity_id,
        observation=ordered[0],
        candidate_count=len(eligible),
    )


class ObservationStore:
    """In-memory append-only observation store.

    Mirrors the semantics of the PostgreSQL tables: append-only, keyed by
    entity and policy version, with selection applied at read time rather
    than write time so a historical release can be rebuilt at its own
    cutoff.
    """

    def __init__(self) -> None:
        self._videos: dict[tuple[str, str], list[VideoResolution]] = {}
        self._channels: dict[tuple[str, str], list[ChannelResolution]] = {}

    def append_video(self, observation: VideoResolution, *, policy_version: str) -> None:
        """Append a video observation. Never replaces an existing one."""
        key = (observation.video_id, policy_version)
        self._videos.setdefault(key, []).append(observation)

    def append_channel(self, observation: ChannelResolution, *, policy_version: str) -> None:
        """Append a channel observation. Never replaces an existing one."""
        key = (observation.channel_id, policy_version)
        self._channels.setdefault(key, []).append(observation)

    def video_candidates(self, video_id: str, *, policy_version: str) -> list[VideoResolution]:
        """Return every stored observation for a video identity."""
        return list(self._videos.get((video_id, policy_version), []))

    def channel_candidates(
        self, channel_id: str, *, policy_version: str
    ) -> list[ChannelResolution]:
        """Return every stored observation for a channel identity."""
        return list(self._channels.get((channel_id, policy_version), []))

    def select_video(
        self, video_id: str, *, policy: EnrichmentPolicy, cutoff: datetime
    ) -> SelectedObservation | None:
        """Select one video observation at the pinned cutoff."""
        candidates = self.video_candidates(video_id, policy_version=policy.version)
        return select_observation(video_id, list(candidates), policy=policy, cutoff=cutoff)

    def select_channel(
        self, channel_id: str, *, policy: EnrichmentPolicy, cutoff: datetime
    ) -> SelectedObservation | None:
        """Select one channel observation at the pinned cutoff."""
        candidates = self.channel_candidates(channel_id, policy_version=policy.version)
        return select_observation(channel_id, list(candidates), policy=policy, cutoff=cutoff)

    def has_fresh_video(self, video_id: str, *, policy: EnrichmentPolicy, cutoff: datetime) -> bool:
        """Whether a cached observation satisfies the policy (Requirement 3.4).

        A fresh cache entry means no duplicate metadata request is issued,
        which is the mechanism by which quota is conserved across datasets
        that share videos.
        """
        return any(
            policy.is_fresh(candidate.observed_at, cutoff)
            for candidate in self.video_candidates(video_id, policy_version=policy.version)
        )

    def has_fresh_channel(
        self, channel_id: str, *, policy: EnrichmentPolicy, cutoff: datetime
    ) -> bool:
        """Whether a cached channel observation satisfies the policy."""
        return any(
            policy.is_fresh(candidate.observed_at, cutoff)
            for candidate in self.channel_candidates(channel_id, policy_version=policy.version)
        )

    @property
    def video_observation_count(self) -> int:
        """Total video observations stored across all identities."""
        return sum(len(values) for values in self._videos.values())

    @property
    def channel_observation_count(self) -> int:
        """Total channel observations stored across all identities."""
        return sum(len(values) for values in self._channels.values())
