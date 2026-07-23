"""Aggregate and filter domain models.

Encodes Requirements 5.1–5.13, 6.1–6.6: exact filtered aggregates,
coverage partitions, and creator summaries with deterministic semantics.
"""

from __future__ import annotations

from datetime import date

from pydantic import Field, model_validator

from creator_map_schemas._base import DomainModel
from creator_map_schemas._enums import CorpusClass
from creator_map_schemas._types import CountryCode, Natural, NonEmptyStr


class Filter(DomainModel):
    """Active filter for deriving views and aggregates.

    Both datasets and corpus_classes must be non-empty sets.
    Sets are stored as sorted tuples for deterministic serialization.
    """

    datasets: tuple[str, ...] = Field(min_length=1)
    corpus_classes: tuple[CorpusClass, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_sorted_and_unique(self) -> Filter:
        """Ensure deterministic ordering: sorted, no duplicates."""
        if len(set(self.datasets)) != len(self.datasets):
            msg = "datasets must contain unique values"
            raise ValueError(msg)
        if list(self.datasets) != sorted(self.datasets):
            msg = "datasets must be sorted for deterministic serialization"
            raise ValueError(msg)
        if len(set(self.corpus_classes)) != len(self.corpus_classes):
            msg = "corpus_classes must contain unique values"
            raise ValueError(msg)
        if list(self.corpus_classes) != sorted(self.corpus_classes):
            msg = "corpus_classes must be sorted for deterministic serialization"
            raise ValueError(msg)
        return self


class CountrySummary(DomainModel):
    """Aggregate counts for one country bucket under one release and filter.

    Country is ISO 3166 alpha-2 or UNKNOWN_COUNTRY sentinel "XX".
    """

    country: CountryCode
    creator_count: Natural
    represented_video_count: Natural
    source_occurrence_count: Natural
    resolved_video_count: Natural
    unavailable_video_count: Natural


class CreatorSummary(DomainModel):
    """Disclosure-reviewed creator aggregate for public presentation.

    channel_id is a PublicChannelKey (disclosure-approved identifier).
    dataset_breakdown maps dataset IDs to distinct video counts per dataset.
    """

    channel_id: NonEmptyStr
    display_name: NonEmptyStr
    country: CountryCode
    represented_video_count: Natural
    dataset_breakdown: tuple[tuple[str, int], ...] = Field(default_factory=tuple)
    last_observed_at: date

    @model_validator(mode="after")
    def _validate_breakdown_sorted(self) -> CreatorSummary:
        """Dataset breakdown keys must be sorted for deterministic output."""
        keys = [k for k, _ in self.dataset_breakdown]
        if keys != sorted(keys):
            msg = "dataset_breakdown must be sorted by dataset key"
            raise ValueError(msg)
        if len(set(keys)) != len(keys):
            msg = "dataset_breakdown must contain unique dataset keys"
            raise ValueError(msg)
        for _, count in self.dataset_breakdown:
            if count < 0:
                msg = f"dataset_breakdown counts must be >= 0; got {count}"
                raise ValueError(msg)
        return self


class CoverageSummary(DomainModel):
    """Coverage metrics for a release and filter.

    Partitions all distinct input videos into resolution states.
    Channel coverage splits into known-country and unknown-country.
    """

    input_occurrence_count: Natural
    distinct_input_video_count: Natural
    resolved_video_count: Natural
    unavailable_video_count: Natural
    resolved_channel_count: Natural
    known_country_channel_count: Natural
    unknown_country_channel_count: Natural

    @model_validator(mode="after")
    def _validate_channel_partition(self) -> CoverageSummary:
        """known + unknown channel counts must equal resolved channel count."""
        if (
            self.known_country_channel_count + self.unknown_country_channel_count
            != self.resolved_channel_count
        ):
            msg = (
                "known_country_channel_count + unknown_country_channel_count "
                "must equal resolved_channel_count"
            )
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_video_partition(self) -> CoverageSummary:
        """Resolved + unavailable video counts must not exceed distinct input videos."""
        if (
            self.resolved_video_count + self.unavailable_video_count
            > self.distinct_input_video_count
        ):
            msg = (
                "resolved_video_count + unavailable_video_count "
                "must not exceed distinct_input_video_count"
            )
            raise ValueError(msg)
        return self
