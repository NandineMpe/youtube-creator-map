"""Property tests for country-detail consistency (design Property 12).

Requirements 10.1 and 10.2 require the detail panel to agree with the map
and table it was opened from. The failure this guards against is quiet:
two views of the same release showing different numbers looks like a data
problem to a reader, and neither number is obviously the wrong one.

The properties are checked against generated releases rather than the one
on disk, because a single build exercises one shape and the disagreements
appear at the edges — an empty country, a country whose creators all sit
on one page, a country with more pages than the first page reveals.

Validates: Requirements 10.1, 10.2
"""

from __future__ import annotations

from creator_map_pipeline.aggregate.fixtures import build_fixture_release
from hypothesis import given, settings
from hypothesis import strategies as st

# --- A generated release --------------------------------------------------


@st.composite
def releases(draw: st.DrawFn):  # type: ignore[no-untyped-def]
    """A fixture release built from a generated seed and page size.

    Varying the page size is what makes this worth running repeatedly: a
    country that fits on one page and a country that spans six exercise
    different paths through the same summary arithmetic.
    """
    seed = draw(st.text(alphabet="abcdefghijklmnop", min_size=1, max_size=12))
    page_size = draw(st.integers(min_value=1, max_value=60))
    return build_fixture_release(seed=f"prop-{seed}", page_size=page_size)


def overview_of(fixture):  # type: ignore[no-untyped-def]
    return fixture.by_path("overview.json").payload


def detail_of(fixture, country: str):  # type: ignore[no-untyped-def]
    return fixture.by_path(f"countries/{country}.json").payload


# --- Property: the panel agrees with the summary it was opened from -------


@given(fixture=releases())
@settings(max_examples=40, deadline=None)
def test_detail_totals_equal_the_overview_summary(fixture) -> None:  # type: ignore[no-untyped-def]
    """Requirement 10.2, stated directly.

    A reader who clicks a country expects the panel to restate what the
    table row said, not to recompute it and get something else.
    """
    for summary in overview_of(fixture)["countries"]:
        detail = detail_of(fixture, summary["country"])

        assert detail["creatorCount"] == summary["creatorCount"]
        assert detail["representedVideoCount"] == summary["representedVideoCount"]
        assert detail["sourceOccurrenceCount"] == summary["sourceOccurrenceCount"]


@given(fixture=releases())
@settings(max_examples=40, deadline=None)
def test_the_panel_is_for_the_country_that_was_selected(fixture) -> None:  # type: ignore[no-untyped-def]
    """Requirement 10.1. Shard paths are constructed from the country
    code, so an off-by-one in that construction would silently show a
    reader someone else's data."""
    for summary in overview_of(fixture)["countries"]:
        assert detail_of(fixture, summary["country"])["country"] == summary["country"]


# --- Property: pagination does not change the totals ----------------------


@given(fixture=releases())
@settings(max_examples=40, deadline=None)
def test_advertised_row_count_matches_the_creator_count(fixture) -> None:  # type: ignore[no-untyped-def]
    """The defect that shipped once: 2,747 rows advertised, 50 published,
    and a cursor that 404ed partway through."""
    for summary in overview_of(fixture)["countries"]:
        detail = detail_of(fixture, summary["country"])

        assert detail["firstPage"]["totalRows"] == summary["creatorCount"]


@given(fixture=releases())
@settings(max_examples=40, deadline=None)
def test_every_advertised_page_exists_and_is_reachable(fixture) -> None:  # type: ignore[no-untyped-def]
    paths = {a.path for a in fixture.artifacts}

    for summary in overview_of(fixture)["countries"]:
        detail = detail_of(fixture, summary["country"])
        for sort_order, pages in detail["pageIndex"].items():
            assert pages, f"{summary['country']}/{sort_order} advertises no pages"
            for path in pages:
                assert path in paths, path


@given(fixture=releases())
@settings(max_examples=40, deadline=None)
def test_pages_partition_the_creators_exactly(fixture) -> None:  # type: ignore[no-untyped-def]
    """Every creator appears once across the pages — no gaps, no repeats.

    Both failures are invisible in a single page view: a gap looks like a
    smaller country, a repeat like a larger one.
    """
    by_path = {a.path: a.payload for a in fixture.artifacts}

    for summary in overview_of(fixture)["countries"]:
        detail = detail_of(fixture, summary["country"])
        for sort_order, pages in detail["pageIndex"].items():
            keys = [row["publicChannelKey"] for path in pages for row in by_path[path]["rows"]]

            assert len(keys) == summary["creatorCount"], sort_order
            assert len(set(keys)) == len(keys), f"{sort_order} repeats a creator"


@given(fixture=releases())
@settings(max_examples=40, deadline=None)
def test_both_sort_orders_contain_the_same_creators(fixture) -> None:  # type: ignore[no-untyped-def]
    """Sorting reorders rows; it must not add or drop any."""
    by_path = {a.path: a.payload for a in fixture.artifacts}

    for summary in overview_of(fixture)["countries"]:
        detail = detail_of(fixture, summary["country"])
        sets = [
            {row["publicChannelKey"] for path in pages for row in by_path[path]["rows"]}
            for pages in detail["pageIndex"].values()
        ]

        assert all(s == sets[0] for s in sets), summary["country"]


@given(fixture=releases())
@settings(max_examples=40, deadline=None)
def test_no_page_exceeds_the_declared_page_size(fixture) -> None:  # type: ignore[no-untyped-def]
    """Requirement 14.5 partitions rows. A page carrying everything
    satisfies no bound and defeats the partitioning."""
    by_path = {a.path: a.payload for a in fixture.artifacts}

    for summary in overview_of(fixture)["countries"]:
        detail = detail_of(fixture, summary["country"])
        for pages in detail["pageIndex"].values():
            for path in pages:
                page = by_path[path]
                assert len(page["rows"]) <= page["pageSize"]


# --- Property: the first page is the first page ---------------------------


@given(fixture=releases())
@settings(max_examples=40, deadline=None)
def test_the_embedded_first_page_matches_the_addressable_one(fixture) -> None:  # type: ignore[no-untyped-def]
    """The shard embeds page 0 to save a round trip. If the embedded copy
    ever diverged from the addressable one, a reader would see different
    rows before and after paging away and back."""
    by_path = {a.path: a.payload for a in fixture.artifacts}

    for summary in overview_of(fixture)["countries"]:
        detail = detail_of(fixture, summary["country"])
        first_path = detail["pageIndex"][detail["firstPage"]["sortOrder"]][0]

        assert detail["firstPage"]["rows"] == by_path[first_path]["rows"]


@given(fixture=releases())
@settings(max_examples=40, deadline=None)
def test_a_next_cursor_is_present_exactly_when_more_rows_follow(fixture) -> None:  # type: ignore[no-untyped-def]
    """A cursor on the last page invites a fetch that 404s; a missing one
    on a middle page truncates the country silently."""
    for summary in overview_of(fixture)["countries"]:
        page = detail_of(fixture, summary["country"])["firstPage"]
        has_more = page["totalRows"] > len(page["rows"])

        assert (page["nextCursor"] is not None) == has_more, summary["country"]


# --- Property: dataset breakdown does not silently become a total ---------


@given(fixture=releases())
@settings(max_examples=40, deadline=None)
def test_dataset_breakdown_never_undercounts_represented_videos(fixture) -> None:  # type: ignore[no-untyped-def]
    """Per-dataset counts sum to at least the represented total, because
    a video in two datasets is one represented video and two dataset
    observations. Summing them client-side overstates — which is why the
    breakdown is published rather than left to be derived."""
    for summary in overview_of(fixture)["countries"]:
        detail = detail_of(fixture, summary["country"])
        breakdown = sum(d["representedVideoCount"] for d in detail["datasetBreakdown"])

        assert breakdown >= detail["representedVideoCount"], summary["country"]


# --- Property: totals roll up ---------------------------------------------


@given(fixture=releases())
@settings(max_examples=40, deadline=None)
def test_country_creator_counts_sum_to_the_release_total(fixture) -> None:  # type: ignore[no-untyped-def]
    overview = overview_of(fixture)

    assert sum(c["creatorCount"] for c in overview["countries"]) == overview["creatorCount"]


@given(fixture=releases())
@settings(max_examples=40, deadline=None)
def test_no_country_reports_more_videos_than_the_release(fixture) -> None:  # type: ignore[no-untyped-def]
    overview = overview_of(fixture)

    for summary in overview["countries"]:
        assert summary["representedVideoCount"] <= overview["representedVideoCount"]
