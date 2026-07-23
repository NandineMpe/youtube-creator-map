"""Unit tests for the versioned domain contracts.

Covers required-field failures, deterministic serialization, and the
fail-closed validators that encode acceptance criteria from requirements
1.x, 2.x, 3.x, 4.x, 6.x, and 7.x.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from creator_map_schemas import (
    UNKNOWN_COUNTRY,
    AccessStatus,
    ChannelResolution,
    ChannelResolutionStatus,
    CorpusClass,
    CountrySummary,
    CoverageSummary,
    CreatorSummary,
    DatasetContract,
    DatasetSnapshotRef,
    DisclosurePolicy,
    EnrichmentPolicy,
    EntityKind,
    ErrorClass,
    FailureDisposition,
    Filter,
    NormalizedOccurrence,
    ObservationTieBreaker,
    OccurrenceUnit,
    QuotaLedger,
    ReleaseManifest,
    RetryPolicy,
    SourceKind,
    SuppressionKind,
    SuppressionRecord,
    SuppressionScope,
    VideoResolution,
    VideoResolutionPartition,
    VideoResolutionPartitionState,
    VideoResolutionStatus,
    WorkItem,
    WorkItemState,
)
from pydantic import ValidationError

INSTANT = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)


def _full_dispositions() -> tuple[tuple[ErrorClass, FailureDisposition], ...]:
    """Build a total, sorted ErrorClass -> FailureDisposition mapping."""
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


def _retry_policy(**overrides: object) -> RetryPolicy:
    fields: dict[str, object] = {
        "policy_id": "retry",
        "version": "1.0.0",
        "max_attempts": 5,
        "initial_delay_seconds": 1.0,
        "max_delay_seconds": 60.0,
        "backoff_multiplier": 2.0,
        "jitter_fraction": 0.1,
        "dispositions": _full_dispositions(),
    }
    fields.update(overrides)
    return RetryPolicy.model_validate(fields)


def _enrichment_policy(**overrides: object) -> EnrichmentPolicy:
    fields: dict[str, object] = {
        "policy_id": "enrich",
        "version": "1.0.0",
        "approved_at": INSTANT,
        "freshness_seconds": 86_400,
        "video_fields": ("id", "snippet.channelId"),
        "channel_fields": ("id", "snippet.country", "snippet.title"),
        "tie_breaker": ObservationTieBreaker.LATEST_OBSERVED_THEN_DIGEST,
        "retry_policy": _retry_policy(),
        "quota_reserve": 1_000,
        "max_batch_size": 50,
    }
    fields.update(overrides)
    return EnrichmentPolicy.model_validate(fields)


def _dataset_contract(**overrides: object) -> DatasetContract:
    fields: dict[str, object] = {
        "id": "panda-70m",
        "display_name": "Panda-70M",
        "version": "2024.1",
        "corpus_class": CorpusClass.CANDIDATE,
        "source_kind": SourceKind.METADATA_ONLY,
        "access_status": AccessStatus.APPROVED,
        "snapshot_digest": "sha256:abc",
        "adapter_version": "1.0.0",
        "occurrence_unit": OccurrenceUnit.CLIP,
        "source_citation": "https://example.invalid/panda70m",
        "terms_review_id": "review-001",
    }
    fields.update(overrides)
    return DatasetContract.model_validate(fields)


def _occurrence(**overrides: object) -> NormalizedOccurrence:
    fields: dict[str, object] = {
        "dataset_id": "panda-70m",
        "snapshot_digest": "sha256:abc",
        "source_locator": "shard-0:row-17",
        "video_id": "dQw4w9WgXcQ",
        "occurrence_unit": OccurrenceUnit.CLIP,
        "extracted_at": INSTANT,
        "adapter_version": "1.0.0",
    }
    fields.update(overrides)
    return NormalizedOccurrence.model_validate(fields)


# --- Immutability and deterministic serialization -------------------------


def test_domain_models_are_frozen() -> None:
    contract = _dataset_contract()
    with pytest.raises(ValidationError):
        contract.version = "2024.2"  # type: ignore[misc]


def test_deterministic_json_sorts_keys_and_is_stable() -> None:
    contract = _dataset_contract()
    first = contract.to_deterministic_json()
    second = _dataset_contract().to_deterministic_json()

    assert first == second
    # Keys are emitted in sorted order at the top level.
    assert first.index('"access_status"') < first.index('"adapter_version"')
    assert first.index('"source_citation"') < first.index('"terms_review_id"')
    # Compact separators leave no incidental whitespace.
    assert ", " not in first


def test_deterministic_json_bytes_round_trip() -> None:
    contract = _dataset_contract()
    restored = DatasetContract.from_json(contract.to_deterministic_json_bytes())
    assert restored == contract


def test_extra_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        _dataset_contract(unexpected_field="value")


@pytest.mark.parametrize(
    "missing",
    ["id", "version", "snapshot_digest", "terms_review_id", "source_citation"],
)
def test_dataset_contract_requires_every_provenance_field(missing: str) -> None:
    """Requirement 1.2/1.5: incomplete contracts fail closed."""
    fields = _dataset_contract().model_dump(mode="json")
    del fields[missing]
    with pytest.raises(ValidationError):
        DatasetContract.model_validate(fields)


# --- Occurrence clip bounds (Requirement 2.9, 2.10) -----------------------


def test_occurrence_accepts_valid_clip_bounds() -> None:
    occurrence = _occurrence(clip_start=0.0, clip_end=12.5)
    assert occurrence.clip_start == 0.0
    assert occurrence.clip_end == 12.5


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (-1.0, 5.0),  # start below zero
        (5.0, 5.0),  # start not strictly less than end
        (9.0, 2.0),  # inverted bounds
    ],
)
def test_occurrence_rejects_invalid_clip_bounds(start: float, end: float) -> None:
    """Requirement 2.9: bounds are valid only when 0 <= start < end."""
    with pytest.raises(ValidationError):
        _occurrence(clip_start=start, clip_end=end)


@pytest.mark.parametrize(
    ("start", "end"),
    [(3.0, None), (None, 3.0)],
)
def test_occurrence_rejects_half_present_clip_bounds(
    start: float | None, end: float | None
) -> None:
    with pytest.raises(ValidationError):
        _occurrence(clip_start=start, clip_end=end)


@pytest.mark.parametrize(
    "field",
    ["dataset_id", "snapshot_digest", "source_locator", "adapter_version"],
)
def test_occurrence_requires_mandatory_provenance(field: str) -> None:
    """Requirement 2.7: provenance fields are mandatory for acceptance."""
    fields = _occurrence().model_dump(mode="json")
    del fields[field]
    with pytest.raises(ValidationError):
        NormalizedOccurrence.model_validate(fields)


# --- Resolution observations (Requirement 3.5-3.8) ------------------------


def test_resolved_video_requires_channel() -> None:
    with pytest.raises(ValidationError):
        VideoResolution.model_validate(
            {
                "video_id": "dQw4w9WgXcQ",
                "status": VideoResolutionStatus.RESOLVED,
                "observed_at": INSTANT,
            }
        )


def test_unavailable_video_must_not_carry_channel() -> None:
    """Requirement 3.6: an omitted ID yields no channel attribution."""
    with pytest.raises(ValidationError):
        VideoResolution.model_validate(
            {
                "video_id": "dQw4w9WgXcQ",
                "status": VideoResolutionStatus.UNAVAILABLE_UNCLASSIFIED,
                "channel_id": "UC_x5XG1OV2P6uZZ5FSM9Ttw",
                "observed_at": INSTANT,
            }
        )


def test_channel_resolution_allows_absent_country() -> None:
    """Requirement 3.8: a resolved channel may carry no declared country."""
    resolution = ChannelResolution.model_validate(
        {
            "channel_id": "UC_x5XG1OV2P6uZZ5FSM9Ttw",
            "status": ChannelResolutionStatus.RESOLVED,
            "display_name": "Example",
            "observed_at": INSTANT,
        }
    )
    assert resolution.declared_country is None


def test_channel_resolution_rejects_unsupported_country_code() -> None:
    """Requirement 3.8: unsupported codes are never coerced to a country."""
    with pytest.raises(ValidationError):
        ChannelResolution.model_validate(
            {
                "channel_id": "UC_x5XG1OV2P6uZZ5FSM9Ttw",
                "status": ChannelResolutionStatus.RESOLVED,
                "declared_country": "ZZZ",
                "observed_at": INSTANT,
            }
        )


def test_unavailable_channel_carries_no_metadata() -> None:
    with pytest.raises(ValidationError):
        ChannelResolution.model_validate(
            {
                "channel_id": "UC_x5XG1OV2P6uZZ5FSM9Ttw",
                "status": ChannelResolutionStatus.UNAVAILABLE_UNCLASSIFIED,
                "display_name": "Example",
                "observed_at": INSTANT,
            }
        )


# --- Enrichment and retry policy (Requirement 3.10-3.12, 4.8-4.11) -------


def test_retry_policy_requires_total_disposition_mapping() -> None:
    """A partial mapping leaves a failure with no defined transition."""
    partial = tuple(sorted({ErrorClass.NETWORK: FailureDisposition.RETRYABLE}.items()))
    with pytest.raises(ValidationError, match="every ErrorClass"):
        _retry_policy(dispositions=partial)


def test_retry_policy_classifies_credentials_as_operator_halt() -> None:
    """Requirement 4.11: invalid credentials halt rather than retry."""
    policy = _retry_policy()
    assert policy.disposition_for(ErrorClass.INVALID_CREDENTIAL) is FailureDisposition.OPERATOR_HALT
    assert policy.disposition_for(ErrorClass.RATE_LIMITED) is FailureDisposition.RETRYABLE
    assert policy.disposition_for(ErrorClass.NOT_FOUND) is FailureDisposition.NON_RETRYABLE


def test_retry_policy_backoff_grows_and_clamps() -> None:
    """Requirement 4.8: bounded exponential backoff."""
    policy = _retry_policy(initial_delay_seconds=1.0, max_delay_seconds=10.0)
    assert policy.delay_for_attempt(1) == 1.0
    assert policy.delay_for_attempt(2) == 2.0
    assert policy.delay_for_attempt(3) == 4.0
    # Clamped at the configured bound rather than growing without limit.
    assert policy.delay_for_attempt(9) == 10.0


def test_retry_policy_rejects_attempt_below_one() -> None:
    with pytest.raises(ValueError, match="attempt must be >= 1"):
        _retry_policy().delay_for_attempt(0)


def test_retry_policy_rejects_initial_delay_above_bound() -> None:
    with pytest.raises(ValidationError):
        _retry_policy(initial_delay_seconds=90.0, max_delay_seconds=60.0)


def test_enrichment_policy_caps_batch_at_api_maximum() -> None:
    """Requirement 4.2: no more than 50 items per metadata batch."""
    with pytest.raises(ValidationError):
        _enrichment_policy(max_batch_size=51)


def test_enrichment_policy_requires_sorted_unique_fields() -> None:
    with pytest.raises(ValidationError, match="video_fields must be sorted"):
        _enrichment_policy(video_fields=("snippet.channelId", "id"))
    with pytest.raises(ValidationError, match="channel_fields must be unique"):
        _enrichment_policy(channel_fields=("id", "id"))


def test_enrichment_freshness_excludes_observations_after_cutoff() -> None:
    """Requirement 3.10: observations later than the cutoff are ineligible."""
    policy = _enrichment_policy(freshness_seconds=86_400)
    cutoff = INSTANT
    assert policy.is_fresh(cutoff - timedelta(hours=1), cutoff) is True
    assert policy.is_fresh(cutoff, cutoff) is True
    assert policy.is_fresh(cutoff + timedelta(seconds=1), cutoff) is False


def test_enrichment_freshness_excludes_stale_observations() -> None:
    """Requirement 3.4: only observations inside the window are reused."""
    policy = _enrichment_policy(freshness_seconds=3_600)
    cutoff = INSTANT
    assert policy.is_fresh(cutoff - timedelta(minutes=59), cutoff) is True
    assert policy.is_fresh(cutoff - timedelta(hours=2), cutoff) is False


# --- Work items (Requirement 4.1) ----------------------------------------


def test_leased_work_item_requires_expiry() -> None:
    with pytest.raises(ValidationError):
        WorkItem.model_validate(
            {
                "job_id": "job-1",
                "entity_kind": EntityKind.VIDEO,
                "entity_id": "dQw4w9WgXcQ",
                "state": WorkItemState.LEASED,
                "attempts": 1,
                "next_attempt_at": INSTANT,
            }
        )


def test_terminal_work_item_requires_error_class() -> None:
    """Requirement 4.9/4.10: terminal states record the final error class."""
    with pytest.raises(ValidationError):
        WorkItem.model_validate(
            {
                "job_id": "job-1",
                "entity_kind": EntityKind.VIDEO,
                "entity_id": "dQw4w9WgXcQ",
                "state": WorkItemState.TERMINAL_FAILURE,
                "attempts": 5,
                "next_attempt_at": INSTANT,
            }
        )


def test_quota_ledger_rejects_negative_usage() -> None:
    with pytest.raises(ValidationError):
        QuotaLedger.model_validate(
            {
                "date": date(2026, 1, 15),
                "operation": "videos.list",
                "requests": -1,
                "estimated_units": 0,
            }
        )


# --- Coverage partition (Requirement 6.2-6.4) ----------------------------


def test_partition_counts_must_sum_to_distinct_input_videos() -> None:
    partition = VideoResolutionPartition.model_validate(
        {
            "distinct_input_video_count": 10,
            "resolved_count": 6,
            "unavailable_unclassified_count": 2,
            "retryable_or_pending_count": 1,
            "invalid_count": 1,
            "terminal_failure_count": 0,
        }
    )
    assert partition.count_for(VideoResolutionPartitionState.RESOLVED) == 6
    assert partition.count_for(VideoResolutionPartitionState.INVALID) == 1


def test_partition_rejects_non_reconciling_counts() -> None:
    """Requirement 6.4: a drifted partition fails closed."""
    with pytest.raises(ValidationError, match="must sum to distinct_input_video_count"):
        VideoResolutionPartition.model_validate(
            {
                "distinct_input_video_count": 10,
                "resolved_count": 6,
                "unavailable_unclassified_count": 2,
                "retryable_or_pending_count": 1,
                "invalid_count": 0,
                "terminal_failure_count": 0,
            }
        )


def test_partition_covers_every_declared_state() -> None:
    """Every enum state is addressable, so no video can fall outside."""
    partition = VideoResolutionPartition.model_validate(
        {
            "distinct_input_video_count": 5,
            "resolved_count": 1,
            "unavailable_unclassified_count": 1,
            "retryable_or_pending_count": 1,
            "invalid_count": 1,
            "terminal_failure_count": 1,
        }
    )
    total = sum(partition.count_for(state) for state in VideoResolutionPartitionState)
    assert total == partition.distinct_input_video_count


def test_coverage_summary_reconciles_channel_partition() -> None:
    """Requirement 6.5: known + unknown channels equal resolved channels."""
    with pytest.raises(ValidationError, match="must equal resolved_channel_count"):
        CoverageSummary.model_validate(
            {
                "input_occurrence_count": 100,
                "distinct_input_video_count": 80,
                "resolved_video_count": 70,
                "unavailable_video_count": 10,
                "resolved_channel_count": 20,
                "known_country_channel_count": 12,
                "unknown_country_channel_count": 5,
            }
        )


# --- Filters and aggregates (Requirement 5.x) -----------------------------


def test_filter_requires_sorted_unique_members() -> None:
    """Requirement 11.2: equivalent states serialize identically."""
    with pytest.raises(ValidationError, match="datasets must be sorted"):
        Filter.model_validate({"datasets": ("b", "a"), "corpus_classes": (CorpusClass.CANDIDATE,)})
    with pytest.raises(ValidationError, match="datasets must contain unique"):
        Filter.model_validate({"datasets": ("a", "a"), "corpus_classes": (CorpusClass.CANDIDATE,)})


def test_filter_rejects_empty_selection() -> None:
    with pytest.raises(ValidationError):
        Filter.model_validate({"datasets": (), "corpus_classes": (CorpusClass.CANDIDATE,)})


def test_country_summary_accepts_unknown_bucket() -> None:
    """Requirement 5.7: unknown country is a first-class bucket."""
    summary = CountrySummary.model_validate(
        {
            "country": UNKNOWN_COUNTRY,
            "creator_count": 3,
            "represented_video_count": 9,
            "source_occurrence_count": 14,
            "resolved_video_count": 9,
            "unavailable_video_count": 0,
        }
    )
    assert summary.country == UNKNOWN_COUNTRY


def test_creator_summary_requires_sorted_dataset_breakdown() -> None:
    with pytest.raises(ValidationError, match="sorted by dataset key"):
        CreatorSummary.model_validate(
            {
                "channel_id": "pk_abc",
                "display_name": "Example",
                "country": "DE",
                "represented_video_count": 4,
                "dataset_breakdown": (("panda-70m", 3), ("howto100m", 2)),
                "last_observed_at": date(2026, 1, 15),
            }
        )


# --- Suppression records (Requirement 7.4, 7.8) --------------------------


def test_field_scoped_suppression_requires_fields() -> None:
    with pytest.raises(ValidationError, match="suppressed_fields is required"):
        SuppressionRecord.model_validate(
            {
                "record_id": "sup-1",
                "channel_id": "UC_x5XG1OV2P6uZZ5FSM9Ttw",
                "kind": SuppressionKind.CORRECTION,
                "scope": SuppressionScope.FIELDS,
                "recorded_at": INSTANT,
                "restricted_reason": "creator correction request",
            }
        )


def test_full_scope_suppression_rejects_named_fields() -> None:
    with pytest.raises(ValidationError, match="must be empty when scope is Full"):
        SuppressionRecord.model_validate(
            {
                "record_id": "sup-2",
                "channel_id": "UC_x5XG1OV2P6uZZ5FSM9Ttw",
                "kind": SuppressionKind.OPT_OUT,
                "scope": SuppressionScope.FULL,
                "suppressed_fields": ("display_name",),
                "recorded_at": INSTANT,
                "restricted_reason": "opt-out",
            }
        )


def test_suppression_reason_is_not_a_public_field_name() -> None:
    """Requirement 7.8: the reason is restricted, so it is named as such.

    The public artifact serializer allowlists fields by name; prefixing the
    reason with `restricted_` keeps it from being mistaken for a public
    field if a future serializer is written by field enumeration.
    """
    record = SuppressionRecord.model_validate(
        {
            "record_id": "sup-3",
            "channel_id": "UC_x5XG1OV2P6uZZ5FSM9Ttw",
            "kind": SuppressionKind.SUPPRESSION,
            "scope": SuppressionScope.FULL,
            "recorded_at": INSTANT,
            "restricted_reason": "privacy review",
        }
    )
    public_named = [name for name in record.model_dump() if not name.startswith("restricted_")]
    assert "reason" not in public_named


# --- Release manifest (Requirement 8.1) ----------------------------------


def _manifest(**overrides: object) -> ReleaseManifest:
    fields: dict[str, object] = {
        "release_id": "2026-01-15T12-00-00Z",
        "generated_at": INSTANT,
        "enrichment_cutoff": INSTANT - timedelta(hours=1),
        "included_snapshots": (
            DatasetSnapshotRef.model_validate(
                {
                    "dataset_id": "panda-70m",
                    "version": "2024.1",
                    "snapshot_digest": "sha256:abc",
                }
            ),
        ),
        "default_filter": Filter.model_validate(
            {"datasets": ("panda-70m",), "corpus_classes": (CorpusClass.CANDIDATE,)}
        ),
        "artifact_digests": (("countries.json", "sha256:def"),),
        "methodology_version": "1.0.0",
        "disclosure_policy_version": "1.0.0",
    }
    fields.update(overrides)
    return ReleaseManifest.model_validate(fields)


def test_manifest_rejects_cutoff_after_generation() -> None:
    with pytest.raises(ValidationError, match="enrichment_cutoff must be <= generated_at"):
        _manifest(enrichment_cutoff=INSTANT + timedelta(hours=1))


def test_manifest_requires_sorted_artifact_digests() -> None:
    with pytest.raises(ValidationError, match="artifact_digests must be sorted"):
        _manifest(artifact_digests=(("z.json", "sha256:1"), ("a.json", "sha256:2")))


def test_manifest_requires_at_least_one_artifact() -> None:
    with pytest.raises(ValidationError):
        _manifest(artifact_digests=())


def test_manifest_serialization_is_byte_stable() -> None:
    """Requirement 8.9: manifest bytes must be reproducible."""
    assert _manifest().to_deterministic_json_bytes() == _manifest().to_deterministic_json_bytes()


# --- Disclosure policy (Requirement 7.1) ---------------------------------


def test_disclosure_policy_requires_allowed_fields() -> None:
    """Requirement 7.1: a policy missing required rules is invalid."""
    with pytest.raises(ValidationError):
        DisclosurePolicy.model_validate(
            {
                "policy_id": "disclosure",
                "version": "1.0.0",
                "approved_at": INSTANT,
                "min_represented_video_count": 5,
                "allowed_fields": (),
            }
        )


def test_disclosure_policy_requires_sorted_allowed_fields() -> None:
    with pytest.raises(ValidationError, match="allowed_fields must be sorted"):
        DisclosurePolicy.model_validate(
            {
                "policy_id": "disclosure",
                "version": "1.0.0",
                "approved_at": INSTANT,
                "min_represented_video_count": 5,
                "allowed_fields": ("display_name", "country"),
            }
        )
