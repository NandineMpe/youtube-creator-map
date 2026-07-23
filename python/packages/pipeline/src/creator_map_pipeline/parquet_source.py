"""Bounded Parquet snapshot reader.

Reads a columnar snapshot row group by row group so a multi-gigabyte shard
never has to be materialised whole, and projects only the columns an adapter
declares. Projection is a security control as much as a performance one:
YouTube-Commons carries a full transcript in `text`, and reading it would
pull licensed content into the pipeline for no reason (Requirement 15.12
keeps processing to metadata).

The same field and record limits as the delimited readers apply, so a
hostile Parquet file is bounded the same way (Requirement 15.7).

Requirement refs: 2.15, 2.16, 15.7-15.9, 15.12
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pyarrow.parquet as pq

from creator_map_pipeline.snapshot import (
    Quarantined,
    QuarantineReason,
    ReadOutcome,
    SnapshotLimits,
    SourceRecord,
)


class ParquetSchemaError(RuntimeError):
    """Raised when a Parquet snapshot lacks the columns an adapter needs."""


def read_parquet_records(
    path: Path,
    *,
    columns: tuple[str, ...],
    optional_columns: tuple[str, ...] = (),
    source_name: str | None = None,
    limits: SnapshotLimits | None = None,
    max_rows: int | None = None,
) -> ReadOutcome:
    """Read selected columns from a Parquet snapshot.

    `columns` is the required projection: a missing one fails closed before
    any row is produced, so schema drift surfaces as an error rather than as
    silently empty fields (Requirement 2.16). `optional_columns` are read
    when present and ignored when absent.

    No column outside these two sets is read from disk.

    `max_rows` bounds a development run over a large shard; it is a
    deliberate sampling limit, distinct from the hostile-input record cap in
    `limits`.
    """
    active = limits or SnapshotLimits()
    name = source_name or path.name
    outcome = ReadOutcome()

    parquet_file = pq.ParquetFile(path)
    available = set(parquet_file.schema_arrow.names)
    missing = set(columns) - available
    if missing:
        msg = f"snapshot {name} is missing required columns: {', '.join(sorted(missing))}"
        raise ParquetSchemaError(msg)

    projection = list(columns) + [c for c in optional_columns if c in available]

    row_index = 0
    for group in range(parquet_file.metadata.num_row_groups):
        table = parquet_file.read_row_group(group, columns=projection)

        for row in table.to_pylist():
            if max_rows is not None and row_index >= max_rows:
                return outcome

            locator = f"{name}:row-{row_index}"
            row_index += 1

            if outcome.examined >= active.max_records:
                outcome.quarantined.append(Quarantined(locator, QuarantineReason.TOO_MANY_RECORDS))
                return outcome

            fields: dict[str, str] = {}
            violation: Quarantined | None = None
            record_bytes = 0

            for key, value in row.items():
                if value is None:
                    continue
                text = value if isinstance(value, str) else str(value)
                size = len(text.encode("utf-8", errors="replace"))

                if size > active.max_field_bytes:
                    violation = Quarantined(
                        locator, QuarantineReason.FIELD_TOO_LARGE, f"field {key!r}"
                    )
                    break

                # No formula check here, deliberately. The guard in the
                # delimited readers stops spreadsheet software evaluating a
                # leading =, +, -, or @ in an exported CSV. A Parquet string
                # column is typed data that no spreadsheet evaluates, and
                # applying the check here rejects every YouTube identifier
                # beginning with "-" — a legal base64url character present in
                # roughly one identifier in forty. Discarding real evidence
                # to guard against a risk this format does not carry would
                # corrupt the occurrence counts.

                record_bytes += size
                fields[key] = text

            if violation is not None:
                outcome.quarantined.append(violation)
                continue

            if record_bytes > active.max_record_bytes:
                outcome.quarantined.append(Quarantined(locator, QuarantineReason.RECORD_TOO_LARGE))
                continue

            outcome.records.append(SourceRecord(locator=locator, fields=fields))

    return outcome


def iter_parquet_column_pairs(
    path: Path, *, key_column: str, value_column: str, max_rows: int | None = None
) -> Iterator[tuple[str, str]]:
    """Stream non-null pairs from two columns.

    Used to build the video-to-channel and channel-to-name maps that back
    the snapshot-derived resolvers, without materialising the whole shard.
    """
    parquet_file = pq.ParquetFile(path)
    available = set(parquet_file.schema_arrow.names)
    missing = {key_column, value_column} - available
    if missing:
        msg = f"snapshot is missing columns: {', '.join(sorted(missing))}"
        raise ParquetSchemaError(msg)

    emitted = 0
    for group in range(parquet_file.metadata.num_row_groups):
        table = parquet_file.read_row_group(group, columns=[key_column, value_column])
        keys = table.column(key_column).to_pylist()
        values = table.column(value_column).to_pylist()

        for key, value in zip(keys, values, strict=True):
            if max_rows is not None and emitted >= max_rows:
                return
            if not key or not value:
                continue
            emitted += 1
            yield str(key), str(value)


def parquet_row_count(path: Path) -> int:
    """Return the total row count without reading any column data."""
    return int(pq.ParquetFile(path).metadata.num_rows)
