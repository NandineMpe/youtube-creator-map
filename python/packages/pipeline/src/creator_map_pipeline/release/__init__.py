"""Release validation, staging, activation, and rollback.

A release moves through four states, and the boundaries between them are
what the requirements actually constrain: a candidate is assembled, gated,
staged, then activated by a single atomic pointer change. Failure at any
point leaves the previously active release serving (Requirement 8.3).

Requirement refs: 8.1-8.12, 12.12, 12.13, 14.1, 14.9-14.11, 15.3, 15.14, 15.15
"""

from creator_map_pipeline.release.gates import (
    DEFAULT_GATES,
    OVERVIEW_BUDGET_BYTES,
    GateOutcome,
    GateResult,
    ReleaseCandidate,
    ValidationReport,
    canonical_report_bytes,
    run_gates,
)
from creator_map_pipeline.release.manager import (
    ActivationError,
    ReleaseManager,
    StagedRelease,
    candidate_from_artifacts,
)

__all__ = [
    "DEFAULT_GATES",
    "OVERVIEW_BUDGET_BYTES",
    "ActivationError",
    "GateOutcome",
    "GateResult",
    "ReleaseCandidate",
    "ReleaseManager",
    "StagedRelease",
    "ValidationReport",
    "candidate_from_artifacts",
    "canonical_report_bytes",
    "run_gates",
]
