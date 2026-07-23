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
    #: Per-country thresholds that replace the default for named buckets.
    #:
    #: A single global threshold cannot express a decision that differs by
    #: region, and pretending otherwise would push the difference into
    #: build-script conditionals where it is invisible to the policy
    #: record. Requirement 7.1 pins a release to a policy *version*, so a
    #: change here is a new version and the release that used the old one
    #: stays reproducible.
    #:
    #: Lowering a threshold widens what is published about identifiable
    #: people. Each entry should trace to a recorded decision.
    country_thresholds: tuple[tuple[str, int], ...] = Field(
        default_factory=tuple,
        description="Country code to minimum represented-video count",
    )

    def threshold_for(self, country: str) -> int:
        """The minimum represented-video count for one country bucket."""
        for code, minimum in self.country_thresholds:
            if code == country:
                return minimum
        return self.min_represented_video_count

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
    def _validate_country_thresholds(self) -> DisclosurePolicy:
        """Overrides must be sorted, unique, and well-formed."""
        codes = [code for code, _ in self.country_thresholds]
        if list(codes) != sorted(codes):
            msg = "country_thresholds must be sorted by country code"
            raise ValueError(msg)
        if len(set(codes)) != len(codes):
            msg = "country_thresholds must contain each country at most once"
            raise ValueError(msg)
        for code, minimum in self.country_thresholds:
            if len(code) != 2 or not code.isalpha() or not code.isupper():
                msg = f"country_thresholds key must be an ISO alpha-2 code; got {code!r}"
                raise ValueError(msg)
            if minimum < 1:
                # Zero would publish channels with no represented videos at
                # all, which is not a weaker threshold but a different and
                # meaningless claim.
                msg = f"country_thresholds value must be >= 1; got {minimum}"
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
