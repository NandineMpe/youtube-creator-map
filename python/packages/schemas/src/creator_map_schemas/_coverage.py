"""Video resolution partition for coverage reporting.

Requirement 6.2-6.4 require every distinct input video to be assigned to
exactly one of five mutually disjoint states whose counts sum to the
distinct input-video count.

This is deliberately distinct from VideoResolutionStatus: that enum
describes one observation's outcome, while the partition describes how the
Aggregate_Builder classifies an identity for a release, folding in
work-item state (pending/retryable/terminal) that no single observation
carries.

Requirement refs: 6.1-6.6
"""

from __future__ import annotations

from enum import StrEnum, unique

from pydantic import model_validator

from creator_map_schemas._base import DomainModel
from creator_map_schemas._types import Natural


@unique
class VideoResolutionPartitionState(StrEnum):
    """The five mutually exclusive resolution states for a distinct video."""

    RESOLVED = "Resolved"
    UNAVAILABLE_UNCLASSIFIED = "UnavailableUnclassified"
    RETRYABLE_OR_PENDING = "RetryableOrPending"
    INVALID = "Invalid"
    TERMINAL_FAILURE = "TerminalFailure"


class VideoResolutionPartition(DomainModel):
    """Counts for each state of the video resolution partition.

    The model enforces the conservation law from Requirement 6.4 at
    construction: the five state counts must sum to the distinct input
    video count. A partition that does not reconcile cannot be built,
    so a drifted aggregate fails closed rather than publishing.
    """

    distinct_input_video_count: Natural
    resolved_count: Natural
    unavailable_unclassified_count: Natural
    retryable_or_pending_count: Natural
    invalid_count: Natural
    terminal_failure_count: Natural

    @model_validator(mode="after")
    def _validate_partition_sums(self) -> VideoResolutionPartition:
        """State counts must sum exactly to the distinct input video count."""
        total = (
            self.resolved_count
            + self.unavailable_unclassified_count
            + self.retryable_or_pending_count
            + self.invalid_count
            + self.terminal_failure_count
        )
        if total != self.distinct_input_video_count:
            msg = (
                f"video resolution partition must sum to distinct_input_video_count; "
                f"got {total} != {self.distinct_input_video_count}"
            )
            raise ValueError(msg)
        return self

    def count_for(self, state: VideoResolutionPartitionState) -> int:
        """Return the count assigned to one partition state."""
        mapping = {
            VideoResolutionPartitionState.RESOLVED: self.resolved_count,
            VideoResolutionPartitionState.UNAVAILABLE_UNCLASSIFIED: (
                self.unavailable_unclassified_count
            ),
            VideoResolutionPartitionState.RETRYABLE_OR_PENDING: self.retryable_or_pending_count,
            VideoResolutionPartitionState.INVALID: self.invalid_count,
            VideoResolutionPartitionState.TERMINAL_FAILURE: self.terminal_failure_count,
        }
        return mapping[state]
