"""Metadata resolvers producing append-only observations.

Two resolver families implement one protocol:

- `HuggingFaceChannelResolver` derives video-to-channel observations from a
  pinned dataset snapshot that already carries `channel_id`. The observation
  is real and its provenance is the snapshot digest, not an API response.
- `YouTubeMetadataResolver` (in `youtube.py`) calls the approved metadata
  API, which is the only source of Declared_Country.

Both must classify an unresolvable identity as Unavailable_Unclassified
rather than guessing a finer status (Requirement 3.6), and neither may
infer a country from anything but the channel country field
(Requirement 3.8, Invariant 6).

Requirement refs: 3.5-3.9, 4.18, 15.12
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from creator_map_schemas import (
    ChannelResolution,
    ChannelResolutionStatus,
    VideoResolution,
    VideoResolutionStatus,
)


class ResolverError(RuntimeError):
    """Raised when a resolver cannot complete a batch."""


@dataclass(frozen=True, slots=True)
class VideoObservationResult:
    """Observations produced for one batch of video identities."""

    observations: tuple[VideoResolution, ...]
    #: Quota units the batch consumed. Zero for snapshot-derived resolvers,
    #: which matters because Requirement 4.16 permits zero-cost batches to
    #: proceed regardless of the quota reserve.
    quota_units: int = 0


@dataclass(frozen=True, slots=True)
class ChannelObservationResult:
    """Observations produced for one batch of channel identities."""

    observations: tuple[ChannelResolution, ...]
    quota_units: int = 0


class VideoResolver(Protocol):
    """Resolves video identities to channel attribution."""

    def resolve_videos(
        self, video_ids: tuple[str, ...], *, observed_at: datetime
    ) -> VideoObservationResult: ...


class ChannelResolver(Protocol):
    """Resolves channel identities to display metadata and country."""

    def resolve_channels(
        self, channel_ids: tuple[str, ...], *, observed_at: datetime
    ) -> ChannelObservationResult: ...


class HuggingFaceChannelResolver:
    """Resolves video-to-channel from a pinned dataset snapshot.

    Several permitted corpora (YouTube-Commons among them) publish the
    channel identifier alongside the video identifier. Where that holds, the
    video-to-channel mapping is already documented provenance and needs no
    metadata request: the observation's authority is the snapshot digest.

    This resolver deliberately produces *no* country. The snapshot does not
    carry Declared_Country, and Requirement 3.8 forbids inferring it from
    any other field, so channel resolution remains the API's job.
    """

    #: Snapshot-derived observations cost no API quota.
    quota_units_per_batch = 0

    def __init__(
        self,
        video_to_channel: dict[str, str],
        *,
        snapshot_digest: str,
    ) -> None:
        self._mapping = video_to_channel
        # The response digest records what authorised the observation. For a
        # snapshot resolver that is the snapshot itself, which keeps the
        # provenance chain intact and the tie-breaker in Requirement 3.11
        # well-defined.
        self._snapshot_digest = snapshot_digest

    def resolve_videos(
        self, video_ids: tuple[str, ...], *, observed_at: datetime
    ) -> VideoObservationResult:
        """Return one observation per requested identity.

        Every requested ID yields exactly one observation: resolved when the
        snapshot carries a channel, Unavailable_Unclassified otherwise. A
        missing entry is never silently dropped, because Requirement 6.2
        needs every distinct input video to land in exactly one partition
        state.
        """
        observations: list[VideoResolution] = []

        for video_id in video_ids:
            channel_id = self._mapping.get(video_id)
            if channel_id:
                observations.append(
                    VideoResolution(
                        video_id=video_id,
                        status=VideoResolutionStatus.RESOLVED,
                        channel_id=channel_id,
                        observed_at=observed_at,
                        response_digest=self._snapshot_digest,
                    )
                )
            else:
                observations.append(
                    VideoResolution(
                        video_id=video_id,
                        status=VideoResolutionStatus.UNAVAILABLE_UNCLASSIFIED,
                        observed_at=observed_at,
                        response_digest=self._snapshot_digest,
                    )
                )

        return VideoObservationResult(
            observations=tuple(observations),
            quota_units=self.quota_units_per_batch,
        )


class HuggingFaceDisplayNameResolver:
    """Resolves channel display names from a pinned dataset snapshot.

    YouTube-Commons carries a `channel` display name but no country. This
    resolver supplies the name while leaving `declared_country` absent, so
    every channel it resolves lands in Unknown_Country until the metadata
    API supplies a country. That is the correct behaviour under Invariant 6,
    not a placeholder.
    """

    quota_units_per_batch = 0

    def __init__(
        self,
        channel_to_name: dict[str, str],
        *,
        snapshot_digest: str,
    ) -> None:
        self._names = channel_to_name
        self._snapshot_digest = snapshot_digest

    def resolve_channels(
        self, channel_ids: tuple[str, ...], *, observed_at: datetime
    ) -> ChannelObservationResult:
        observations: list[ChannelResolution] = []

        for channel_id in channel_ids:
            display_name = self._names.get(channel_id)
            if display_name:
                observations.append(
                    ChannelResolution(
                        channel_id=channel_id,
                        status=ChannelResolutionStatus.RESOLVED,
                        display_name=display_name,
                        # Absent by construction: the snapshot has no country
                        # field, and nothing else may substitute for one.
                        declared_country=None,
                        observed_at=observed_at,
                        response_digest=self._snapshot_digest,
                    )
                )
            else:
                observations.append(
                    ChannelResolution(
                        channel_id=channel_id,
                        status=ChannelResolutionStatus.UNAVAILABLE_UNCLASSIFIED,
                        observed_at=observed_at,
                        response_digest=self._snapshot_digest,
                    )
                )

        return ChannelObservationResult(
            observations=tuple(observations),
            quota_units=self.quota_units_per_batch,
        )
