"""Disclosure policy domain model.

Encodes the versioned publication policy that governs what creator
information may cross the publication boundary. Fail-closed: absent,
invalid, or incomplete policies reject all release candidates.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field, model_validator

from creator_map_schemas._base import DomainModel
from creator_map_schemas._types import Natural, NonEmptyStr


class DisclosurePolicy(DomainModel):
    """Versioned disclosure policy governing creator publication.

    The policy defines thresholds, allowed fields, risk rules,
    and suppression/opt-out handling. All conditions must be
    satisfied (fail-closed) before a creator's data may appear
    in public artifacts.
    """

    policy_id: NonEmptyStr
    version: NonEmptyStr
    approved_at: datetime
    min_represented_video_count: Natural = Field(
        ge=0, description="Minimum distinct videos for creator disclosure"
    )
    allowed_fields: tuple[str, ...] = Field(
        min_length=1, description="Ordered set of field names allowed for public display"
    )
    suppression_rules: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Ordered identifiers of active suppression/opt-out rules",
    )

    @model_validator(mode="after")
    def _validate_allowed_fields_sorted(self) -> DisclosurePolicy:
        """Allowed fields must be sorted and unique for deterministic application."""
        if list(self.allowed_fields) != sorted(self.allowed_fields):
            msg = "allowed_fields must be sorted"
            raise ValueError(msg)
        if len(set(self.allowed_fields)) != len(self.allowed_fields):
            msg = "allowed_fields must be unique"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_suppression_rules_sorted(self) -> DisclosurePolicy:
        """Suppression rules must be sorted and unique."""
        if list(self.suppression_rules) != sorted(self.suppression_rules):
            msg = "suppression_rules must be sorted"
            raise ValueError(msg)
        if len(set(self.suppression_rules)) != len(self.suppression_rules):
            msg = "suppression_rules must be unique"
            raise ValueError(msg)
        return self
