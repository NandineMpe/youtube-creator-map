"""Tests for digest-gated, bounded snapshot readers.

Requirement refs: 1.5-1.7, 2.15, 15.7-15.9
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
from creator_map_pipeline.snapshot import (
    QuarantineReason,
    SnapshotLimits,
    compute_digest,
    is_formula,
    read_csv_records,
    read_jsonl_records,
    safe_archive_members,
    validate_snapshot,
)
from creator_map_schemas import (
    AccessStatus,
    CorpusClass,
    DatasetContract,
    OccurrenceUnit,
    SourceKind,
)


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


# --- Requirement 1.6 / 1.7: digest gate ----------------------------------


def test_matching_digest_validates(tmp_path: Path) -> None:
    snapshot = tmp_path / "snap.csv"
    snapshot.write_text("video_id\ndQw4w9WgXcQ\n", encoding="utf-8")
    digest = compute_digest(snapshot)

    report = validate_snapshot(snapshot, contract(snapshot_digest=digest))

    assert report.ok
    assert report.actual_digest == digest
    assert report.reasons == ()


def test_mismatched_digest_fails_closed(tmp_path: Path) -> None:
    """Requirement 1.7: nothing is emitted when the digest differs."""
    snapshot = tmp_path / "snap.csv"
    snapshot.write_text("video_id\ndQw4w9WgXcQ\n", encoding="utf-8")

    report = validate_snapshot(snapshot, contract())

    assert not report.ok
    assert any("digest does not match" in reason for reason in report.reasons)
    assert report.dataset_id == "ds"
    assert report.dataset_version == "v1"


def test_missing_snapshot_fails_closed(tmp_path: Path) -> None:
    report = validate_snapshot(tmp_path / "absent.csv", contract())
    assert not report.ok
    assert report.actual_digest is None


def test_unapproved_contract_fails_validation(tmp_path: Path) -> None:
    snapshot = tmp_path / "snap.csv"
    snapshot.write_text("x\n", encoding="utf-8")
    digest = compute_digest(snapshot)

    report = validate_snapshot(
        snapshot,
        contract(snapshot_digest=digest, access_status=AccessStatus.PROPOSED),
    )
    assert not report.ok
    assert any("access_status" in reason for reason in report.reasons)


def test_digest_is_stable_and_content_addressed(tmp_path: Path) -> None:
    first = tmp_path / "a.bin"
    second = tmp_path / "b.bin"
    first.write_bytes(b"identical")
    second.write_bytes(b"identical")

    assert compute_digest(first) == compute_digest(second)
    assert compute_digest(first).startswith("sha256:")


# --- Requirement 15.7: formula non-execution -----------------------------


@pytest.mark.parametrize(
    "value",
    ["=SUM(A1)", "+1+1", "-2+3", "@import", "\t=cmd", " =formula"],
)
def test_detects_formula_values(value: str) -> None:
    assert is_formula(value)


@pytest.mark.parametrize("value", ["dQw4w9WgXcQ", "-", "", "plain text", "3-4"])
def test_does_not_flag_ordinary_values(value: str) -> None:
    assert not is_formula(value)


@pytest.mark.parametrize(
    "video_id",
    ["-abc123XYZ_", "-wcnJ3vc1Bo", "_-Hh6EnTbUE", "-JyZLS4E9aE"],
)
def test_does_not_flag_hyphen_leading_video_ids(video_id: str) -> None:
    """The base64url alphabet includes '-', so real IDs start with it.

    Roughly one identifier in forty begins with a hyphen. Treating those as
    formulas silently discarded real evidence and understated occurrence
    counts; this was observed on live YouTube-Commons data.
    """
    assert not is_formula(video_id)


@pytest.mark.parametrize(
    "value",
    ["-2+3", "-SUM(A1)", "-cmd|'/c calc'!A1", "-1234567890123"],
)
def test_still_flags_hyphen_leading_formulas(value: str) -> None:
    """The exemption is exactly the 11-char identifier shape, nothing wider."""
    assert is_formula(value)


def test_formula_field_is_quarantined() -> None:
    handle = io.StringIO("video_id,note\ndQw4w9WgXcQ,=cmd|'/c calc'!A1\n")
    outcome = read_csv_records(handle, source_name="s")

    assert outcome.records == []
    assert outcome.quarantined[0].reason is QuarantineReason.FORMULA_CONTENT


# --- Requirement 15.7: size limits ---------------------------------------


def test_oversized_field_is_quarantined() -> None:
    limits = SnapshotLimits(max_field_bytes=16, max_record_bytes=1024)
    handle = io.StringIO(f"video_id,note\ndQw4w9WgXcQ,{'x' * 100}\n")

    outcome = read_csv_records(handle, source_name="s", limits=limits)

    assert outcome.records == []
    assert outcome.quarantined[0].reason is QuarantineReason.FIELD_TOO_LARGE


def test_oversized_record_is_quarantined() -> None:
    limits = SnapshotLimits(max_field_bytes=32, max_record_bytes=40)
    handle = io.StringIO(f"a,b\n{'x' * 30},{'y' * 30}\n")

    outcome = read_csv_records(handle, source_name="s", limits=limits)

    assert outcome.quarantined[0].reason is QuarantineReason.RECORD_TOO_LARGE


def test_record_count_limit_stops_reading() -> None:
    limits = SnapshotLimits(max_records=2)
    rows = "\n".join(f"id{i}" for i in range(10))
    handle = io.StringIO(f"video_id\n{rows}\n")

    outcome = read_csv_records(handle, source_name="s", limits=limits)

    assert len(outcome.records) == 2
    assert outcome.quarantined[-1].reason is QuarantineReason.TOO_MANY_RECORDS


def test_limits_reject_inconsistent_configuration() -> None:
    with pytest.raises(ValueError, match="must not exceed"):
        SnapshotLimits(max_field_bytes=100, max_record_bytes=10)


# --- Requirement 15.9: compliant content is not quarantined --------------


def test_compliant_records_are_read_without_quarantine() -> None:
    handle = io.StringIO("video_id,clip_start,clip_end\ndQw4w9WgXcQ,0,10\n9bZkp7q19f0,5,15\n")
    outcome = read_csv_records(handle, source_name="snap")

    assert len(outcome.records) == 2
    assert outcome.quarantined == []
    assert outcome.records[0].locator == "snap:row-0"
    assert outcome.records[0].fields["video_id"] == "dQw4w9WgXcQ"


def test_malformed_rows_are_quarantined_individually() -> None:
    """Requirement 2.15: a bad record is quarantined; the run continues."""
    handle = io.StringIO("a,b\n1,2\n1\n3,4\n")
    outcome = read_csv_records(handle, source_name="s")

    assert len(outcome.records) == 2
    assert len(outcome.quarantined) == 1
    assert outcome.quarantined[0].reason is QuarantineReason.MALFORMED_RECORD


# --- JSON lines ----------------------------------------------------------


def test_reads_json_lines() -> None:
    handle = io.StringIO('{"video_id":"dQw4w9WgXcQ"}\n{"video_id":"9bZkp7q19f0"}\n')
    outcome = read_jsonl_records(handle, source_name="snap")

    assert len(outcome.records) == 2
    assert outcome.records[1].locator == "snap:line-1"


def test_malformed_json_is_quarantined() -> None:
    handle = io.StringIO('{"video_id":"ok"}\n{not json}\n')
    outcome = read_jsonl_records(handle, source_name="s")

    assert len(outcome.records) == 1
    assert outcome.quarantined[0].reason is QuarantineReason.MALFORMED_RECORD


def test_non_object_json_is_quarantined() -> None:
    handle = io.StringIO('["array","not","object"]\n')
    outcome = read_jsonl_records(handle, source_name="s")
    assert outcome.quarantined[0].reason is QuarantineReason.MALFORMED_RECORD


def test_blank_lines_are_skipped_not_quarantined() -> None:
    handle = io.StringIO('{"video_id":"ok"}\n\n  \n{"video_id":"two"}\n')
    outcome = read_jsonl_records(handle, source_name="s")

    assert len(outcome.records) == 2
    assert outcome.quarantined == []


# --- Requirement 15.7: archive safety ------------------------------------


def _archive(tmp_path: Path, members: dict[str, bytes]) -> zipfile.ZipFile:
    path = tmp_path / "archive.zip"
    with zipfile.ZipFile(path, "w") as zf:
        for name, payload in members.items():
            zf.writestr(name, payload)
    return zipfile.ZipFile(path)


@pytest.mark.parametrize(
    "name",
    ["../escape.csv", "../../etc/passwd", "/absolute.csv", "a/../../b.csv"],
)
def test_path_traversal_members_are_quarantined(tmp_path: Path, name: str) -> None:
    archive = _archive(tmp_path, {name: b"data"})
    verdicts = dict((info.filename, verdict) for info, verdict in safe_archive_members(archive))
    verdict = verdicts[name]
    assert verdict is not None
    assert verdict.reason is QuarantineReason.PATH_TRAVERSAL


def test_safe_member_names_pass(tmp_path: Path) -> None:
    archive = _archive(tmp_path, {"data/part-0.csv": b"video_id\n"})
    verdicts = [verdict for _info, verdict in safe_archive_members(archive)]
    assert verdicts == [None]


def test_decompression_bomb_is_quarantined(tmp_path: Path) -> None:
    """A member expanding far beyond its compressed size is rejected."""
    path = tmp_path / "bomb.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("bomb.txt", b"\0" * (2 * 1024 * 1024))

    archive = zipfile.ZipFile(path)
    verdicts = [verdict for _info, verdict in safe_archive_members(archive)]

    assert verdicts[0] is not None
    assert verdicts[0].reason is QuarantineReason.ARCHIVE_RATIO


def test_total_expansion_limit_stops_iteration(tmp_path: Path) -> None:
    archive = _archive(tmp_path, {f"part-{i}.txt": b"x" * 1000 for i in range(5)})
    limits = SnapshotLimits(max_uncompressed_bytes=2000)

    verdicts = [verdict for _info, verdict in safe_archive_members(archive, limits=limits)]

    assert verdicts[-1] is not None
    assert verdicts[-1].reason is QuarantineReason.ARCHIVE_TOO_LARGE


def test_member_count_limit(tmp_path: Path) -> None:
    archive = _archive(tmp_path, {f"p{i}.txt": b"x" for i in range(6)})
    limits = SnapshotLimits(max_archive_members=3)

    verdicts = [verdict for _info, verdict in safe_archive_members(archive, limits=limits)]

    assert verdicts[-1] is not None
    assert verdicts[-1].reason is QuarantineReason.ARCHIVE_TOO_MANY_MEMBERS


def test_quarantine_reasons_carry_no_sensitive_content() -> None:
    """Requirement 15.8: the reason is a stable, non-sensitive class."""
    handle = io.StringIO("video_id,secret\nok,=EXEC('rm -rf /')\n")
    outcome = read_csv_records(handle, source_name="s")

    quarantined = outcome.quarantined[0]
    assert "rm -rf" not in quarantined.detail
    assert quarantined.reason.value == "formula_content_rejected"
