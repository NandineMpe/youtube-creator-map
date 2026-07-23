"""Versioned enumeration types for the creator-map domain.

All enums are string-valued for deterministic JSON serialization.
"""

from enum import StrEnum, unique


@unique
class CorpusClass(StrEnum):
    """Distinguishes candidate corpora from comparison corpora."""

    CANDIDATE = "Candidate"
    COMPARISON = "Comparison"


@unique
class SourceKind(StrEnum):
    """Classification of dataset source material type."""

    METADATA_ONLY = "MetadataOnly"
    MEDIA_INDEX = "MediaIndex"
    SUBTITLE_INDEX = "SubtitleIndex"


@unique
class AccessStatus(StrEnum):
    """Access review status for a dataset contract."""

    PROPOSED = "Proposed"
    APPROVED = "Approved"
    BLOCKED = "Blocked"


@unique
class OccurrenceUnit(StrEnum):
    """Unit of measurement for source occurrences."""

    CLIP = "Clip"
    TIMESTAMP = "Timestamp"
    SEGMENT = "Segment"
    ROW = "Row"
    VIDEO = "Video"


@unique
class VideoResolutionStatus(StrEnum):
    """Resolution status for a video identity."""

    RESOLVED = "Resolved"
    UNAVAILABLE_UNCLASSIFIED = "UnavailableUnclassified"
    INVALID = "Invalid"


@unique
class ChannelResolutionStatus(StrEnum):
    """Resolution status for a channel identity."""

    RESOLVED = "Resolved"
    UNAVAILABLE_UNCLASSIFIED = "UnavailableUnclassified"


@unique
class WorkItemState(StrEnum):
    """State machine for enrichment work items."""

    PENDING = "Pending"
    LEASED = "Leased"
    SUCCEEDED = "Succeeded"
    RETRYABLE_FAILURE = "RetryableFailure"
    TERMINAL_FAILURE = "TerminalFailure"


@unique
class EntityKind(StrEnum):
    """Kind of entity being enriched."""

    VIDEO = "Video"
    CHANNEL = "Channel"
