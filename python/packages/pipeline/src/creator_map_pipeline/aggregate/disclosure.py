"""The versioned disclosure-policy engine.

Requirement 7.1 makes this fail closed: an absent, unapproved, or incomplete
policy rejects every release candidate rather than falling back to a
permissive default. Requirement 7.7 extends that to individual decisions —
if the engine cannot determine that a creator or field is permitted, it is
prohibited.

Requirement 7.8 keeps the suppression reason internal: a suppressed creator
is simply absent from public artifacts, with no marker explaining why, since
the explanation would itself be disclosure.

Requirement refs: 7.1-7.4, 7.7, 7.8
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from enum import StrEnum, unique

from creator_map_schemas import (
    DisclosurePolicy,
    SuppressionRecord,
    SuppressionScope,
)


class PolicyNotApproved(RuntimeError):
    """Raised when no usable disclosure policy governs a build."""


@unique
class DisclosureOutcome(StrEnum):
    """Why a creator was or was not published.

    These are internal diagnostics. Requirement 7.8 prohibits exposing them
    on any public surface.
    """

    PERMITTED = "permitted"
    BELOW_THRESHOLD = "below_minimum_video_count"
    SUPPRESSED = "suppressed_by_record"
    MISSING_REQUIRED_FIELD = "missing_required_field"
    INDETERMINATE = "indeterminate"


@dataclass(frozen=True, slots=True)
class DisclosureDecision:
    """The engine's verdict for one creator."""

    permitted: bool
    outcome: DisclosureOutcome
    #: Fields withheld even when the creator is otherwise permitted.
    withheld_fields: frozenset[str] = frozenset()


def public_channel_key(channel_id: str, *, secret: str) -> str:
    """Derive a disclosure-approved public key for a channel.

    Requirement 7.2 requires this to be distinct from the raw source
    identifier. A keyed HMAC rather than a plain hash: a bare digest of a
    24-character channel ID is trivially reversible by enumerating known
    channels, which would make the "distinct" key a re-encoding of the very
    identifier it is meant to replace.

    The secret is held in the restricted environment and never published, so
    a public key cannot be inverted without it.
    """
    if not secret:
        # Failing closed rather than silently deriving a reversible key.
        msg = "a non-empty secret is required to derive a public channel key"
        raise ValueError(msg)
    digest = hmac.new(
        secret.encode("utf-8"), channel_id.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return f"pk_{digest[:32]}"


@dataclass(frozen=True, slots=True)
class CreatorCandidate:
    """A creator being considered for publication."""

    channel_id: str
    display_name: str | None
    represented_video_count: int


class DisclosureEngine:
    """Applies a release-pinned disclosure policy to creator candidates."""

    def __init__(
        self,
        policy: DisclosurePolicy | None,
        *,
        suppressions: tuple[SuppressionRecord, ...] = (),
        public_key_secret: str = "",
    ) -> None:
        # Requirement 7.1: absent or unapproved policy rejects everything.
        if policy is None:
            msg = "no disclosure policy is configured; refusing to publish"
            raise PolicyNotApproved(msg)
        if not policy.allowed_fields:
            msg = "disclosure policy names no allowed fields; refusing to publish"
            raise PolicyNotApproved(msg)

        self._policy = policy
        self._secret = public_key_secret
        # Index active suppressions by channel for constant-time lookup.
        self._suppressions: dict[str, list[SuppressionRecord]] = {}
        for record in suppressions:
            self._suppressions.setdefault(record.channel_id, []).append(record)

    @property
    def policy(self) -> DisclosurePolicy:
        return self._policy

    @property
    def allowed_fields(self) -> frozenset[str]:
        return frozenset(self._policy.allowed_fields)

    def decide(self, candidate: CreatorCandidate) -> DisclosureDecision:
        """Return whether and how a creator may be published."""
        # Requirement 7.4: exclusion records apply before anything else, so
        # a suppressed creator is never evaluated on other grounds.
        records = self._suppressions.get(candidate.channel_id, [])
        full_scope = [r for r in records if r.scope is SuppressionScope.FULL]
        if full_scope:
            return DisclosureDecision(
                permitted=False, outcome=DisclosureOutcome.SUPPRESSED
            )

        if candidate.represented_video_count < self._policy.min_represented_video_count:
            return DisclosureDecision(
                permitted=False, outcome=DisclosureOutcome.BELOW_THRESHOLD
            )

        # A creator with no display name cannot satisfy a policy that
        # requires one. Requirement 7.7: rather than publishing a blank or
        # inventing a placeholder, the creator is withheld.
        if "display_name" in self.allowed_fields and not candidate.display_name:
            return DisclosureDecision(
                permitted=False, outcome=DisclosureOutcome.MISSING_REQUIRED_FIELD
            )

        withheld = {
            field
            for record in records
            if record.scope is SuppressionScope.FIELDS
            for field in record.suppressed_fields
        }

        return DisclosureDecision(
            permitted=True,
            outcome=DisclosureOutcome.PERMITTED,
            withheld_fields=frozenset(withheld),
        )

    def public_key_for(self, channel_id: str) -> str:
        """Derive the public key for a permitted creator."""
        return public_channel_key(channel_id, secret=self._secret)

    def project(
        self, candidate: CreatorCandidate, decision: DisclosureDecision
    ) -> dict[str, object]:
        """Build the publishable field set for a permitted creator.

        Only fields the policy allows and no suppression withholds are
        emitted. A caller cannot widen this by passing extra data because
        the projection is built from the allowlist, not filtered against a
        denylist.
        """
        if not decision.permitted:
            msg = "refusing to project a creator that failed disclosure"
            raise ValueError(msg)

        available: dict[str, object] = {
            "display_name": candidate.display_name,
            "represented_video_count": candidate.represented_video_count,
        }

        return {
            name: value
            for name, value in available.items()
            if name in self.allowed_fields and name not in decision.withheld_fields
        }
