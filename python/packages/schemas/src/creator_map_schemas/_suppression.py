"""Correction, opt-out, and suppression records.

Requirement 7.4 requires the release-pinned exclusion rule to be applied
before a Public_Artifact is generated when a correction, opt-out, or
suppression record matches a creator.

Requirement 7.8 further requires that the suppression reason itself is not
exposed publicly, so the reason field is restricted and never crosses the
publication boundary.

Requirement refs: 7.3, 7.4, 7.8
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum, unique

from pydantic import model_validator

from creator_map_schemas._base import DomainModel
from creator_map_schemas._types import NonEmptyStr


@unique
class SuppressionKind(StrEnum):
    """The kind of exclusion record applied to a creator."""

    CORRECTION = "Correction"
    OPT_OUT = "OptOut"
    SUPPRESSION = "Suppression"


@unique
class SuppressionScope(StrEnum):
    """How much of a creator's public presence the record removes.

    FULL removes the creator from public artifacts entirely. FIELDS removes
    only the named fields while allowing the creator to remain counted in
    aggregates, which supports a correction that withdraws a display name
    without changing the country totals.
    """

    FULL = "Full"
    FIELDS = "Fields"


class SuppressionRecord(DomainModel):
    """One correction, opt-out, or suppression matching a channel.

    The channel_id here is the raw resolved channel identifier used inside
    the restricted environment for matching. It is never serialized into a
    Public_Artifact; the disclosure engine maps permitted creators to a
    Public_Channel_Key separately.
    """

    record_id: NonEmptyStr
    channel_id: NonEmptyStr
    kind: SuppressionKind
    scope: SuppressionScope
    suppressed_fields: tuple[str, ...] = ()
    recorded_at: datetime
    # Restricted: retained for audit and operator review only. Requirement 7.8
    # prohibits exposing the reason on any public surface.
    restricted_reason: NonEmptyStr

    @model_validator(mode="after")
    def _validate_scope_fields(self) -> SuppressionRecord:
        """Field-scoped records name fields; full-scope records do not."""
        if self.scope == SuppressionScope.FIELDS:
            if not self.suppressed_fields:
                msg = "suppressed_fields is required when scope is Fields"
                raise ValueError(msg)
        elif self.suppressed_fields:
            msg = "suppressed_fields must be empty when scope is Full"
            raise ValueError(msg)

        if list(self.suppressed_fields) != sorted(self.suppressed_fields):
            msg = "suppressed_fields must be sorted"
            raise ValueError(msg)
        if len(set(self.suppressed_fields)) != len(self.suppressed_fields):
            msg = "suppressed_fields must be unique"
            raise ValueError(msg)
        return self
