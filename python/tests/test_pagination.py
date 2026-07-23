"""Tests for deterministic creator sorting and cursor pagination.

Requirement refs: 10.5-10.8, 14.5
"""

from __future__ import annotations

import pytest
from creator_map_pipeline.aggregate.pagination import (
    CreatorRow,
    CreatorSortOrder,
    InvalidCursor,
    Page,
    decode_cursor,
    encode_cursor,
    paginate,
    sort_rows,
    traverse_all,
)
from hypothesis import given, settings
from hypothesis import strategies as st


def row(key: str, *, count: int = 1, name: str | None = None) -> CreatorRow:
    return CreatorRow(
        public_channel_key=f"pk_{key}",
        display_name=name if name is not None else f"Channel {key}",
        country="DE",
        represented_video_count=count,
        dataset_breakdown=(("ds-a", count),),
        last_observed_at="2026-01-15",
    )


# --- Requirement 10.5: deterministic total ordering ----------------------


def test_video_count_sorts_descending() -> None:
    rows = [row("a", count=1), row("b", count=9), row("c", count=5)]
    ordered = sort_rows(rows, CreatorSortOrder.VIDEO_COUNT_DESC)
    assert [r.represented_video_count for r in ordered] == [9, 5, 1]


def test_ties_break_on_public_key() -> None:
    """Count alone is not a total order; thousands of creators share one."""
    rows = [row("c", count=5), row("a", count=5), row("b", count=5)]
    ordered = sort_rows(rows, CreatorSortOrder.VIDEO_COUNT_DESC)
    assert [r.public_channel_key for r in ordered] == ["pk_a", "pk_b", "pk_c"]


def test_display_name_sorts_case_insensitively() -> None:
    rows = [row("a", name="zebra"), row("b", name="Apple"), row("c", name="mango")]
    ordered = sort_rows(rows, CreatorSortOrder.DISPLAY_NAME_ASC)
    assert [r.display_name for r in ordered] == ["Apple", "mango", "zebra"]


def test_sorting_is_stable_across_input_permutations() -> None:
    """Requirement 10.7: the same set yields the same order every time."""
    rows = [row(f"{i:02d}", count=i % 3) for i in range(20)]
    forward = [r.public_channel_key for r in sort_rows(rows, CreatorSortOrder.VIDEO_COUNT_DESC)]
    backward = [
        r.public_channel_key
        for r in sort_rows(list(reversed(rows)), CreatorSortOrder.VIDEO_COUNT_DESC)
    ]
    assert forward == backward


# --- Cursor encoding -----------------------------------------------------


def test_cursor_round_trips() -> None:
    source = row("a", count=7)
    cursor = encode_cursor(source, CreatorSortOrder.VIDEO_COUNT_DESC)
    assert decode_cursor(cursor, CreatorSortOrder.VIDEO_COUNT_DESC) == (-7, "pk_a")


def test_cursor_is_url_safe() -> None:
    cursor = encode_cursor(row("a"), CreatorSortOrder.VIDEO_COUNT_DESC)
    assert "=" not in cursor
    assert "+" not in cursor
    assert "/" not in cursor


def test_cursor_discloses_no_raw_identifier() -> None:
    """Requirement 7.3: a cursor in a URL reveals nothing extra."""
    import base64

    source = CreatorRow(
        public_channel_key="pk_abc123",
        display_name="Example",
        country="DE",
        represented_video_count=3,
        dataset_breakdown=(),
        last_observed_at="2026-01-15",
    )
    cursor = encode_cursor(source, CreatorSortOrder.VIDEO_COUNT_DESC)
    decoded = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()

    assert "UC" not in decoded
    assert "sourceLocator" not in decoded


@pytest.mark.parametrize("cursor", ["not-base64!!", "", "eyJib2d1cyI6dHJ1ZX0", "YWJjZGVm"])
def test_malformed_cursor_is_refused(cursor: str) -> None:
    with pytest.raises(InvalidCursor):
        decode_cursor(cursor, CreatorSortOrder.VIDEO_COUNT_DESC)


def test_cursor_from_another_sort_order_is_refused() -> None:
    """Accepting it would skip or repeat rows (Requirement 10.6)."""
    cursor = encode_cursor(row("a"), CreatorSortOrder.VIDEO_COUNT_DESC)
    with pytest.raises(InvalidCursor, match="different sort order"):
        decode_cursor(cursor, CreatorSortOrder.DISPLAY_NAME_ASC)


# --- Requirement 10.5: page partitioning ---------------------------------


def test_page_respects_the_configured_size() -> None:
    rows = [row(f"{i:02d}", count=100 - i) for i in range(25)]
    page = paginate(rows, order=CreatorSortOrder.VIDEO_COUNT_DESC, page_size=10)

    assert len(page.rows) == 10
    assert page.total_rows == 25
    assert page.has_more


def test_final_page_has_no_next_cursor() -> None:
    rows = [row(f"{i:02d}") for i in range(5)]
    page = paginate(rows, order=CreatorSortOrder.VIDEO_COUNT_DESC, page_size=10)

    assert len(page.rows) == 5
    assert page.next_cursor is None
    assert not page.has_more


def test_empty_result_yields_an_empty_page() -> None:
    """Requirement 10.11: no rows is a valid state, not an error."""
    page = paginate([], order=CreatorSortOrder.VIDEO_COUNT_DESC, page_size=10)

    assert page.rows == ()
    assert page.next_cursor is None
    assert page.total_rows == 0


def test_page_size_must_be_positive() -> None:
    with pytest.raises(ValueError, match="page size must be >= 1"):
        paginate([], order=CreatorSortOrder.VIDEO_COUNT_DESC, page_size=0)


# --- Requirement 10.6: exactly-once traversal ----------------------------


def test_traversal_visits_every_row_exactly_once() -> None:
    rows = [row(f"{i:03d}", count=i % 7) for i in range(53)]

    visited = traverse_all(rows, order=CreatorSortOrder.VIDEO_COUNT_DESC, page_size=10)

    keys = [r.public_channel_key for r in visited]
    assert len(keys) == 53
    assert len(set(keys)) == 53
    assert set(keys) == {r.public_channel_key for r in rows}


def test_traversal_preserves_sort_order_across_pages() -> None:
    rows = [row(f"{i:03d}", count=i) for i in range(37)]

    visited = traverse_all(rows, order=CreatorSortOrder.VIDEO_COUNT_DESC, page_size=8)

    counts = [r.represented_video_count for r in visited]
    assert counts == sorted(counts, reverse=True)


def test_traversal_handles_heavy_ties() -> None:
    """Every creator sharing one count still appears exactly once."""
    rows = [row(f"{i:03d}", count=5) for i in range(40)]

    visited = traverse_all(rows, order=CreatorSortOrder.VIDEO_COUNT_DESC, page_size=7)

    assert len({r.public_channel_key for r in visited}) == 40


def test_page_size_larger_than_result() -> None:
    rows = [row(f"{i:02d}") for i in range(3)]
    visited = traverse_all(rows, order=CreatorSortOrder.DISPLAY_NAME_ASC, page_size=100)
    assert len(visited) == 3


# --- Requirement 10.7: repeatability -------------------------------------


def test_same_cursor_returns_the_same_rows() -> None:
    rows = [row(f"{i:03d}", count=i % 5) for i in range(30)]

    first = paginate(rows, order=CreatorSortOrder.VIDEO_COUNT_DESC, page_size=10)
    assert first.next_cursor is not None

    a = paginate(
        rows,
        order=CreatorSortOrder.VIDEO_COUNT_DESC,
        page_size=10,
        cursor=first.next_cursor,
    )
    b = paginate(
        rows,
        order=CreatorSortOrder.VIDEO_COUNT_DESC,
        page_size=10,
        cursor=first.next_cursor,
    )

    assert [r.public_channel_key for r in a.rows] == [r.public_channel_key for r in b.rows]


def test_cursor_survives_a_removed_row() -> None:
    """A suppressed creator must not strand the cursor pointing at it."""
    rows = [row(f"{i:03d}", count=100 - i) for i in range(20)]

    first = paginate(rows, order=CreatorSortOrder.VIDEO_COUNT_DESC, page_size=5)
    assert first.next_cursor is not None
    boundary_key = first.rows[-1].public_channel_key

    # The row the cursor names is withdrawn between requests.
    remaining = [r for r in rows if r.public_channel_key != boundary_key]
    page = paginate(
        remaining,
        order=CreatorSortOrder.VIDEO_COUNT_DESC,
        page_size=5,
        cursor=first.next_cursor,
    )

    # Traversal continues from the right position rather than failing.
    assert len(page.rows) == 5
    assert boundary_key not in {r.public_channel_key for r in page.rows}


# --- Property: traversal is exhaustive and non-repeating ------------------


@given(
    counts=st.lists(st.integers(min_value=0, max_value=50), min_size=0, max_size=80),
    page_size=st.integers(min_value=1, max_value=12),
    order=st.sampled_from(list(CreatorSortOrder)),
)
@settings(max_examples=200)
def test_property_traversal_is_exhaustive_and_non_repeating(
    counts: list[int], page_size: int, order: CreatorSortOrder
) -> None:
    """Requirement 10.6 over generated shapes, including heavy ties."""
    rows = [row(f"{i:04d}", count=c) for i, c in enumerate(counts)]

    visited = traverse_all(rows, order=order, page_size=page_size)
    keys = [r.public_channel_key for r in visited]

    assert len(keys) == len(rows)
    assert len(set(keys)) == len(rows)


@given(
    counts=st.lists(st.integers(min_value=0, max_value=20), min_size=1, max_size=40),
    page_size=st.integers(min_value=1, max_value=10),
)
@settings(max_examples=200)
def test_property_traversal_preserves_the_total_order(counts: list[int], page_size: int) -> None:
    rows = [row(f"{i:04d}", count=c) for i, c in enumerate(counts)]

    visited = traverse_all(rows, order=CreatorSortOrder.VIDEO_COUNT_DESC, page_size=page_size)

    keys = [(-r.represented_video_count, r.public_channel_key) for r in visited]
    assert keys == sorted(keys)


@given(
    counts=st.lists(st.integers(min_value=0, max_value=20), min_size=1, max_size=40),
    page_size=st.integers(min_value=1, max_value=10),
)
@settings(max_examples=150)
def test_property_pages_partition_the_result(counts: list[int], page_size: int) -> None:
    """Pages are disjoint and together cover the whole set."""
    rows = [row(f"{i:04d}", count=c) for i, c in enumerate(counts)]

    seen: set[str] = set()
    cursor: str | None = None
    pages: list[Page] = []

    while True:
        page = paginate(
            rows,
            order=CreatorSortOrder.VIDEO_COUNT_DESC,
            page_size=page_size,
            cursor=cursor,
        )
        pages.append(page)
        keys = {r.public_channel_key for r in page.rows}
        assert not (seen & keys), "pages overlap"
        seen |= keys
        if page.next_cursor is None:
            break
        cursor = page.next_cursor

    assert seen == {r.public_channel_key for r in rows}
    # Every page but the last is full.
    for page in pages[:-1]:
        assert len(page.rows) == page_size
