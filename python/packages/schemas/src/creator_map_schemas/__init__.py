"""Versioned contracts shared across restricted processing boundaries.

This package must not import pipeline orchestration or restricted infrastructure.
All models are immutable (frozen), strictly validated (fail-closed), and produce
deterministic JSON serialization (sorted keys, consistent formatting).
"""

from creator_map_schemas._aggregates import (
    CountrySummary,
    CoverageSummary,
    CreatorSummary,
    Filter,
)
from creator_map_schemas._base import DomainModel
from creator_map_schemas._coverage import (
    VideoResolutionPartition,
    VideoResolutionPartitionState,
)
from creator_map_schemas._dataset import DatasetContract
from creator_map_schemas._disclosure import DisclosurePolicy
from creator_map_schemas._enrichment import (
    EnrichmentPolicy,
    ErrorClass,
    FailureDisposition,
    ObservationTieBreaker,
    RetryPolicy,
)
from creator_map_schemas._enums import (
    AccessStatus,
    ChannelResolutionStatus,
    CorpusClass,
    EntityKind,
    OccurrenceUnit,
    SourceKind,
    VideoResolutionStatus,
    WorkItemState,
)
from creator_map_schemas._occurrence import NormalizedOccurrence
from creator_map_schemas._release import DatasetSnapshotRef, ReleaseManifest
from creator_map_schemas._resolution import ChannelResolution, VideoResolution
from creator_map_schemas._suppression import (
    SuppressionKind,
    SuppressionRecord,
    SuppressionScope,
)
from creator_map_schemas._types import (
    UNKNOWN_COUNTRY,
    CountryCode,
    Natural,
    NonEmptyStr,
    PositiveNatural,
)
from creator_map_schemas._work import QuotaLedger, WorkItem

PACKAGE_BOUNDARY = "shared-schemas"

__all__ = [
    # Base
    "DomainModel",
    # Enums
    "AccessStatus",
    "ChannelResolutionStatus",
    "CorpusClass",
    "EntityKind",
    "OccurrenceUnit",
    "SourceKind",
    "VideoResolutionStatus",
    "WorkItemState",
    # Types
    "CountryCode",
    "Natural",
    "NonEmptyStr",
    "PositiveNatural",
    "UNKNOWN_COUNTRY",
    # Dataset
    "DatasetContract",
    # Enrichment and retry policy
    "EnrichmentPolicy",
    "ErrorClass",
    "FailureDisposition",
    "ObservationTieBreaker",
    "RetryPolicy",
    # Coverage partition
    "VideoResolutionPartition",
    "VideoResolutionPartitionState",
    # Suppression
    "SuppressionKind",
    "SuppressionRecord",
    "SuppressionScope",
    # Occurrence
    "NormalizedOccurrence",
    # Resolution
    "ChannelResolution",
    "VideoResolution",
    # Work
    "QuotaLedger",
    "WorkItem",
    # Aggregates
    "CountrySummary",
    "CoverageSummary",
    "CreatorSummary",
    "Filter",
    # Disclosure
    "DisclosurePolicy",
    # Release
    "DatasetSnapshotRef",
    "ReleaseManifest",
    # Package boundary
    "PACKAGE_BOUNDARY",
]
