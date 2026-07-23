"""Tests for the source-adapter extraction framework.

Includes Property 2 (Occurrence Conservation), validating Requirements
2.12-2.14.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from creator_map_pipeline.extraction import (
    ExtractedOccurrence,
    ExtractionReport,
    RecordOutcome,
    RepeatedClipAdapter,
    SchemaDriftError,
    SourceAdapter,
    VideoIdColumnAdapter,
    extract_records,
)
from creator_map_pipeline.identity import VideoId
from creator_map_pipeline.snapshot import Quarantined, QuarantineReason, SourceRecord
from creator_map_schemas import (
    AccessStatus,
    CorpusClass,
    DatasetContract,
    OccurrenceUnit,
    SourceKind,
)
from hypothesis import given, settings
from hypothesis import strategies as st

VALID_ID = "dQw4w9WgXcQ"
OTHER_ID = "9bZkp7q19f0"
INSTANT = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)


def contract(**overrides: object) -> DatasetContract:
    fields: dict[str, object] = {
        "id": "ds",
        "display_name": "DS",
        "version": "v1",
        "corpus_class": CorpusClass.CANDIDATE,
        "source_kind": SourceKind.METADATA_ONLY,
        "access_status": AccessStatus.APPROVED,
        "snapshot_digest": "sha256:" + "a" * 64,
        "adapter_version": "1.0.0",
        "occurrence_unit": OccurrenceUnit.CLIP,
        "source_citation": "https://example.invalid/ds",
        "terms_review_id": "r1",
    }
    fields.update(overrides)
    return DatasetContract.model_validate(fields)


def record(index: int, **fields: str) -> SourceRecord:
    return SourceRecord(locator=f"snap:row-{index}", fields=fields)


# --- Record outcome invariants -------------------------------------------


def test_rejected_outcome_requires_a_reason() -> None:
    with pytest.raises(ValueError, match="non-empty rejection reason"):
        RecordOutcome(accepted=False)


def test_rejected_outcome_cannot_emit_occurrences() -> None:
    with pytest.raises(ValueError, match="must not emit occurrences"):
        RecordOutcome(
            accepted=False,
            rejection_reason="bad",
            occurrences=(ExtractedOccurrence(VideoId(VALID_ID)),),
        )


def test_accepted_outcome_cannot_carry_a_rejection_reason() -> None:
    with pytest.raises(ValueError, match="must not carry a rejection reason"):
        RecordOutcome(accepted=True, rejection_reason="bad")


# --- Basic extraction ----------------------------------------------------


def test_extracts_bare_ids_and_urls() -> None:
    records = [
        record(0, video_id=VALID_ID),
        record(1, video_id=f"https://youtu.be/{OTHER_ID}"),
    ]
    result = extract_records(
        records,
        adapter=VideoIdColumnAdapter(),
        contract=contract(),
        extracted_at=INSTANT,
    )

    assert [o.video_id for o in result.occurrences] == [VALID_ID, OTHER_ID]
    assert result.report is not None
    assert result.report.records_accepted == 2
    assert result.report.reconciles()


def test_preserves_provenance_on_every_occurrence() -> None:
    """Requirement 2.7: mandatory provenance fields are recorded."""
    result = extract_records(
        [record(0, video_id=VALID_ID)],
        adapter=VideoIdColumnAdapter(),
        contract=contract(),
        extracted_at=INSTANT,
    )
    occurrence = result.occurrences[0]

    assert occurrence.dataset_id == "ds"
    assert occurrence.snapshot_digest == "sha256:" + "a" * 64
    assert occurrence.source_locator == "snap:row-0"
    assert occurrence.adapter_version == "1.0.0"
    assert occurrence.occurrence_unit is OccurrenceUnit.CLIP
    assert occurrence.extracted_at == INSTANT


def test_malformed_identifier_is_rejected_with_reason() -> None:
    result = extract_records(
        [record(0, video_id="not-an-id")],
        adapter=VideoIdColumnAdapter(),
        contract=contract(),
    )

    assert result.occurrences == []
    assert result.report is not None
    assert result.report.records_rejected == 1
    assert result.rejects[0].detail
    assert result.report.reconciles()


# --- Requirement 2.9 / 2.10: clip bounds ---------------------------------


def test_valid_clip_bounds_are_recorded() -> None:
    result = extract_records(
        [record(0, video_id=VALID_ID, clip_start="1.5", clip_end="9.0")],
        adapter=VideoIdColumnAdapter(),
        contract=contract(),
    )
    assert result.occurrences[0].clip_start == 1.5
    assert result.occurrences[0].clip_end == 9.0


@pytest.mark.parametrize(
    ("start", "end"),
    [("-1", "5"), ("5", "5"), ("9", "2")],
)
def test_invalid_clip_bounds_quarantine_the_record(start: str, end: str) -> None:
    """Requirement 2.10: no occurrence is emitted for invalid bounds."""
    result = extract_records(
        [record(0, video_id=VALID_ID, clip_start=start, clip_end=end)],
        adapter=VideoIdColumnAdapter(),
        contract=contract(),
    )

    assert result.occurrences == []
    assert result.report is not None
    assert result.report.records_rejected == 1


def test_half_present_bounds_are_rejected() -> None:
    result = extract_records(
        [record(0, video_id=VALID_ID, clip_start="1.0")],
        adapter=VideoIdColumnAdapter(),
        contract=contract(),
    )
    assert result.occurrences == []
    assert "incomplete_clip_bounds" in result.report.rejection_reasons  # type: ignore[union-attr]


# --- Requirement 2.11: repeated evidence retained ------------------------


def test_duplicate_records_are_all_retained() -> None:
    """Deduplicating here would destroy the occurrence count."""
    records = [record(i, video_id=VALID_ID) for i in range(4)]
    result = extract_records(records, adapter=VideoIdColumnAdapter(), contract=contract())

    assert len(result.occurrences) == 4
    assert {o.video_id for o in result.occurrences} == {VALID_ID}
    # Locators stay distinct so each occurrence remains auditable.
    assert len({o.source_locator for o in result.occurrences}) == 4


# --- Requirement 2.13: expansion accounting ------------------------------


def test_one_record_emitting_many_occurrences_is_accounted() -> None:
    result = extract_records(
        [record(0, video_id=VALID_ID, clips="0-5;5-10;10-15")],
        adapter=RepeatedClipAdapter(),
        contract=contract(),
    )

    report = result.report
    assert report is not None
    assert report.records_examined == 1
    assert report.records_accepted == 1
    assert report.occurrences_emitted == 3
    assert report.expansion_count == 2
    assert report.reconciles()
    assert len(result.occurrences) == 3


def test_expansion_across_mixed_records() -> None:
    records = [
        record(0, video_id=VALID_ID, clips="0-5;5-10"),
        record(1, video_id=OTHER_ID, clips="0-1"),
        record(2, video_id="bad", clips="0-1"),
    ]
    result = extract_records(records, adapter=RepeatedClipAdapter(), contract=contract())

    report = result.report
    assert report is not None
    assert report.records_examined == 3
    assert report.records_accepted == 2
    assert report.records_rejected == 1
    assert report.occurrences_emitted == 3
    assert report.expansion_count == 1
    assert report.reconciles()


# --- Requirement 2.8: incomplete provenance ------------------------------


class ProvenanceIncompleteAdapter(SourceAdapter):
    """Accepts every record but cannot complete provenance."""

    def required_fields(self) -> frozenset[str]:
        return frozenset({"video_id"})

    def extract(self, record: SourceRecord) -> RecordOutcome:
        return RecordOutcome(
            accepted=True,
            occurrences=(ExtractedOccurrence(VideoId(VALID_ID)),),
            provenance_incomplete=True,
        )


def test_incomplete_provenance_counts_as_accepted_but_is_not_published() -> None:
    """Requirement 2.8: accounted as accepted, withheld from publication."""
    result = extract_records(
        [record(0, video_id=VALID_ID)],
        adapter=ProvenanceIncompleteAdapter(),
        contract=contract(),
    )

    report = result.report
    assert report is not None
    assert report.records_accepted == 1
    assert report.provenance_incomplete_count == 1
    assert report.reconciles()
    # Excluded from published occurrences.
    assert result.occurrences == []


# --- Requirement 2.16: schema drift fails closed -------------------------


def test_missing_expected_field_raises_schema_drift() -> None:
    with pytest.raises(SchemaDriftError, match="missing expected fields"):
        extract_records(
            [record(0, wrong_column=VALID_ID)],
            adapter=VideoIdColumnAdapter(),
            contract=contract(),
        )


def test_schema_drift_publishes_nothing() -> None:
    """The run fails before any occurrence is produced."""
    try:
        extract_records(
            [record(0, unexpected="x")],
            adapter=VideoIdColumnAdapter(),
            contract=contract(),
        )
    except SchemaDriftError as exc:
        assert exc.adapter_version == "1.0.0"
    else:  # pragma: no cover
        pytest.fail("expected SchemaDriftError")


def test_non_reconciling_report_fails_closed() -> None:
    report = ExtractionReport(
        dataset_id="ds",
        dataset_version="v1",
        snapshot_digest="sha256:x",
        adapter_version="1.0.0",
        schema_version="1.0.0",
        records_examined=10,
        records_accepted=5,
        records_rejected=4,
        occurrences_emitted=5,
    )
    assert not report.reconciles()
    with pytest.raises(SchemaDriftError, match="does not reconcile"):
        report.assert_reconciles()


# --- Reader-stage quarantines are accounted ------------------------------


def test_reader_quarantines_are_counted_as_examined_and_rejected() -> None:
    """The report covers the whole snapshot, not only what reached the adapter."""
    prior = [
        Quarantined("snap:row-9", QuarantineReason.FIELD_TOO_LARGE),
        Quarantined("snap:row-10", QuarantineReason.FORMULA_CONTENT),
    ]
    result = extract_records(
        [record(0, video_id=VALID_ID)],
        adapter=VideoIdColumnAdapter(),
        contract=contract(),
        prior_quarantined=prior,
    )

    report = result.report
    assert report is not None
    assert report.records_examined == 3
    assert report.records_accepted == 1
    assert report.records_rejected == 2
    assert report.reconciles()


def test_empty_snapshot_reconciles() -> None:
    result = extract_records([], adapter=VideoIdColumnAdapter(), contract=contract())
    report = result.report
    assert report is not None
    assert report.records_examined == 0
    assert report.reconciles()


# --- Property 2: Occurrence Conservation ----------------------------------
# Validates: Requirements 2.12, 2.13, 2.14

_ID_ALPHABET = st.sampled_from("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-")
valid_ids = st.text(alphabet=_ID_ALPHABET, min_size=11, max_size=11)
invalid_ids = st.one_of(
    st.text(alphabet=_ID_ALPHABET, min_size=0, max_size=10),
    st.text(alphabet=_ID_ALPHABET, min_size=12, max_size=20),
    st.just("not an id"),
    st.just(""),
)


@given(
    rows=st.lists(
        st.one_of(
            valid_ids.map(lambda value: (value, True)),
            invalid_ids.map(lambda value: (value, False)),
        ),
        max_size=40,
    )
)
@settings(max_examples=200)
def test_property_accepted_plus_rejected_equals_examined(
    rows: list[tuple[str, bool]],
) -> None:
    """Invariant 2: every examined record lands in exactly one bucket."""
    records = [record(i, video_id=value) for i, (value, _) in enumerate(rows)]
    result = extract_records(records, adapter=VideoIdColumnAdapter(), contract=contract())

    report = result.report
    assert report is not None
    assert report.records_examined == len(records)
    assert report.records_accepted + report.records_rejected == report.records_examined


@given(
    clip_counts=st.lists(st.integers(min_value=1, max_value=6), max_size=20),
)
@settings(max_examples=200)
def test_property_emitted_equals_accepted_plus_expansion(
    clip_counts: list[int],
) -> None:
    """Invariant 2: emitted occurrences equal reported expansion totals."""
    records = []
    for index, count in enumerate(clip_counts):
        clips = ";".join(f"{i}-{i + 1}" for i in range(count))
        records.append(record(index, video_id=VALID_ID, clips=clips))

    result = extract_records(records, adapter=RepeatedClipAdapter(), contract=contract())

    report = result.report
    assert report is not None
    assert report.occurrences_emitted == sum(clip_counts)
    assert report.occurrences_emitted == report.records_accepted + report.expansion_count
    assert report.reconciles()


@given(
    valid_count=st.integers(min_value=0, max_value=15),
    invalid_count=st.integers(min_value=0, max_value=15),
    prior_count=st.integers(min_value=0, max_value=10),
)
@settings(max_examples=200)
def test_property_conservation_holds_with_reader_quarantines(
    valid_count: int, invalid_count: int, prior_count: int
) -> None:
    """Conservation covers reader-stage rejections too."""
    records = [record(i, video_id=VALID_ID) for i in range(valid_count)]
    records += [record(valid_count + i, video_id="bad") for i in range(invalid_count)]
    prior = [
        Quarantined(f"snap:pre-{i}", QuarantineReason.MALFORMED_RECORD) for i in range(prior_count)
    ]

    result = extract_records(
        records,
        adapter=VideoIdColumnAdapter(),
        contract=contract(),
        prior_quarantined=prior,
    )

    report = result.report
    assert report is not None
    assert report.records_examined == valid_count + invalid_count + prior_count
    assert report.records_accepted == valid_count
    assert report.records_rejected == invalid_count + prior_count
    assert report.reconciles()
    # Every published occurrence corresponds to an accepted record.
    assert len(result.occurrences) == valid_count


@given(duplicates=st.integers(min_value=1, max_value=10))
@settings(max_examples=100)
def test_property_duplicates_never_collapse(duplicates: int) -> None:
    """Requirement 2.11: repeated evidence is retained, never deduplicated."""
    records = [record(i, video_id=VALID_ID) for i in range(duplicates)]
    result = extract_records(records, adapter=VideoIdColumnAdapter(), contract=contract())

    assert len(result.occurrences) == duplicates
    assert result.report is not None
    assert result.report.occurrences_emitted == duplicates
