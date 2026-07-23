"""Tests for filter keys and their delivery paths.

The property that matters here was learned the hard way: the artifact key
is not just a URL fragment, it is an *object storage key*, and Supabase
rejects `+` and `~` with InvalidKey. A key that reads fine and serves
fine locally fails at publish for every per-filter overview, and the
failure only appears when the bytes reach the store.

So these pin the key to the characters object storage accepts, and pin
the invariants that make the key a usable address at all: canonical
(order-independent), unique per combination, and distinct from the
default's unsuffixed path.

Requirement refs: 5.8, 5.9, 5.12, 9.6, 11.2, 15.13
"""

from __future__ import annotations

import re

from creator_map_pipeline.aggregate.filters import (
    enumerate_supported,
    filter_key,
    overview_path,
)
from creator_map_schemas import CorpusClass, Filter

# Characters an object key may safely contain. Supabase Storage rejects
# `+` and `~`; these are the ones every backend the release targets
# accepts.
_SAFE_KEY = re.compile(r"^[A-Za-z0-9._\-/]+$")


def filt(datasets: tuple[str, ...], classes: tuple[CorpusClass, ...]) -> Filter:
    return Filter(datasets=datasets, corpus_classes=classes)


# --- The storage-safety property -----------------------------------------


def test_the_key_contains_no_character_object_storage_rejects() -> None:
    """The bug this file exists for.

    `+` and `~` produce InvalidKey on Supabase, which failed the publish
    for every per-filter overview after everything looked fine locally.
    """
    key = filter_key(
        filt(
            ("youtube-commons", "youtube-commons-b"),
            (CorpusClass.CANDIDATE, CorpusClass.COMPARISON),
        )
    )

    assert "+" not in key
    assert "~" not in key
    assert _SAFE_KEY.match(key), key


def test_every_enumerated_filter_path_is_a_safe_key() -> None:
    """Not just the one combination above — every path a release
    publishes has to be uploadable."""
    default, others = enumerate_supported(
        ("youtube-commons", "youtube-commons-b"),
        (CorpusClass.CANDIDATE, CorpusClass.COMPARISON),
    )

    for supported in (default, *others):
        path = overview_path("r1", supported.active, is_default=supported.is_default)
        assert _SAFE_KEY.match(path), path


def test_paths_with_realistic_ids_stay_safe() -> None:
    """Dataset ids carry hyphens; the key must survive them."""
    key = filter_key(filt(("a-b-c", "d-e-f"), (CorpusClass.CANDIDATE,)))

    assert _SAFE_KEY.match(key), key


# --- Canonical and unique -------------------------------------------------


def test_the_key_sorts_its_members() -> None:
    """The key must be a canonical form of the combination.

    `Filter` already requires sorted members, so an unsorted selection
    cannot even be constructed — but `filter_key` sorts again rather than
    relying on that, so the address stays stable if the model's
    invariant ever loosens. This checks the members land in sorted order
    in the key itself.
    """
    key = filter_key(filt(("a-one", "b-two", "c-three"), (CorpusClass.CANDIDATE,)))

    datasets_part = key.split("__", 1)[1]
    assert datasets_part == "a-one_b-two_c-three"


def test_different_combinations_get_different_keys() -> None:
    keys = {
        filter_key(filt(("a",), (CorpusClass.CANDIDATE,))),
        filter_key(filt(("b",), (CorpusClass.CANDIDATE,))),
        filter_key(filt(("a", "b"), (CorpusClass.CANDIDATE,))),
        filter_key(filt(("a",), (CorpusClass.COMPARISON,))),
    }

    assert len(keys) == 4


# --- The default keeps the unsuffixed path --------------------------------


def test_the_default_filter_uses_the_bare_overview_path() -> None:
    """The initial load must find the overview without knowing anything
    about filter keys."""
    everything = filt(("a", "b"), (CorpusClass.CANDIDATE, CorpusClass.COMPARISON))

    path = overview_path("r1", everything, is_default=True)

    assert path == "releases/r1/overview.json"


def test_a_non_default_filter_lives_under_overviews() -> None:
    path = overview_path("r1", filt(("a",), (CorpusClass.CANDIDATE,)), is_default=False)

    assert path.startswith("releases/r1/overviews/")
    assert path.endswith(".json")


# --- Enumeration ----------------------------------------------------------


def test_enumeration_yields_one_default_marked_as_such() -> None:
    default, _ = enumerate_supported(("a", "b"), (CorpusClass.CANDIDATE,))

    assert default.is_default


def test_single_dataset_and_class_yields_only_the_default() -> None:
    """With nothing to choose between, extra per-filter artifacts would
    be published and never selected."""
    default, others = enumerate_supported(("only",), (CorpusClass.CANDIDATE,))

    assert default.is_default
    assert others == ()


def test_every_enumerated_key_is_unique() -> None:
    default, others = enumerate_supported(
        ("a", "b", "c"), (CorpusClass.CANDIDATE, CorpusClass.COMPARISON)
    )

    keys = [overview_path("r1", s.active, is_default=s.is_default) for s in (default, *others)]

    assert len(set(keys)) == len(keys)
