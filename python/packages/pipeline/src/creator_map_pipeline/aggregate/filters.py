"""Supported filter combinations and their canonical artifact keys.

Requirement 9.6 requires a filter change to update every surface with exact
counts, and Requirement 5.12 forbids additive approximations. Those two
together rule out computing a filtered total in the browser: dataset
overlap means per-dataset counts cannot be summed, and the distinct-ID sets
needed to do it exactly are precisely what the publication boundary keeps
restricted.

So each supported combination gets its own precomputed artifact, as
design.md prescribes. The combination set is deliberately bounded rather
than the full power set — with n datasets the power set is 2^n, which is
unbuildable past a handful, and most of those combinations nobody selects.

Requirement refs: 5.8, 5.9, 5.12, 9.6
"""

from __future__ import annotations

from dataclasses import dataclass

from creator_map_schemas import CorpusClass, Filter

# Separators for the artifact key. Chosen from the characters object
# storage accepts in a key: Supabase rejects `+` and `~` with InvalidKey,
# which fails the publish for every per-filter overview. `_` joins items
# within a group and `__` separates the two groups. Dataset ids and
# corpus-class names use hyphens and letters only, so neither separator
# can occur inside a value and the key stays unambiguously parseable.
#
# This is the storage key, not the URL view-state; the two are
# independent, and the canonical URL codec keeps its own form.
_ITEM_SEPARATOR = "_"
_GROUP_SEPARATOR = "__"


def filter_key(active: Filter) -> str:
    """Return the canonical artifact key for a filter.

    Sorted and joined so two equivalent selections address the same
    artifact, which is the storage-side counterpart of the URL codec's
    canonical form (Requirement 11.2). The result is a valid object key on
    the storage backends the release is published to.
    """
    datasets = _ITEM_SEPARATOR.join(sorted(active.datasets))
    classes = _ITEM_SEPARATOR.join(sorted(c.value for c in active.corpus_classes))
    return f"{classes}{_GROUP_SEPARATOR}{datasets}"


def overview_path(release_id: str, active: Filter, *, is_default: bool) -> str:
    """Return the delivery path for one filter's overview artifact.

    The default filter keeps the unsuffixed path so the initial load needs
    no key computation, and a client that knows nothing about filters still
    finds the right artifact.
    """
    if is_default:
        return f"releases/{release_id}/overview.json"
    return f"releases/{release_id}/overviews/{filter_key(active)}.json"


@dataclass(frozen=True, slots=True)
class SupportedFilter:
    """One filter combination a release publishes aggregates for."""

    active: Filter
    label: str
    is_default: bool = False


def enumerate_supported(
    dataset_ids: tuple[str, ...],
    corpus_classes: tuple[CorpusClass, ...],
) -> tuple[SupportedFilter, tuple[SupportedFilter, ...]]:
    """Return the default filter and every other supported combination.

    The set is:
      - everything (the default),
      - each corpus class alone, when more than one exists,
      - each dataset alone, when more than one exists.

    A visitor selecting an unsupported combination is handled by the client
    falling back to the nearest published filter rather than by the build
    attempting every subset.
    """
    if not dataset_ids or not corpus_classes:
        msg = "a release needs at least one dataset and one corpus class"
        raise ValueError(msg)

    everything = Filter(
        datasets=tuple(sorted(dataset_ids)),
        corpus_classes=tuple(sorted(corpus_classes)),
    )
    default = SupportedFilter(active=everything, label="All datasets", is_default=True)

    others: list[SupportedFilter] = []

    if len(corpus_classes) > 1:
        for corpus_class in sorted(corpus_classes):
            others.append(
                SupportedFilter(
                    active=Filter(
                        datasets=tuple(sorted(dataset_ids)),
                        corpus_classes=(corpus_class,),
                    ),
                    label=f"{corpus_class.value} corpora",
                )
            )

    if len(dataset_ids) > 1:
        for dataset_id in sorted(dataset_ids):
            others.append(
                SupportedFilter(
                    active=Filter(
                        datasets=(dataset_id,),
                        corpus_classes=tuple(sorted(corpus_classes)),
                    ),
                    label=dataset_id,
                )
            )

    return default, tuple(others)
