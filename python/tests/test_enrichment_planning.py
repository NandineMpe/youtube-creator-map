"""Tests for work planning, observation caching, and cutoff selection.

Requirement refs: 3.1-3.4, 3.9-3.12, 4.1, 4.2
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from creator_map_pipeline.enrichment import (
    ObservationStore,
    plan_channel_work,
    plan_video_work,
    select_observation,
)
from creator_map_pipeline.enrichment.resolver import (
    HuggingFaceChannelResolver,
    HuggingFaceDisplayNameResolver,
)
from creator_map_schemas import (
    ChannelResolution,
    ChannelResolutionStatus,
    EnrichmentPolicy,
    ErrorClass,
    FailureDisposition,
    ObservationTieBreaker,
    RetryPolicy,
    VideoResolution,
    VideoResolutionStatus,
)

INSTANT = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
DIGEST = "sha256:" + "a" * 64


def _dispositions() -> tuple[tuple[ErrorClass, FailureDisposition], ...]:
    mapping = {
        ErrorClass.RATE_LIMITED: FailureDisposition.RETRYABLE,
        ErrorClass.NETWORK: FailureDisposition.RETRYABLE,
        ErrorClass.SERVER: FailureDisposition.RETRYABLE,
        ErrorClass.TIMEOUT: FailureDisposition.RETRYABLE,
        ErrorClass.MALFORMED_RESPONSE: FailureDisposition.RETRYABLE,
        ErrorClass.NOT_FOUND: FailureDisposition.NON_RETRYABLE,
        ErrorClass.INVALID_REQUEST: FailureDisposition.NON_RETRYABLE,
        ErrorClass.INVALID_CREDENTIAL: FailureDisposition.OPERATOR_HALT,
        ErrorClass.POLICY_BLOCKED: FailureDisposition.OPERATOR_HALT,
    }
    return tuple(sorted(mapping.items()))


def policy(**overrides: object) -> EnrichmentPolicy:
    fields: dict[str, object] = {
        "policy_id": "enrich",
        "version": "1.0.0",
        "approved_at": INSTANT,
        "freshness_seconds": 86_400,
        "video_fields": ("id", "snippet.channelId"),
        "channel_fields": ("id", "snippet.country", "snippet.title"),
        "tie_breaker": ObservationTieBreaker.LATEST_OBSERVED_THEN_DIGEST,
        "retry_policy": RetryPolicy.model_validate(
            {
                "policy_id": "retry",
                "version": "1.0.0",
                "max_attempts": 5,
                "initial_delay_seconds": 1.0,
                "max_delay_seconds": 60.0,
                "backoff_multiplier": 2.0,
                "jitter_fraction": 0.1,
                "dispositions": _dispositions(),
            }
        ),
        "quota_reserve": 1000,
        "max_batch_size": 50,
    }
    fields.update(overrides)
    return EnrichmentPolicy.model_validate(fields)


def video_obs(
    video_id: str, *, at: datetime, channel: str | None = "UC_a", digest: str = DIGEST
) -> VideoResolution:
    if channel is None:
        return VideoResolution(
            video_id=video_id,
            status=VideoResolutionStatus.UNAVAILABLE_UNCLASSIFIED,
            observed_at=at,
            response_digest=digest,
        )
    return VideoResolution(
        video_id=video_id,
        status=VideoResolutionStatus.RESOLVED,
        channel_id=channel,
        observed_at=at,
        response_digest=digest,
    )


# --- Requirement 3.1 / 3.2: one work item per distinct entity ------------


def test_distinct_videos_yield_one_work_item_each() -> None:
    store = ObservationStore()
    plan = plan_video_work({"a", "b", "c"}, policy=policy(), cutoff=INSTANT, store=store)

    assert plan.total == 3
    assert len(set(plan.pending)) == len(plan.pending)


def test_video_in_many_datasets_is_planned_once() -> None:
    """Requirement 3.3: overlap does not multiply enrichment work."""
    store = ObservationStore()
    # The caller passes a set, so dataset overlap has already collapsed.
    shared = {"shared-video"}
    plan = plan_video_work(shared, policy=policy(), cutoff=INSTANT, store=store)

    assert plan.pending == ("shared-video",)
    assert plan.projected_requests == 1


def test_plan_ordering_is_deterministic() -> None:
    """A replanned run must produce identical batches."""
    store = ObservationStore()
    ids = {"zeta", "alpha", "mid", "beta"}

    first = plan_video_work(ids, policy=policy(), cutoff=INSTANT, store=store)
    second = plan_video_work(ids, policy=policy(), cutoff=INSTANT, store=store)

    assert first.pending == second.pending
    assert list(first.pending) == sorted(first.pending)


# --- Requirement 3.4: cached observations avoid duplicate requests -------


def test_fresh_cached_observation_is_not_re_requested() -> None:
    store = ObservationStore()
    store.append_video(video_obs("cached", at=INSTANT - timedelta(hours=1)), policy_version="1.0.0")

    plan = plan_video_work({"cached", "fresh-needed"}, policy=policy(), cutoff=INSTANT, store=store)

    assert plan.cached == ("cached",)
    assert plan.pending == ("fresh-needed",)
    assert plan.projected_requests == 1


def test_stale_observation_is_re_requested() -> None:
    """Beyond the freshness window the identity is planned again."""
    store = ObservationStore()
    store.append_video(video_obs("stale", at=INSTANT - timedelta(days=30)), policy_version="1.0.0")

    plan = plan_video_work({"stale"}, policy=policy(), cutoff=INSTANT, store=store)

    assert plan.pending == ("stale",)
    assert plan.cached == ()


def test_observation_under_another_policy_version_does_not_satisfy() -> None:
    """Requirement 3.1: work identity includes the policy version."""
    store = ObservationStore()
    store.append_video(video_obs("v", at=INSTANT), policy_version="0.9.0")

    plan = plan_video_work({"v"}, policy=policy(), cutoff=INSTANT, store=store)

    assert plan.pending == ("v",)


# --- Requirement 4.2: batches are bounded --------------------------------


def test_batches_respect_the_maximum() -> None:
    store = ObservationStore()
    ids = {f"video-{i:03d}" for i in range(120)}
    plan = plan_video_work(ids, policy=policy(), cutoff=INSTANT, store=store)

    batches = plan.batches(50)

    assert len(batches) == 3
    assert [len(b) for b in batches] == [50, 50, 20]
    assert sum(len(b) for b in batches) == 120
    # Every identity appears exactly once across batches.
    flattened = [entity for batch in batches for entity in batch]
    assert len(set(flattened)) == 120


def test_batch_size_must_be_positive() -> None:
    store = ObservationStore()
    plan = plan_video_work({"a"}, policy=policy(), cutoff=INSTANT, store=store)
    with pytest.raises(ValueError, match="batch size must be >= 1"):
        plan.batches(0)


# --- Requirement 3.9: observations are append-only -----------------------


def test_appending_never_replaces() -> None:
    store = ObservationStore()
    store.append_video(video_obs("v", at=INSTANT, channel="UC_a"), policy_version="1.0.0")
    store.append_video(
        video_obs("v", at=INSTANT + timedelta(hours=1), channel="UC_b"),
        policy_version="1.0.0",
    )

    candidates = store.video_candidates("v", policy_version="1.0.0")
    assert len(candidates) == 2
    assert {c.channel_id for c in candidates} == {"UC_a", "UC_b"}


# --- Requirement 3.10-3.12: cutoff and deterministic selection -----------


def test_observations_after_the_cutoff_are_excluded() -> None:
    store = ObservationStore()
    store.append_video(
        video_obs("v", at=INSTANT - timedelta(hours=1), channel="UC_old"), policy_version="1.0.0"
    )
    store.append_video(
        video_obs("v", at=INSTANT + timedelta(hours=1), channel="UC_new"), policy_version="1.0.0"
    )

    selected = store.select_video("v", policy=policy(), cutoff=INSTANT)

    assert selected is not None
    assert selected.observation.channel_id == "UC_old"
    assert selected.candidate_count == 1


def test_latest_eligible_observation_wins() -> None:
    store = ObservationStore()
    for hours, channel in ((3, "UC_older"), (1, "UC_newer")):
        store.append_video(
            video_obs("v", at=INSTANT - timedelta(hours=hours), channel=channel),
            policy_version="1.0.0",
        )

    selected = store.select_video("v", policy=policy(), cutoff=INSTANT)
    assert selected is not None
    assert selected.observation.channel_id == "UC_newer"


def test_earliest_tie_breaker_is_honoured() -> None:
    store = ObservationStore()
    for hours, channel in ((3, "UC_older"), (1, "UC_newer")):
        store.append_video(
            video_obs("v", at=INSTANT - timedelta(hours=hours), channel=channel),
            policy_version="1.0.0",
        )

    selected = store.select_video(
        "v",
        policy=policy(tie_breaker=ObservationTieBreaker.EARLIEST_OBSERVED_THEN_DIGEST),
        cutoff=INSTANT,
    )
    assert selected is not None
    assert selected.observation.channel_id == "UC_older"


def test_identical_timestamps_break_on_digest() -> None:
    """Requirement 3.11: selection is total, never insertion-order dependent."""
    store = ObservationStore()
    store.append_video(
        video_obs("v", at=INSTANT, channel="UC_a", digest="sha256:" + "1" * 64),
        policy_version="1.0.0",
    )
    store.append_video(
        video_obs("v", at=INSTANT, channel="UC_b", digest="sha256:" + "2" * 64),
        policy_version="1.0.0",
    )

    first = store.select_video("v", policy=policy(), cutoff=INSTANT)

    # Reverse insertion order in a fresh store; selection must not change.
    other = ObservationStore()
    other.append_video(
        video_obs("v", at=INSTANT, channel="UC_b", digest="sha256:" + "2" * 64),
        policy_version="1.0.0",
    )
    other.append_video(
        video_obs("v", at=INSTANT, channel="UC_a", digest="sha256:" + "1" * 64),
        policy_version="1.0.0",
    )
    second = other.select_video("v", policy=policy(), cutoff=INSTANT)

    assert first is not None and second is not None
    assert first.observation.channel_id == second.observation.channel_id


def test_selection_is_reproducible_for_fixed_inputs() -> None:
    """Requirement 3.12: same inputs, policy, and cutoff -> same selection."""
    store = ObservationStore()
    for hours in range(5):
        store.append_video(
            video_obs("v", at=INSTANT - timedelta(hours=hours), channel=f"UC_{hours}"),
            policy_version="1.0.0",
        )

    results = {
        store.select_video("v", policy=policy(), cutoff=INSTANT).observation.channel_id  # type: ignore[union-attr]
        for _ in range(10)
    }
    assert len(results) == 1


def test_no_eligible_observation_returns_none() -> None:
    store = ObservationStore()
    assert store.select_video("absent", policy=policy(), cutoff=INSTANT) is None


def test_select_observation_on_empty_candidates() -> None:
    assert select_observation("e", [], policy=policy(), cutoff=INSTANT) is None


# --- Snapshot-derived resolvers ------------------------------------------


def test_hugging_face_resolver_produces_real_observations() -> None:
    resolver = HuggingFaceChannelResolver(
        {"vid-a": "UC_alpha", "vid-b": "UC_beta"}, snapshot_digest=DIGEST
    )

    result = resolver.resolve_videos(("vid-a", "vid-b"), observed_at=INSTANT)

    assert len(result.observations) == 2
    assert result.observations[0].channel_id == "UC_alpha"
    assert result.observations[0].status is VideoResolutionStatus.RESOLVED
    # Snapshot-derived observations consume no API quota.
    assert result.quota_units == 0


def test_hugging_face_resolver_marks_missing_as_unavailable() -> None:
    """Requirement 3.6: absence is unclassified, never a finer guess."""
    resolver = HuggingFaceChannelResolver({"known": "UC_a"}, snapshot_digest=DIGEST)

    result = resolver.resolve_videos(("known", "missing"), observed_at=INSTANT)

    statuses = {o.video_id: o.status for o in result.observations}
    assert statuses["missing"] is VideoResolutionStatus.UNAVAILABLE_UNCLASSIFIED
    # An unavailable observation carries no channel attribution.
    missing = next(o for o in result.observations if o.video_id == "missing")
    assert missing.channel_id is None


def test_hugging_face_resolver_returns_one_observation_per_request() -> None:
    """Every requested identity must land in exactly one partition state."""
    resolver = HuggingFaceChannelResolver({}, snapshot_digest=DIGEST)
    requested = ("a", "b", "c", "d")

    result = resolver.resolve_videos(requested, observed_at=INSTANT)

    assert tuple(o.video_id for o in result.observations) == requested


def test_display_name_resolver_never_supplies_a_country() -> None:
    """Invariant 6: the snapshot has no country, so none is invented."""
    resolver = HuggingFaceDisplayNameResolver({"UC_a": "Example Channel"}, snapshot_digest=DIGEST)

    result = resolver.resolve_channels(("UC_a",), observed_at=INSTANT)

    observation = result.observations[0]
    assert observation.status is ChannelResolutionStatus.RESOLVED
    assert observation.display_name == "Example Channel"
    assert observation.declared_country is None


def test_channel_planning_uses_cache() -> None:
    store = ObservationStore()
    store.append_channel(
        ChannelResolution(
            channel_id="UC_cached",
            status=ChannelResolutionStatus.RESOLVED,
            display_name="Cached",
            observed_at=INSTANT - timedelta(hours=1),
            response_digest=DIGEST,
        ),
        policy_version="1.0.0",
    )

    plan = plan_channel_work({"UC_cached", "UC_new"}, policy=policy(), cutoff=INSTANT, store=store)

    assert plan.cached == ("UC_cached",)
    assert plan.pending == ("UC_new",)
