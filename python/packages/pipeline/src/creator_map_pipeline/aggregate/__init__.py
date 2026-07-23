"""Exact filtered aggregation and the publication boundary.

Computes country, creator, dataset, occurrence, and coverage aggregates with
exact distinct-set operations rather than additive approximations
(Requirement 5.12), then applies the versioned disclosure policy before any
artifact is generated.

Requirement refs: 5.1-5.13, 6.1-6.11, 7.1-7.11
"""

from creator_map_pipeline.aggregate.builder import (
    AggregateInputs,
    AggregateResult,
    build_aggregates,
)
from creator_map_pipeline.aggregate.disclosure import (
    DisclosureDecision,
    DisclosureEngine,
    public_channel_key,
)

__all__ = [
    "AggregateInputs",
    "AggregateResult",
    "DisclosureDecision",
    "DisclosureEngine",
    "build_aggregates",
    "public_channel_key",
]
