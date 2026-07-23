"""Release manifest domain model.

Encodes Requirement 8.1: versioned public release index with pinned
enrichment cutoff, included snapshots, artifact digests, and policy versions.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field, model_validator

from creator_map_schemas._aggregates import Filter
from creator_map_schemas._base import DomainModel
from creator_map_schemas._types import NonEmptyStr


class DatasetSnapshotRef(DomainModel):
    """Reference to a specific dataset snapshot included in a release."""

    dataset_id: NonEmptyStr
    version: NonEmptyStr
    snapshot_digest: NonEmptyStr


class ReleaseManifest(DomainModel):
    """Versioned public release index.

    Contains all metadata needed to verify and load a release:
    generation time, enrichment cutoff, included snapshots, default filter,
    artifact digests for integrity verification, and policy versions.
    """

    release_id: NonEmptyStr
    generated_at: datetime
    enrichment_cutoff: datetime
    included_snapshots: tuple[DatasetSnapshotRef, ...] = Field(min_length=1)
    default_filter: Filter
    artifact_digests: tuple[tuple[str, str], ...] = Field(min_length=1)
    methodology_version: NonEmptyStr
    disclosure_policy_version: NonEmptyStr

    @model_validator(mode="after")
    def _validate_artifact_digests_sorted(self) -> ReleaseManifest:
        """Artifact digest paths must be sorted for deterministic manifests."""
        paths = [path for path, _ in self.artifact_digests]
        if paths != sorted(paths):
            msg = "artifact_digests must be sorted by path"
            raise ValueError(msg)
        if len(set(paths)) != len(paths):
            msg = "artifact_digests must contain unique paths"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_cutoff_before_generated(self) -> ReleaseManifest:
        """Enrichment cutoff must be at or before generation time."""
        if self.enrichment_cutoff > self.generated_at:
            msg = "enrichment_cutoff must be <= generated_at"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_included_snapshots_sorted(self) -> ReleaseManifest:
        """Included snapshots must be sorted by (dataset_id, version)."""
        keys = [(s.dataset_id, s.version) for s in self.included_snapshots]
        if keys != sorted(keys):
            msg = "included_snapshots must be sorted by (dataset_id, version)"
            raise ValueError(msg)
        if len(set(keys)) != len(keys):
            msg = "included_snapshots must contain unique (dataset_id, version) pairs"
            raise ValueError(msg)
        return self
