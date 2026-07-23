"""Tests for disclosure-reviewed public artifact generation.

Includes Property 13 (Disclosure Noninterference), validating Requirements
7.2-7.7, 7.10, and 7.11.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from creator_map_pipeline.aggregate.artifacts import (
    ArtifactSet,
    DisclosureViolation,
    approved_creator_rows,
    assert_publishable,
    build_active_pointer,
    build_country_detail,
    build_manifest,
    build_overview,
    canonical_bytes,
    country_shard_path,
    digest_of,
    find_prohibited_content,
)
from creator_map_pipeline.aggregate.builder import AggregateResult, CreatorAggregate
from creator_map_pipeline.aggregate.disclosure import DisclosureEngine
from creator_map_pipeline.aggregate.pagination import CreatorSortOrder
from creator_map_schemas import (
    UNKNOWN_COUNTRY,
    CorpusClass,
    CountrySummary,
    CoverageSummary,
    DisclosurePolicy,
    Filter,
    SuppressionKind,
    SuppressionRecord,
    SuppressionScope,
    VideoResolutionPartition,
)
from hypothesis import given, settings
from hypothesis import strategies as st

INSTANT = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
SECRET = "restricted-key-material"
RAW_CHANNEL = "UC_x5XG1OV2P6uZZ5FSM9Ttw"
DIGEST = "sha256:" + "a" * 64


def policy(**overrides: object) -> DisclosurePolicy:
    fields: dict[str, object] = {
        "policy_id": "p",
        "version": "1.0.0",
        "approved_at": INSTANT,
        "min_represented_video_count": 3,
        "allowed_fields": ("display_name", "represented_video_count"),
    }
    fields.update(overrides)
    return DisclosurePolicy.model_validate(fields)


def engine(**kwargs: object) -> DisclosureEngine:
    return DisclosureEngine(
        kwargs.pop("policy", policy()),  # type: ignore[arg-type]
        public_key_secret=SECRET,
        **kwargs,  # type: ignore[arg-type]
    )


def creator(channel: str = RAW_CHANNEL, *, count: int = 10) -> CreatorAggregate:
    return CreatorAggregate(
        channel_id=channel,
        display_name="Example Channel",
        country="DE",
        represented_video_count=count,
        dataset_breakdown=(("ds-a", count),),
    )


def summary(country: str = "DE") -> CountrySummary:
    return CountrySummary(
        country=country,
        creator_count=2,
        represented_video_count=12,
        source_occurrence_count=15,
        resolved_video_count=12,
        unavailable_video_count=0,
    )


def coverage() -> CoverageSummary:
    return CoverageSummary(
        input_occurrence_count=15,
        distinct_input_video_count=12,
        resolved_video_count=12,
        unavailable_video_count=0,
        resolved_channel_count=2,
        known_country_channel_count=2,
        unknown_country_channel_count=0,
    )


def partition() -> VideoResolutionPartition:
    return VideoResolutionPartition(
        distinct_input_video_count=12,
        resolved_count=12,
        unavailable_unclassified_count=0,
        retryable_or_pending_count=0,
        invalid_count=0,
        terminal_failure_count=0,
    )


def active_filter() -> Filter:
    return Filter(datasets=("ds-a",), corpus_classes=(CorpusClass.CANDIDATE,))


# --- Deterministic serialization -----------------------------------------


def test_canonical_bytes_are_stable() -> None:
    """Requirement 5.13: the same aggregate yields the same bytes."""
    payload = {"b": 2, "a": 1, "nested": {"z": 1, "y": 2}}
    assert canonical_bytes(payload) == canonical_bytes(dict(reversed(payload.items())))


def test_canonical_bytes_sort_keys() -> None:
    assert canonical_bytes({"b": 1, "a": 2}) == b'{"a":2,"b":1}'


def test_digest_matches_the_manifest_shape() -> None:
    value = digest_of({"a": 1})
    assert value.startswith("sha256:")
    assert len(value) == len("sha256:") + 64


# --- Requirement 7.5/7.6: recursive prohibited-content inspection --------


def test_clean_payload_passes() -> None:
    payload = build_overview(
        AggregateResult(
            countries=[summary()],
            coverage=coverage(),
            partition=partition(),
            represented_video_count=12,
            creator_count=2,
        ),
        release_id="r1",
        active_filter=active_filter(),
    )
    assert find_prohibited_content(payload) == []


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("videoIds", ["dQw4w9WgXcQ"]),
        ("sourceLocator", "shard-0:row-1"),
        ("channelId", RAW_CHANNEL),
        ("rawResponse", "{}"),
        ("email", "a@b.invalid"),
        ("responseDigest", DIGEST),
        ("termsReviewId", "review-1"),
        ("acquisitionPath", "documented"),
    ],
)
def test_prohibited_keys_are_found(key: str, value: object) -> None:
    findings = find_prohibited_content({key: value})
    assert findings, f"{key} should have been flagged"


def test_prohibited_key_found_at_depth() -> None:
    """Requirement 7.6: inspection is recursive, not top-level only."""
    payload = {"a": {"b": [{"c": {"sourceLocator": "x"}}]}}
    findings = find_prohibited_content(payload)
    assert len(findings) == 1
    assert findings[0].path == "a.b[0].c.sourceLocator"


def test_raw_channel_id_in_a_value_is_found() -> None:
    findings = find_prohibited_content({"note": f"see {RAW_CHANNEL}"})
    assert any("channel identifier" in f.reason for f in findings)


def test_methodology_prose_is_not_flagged() -> None:
    """Requirement 12 obliges publishing this copy."""
    payload = {
        "note": "Counts are distinct source-video identifiers within the filter.",
        "description": "Boundaries are presentation conventions, not location.",
    }
    assert find_prohibited_content(payload) == []


@pytest.mark.parametrize("name", ["101Treesrus", "1BreezyLife", "_le__s__ya_", "1DeathEater"])
def test_real_channel_names_are_not_mistaken_for_identifiers(name: str) -> None:
    """Observed on live data: 770 channels have 11-character names.

    Flagging them blocked the entire build. The shape heuristic cannot
    tell a name from an identifier, and the field's meaning already can.
    """
    assert find_prohibited_content({"displayName": name}) == []


def test_name_exemption_still_catches_real_leaks() -> None:
    """The exemption covers only the guess-from-shape heuristic."""
    assert find_prohibited_content({"displayName": RAW_CHANNEL})
    assert find_prohibited_content({"displayName": "see youtube.com/watch?v=dQw4w9WgXcQ"})


def test_bare_identifier_under_a_prose_key_is_still_found() -> None:
    """The prose exemption must not become a hiding place."""
    assert find_prohibited_content({"note": "dQw4w9WgXcQ"})


def test_assert_publishable_raises_with_findings() -> None:
    with pytest.raises(DisclosureViolation) as excinfo:
        assert_publishable({"sourceLocator": "a", "videoIds": ["b"]})
    assert len(excinfo.value.findings) == 2


def test_violation_message_omits_the_offending_value() -> None:
    """The error is logged; it must not become the leak it prevents."""
    with pytest.raises(DisclosureViolation) as excinfo:
        assert_publishable({"apiKey": "sb_secret_supersecretvalue"})
    assert "supersecretvalue" not in str(excinfo.value)


# --- Requirement 7.2/7.3: creator rows carry only approved fields --------


def test_approved_rows_use_public_keys_not_raw_ids() -> None:
    rows = approved_creator_rows([creator()], engine=engine(), observed_at="2026-01-15")
    assert len(rows) == 1
    assert rows[0].public_channel_key.startswith("pk_")
    assert RAW_CHANNEL not in rows[0].public_channel_key


def test_creators_failing_policy_are_absent() -> None:
    rows = approved_creator_rows(
        [creator(count=1)],
        engine=engine(policy=policy(min_represented_video_count=5)),
        observed_at="2026-01-15",
    )
    assert rows == []


def test_suppressed_creator_leaves_no_trace() -> None:
    """Requirement 7.8: not even a count of what was withheld."""
    record = SuppressionRecord.model_validate(
        {
            "record_id": "s1",
            "channel_id": RAW_CHANNEL,
            "kind": SuppressionKind.OPT_OUT,
            "scope": SuppressionScope.FULL,
            "recorded_at": INSTANT,
            "restricted_reason": "opt-out",
        }
    )
    rows = approved_creator_rows(
        [creator(), creator("UC_other_channel_abcdefghij", count=8)],
        engine=engine(suppressions=(record,)),
        observed_at="2026-01-15",
    )

    assert len(rows) == 1
    payload = build_country_detail(
        "DE",
        summary=summary(),
        coverage=coverage(),
        partition=partition(),
        rows=rows,
        page_size=10,
    )
    serialized = canonical_bytes(payload).decode()
    assert RAW_CHANNEL not in serialized
    assert "suppress" not in serialized.lower()
    assert "withheld" not in serialized.lower()


# --- Artifact construction ------------------------------------------------


def test_overview_carries_the_full_partition() -> None:
    """Requirement 6.7: every resolution state appears beside the totals."""
    payload = build_overview(
        AggregateResult(
            countries=[summary()],
            coverage=coverage(),
            partition=partition(),
            represented_video_count=12,
            creator_count=2,
        ),
        release_id="r1",
        active_filter=active_filter(),
    )
    states = payload["coverage"]["partition"]
    assert set(states) == {
        "resolved",
        "unavailableUnclassified",
        "retryableOrPending",
        "invalid",
        "terminalFailure",
    }


def test_overview_excludes_unknown_from_country_count() -> None:
    """Requirement 6.8: Unknown is a bucket, not a geography."""
    result = AggregateResult(
        countries=[summary("DE"), summary(UNKNOWN_COUNTRY)],
        coverage=coverage(),
        partition=partition(),
        represented_video_count=12,
        creator_count=2,
    )
    payload = build_overview(result, release_id="r1", active_filter=active_filter())

    assert payload["representedCountryCount"] == 1
    assert len(payload["countries"]) == 2


def test_incomplete_result_refuses_to_build() -> None:
    with pytest.raises(ValueError, match="incomplete"):
        build_overview(AggregateResult(), release_id="r1", active_filter=active_filter())


def test_country_detail_includes_the_first_page() -> None:
    """Requirement 14.4: detail is deferred, but its first page ships with it."""
    rows = approved_creator_rows(
        [creator(f"UC_channel_{i:015d}", count=10 - i) for i in range(8)],
        engine=engine(),
        observed_at="2026-01-15",
    )
    payload = build_country_detail(
        "DE",
        summary=summary(),
        coverage=coverage(),
        partition=partition(),
        rows=rows,
        page_size=5,
    )

    assert len(payload["firstPage"]["rows"]) == 5
    assert payload["firstPage"]["nextCursor"] is not None
    assert payload["firstPage"]["totalRows"] == 8
    assert payload["firstPage"]["sortOrder"] == CreatorSortOrder.VIDEO_COUNT_DESC.value


def test_country_detail_with_no_approved_creators() -> None:
    """Requirement 10.11: an empty page is valid, with totals preserved."""
    payload = build_country_detail(
        "DE",
        summary=summary(),
        coverage=coverage(),
        partition=partition(),
        rows=[],
        page_size=10,
    )
    assert payload["firstPage"]["rows"] == []
    assert payload["firstPage"]["nextCursor"] is None
    assert payload["creatorCount"] == 2


def test_manifest_records_every_digest() -> None:
    payload = build_manifest(
        release_id="r1",
        generated_at=INSTANT,
        enrichment_cutoff=INSTANT,
        default_filter=active_filter(),
        datasets=[{"datasetId": "ds-a", "displayName": "DS A"}],
        artifact_digests={"b.json": DIGEST, "a.json": DIGEST},
        methodology_version="1.0.0",
        disclosure_policy_version="1.0.0",
        boundary_metadata={"datasetName": "Natural Earth", "version": "5.1.1"},
    )
    assert list(payload["artifactDigests"]) == ["a.json", "b.json"]
    assert payload["generatedAt"].endswith("Z")


def test_active_pointer_stays_minimal() -> None:
    """Requirement 14.10: the pointer refreshes separately from artifacts."""
    payload = build_active_pointer(
        release_id="r1", manifest_path="releases/r1/manifest.json", manifest_digest=DIGEST
    )
    assert set(payload) == {
        "schemaVersion",
        "releaseId",
        "manifestPath",
        "manifestDigest",
    }


def test_shard_paths_are_versioned_by_release() -> None:
    """Requirement 14.10: artifacts are immutably addressed per release."""
    assert country_shard_path("r1", "DE") == "releases/r1/countries/DE.json"
    assert country_shard_path("r1", UNKNOWN_COUNTRY).endswith("XX.json")


def test_artifact_set_digests_and_inspects() -> None:
    artifacts = ArtifactSet()
    artifacts.add("a.json", {"value": 1})
    artifacts.add("b.json", {"value": 2})

    assert set(artifacts.digests) == {"a.json", "b.json"}
    assert artifacts.total_bytes > 0
    assert all(d.startswith("sha256:") for d in artifacts.digests.values())


def test_artifact_set_refuses_prohibited_content() -> None:
    artifacts = ArtifactSet()
    with pytest.raises(DisclosureViolation):
        artifacts.add("bad.json", {"sourceLocator": "shard-0:row-1"})
    assert artifacts.artifacts == []


# --- Property 13: Disclosure Noninterference ------------------------------
# Validates: Requirements 7.2-7.7, 7.10, 7.11


channel_ids = st.text(
    alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-",
    min_size=22,
    max_size=22,
).map(lambda s: f"UC{s}")


@given(
    suppressed=st.lists(channel_ids, min_size=1, max_size=5, unique=True),
    permitted=st.lists(channel_ids, min_size=0, max_size=5, unique=True),
    counts=st.integers(min_value=3, max_value=50),
)
@settings(max_examples=200)
def test_property_suppressed_creators_never_reach_an_artifact(
    suppressed: list[str], permitted: list[str], counts: int
) -> None:
    """No suppressed identity appears anywhere in the generated bytes."""
    permitted = [c for c in permitted if c not in suppressed]

    records = tuple(
        SuppressionRecord.model_validate(
            {
                "record_id": f"s{i}",
                "channel_id": channel,
                "kind": SuppressionKind.SUPPRESSION,
                "scope": SuppressionScope.FULL,
                "recorded_at": INSTANT,
                "restricted_reason": "privacy review",
            }
        )
        for i, channel in enumerate(suppressed)
    )

    creators = [creator(c, count=counts) for c in suppressed + permitted]
    rows = approved_creator_rows(
        creators, engine=engine(suppressions=records), observed_at="2026-01-15"
    )

    payload = build_country_detail(
        "DE",
        summary=summary(),
        coverage=coverage(),
        partition=partition(),
        rows=rows,
        page_size=10,
    )
    serialized = canonical_bytes(payload).decode()

    for channel in suppressed:
        assert channel not in serialized
    assert len(rows) == len(permitted)


@given(
    channels=st.lists(channel_ids, min_size=1, max_size=8, unique=True),
    counts=st.integers(min_value=3, max_value=40),
)
@settings(max_examples=200)
def test_property_no_raw_channel_id_survives_projection(channels: list[str], counts: int) -> None:
    """Requirement 7.2: every published key is distinct from its source."""
    creators = [creator(c, count=counts) for c in channels]
    rows = approved_creator_rows(creators, engine=engine(), observed_at="2026-01-15")

    payload = build_country_detail(
        "DE",
        summary=summary(),
        coverage=coverage(),
        partition=partition(),
        rows=rows,
        page_size=50,
    )
    serialized = canonical_bytes(payload).decode()

    for channel in channels:
        assert channel not in serialized

    # And the artifact passes its own inspection.
    assert find_prohibited_content(payload) == []


@given(
    channels=st.lists(channel_ids, min_size=0, max_size=6, unique=True),
    threshold=st.integers(min_value=1, max_value=30),
    count=st.integers(min_value=1, max_value=30),
)
@settings(max_examples=200)
def test_property_threshold_is_enforced_uniformly(
    channels: list[str], threshold: int, count: int
) -> None:
    """A creator below the threshold is published by no path."""
    creators = [creator(c, count=count) for c in channels]
    rows = approved_creator_rows(
        creators,
        engine=engine(policy=policy(min_represented_video_count=threshold)),
        observed_at="2026-01-15",
    )

    expected = len(channels) if count >= threshold else 0
    assert len(rows) == expected
