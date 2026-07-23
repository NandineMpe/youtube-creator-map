"""Source-adapter extraction framework with exact record accounting.

The central guarantee is Invariant 2 (Requirement 2.14): for every completed
extraction, accepted + rejected = examined, and emitted occurrences equal the
sum of per-record emitted counts. One accepted record may legitimately emit
several occurrences (a row carrying repeated clips), so expansion is
accounted separately rather than folded into the accepted count.

Requirement 2.11 forbids deduplicating source evidence: repeated clips,
timestamps, and rows are retained because they are what the occurrence count
measures.

Requirement refs: 2.7-2.16
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime

from creator_map_schemas import DatasetContract, NormalizedOccurrence

from creator_map_pipeline.identity import (
    Rejected,
    VideoId,
    normalize_video_id,
)
from creator_map_pipeline.snapshot import Quarantined, QuarantineReason, SourceRecord


class SchemaDriftError(RuntimeError):
    """Raised when a snapshot no longer matches its adapter's expectations.

    Requirement 2.16 requires the run to fail closed: no occurrence from the
    run is published, prior outputs are untouched, and a versioned adapter
    update plus a fresh report are required.
    """

    def __init__(self, adapter_version: str, detail: str) -> None:
        super().__init__(f"source schema drift detected by adapter {adapter_version}: {detail}")
        self.adapter_version = adapter_version
        self.detail = detail


@dataclass(frozen=True, slots=True)
class ExtractedOccurrence:
    """One occurrence an adapter produced from a source record."""

    video_id: VideoId
    clip_start: float | None = None
    clip_end: float | None = None


@dataclass(frozen=True, slots=True)
class RecordOutcome:
    """What one source record produced.

    A record is accepted or rejected, never both and never neither
    (Requirement 2.12). `occurrences` is non-empty only when accepted.
    """

    accepted: bool
    occurrences: tuple[ExtractedOccurrence, ...] = ()
    rejection_reason: str = ""
    #: Set when a record passes validation but cannot be fully provenanced.
    provenance_incomplete: bool = False

    def __post_init__(self) -> None:
        if self.accepted and self.rejection_reason:
            msg = "an accepted record must not carry a rejection reason"
            raise ValueError(msg)
        if not self.accepted and not self.rejection_reason:
            # Requirement 2.6/2.15: rejections always carry a reason.
            msg = "a rejected record must carry a non-empty rejection reason"
            raise ValueError(msg)
        if not self.accepted and self.occurrences:
            msg = "a rejected record must not emit occurrences"
            raise ValueError(msg)


@dataclass(slots=True)
class ExtractionReport:
    """Versioned accounting for one adapter run (Requirement 2.13, 2.14)."""

    dataset_id: str
    dataset_version: str
    snapshot_digest: str
    adapter_version: str
    schema_version: str
    records_examined: int = 0
    records_accepted: int = 0
    records_rejected: int = 0
    occurrences_emitted: int = 0
    #: Occurrences beyond one per accepted record.
    expansion_count: int = 0
    #: Accepted records excluded from publication for incomplete provenance.
    provenance_incomplete_count: int = 0
    rejection_reasons: dict[str, int] = field(default_factory=dict)

    def reconciles(self) -> bool:
        """Whether the report satisfies Invariant 2."""
        return (
            self.records_accepted + self.records_rejected == self.records_examined
            and self.occurrences_emitted == self.records_accepted + self.expansion_count
        )

    def assert_reconciles(self) -> None:
        """Fail closed when accounting does not balance."""
        if not self.reconciles():
            msg = (
                f"extraction accounting does not reconcile: "
                f"accepted={self.records_accepted} + rejected={self.records_rejected} "
                f"!= examined={self.records_examined}, or "
                f"emitted={self.occurrences_emitted} != accepted + "
                f"expansion={self.expansion_count}"
            )
            raise SchemaDriftError(self.adapter_version, msg)


class SourceAdapter(ABC):
    """Source-kind-specific extraction logic.

    An adapter maps one raw record to zero or more occurrences. It does not
    deduplicate, does not persist, and does not decide publication; those are
    the framework's concerns so every adapter accounts identically.
    """

    #: Bumped whenever extraction behaviour changes (Requirement 2.16).
    adapter_version: str = "1.0.0"
    #: The source schema this adapter understands.
    schema_version: str = "1.0.0"

    @abstractmethod
    def required_fields(self) -> frozenset[str]:
        """Field names a record must contain for this adapter to read it."""

    def optional_fields(self) -> frozenset[str]:
        """Field names the adapter uses when present but does not require.

        Columnar readers project `required | optional`, so declaring an
        optional field here is what makes it readable without making its
        absence a schema-drift failure.
        """
        return frozenset()

    @abstractmethod
    def extract(self, record: SourceRecord) -> RecordOutcome:
        """Map one source record to its outcome."""

    def validate_schema(self, sample: SourceRecord) -> None:
        """Fail closed when a record lacks the expected fields.

        Requirement 2.16: drift is detected before any occurrence is
        published, not midway through a run.
        """
        missing = self.required_fields() - set(sample.fields)
        if missing:
            raise SchemaDriftError(
                self.adapter_version,
                f"missing expected fields: {', '.join(sorted(missing))}",
            )


def _parse_optional_float(raw: str | None) -> float | None:
    """Parse a bound, returning None when absent or unparseable."""
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


class VideoIdColumnAdapter(SourceAdapter):
    """Adapter for sources with one identifier per record.

    Handles the common metadata-index shape: a column holding a bare ID or a
    YouTube URL, with optional clip bounds.
    """

    def __init__(
        self,
        *,
        video_field: str = "video_id",
        clip_start_field: str = "clip_start",
        clip_end_field: str = "clip_end",
        adapter_version: str = "1.0.0",
    ) -> None:
        self.video_field = video_field
        self.clip_start_field = clip_start_field
        self.clip_end_field = clip_end_field
        self.adapter_version = adapter_version

    def required_fields(self) -> frozenset[str]:
        return frozenset({self.video_field})

    def optional_fields(self) -> frozenset[str]:
        return frozenset({self.clip_start_field, self.clip_end_field})

    def extract(self, record: SourceRecord) -> RecordOutcome:
        raw = record.fields.get(self.video_field)
        result = normalize_video_id(raw)
        if isinstance(result, Rejected):
            return RecordOutcome(accepted=False, rejection_reason=result.detail)

        start = _parse_optional_float(record.fields.get(self.clip_start_field))
        end = _parse_optional_float(record.fields.get(self.clip_end_field))

        # Requirement 2.10: invalid bounds quarantine the record rather than
        # silently dropping the bounds and publishing a bare occurrence.
        if (start is None) != (end is None):
            return RecordOutcome(accepted=False, rejection_reason="incomplete_clip_bounds")
        if start is not None and end is not None and not (0 <= start < end):
            return RecordOutcome(accepted=False, rejection_reason="invalid_clip_bounds")

        return RecordOutcome(
            accepted=True,
            occurrences=(ExtractedOccurrence(video_id=result, clip_start=start, clip_end=end),),
        )


class RepeatedClipAdapter(SourceAdapter):
    """Adapter for records carrying several clips of one video.

    Demonstrates expansion: one accepted record emits several occurrences,
    all retained (Requirement 2.11) and accounted (Requirement 2.13).
    """

    def __init__(
        self,
        *,
        video_field: str = "video_id",
        clips_field: str = "clips",
        adapter_version: str = "1.0.0",
    ) -> None:
        self.video_field = video_field
        self.clips_field = clips_field
        self.adapter_version = adapter_version

    def required_fields(self) -> frozenset[str]:
        return frozenset({self.video_field, self.clips_field})

    def extract(self, record: SourceRecord) -> RecordOutcome:
        result = normalize_video_id(record.fields.get(self.video_field))
        if isinstance(result, Rejected):
            return RecordOutcome(accepted=False, rejection_reason=result.detail)

        raw_clips = record.fields.get(self.clips_field, "")
        occurrences: list[ExtractedOccurrence] = []

        for pair in (chunk for chunk in raw_clips.split(";") if chunk):
            parts = pair.split("-", 1)
            if len(parts) != 2:
                return RecordOutcome(accepted=False, rejection_reason="malformed_clip_pair")
            start = _parse_optional_float(parts[0])
            end = _parse_optional_float(parts[1])
            if start is None or end is None or not (0 <= start < end):
                return RecordOutcome(accepted=False, rejection_reason="invalid_clip_bounds")
            occurrences.append(ExtractedOccurrence(video_id=result, clip_start=start, clip_end=end))

        if not occurrences:
            return RecordOutcome(accepted=False, rejection_reason="no_valid_clips")

        return RecordOutcome(accepted=True, occurrences=tuple(occurrences))


@dataclass(slots=True)
class ExtractionResult:
    """Everything one extraction run produced."""

    occurrences: list[NormalizedOccurrence] = field(default_factory=list)
    rejects: list[Quarantined] = field(default_factory=list)
    report: ExtractionReport | None = None


def extract_records(
    records: list[SourceRecord],
    *,
    adapter: SourceAdapter,
    contract: DatasetContract,
    prior_quarantined: list[Quarantined] | None = None,
    extracted_at: datetime | None = None,
) -> ExtractionResult:
    """Run an adapter over records, accounting for every one exactly once.

    `prior_quarantined` carries reader-stage rejections (size limits, formula
    content, malformed rows) so the report accounts for the whole snapshot
    rather than only what reached the adapter.
    """
    timestamp = extracted_at or datetime.now(UTC)
    result = ExtractionResult()
    report = ExtractionReport(
        dataset_id=contract.id,
        dataset_version=contract.version,
        snapshot_digest=contract.snapshot_digest,
        adapter_version=adapter.adapter_version,
        schema_version=adapter.schema_version,
    )

    # Reader-stage quarantines are examined-and-rejected records.
    for quarantined in prior_quarantined or []:
        report.records_examined += 1
        report.records_rejected += 1
        report.rejection_reasons[quarantined.reason.value] = (
            report.rejection_reasons.get(quarantined.reason.value, 0) + 1
        )
        result.rejects.append(quarantined)

    if records:
        # Drift is checked once, up front: publishing half a run and then
        # discovering drift would violate the fail-closed requirement.
        adapter.validate_schema(records[0])

    for record in records:
        report.records_examined += 1
        outcome = adapter.extract(record)

        if not outcome.accepted:
            report.records_rejected += 1
            report.rejection_reasons[outcome.rejection_reason] = (
                report.rejection_reasons.get(outcome.rejection_reason, 0) + 1
            )
            result.rejects.append(
                Quarantined(
                    locator=record.locator,
                    reason=QuarantineReason.MALFORMED_RECORD,
                    detail=outcome.rejection_reason,
                )
            )
            continue

        # Requirement 2.8: a record that passed validation but cannot be
        # fully provenanced still counts as accepted for accounting, and is
        # withheld from publication rather than published incomplete.
        report.records_accepted += 1
        emitted = len(outcome.occurrences)
        report.occurrences_emitted += emitted
        report.expansion_count += emitted - 1

        if outcome.provenance_incomplete:
            report.provenance_incomplete_count += 1
            continue

        for extracted in outcome.occurrences:
            result.occurrences.append(
                NormalizedOccurrence(
                    dataset_id=contract.id,
                    snapshot_digest=contract.snapshot_digest,
                    source_locator=record.locator,
                    video_id=extracted.video_id.value,
                    clip_start=extracted.clip_start,
                    clip_end=extracted.clip_end,
                    occurrence_unit=contract.occurrence_unit,
                    extracted_at=timestamp,
                    adapter_version=adapter.adapter_version,
                )
            )

    report.assert_reconciles()
    result.report = report
    return result
