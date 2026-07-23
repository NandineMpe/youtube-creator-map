"""Dataset contract domain model.

Encodes Requirement 1.1–1.2: governed dataset registration with
immutable versioned contracts, review status, and source provenance.
"""

from __future__ import annotations

from creator_map_schemas._base import DomainModel
from creator_map_schemas._enums import AccessStatus, CorpusClass, OccurrenceUnit, SourceKind
from creator_map_schemas._types import NonEmptyStr


class DatasetContract(DomainModel):
    """A versioned registry record for one dataset snapshot.

    The pair (id, version) forms the Dataset_Contract_Key.
    All fields are required for a valid contract; missing fields result
    in fail-closed rejection per the design.
    """

    id: NonEmptyStr
    display_name: NonEmptyStr
    version: NonEmptyStr
    corpus_class: CorpusClass
    source_kind: SourceKind
    access_status: AccessStatus
    snapshot_digest: NonEmptyStr
    adapter_version: NonEmptyStr
    occurrence_unit: OccurrenceUnit
    source_citation: NonEmptyStr
    terms_review_id: NonEmptyStr
