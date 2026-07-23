"""Normalized occurrence domain model.

Encodes Requirements 2.7–2.10: provenance-complete occurrence records
with validated clip bounds and mandatory fields.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import model_validator

from creator_map_schemas._base import DomainModel
from creator_map_schemas._enums import OccurrenceUnit
from creator_map_schemas._types import NonEmptyStr


class NormalizedOccurrence(DomainModel):
    """An accepted source occurrence with complete provenance.

    Clip bounds, when present, must satisfy 0 <= clip_start < clip_end.
    The source_locator is opaque and restricted by default.
    """

    dataset_id: NonEmptyStr
    snapshot_digest: NonEmptyStr
    source_locator: NonEmptyStr
    video_id: NonEmptyStr
    clip_start: float | None = None
    clip_end: float | None = None
    occurrence_unit: OccurrenceUnit
    extracted_at: datetime
    adapter_version: NonEmptyStr

    @model_validator(mode="after")
    def _validate_clip_bounds(self) -> NormalizedOccurrence:
        """Enforce 0 <= clip_start < clip_end when clip bounds are present."""
        if self.clip_start is not None and self.clip_end is not None:
            if self.clip_start < 0:
                msg = f"clip_start must be >= 0; got {self.clip_start}"
                raise ValueError(msg)
            if self.clip_start >= self.clip_end:
                msg = (
                    f"clip_start must be < clip_end; "
                    f"got start={self.clip_start}, end={self.clip_end}"
                )
                raise ValueError(msg)
        elif self.clip_start is not None and self.clip_end is None:
            msg = "clip_end is required when clip_start is provided"
            raise ValueError(msg)
        elif self.clip_start is None and self.clip_end is not None:
            msg = "clip_start is required when clip_end is provided"
            raise ValueError(msg)
        return self
