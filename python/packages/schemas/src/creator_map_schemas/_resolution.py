"""Resolution observation domain models.

Encodes Requirements 3.5–3.12: append-only video and channel enrichment
observations with response digests and country semantics.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import model_validator

from creator_map_schemas._base import DomainModel
from creator_map_schemas._enums import ChannelResolutionStatus, VideoResolutionStatus
from creator_map_schemas._types import CountryCode, NonEmptyStr


class VideoResolution(DomainModel):
    """A single video enrichment observation.

    A resolved video has a channel_id; unavailable/invalid videos do not.
    response_digest is present when a response was received.
    """

    video_id: NonEmptyStr
    status: VideoResolutionStatus
    channel_id: str | None = None
    observed_at: datetime
    response_digest: str | None = None

    @model_validator(mode="after")
    def _validate_channel_for_resolved(self) -> VideoResolution:
        """Resolved videos must have a channel_id; others must not."""
        if self.status == VideoResolutionStatus.RESOLVED:
            if not self.channel_id:
                msg = "channel_id is required when status is Resolved"
                raise ValueError(msg)
        else:
            if self.channel_id:
                msg = f"channel_id must be absent when status is {self.status}"
                raise ValueError(msg)
        return self


class ChannelResolution(DomainModel):
    """A single channel enrichment observation.

    Country is accepted only from the channel metadata field.
    Absent or unsupported declared_country maps to Unknown (None here;
    aggregation assigns the UNKNOWN_COUNTRY sentinel).
    """

    channel_id: NonEmptyStr
    status: ChannelResolutionStatus
    display_name: str | None = None
    declared_country: CountryCode | None = None
    observed_at: datetime
    response_digest: str | None = None

    @model_validator(mode="after")
    def _validate_resolved_fields(self) -> ChannelResolution:
        """Resolved channels should ideally have display_name; unavailable must not."""
        if self.status == ChannelResolutionStatus.UNAVAILABLE_UNCLASSIFIED:
            if self.display_name is not None:
                msg = "display_name must be absent when status is UnavailableUnclassified"
                raise ValueError(msg)
            if self.declared_country is not None:
                msg = "declared_country must be absent when status is UnavailableUnclassified"
                raise ValueError(msg)
        return self
