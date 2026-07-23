"""Tests for the versioned disclosure-policy engine.

Requirement refs: 7.1-7.4, 7.7, 7.8
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from creator_map_pipeline.aggregate.disclosure import (
    CreatorCandidate,
    DisclosureEngine,
    DisclosureOutcome,
    PolicyNotApproved,
    public_channel_key,
)
from creator_map_schemas import (
    DisclosurePolicy,
    SuppressionKind,
    SuppressionRecord,
    SuppressionScope,
)
from pydantic import ValidationError

INSTANT = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
SECRET = "restricted-secret-value"
RAW_CHANNEL = "UC_x5XG1OV2P6uZZ5FSM9Ttw"


def policy(**overrides: object) -> DisclosurePolicy:
    fields: dict[str, object] = {
        "policy_id": "disclosure",
        "version": "1.0.0",
        "approved_at": INSTANT,
        "min_represented_video_count": 3,
        "allowed_fields": ("display_name", "represented_video_count"),
    }
    fields.update(overrides)
    return DisclosurePolicy.model_validate(fields)


def candidate(**overrides: object) -> CreatorCandidate:
    fields: dict[str, object] = {
        "channel_id": RAW_CHANNEL,
        "display_name": "Example Channel",
        "represented_video_count": 10,
        "country": "US",
    }
    fields.update(overrides)
    return CreatorCandidate(**fields)  # type: ignore[arg-type]


def suppression(**overrides: object) -> SuppressionRecord:
    fields: dict[str, object] = {
        "record_id": "sup-1",
        "channel_id": RAW_CHANNEL,
        "kind": SuppressionKind.OPT_OUT,
        "scope": SuppressionScope.FULL,
        "recorded_at": INSTANT,
        "restricted_reason": "creator opt-out",
    }
    fields.update(overrides)
    return SuppressionRecord.model_validate(fields)


# --- Requirement 7.1: fail closed without an approved policy -------------


def test_absent_policy_refuses_to_publish() -> None:
    with pytest.raises(PolicyNotApproved, match="no disclosure policy"):
        DisclosureEngine(None)


def test_policy_without_allowed_fields_refuses_to_publish() -> None:
    """A policy naming no publishable fields cannot govern publication."""
    # The schema forbids an empty tuple, so this is constructed by bypassing
    # the field constraint the same way a corrupt stored policy would.
    broken = policy().model_copy(update={"allowed_fields": ()})
    with pytest.raises(PolicyNotApproved, match="no allowed fields"):
        DisclosureEngine(broken)


# --- Requirement 7.2: public keys are distinct from source identifiers ---


def test_public_key_is_not_the_raw_channel_id() -> None:
    key = public_channel_key(RAW_CHANNEL, secret=SECRET)

    assert key != RAW_CHANNEL
    assert RAW_CHANNEL not in key
    assert key.startswith("pk_")


def test_public_key_is_stable_for_the_same_channel() -> None:
    """A release must address the same creator by the same key."""
    first = public_channel_key(RAW_CHANNEL, secret=SECRET)
    second = public_channel_key(RAW_CHANNEL, secret=SECRET)
    assert first == second


def test_public_key_differs_per_channel() -> None:
    assert public_channel_key("UC_a", secret=SECRET) != public_channel_key("UC_b", secret=SECRET)


def test_public_key_depends_on_the_secret() -> None:
    """Without the restricted secret a key cannot be reproduced.

    A plain digest of a 24-character channel ID would be reversible by
    enumeration; keying it is what makes the public key genuinely distinct
    rather than a re-encoding.
    """
    assert public_channel_key(RAW_CHANNEL, secret="secret-a") != public_channel_key(
        RAW_CHANNEL, secret="secret-b"
    )


def test_public_key_requires_a_secret() -> None:
    with pytest.raises(ValueError, match="non-empty secret"):
        public_channel_key(RAW_CHANNEL, secret="")


def test_public_key_matches_the_published_shape() -> None:
    """The artifact schema rejects anything else, so they must agree."""
    import re

    key = public_channel_key(RAW_CHANNEL, secret=SECRET)
    assert re.match(r"^pk_[A-Za-z0-9_-]{8,64}$", key)
    assert not key.startswith("pk_UC")


# --- Requirement 7.2: threshold and field rules --------------------------


def test_creator_meeting_every_condition_is_permitted() -> None:
    engine = DisclosureEngine(policy(), public_key_secret=SECRET)
    decision = engine.decide(candidate())

    assert decision.permitted
    assert decision.outcome is DisclosureOutcome.PERMITTED


def test_creator_below_threshold_is_excluded() -> None:
    engine = DisclosureEngine(policy(min_represented_video_count=5), public_key_secret=SECRET)
    decision = engine.decide(candidate(represented_video_count=4))

    assert not decision.permitted
    assert decision.outcome is DisclosureOutcome.BELOW_THRESHOLD


def test_threshold_boundary_is_inclusive() -> None:
    engine = DisclosureEngine(policy(min_represented_video_count=5), public_key_secret=SECRET)
    assert engine.decide(candidate(represented_video_count=5)).permitted


def test_creator_missing_a_required_field_is_withheld() -> None:
    """Requirement 7.7: uncertainty resolves to prohibited, not to a blank."""
    engine = DisclosureEngine(policy(), public_key_secret=SECRET)
    decision = engine.decide(candidate(display_name=None))

    assert not decision.permitted
    assert decision.outcome is DisclosureOutcome.MISSING_REQUIRED_FIELD


# --- Per-country thresholds ----------------------------------------------


def test_country_threshold_overrides_the_default() -> None:
    """A decision that differs by region has to live in the policy.

    Putting it in build-script conditionals would hide it from the policy
    version a release pins, and Requirement 7.1 makes that version the
    record of what governed publication.
    """
    engine = DisclosureEngine(
        policy(min_represented_video_count=5, country_thresholds=(("IE", 1), ("ZA", 1))),
        public_key_secret=SECRET,
    )

    # One video clears the override but not the default.
    assert engine.decide(candidate(represented_video_count=1, country="ZA")).permitted
    assert engine.decide(candidate(represented_video_count=1, country="IE")).permitted
    assert not engine.decide(candidate(represented_video_count=1, country="US")).permitted


def test_country_without_an_override_keeps_the_default() -> None:
    engine = DisclosureEngine(
        policy(min_represented_video_count=5, country_thresholds=(("ZA", 1),)),
        public_key_secret=SECRET,
    )
    assert not engine.decide(candidate(represented_video_count=4, country="GB")).permitted
    assert engine.decide(candidate(represented_video_count=5, country="GB")).permitted


def test_overrides_do_not_bypass_other_conditions() -> None:
    """A lowered threshold widens one condition, not all of them."""
    record = suppression()
    engine = DisclosureEngine(
        policy(country_thresholds=(("ZA", 1),)),
        suppressions=(record,),
        public_key_secret=SECRET,
    )
    decision = engine.decide(candidate(represented_video_count=99, country="ZA"))

    assert not decision.permitted
    assert decision.outcome is DisclosureOutcome.SUPPRESSED


def test_override_still_requires_a_display_name() -> None:
    engine = DisclosureEngine(policy(country_thresholds=(("ZA", 1),)), public_key_secret=SECRET)
    decision = engine.decide(candidate(represented_video_count=50, country="ZA", display_name=None))
    assert not decision.permitted


@pytest.mark.parametrize(
    "thresholds",
    [
        (("ZA", 1), ("IE", 1)),  # unsorted
        (("ZA", 1), ("ZA", 2)),  # duplicate
        (("zaf", 1),),  # not alpha-2
        (("za", 1),),  # not uppercase
        (("ZA", 0),),  # zero would publish channels with no videos
    ],
)
def test_malformed_overrides_are_rejected(thresholds: object) -> None:
    with pytest.raises(ValidationError):
        policy(country_thresholds=thresholds)


# --- Requirement 7.4: corrections, opt-outs, suppressions ----------------


def test_full_suppression_excludes_the_creator() -> None:
    engine = DisclosureEngine(policy(), suppressions=(suppression(),), public_key_secret=SECRET)
    decision = engine.decide(candidate())

    assert not decision.permitted
    assert decision.outcome is DisclosureOutcome.SUPPRESSED


def test_suppression_applies_before_the_threshold() -> None:
    """A suppressed creator is never evaluated on other grounds."""
    engine = DisclosureEngine(
        policy(min_represented_video_count=1000),
        suppressions=(suppression(),),
        public_key_secret=SECRET,
    )
    decision = engine.decide(candidate(represented_video_count=1))

    # Suppression, not the threshold, is the reported cause.
    assert decision.outcome is DisclosureOutcome.SUPPRESSED


def test_field_scoped_suppression_withholds_only_named_fields() -> None:
    record = suppression(
        record_id="sup-2",
        kind=SuppressionKind.CORRECTION,
        scope=SuppressionScope.FIELDS,
        suppressed_fields=("display_name",),
    )
    engine = DisclosureEngine(policy(), suppressions=(record,), public_key_secret=SECRET)
    decision = engine.decide(candidate())

    assert decision.permitted
    assert decision.withheld_fields == frozenset({"display_name"})


def test_suppression_of_another_channel_does_not_apply() -> None:
    record = suppression(channel_id="UC_someone_else")
    engine = DisclosureEngine(policy(), suppressions=(record,), public_key_secret=SECRET)

    assert engine.decide(candidate()).permitted


# --- Projection emits only allowed fields --------------------------------


def test_projection_emits_only_policy_allowed_fields() -> None:
    engine = DisclosureEngine(
        policy(allowed_fields=("represented_video_count",)), public_key_secret=SECRET
    )
    decision = engine.decide(candidate())
    projected = engine.project(candidate(), decision)

    assert set(projected) == {"represented_video_count"}
    assert "display_name" not in projected


def test_projection_omits_withheld_fields() -> None:
    record = suppression(
        record_id="sup-3",
        kind=SuppressionKind.CORRECTION,
        scope=SuppressionScope.FIELDS,
        suppressed_fields=("display_name",),
    )
    engine = DisclosureEngine(policy(), suppressions=(record,), public_key_secret=SECRET)
    decision = engine.decide(candidate())
    projected = engine.project(candidate(), decision)

    assert "display_name" not in projected
    assert "represented_video_count" in projected


def test_projection_never_emits_the_raw_channel_id() -> None:
    """Requirement 7.3: no creator-identifying source value is published."""
    engine = DisclosureEngine(policy(), public_key_secret=SECRET)
    decision = engine.decide(candidate())
    projected = engine.project(candidate(), decision)

    assert RAW_CHANNEL not in str(projected)
    assert "channel_id" not in projected


def test_projecting_a_refused_creator_raises() -> None:
    """Defence in depth: a refused creator cannot be projected by mistake."""
    engine = DisclosureEngine(policy(), suppressions=(suppression(),), public_key_secret=SECRET)
    decision = engine.decide(candidate())

    with pytest.raises(ValueError, match="failed disclosure"):
        engine.project(candidate(), decision)


# --- Requirement 7.8: the suppression reason stays internal --------------


def test_decision_carries_no_suppression_reason() -> None:
    record = suppression(restricted_reason="legal hold following complaint")
    engine = DisclosureEngine(policy(), suppressions=(record,), public_key_secret=SECRET)
    decision = engine.decide(candidate())

    rendered = repr(decision)
    assert "legal hold" not in rendered
    assert "complaint" not in rendered
