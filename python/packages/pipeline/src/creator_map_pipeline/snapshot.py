"""Digest-gated, bounded snapshot readers.

Dataset files are untrusted input. Before any record is read, the snapshot's
content digest must equal the digest pinned in the approved contract
(Requirement 1.6); a mismatch emits nothing and records a failed validation
(Requirement 1.7).

While reading, the approved limits of Requirement 15.7 are enforced: field
and record size caps, path-traversal rejection, spreadsheet formula
non-execution, and archive-decompression bounds. A violation quarantines the
affected input with a non-sensitive reason rather than aborting the run
(Requirement 15.8), while content that satisfies every limit proceeds
without precautionary quarantine (Requirement 15.9).

Requirement refs: 1.5-1.7, 2.15, 2.16, 15.7-15.9
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import StrEnum, unique
from pathlib import Path, PurePosixPath
from typing import Final

from creator_map_schemas import AccessStatus, DatasetContract

#: Read the file in chunks so digesting never loads a large snapshot whole.
_DIGEST_CHUNK_BYTES: Final = 1024 * 1024

#: Exactly the bare-identifier shape, used to exempt hyphen-leading YouTube
#: IDs from the formula guard. Kept local to avoid a dependency cycle with
#: the identity module, which imports nothing from here.
_VIDEO_ID_SHAPE: Final = re.compile(r"^[A-Za-z0-9_-]{11}$")


@dataclass(frozen=True, slots=True)
class SnapshotLimits:
    """Approved bounds applied to untrusted snapshot content.

    Defaults are deliberately conservative. A dataset that legitimately
    exceeds one raises the limit explicitly in its adapter configuration,
    which makes the exception reviewable rather than implicit.
    """

    max_field_bytes: int = 64 * 1024
    max_record_bytes: int = 1024 * 1024
    max_records: int = 50_000_000
    #: Total bytes an archive may expand to. Bounds decompression bombs.
    max_uncompressed_bytes: int = 8 * 1024 * 1024 * 1024
    #: Maximum expansion factor before an archive is treated as hostile.
    max_compression_ratio: int = 200
    max_archive_members: int = 10_000

    def __post_init__(self) -> None:
        if self.max_field_bytes <= 0 or self.max_record_bytes <= 0:
            msg = "size limits must be positive"
            raise ValueError(msg)
        if self.max_field_bytes > self.max_record_bytes:
            msg = "max_field_bytes must not exceed max_record_bytes"
            raise ValueError(msg)


@unique
class QuarantineReason(StrEnum):
    """Non-sensitive reasons an input was quarantined (Requirement 15.8)."""

    FIELD_TOO_LARGE = "field_exceeds_size_limit"
    RECORD_TOO_LARGE = "record_exceeds_size_limit"
    TOO_MANY_RECORDS = "record_count_exceeds_limit"
    PATH_TRAVERSAL = "archive_member_path_traversal"
    ARCHIVE_TOO_LARGE = "archive_expansion_exceeds_limit"
    ARCHIVE_RATIO = "archive_compression_ratio_exceeds_limit"
    ARCHIVE_TOO_MANY_MEMBERS = "archive_member_count_exceeds_limit"
    FORMULA_CONTENT = "formula_content_rejected"
    MALFORMED_RECORD = "malformed_record"
    ENCODING = "undecodable_content"


class SnapshotValidationError(RuntimeError):
    """Raised when a snapshot cannot be admitted for extraction at all."""


@dataclass(frozen=True, slots=True)
class ValidationReport:
    """The result of validating a snapshot against its contract."""

    ok: bool
    dataset_id: str
    dataset_version: str
    expected_digest: str
    actual_digest: str | None
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SourceRecord:
    """One raw record read from a snapshot, with its locator."""

    locator: str
    fields: dict[str, str]


@dataclass(frozen=True, slots=True)
class Quarantined:
    """One record or member rejected during reading."""

    locator: str
    reason: QuarantineReason
    detail: str = ""


@dataclass(slots=True)
class ReadOutcome:
    """Records read plus everything quarantined along the way."""

    records: list[SourceRecord] = field(default_factory=list)
    quarantined: list[Quarantined] = field(default_factory=list)

    @property
    def examined(self) -> int:
        """Total records examined: accepted plus quarantined."""
        return len(self.records) + len(self.quarantined)


def compute_digest(path: Path) -> str:
    """Return the `sha256:<hex>` content address of a file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_DIGEST_CHUNK_BYTES):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def validate_snapshot(path: Path, contract: DatasetContract) -> ValidationReport:
    """Verify a snapshot against its approved contract.

    Requirement 1.6: the digest must match before any occurrence is emitted.
    Requirement 1.7: on mismatch, nothing is emitted and the failure is
    recorded against the contract key.
    """
    reasons: list[str] = []

    if contract.access_status is not AccessStatus.APPROVED:
        reasons.append(f"contract access_status is {contract.access_status.value}")

    if not path.is_file():
        return ValidationReport(
            ok=False,
            dataset_id=contract.id,
            dataset_version=contract.version,
            expected_digest=contract.snapshot_digest,
            actual_digest=None,
            reasons=(*reasons, "snapshot file not found"),
        )

    actual = compute_digest(path)
    if actual != contract.snapshot_digest:
        reasons.append("snapshot digest does not match the approved contract digest")

    return ValidationReport(
        ok=not reasons,
        dataset_id=contract.id,
        dataset_version=contract.version,
        expected_digest=contract.snapshot_digest,
        actual_digest=actual,
        reasons=tuple(reasons),
    )


def is_formula(value: str) -> bool:
    """Report whether a cell value would be executed as a formula.

    Spreadsheet software treats a leading =, +, -, @, or a tab/CR followed by
    one as a formula, so such values are rejected rather than sanitised
    (Requirement 15.7).

    A bare YouTube identifier is excluded from this check. The base64url
    alphabet includes "-", so roughly one identifier in forty begins with a
    hyphen; treating those as formulas would silently discard real evidence
    and understate every occurrence count. An 11-character base64url token
    is not a formula under any spreadsheet's grammar — a formula needs an
    operator or reference after the sign — so exempting exactly that shape
    costs no protection.
    """
    stripped = value.lstrip("\t\r\n ")
    if stripped[:1] not in {"=", "+", "-", "@"} or len(stripped) <= 1:
        return False
    return not _VIDEO_ID_SHAPE.match(stripped)


def _check_record_limits(
    locator: str, row: dict[str, str], limits: SnapshotLimits
) -> Quarantined | None:
    """Apply per-record and per-field limits to one raw row."""
    record_bytes = 0
    for key, value in row.items():
        if value is None:  # pragma: no cover - csv yields str or None
            continue
        size = len(value.encode("utf-8", errors="replace"))
        if size > limits.max_field_bytes:
            return Quarantined(locator, QuarantineReason.FIELD_TOO_LARGE, f"field {key!r}")
        if is_formula(value):
            return Quarantined(locator, QuarantineReason.FORMULA_CONTENT, f"field {key!r}")
        record_bytes += size

    if record_bytes > limits.max_record_bytes:
        return Quarantined(locator, QuarantineReason.RECORD_TOO_LARGE)
    return None


def read_csv_records(
    handle: io.TextIOBase,
    *,
    source_name: str,
    limits: SnapshotLimits | None = None,
) -> ReadOutcome:
    """Read delimited records, enforcing size and formula limits."""
    active = limits or SnapshotLimits()
    outcome = ReadOutcome()

    reader = csv.DictReader(handle)
    for index, raw_row in enumerate(reader):
        locator = f"{source_name}:row-{index}"

        if outcome.examined >= active.max_records:
            outcome.quarantined.append(Quarantined(locator, QuarantineReason.TOO_MANY_RECORDS))
            break

        # A short row yields None values; a long row collects the surplus
        # under the restkey. Both are malformed rather than merely unusual.
        row = {
            key: value
            for key, value in raw_row.items()
            if key is not None and isinstance(value, str)
        }
        if len(row) != len(raw_row):
            outcome.quarantined.append(Quarantined(locator, QuarantineReason.MALFORMED_RECORD))
            continue

        violation = _check_record_limits(locator, row, active)
        if violation is not None:
            outcome.quarantined.append(violation)
            continue

        outcome.records.append(SourceRecord(locator=locator, fields=row))

    return outcome


def read_jsonl_records(
    handle: io.TextIOBase,
    *,
    source_name: str,
    limits: SnapshotLimits | None = None,
) -> ReadOutcome:
    """Read JSON-lines records, enforcing size limits."""
    active = limits or SnapshotLimits()
    outcome = ReadOutcome()

    for index, line in enumerate(handle):
        locator = f"{source_name}:line-{index}"

        if outcome.examined >= active.max_records:
            outcome.quarantined.append(Quarantined(locator, QuarantineReason.TOO_MANY_RECORDS))
            break

        stripped = line.strip()
        if not stripped:
            continue

        if len(stripped.encode("utf-8", errors="replace")) > active.max_record_bytes:
            outcome.quarantined.append(Quarantined(locator, QuarantineReason.RECORD_TOO_LARGE))
            continue

        try:
            parsed = json.loads(stripped)
        except (json.JSONDecodeError, ValueError):
            outcome.quarantined.append(Quarantined(locator, QuarantineReason.MALFORMED_RECORD))
            continue

        if not isinstance(parsed, dict):
            outcome.quarantined.append(Quarantined(locator, QuarantineReason.MALFORMED_RECORD))
            continue

        row = {str(key): str(value) for key, value in parsed.items() if value is not None}
        violation = _check_record_limits(locator, row, active)
        if violation is not None:
            outcome.quarantined.append(violation)
            continue

        outcome.records.append(SourceRecord(locator=locator, fields=row))

    return outcome


def safe_archive_members(
    archive: zipfile.ZipFile, *, limits: SnapshotLimits | None = None
) -> Iterator[tuple[zipfile.ZipInfo, Quarantined | None]]:
    """Yield archive members with a quarantine verdict for each.

    Enforces member count, path traversal, total expansion, and per-member
    compression ratio. Yielding a verdict rather than raising lets the caller
    quarantine one hostile member while continuing with the rest.
    """
    active = limits or SnapshotLimits()
    total_uncompressed = 0

    for index, info in enumerate(archive.infolist()):
        if index >= active.max_archive_members:
            yield info, Quarantined(info.filename, QuarantineReason.ARCHIVE_TOO_MANY_MEMBERS)
            return

        if _is_unsafe_member_path(info.filename):
            yield info, Quarantined(info.filename, QuarantineReason.PATH_TRAVERSAL)
            continue

        if info.is_dir():
            continue

        total_uncompressed += info.file_size
        if total_uncompressed > active.max_uncompressed_bytes:
            yield info, Quarantined(info.filename, QuarantineReason.ARCHIVE_TOO_LARGE)
            return

        # A member that expands far beyond its compressed size is the
        # signature of a decompression bomb.
        if info.compress_size > 0:
            ratio = info.file_size / info.compress_size
            if ratio > active.max_compression_ratio:
                yield info, Quarantined(info.filename, QuarantineReason.ARCHIVE_RATIO)
                continue

        yield info, None


def _is_unsafe_member_path(name: str) -> bool:
    """Report whether an archive member name escapes the extraction root."""
    if not name:
        return True
    # Normalise separators before inspection so a Windows-style path cannot
    # slip past a POSIX-only check.
    normalized = name.replace("\\", "/")
    if normalized.startswith("/"):
        return True
    # A Windows drive letter or UNC prefix is absolute regardless of slashes.
    if len(normalized) >= 2 and normalized[1] == ":":
        return True
    pure = PurePosixPath(normalized)
    return any(part == ".." for part in pure.parts)
