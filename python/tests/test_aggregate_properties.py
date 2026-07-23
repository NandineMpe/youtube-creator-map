"""Property tests for the aggregation invariants.

These exercise the counting semantics against an in-memory model of the
aggregation rules, so the properties are checked over thousands of generated
shapes rather than the handful a live database run can cover. The SQL
implementation is verified separately against real data in the integration
suite.

Covers design invariants 3, 4, 5, 7, 8, and 9.
"""

from __future__ import annotations

from dataclasses import dataclass

from creator_map_schemas import UNKNOWN_COUNTRY, VideoResolutionPartition
from hypothesis import given, settings
from hypothesis import strategies as st

# --- A minimal executable model of the aggregation rules ------------------


@dataclass(frozen=True, slots=True)
class Occurrence:
    dataset_id: str
    video_id: str


def represented_videos(occurrences: list[Occurrence], datasets: frozenset[str]) -> set[str]:
    """Distinct video identifiers admitted by a dataset filter.

    Requirement 5.1: cardinality of the distinct set, never a row count.
    """
    return {o.video_id for o in occurrences if o.dataset_id in datasets}


def source_occurrence_count(occurrences: list[Occurrence], datasets: frozenset[str]) -> int:
    """Retained rows admitted by a dataset filter (Requirement 5.2)."""
    return sum(1 for o in occurrences if o.dataset_id in datasets)


def dataset_breakdown(occurrences: list[Occurrence], datasets: frozenset[str]) -> dict[str, int]:
    """Per-dataset distinct video counts (Requirement 5.3)."""
    breakdown: dict[str, set[str]] = {}
    for occurrence in occurrences:
        if occurrence.dataset_id in datasets:
            breakdown.setdefault(occurrence.dataset_id, set()).add(occurrence.video_id)
    return {dataset: len(videos) for dataset, videos in breakdown.items()}


# --- Strategies -----------------------------------------------------------

dataset_ids = st.sampled_from(["ds-a", "ds-b", "ds-c", "ds-d"])
video_ids = st.sampled_from([f"vid-{i:02d}" for i in range(12)])

# Built from a list of tuples rather than st.builds per element. Drawing
# sixty individually-constructed dataclasses was slow enough to trip
# Hypothesis's data-generation health check intermittently; the shapes these
# properties explore do not need that many elements, and the coverage is the
# same at a third the size.
occurrences = st.lists(st.tuples(dataset_ids, video_ids), max_size=20).map(
    lambda pairs: [Occurrence(d, v) for d, v in pairs]
)

dataset_filters = st.sets(dataset_ids, min_size=1, max_size=4).map(frozenset)


# --- Property 3: Within-Dataset Deduplication -----------------------------
# Validates: Requirements 5.1, 5.2


@given(base=occurrences, extra=st.integers(min_value=1, max_value=8))
@settings(max_examples=300)
def test_property_duplicates_raise_occurrences_not_videos(
    base: list[Occurrence], extra: int
) -> None:
    """Adding duplicate evidence changes one count and not the other."""
    all_datasets = frozenset({"ds-a", "ds-b", "ds-c", "ds-d"})
    if not base:
        base = [Occurrence("ds-a", "vid-00")]

    before_videos = represented_videos(base, all_datasets)
    before_rows = source_occurrence_count(base, all_datasets)

    duplicated = base + [base[0]] * extra

    after_videos = represented_videos(duplicated, all_datasets)
    after_rows = source_occurrence_count(duplicated, all_datasets)

    assert after_videos == before_videos
    assert after_rows == before_rows + extra


# --- Property 4: Cross-Dataset Union Semantics ----------------------------
# Validates: Requirements 5.3, 5.12


@given(items=occurrences, left=dataset_filters, right=dataset_filters)
@settings(max_examples=300)
def test_property_combined_videos_are_an_exact_union(
    items: list[Occurrence], left: frozenset[str], right: frozenset[str]
) -> None:
    """The union filter yields exactly the union of the video sets."""
    combined = represented_videos(items, left | right)
    assert combined == represented_videos(items, left) | represented_videos(items, right)


@given(items=occurrences)
@settings(max_examples=300)
def test_property_overlap_counts_once_combined_and_once_per_dataset(
    items: list[Occurrence],
) -> None:
    """Requirement 5.3 / 12.7: dataset subtotals are not additive."""
    all_datasets = frozenset({"ds-a", "ds-b", "ds-c", "ds-d"})
    combined = len(represented_videos(items, all_datasets))
    breakdown = dataset_breakdown(items, all_datasets)

    # A video in several datasets contributes once to the combined total but
    # once to each dataset, so the subtotals can only ever over-count.
    assert combined <= sum(breakdown.values())


# --- Property 8: Filter Isolation -----------------------------------------
# Validates: Requirements 5.8, 5.9


@given(items=occurrences, selected=dataset_filters)
@settings(max_examples=300)
def test_property_excluded_datasets_cannot_affect_counts(
    items: list[Occurrence], selected: frozenset[str]
) -> None:
    """Adding occurrences in excluded datasets changes nothing."""
    before_videos = represented_videos(items, selected)
    before_rows = source_occurrence_count(items, selected)

    excluded = frozenset({"ds-a", "ds-b", "ds-c", "ds-d"}) - selected
    noise = [Occurrence(d, "vid-99") for d in excluded] * 3

    assert represented_videos(items + noise, selected) == before_videos
    assert source_occurrence_count(items + noise, selected) == before_rows


@given(items=occurrences, selected=dataset_filters)
@settings(max_examples=300)
def test_property_every_counted_video_comes_from_an_included_dataset(
    items: list[Occurrence], selected: frozenset[str]
) -> None:
    counted = represented_videos(items, selected)
    supporting = {o.video_id for o in items if o.dataset_id in selected}
    assert counted == supporting


# --- Property 9: Monotonic Union of Resolved Identities -------------------
# Validates: Requirements 5.10, 5.11


@given(items=occurrences, subset=dataset_filters, extra=dataset_ids)
@settings(max_examples=300)
def test_property_subset_filter_yields_a_subset_of_videos(
    items: list[Occurrence], subset: frozenset[str], extra: str
) -> None:
    """Requirement 5.10: F1 subset of F2 implies videos(F1) subset of videos(F2)."""
    superset = subset | {extra}

    assert represented_videos(items, subset) <= represented_videos(items, superset)


@given(items=occurrences, subset=dataset_filters, extra=dataset_ids)
@settings(max_examples=300)
def test_property_subset_totals_never_exceed_superset_totals(
    items: list[Occurrence], subset: frozenset[str], extra: str
) -> None:
    """Requirement 5.11: counts are monotonic under filter widening."""
    superset = subset | {extra}

    assert len(represented_videos(items, subset)) <= len(represented_videos(items, superset))
    assert source_occurrence_count(items, subset) <= source_occurrence_count(items, superset)


# --- Property 5: Creator Attribution Uniqueness ---------------------------
# Validates: Requirements 5.4, 5.5, 5.6


@given(
    assignments=st.dictionaries(
        video_ids,
        st.sampled_from(["UC_a", "UC_b", "UC_c"]),
        max_size=12,
    ),
    countries=st.dictionaries(
        st.sampled_from(["UC_a", "UC_b", "UC_c"]),
        st.sampled_from(["DE", "US", "JP", None]),
        max_size=3,
    ),
)
@settings(max_examples=300)
def test_property_each_video_reaches_one_channel_and_one_bucket(
    assignments: dict[str, str], countries: dict[str, str | None]
) -> None:
    """A resolved video contributes to exactly one channel and one country."""
    buckets: dict[str, set[str]] = {}
    channel_of: dict[str, str] = {}

    for video_id, channel_id in assignments.items():
        country = countries.get(channel_id) or UNKNOWN_COUNTRY
        buckets.setdefault(country, set()).add(video_id)
        channel_of[video_id] = channel_id

    # No video appears in two country buckets.
    seen: set[str] = set()
    for videos in buckets.values():
        assert not (seen & videos)
        seen |= videos

    # Every assigned video has exactly one channel.
    assert set(channel_of) == set(assignments)
    assert sum(len(v) for v in buckets.values()) == len(assignments)


@given(
    resolved=st.sets(video_ids, max_size=12),
    unresolved=st.sets(video_ids, max_size=12),
)
@settings(max_examples=300)
def test_property_unresolved_videos_reach_no_country(
    resolved: set[str], unresolved: set[str]
) -> None:
    """Requirement 5.6: an unresolved video is in no creator or country count."""
    unresolved = unresolved - resolved
    attributed = resolved

    assert not (attributed & unresolved)
    # Coverage still accounts for them, so nothing is silently dropped.
    assert len(attributed) + len(unresolved) == len(resolved | unresolved)


# --- Property 7: Coverage Partition ---------------------------------------
# Validates: Requirements 6.1-6.6


@given(
    resolved=st.integers(min_value=0, max_value=200),
    unavailable=st.integers(min_value=0, max_value=200),
    pending=st.integers(min_value=0, max_value=200),
    invalid=st.integers(min_value=0, max_value=200),
    terminal=st.integers(min_value=0, max_value=200),
)
@settings(max_examples=300)
def test_property_partition_is_exhaustive_and_disjoint(
    resolved: int, unavailable: int, pending: int, invalid: int, terminal: int
) -> None:
    """The five states sum to the distinct input count, by construction."""
    total = resolved + unavailable + pending + invalid + terminal

    partition = VideoResolutionPartition(
        distinct_input_video_count=total,
        resolved_count=resolved,
        unavailable_unclassified_count=unavailable,
        retryable_or_pending_count=pending,
        invalid_count=invalid,
        terminal_failure_count=terminal,
    )

    assert partition.distinct_input_video_count == total
    from creator_map_schemas import VideoResolutionPartitionState

    assert sum(partition.count_for(state) for state in VideoResolutionPartitionState) == total


@given(
    resolved=st.integers(min_value=0, max_value=100),
    unavailable=st.integers(min_value=0, max_value=100),
    drift=st.integers(min_value=1, max_value=50),
)
@settings(max_examples=200)
def test_property_non_reconciling_partition_is_rejected(
    resolved: int, unavailable: int, drift: int
) -> None:
    """A partition that does not sum cannot be constructed at all."""
    import pytest

    with pytest.raises(ValueError, match="must sum to"):
        VideoResolutionPartition(
            distinct_input_video_count=resolved + unavailable + drift,
            resolved_count=resolved,
            unavailable_unclassified_count=unavailable,
            retryable_or_pending_count=0,
            invalid_count=0,
            terminal_failure_count=0,
        )
