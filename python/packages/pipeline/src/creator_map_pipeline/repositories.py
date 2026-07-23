"""PostgreSQL-backed repositories for governed ingestion.

Every write here is an Administrative_Operation: it requires an authenticated
actor and produces a durable audit record. Requirement 15.21 makes the audit
write a precondition rather than a side effect — if the audit record cannot
be written, the operation is rolled back rather than completed silently.

Requirement refs: 1.1-1.9, 2.7-2.16, 15.16-15.21
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import psycopg
from creator_map_schemas import (
    AccessStatus,
    CorpusClass,
    DatasetContract,
    NormalizedOccurrence,
    OccurrenceUnit,
    SourceKind,
)
from psycopg import sql
from psycopg.types.json import Jsonb

from creator_map_pipeline.extraction import ExtractionReport
from creator_map_pipeline.registry import DatasetContractKey, DatasetRepository
from creator_map_pipeline.snapshot import Quarantined


class AuditWriteFailure(RuntimeError):
    """Raised when an audit record cannot be written durably.

    The caller must roll back the associated operation (Requirement 15.21).
    """


@dataclass(frozen=True, slots=True)
class AuditEntry:
    """A non-sensitive audit record."""

    actor: str
    action: str
    resource_class: str
    outcome: str
    detail: dict[str, str]


def record_audit(cursor: psycopg.Cursor[tuple[object, ...]], entry: AuditEntry) -> None:
    """Write an audit record, raising if it cannot be persisted.

    Deliberately not swallowing errors: a silent audit failure would leave a
    restricted operation unrecorded, which Requirement 15.20 forbids.
    """
    try:
        cursor.execute(
            "insert into governance.audit_log "
            "(actor, action, resource_class, outcome, detail) "
            "values (%s, %s, %s, %s, %s)",
            (
                entry.actor,
                entry.action,
                entry.resource_class,
                entry.outcome,
                Jsonb(entry.detail),
            ),
        )
    except psycopg.Error as exc:
        msg = f"audit record could not be written: {type(exc).__name__}"
        raise AuditWriteFailure(msg) from exc


class PostgresDatasetRepository(DatasetRepository):
    """Dataset contract storage backed by the immutable registry tables."""

    _COLUMNS = (
        "dataset_id",
        "dataset_version",
        "display_name",
        "corpus_class",
        "source_kind",
        "access_status",
        "snapshot_digest",
        "adapter_version",
        "occurrence_unit",
        "source_citation",
        "acquisition_path",
        "terms_review_id",
    )

    def __init__(
        self,
        cursor: psycopg.Cursor[tuple[object, ...]],
        *,
        acquisition_path: str = "documented",
    ) -> None:
        self._cursor = cursor
        # The contract schema does not carry acquisition_path, but the
        # registry table requires it for Requirement 1.2. It is supplied at
        # the repository boundary rather than invented per-row.
        self._acquisition_path = acquisition_path

    def get(self, key: DatasetContractKey) -> DatasetContract | None:
        self._cursor.execute(
            sql.SQL(
                "select {} from provenance.dataset_contract "
                "where dataset_id = %s and dataset_version = %s"
            ).format(sql.SQL(", ").join(sql.Identifier(c) for c in self._COLUMNS)),
            (key.dataset_id, key.dataset_version),
        )
        row = self._cursor.fetchone()
        return None if row is None else self._to_contract(row)

    def put(self, key: DatasetContractKey, contract: DatasetContract) -> None:
        self._cursor.execute(
            "insert into provenance.dataset_contract "
            "(dataset_id, dataset_version, display_name, corpus_class, source_kind, "
            " access_status, snapshot_digest, adapter_version, occurrence_unit, "
            " source_citation, acquisition_path, terms_review_id) "
            "values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                contract.id,
                contract.version,
                contract.display_name,
                contract.corpus_class.value,
                contract.source_kind.value,
                contract.access_status.value,
                contract.snapshot_digest,
                contract.adapter_version,
                contract.occurrence_unit.value,
                contract.source_citation,
                self._acquisition_path,
                contract.terms_review_id,
            ),
        )

    def list_all(self) -> tuple[tuple[DatasetContractKey, DatasetContract], ...]:
        self._cursor.execute(
            sql.SQL(
                "select {} from provenance.dataset_contract order by dataset_id, dataset_version"
            ).format(sql.SQL(", ").join(sql.Identifier(c) for c in self._COLUMNS))
        )
        rows = self._cursor.fetchall()
        return tuple(
            (
                DatasetContractKey(str(row[0]), str(row[1])),
                self._to_contract(row),
            )
            for row in rows
        )

    @staticmethod
    def _to_contract(row: tuple[object, ...]) -> DatasetContract:
        # PostgreSQL returns enum columns as plain strings, and the domain
        # models validate strictly (no implicit str -> enum coercion). The
        # enum types are reconstructed explicitly here so strictness is
        # preserved for in-process construction while the database boundary
        # still round-trips.
        return DatasetContract.model_validate(
            {
                "id": row[0],
                "version": row[1],
                "display_name": row[2],
                "corpus_class": CorpusClass(str(row[3])),
                "source_kind": SourceKind(str(row[4])),
                "access_status": AccessStatus(str(row[5])),
                "snapshot_digest": row[6],
                "adapter_version": row[7],
                "occurrence_unit": OccurrenceUnit(str(row[8])),
                "source_citation": row[9],
                # row[10] is acquisition_path, which the public contract
                # model does not carry.
                "terms_review_id": row[11],
            }
        )


def persist_snapshot(
    cursor: psycopg.Cursor[tuple[object, ...]],
    *,
    contract: DatasetContract,
    storage_uri: str,
    byte_size: int,
) -> None:
    """Record an immutable source snapshot.

    Idempotent on the digest: re-registering the same snapshot is a no-op
    rather than an error, so a resumed ingestion run does not fail here.
    """
    cursor.execute(
        "insert into provenance.source_snapshot "
        "(snapshot_digest, dataset_id, dataset_version, storage_uri, byte_size) "
        "values (%s,%s,%s,%s,%s) on conflict (snapshot_digest) do nothing",
        (
            contract.snapshot_digest,
            contract.id,
            contract.version,
            storage_uri,
            byte_size,
        ),
    )


def persist_occurrences(
    cursor: psycopg.Cursor[tuple[object, ...]],
    *,
    contract: DatasetContract,
    occurrences: list[NormalizedOccurrence],
) -> int:
    """Append occurrences and refresh dataset-video membership.

    Occurrences are inserted without deduplication (Requirement 2.11); the
    membership table is the deduplicated projection used for planning
    enrichment work (Requirement 3.3).
    """
    if not occurrences:
        return 0

    with cursor.copy(
        "copy provenance.normalized_occurrence "
        "(dataset_id, dataset_version, snapshot_digest, source_locator, video_id, "
        " clip_start, clip_end, occurrence_unit, extracted_at, adapter_version) "
        "from stdin"
    ) as copy:
        for occurrence in occurrences:
            copy.write_row(
                (
                    occurrence.dataset_id,
                    contract.version,
                    occurrence.snapshot_digest,
                    occurrence.source_locator,
                    occurrence.video_id,
                    occurrence.clip_start,
                    occurrence.clip_end,
                    occurrence.occurrence_unit.value,
                    occurrence.extracted_at,
                    occurrence.adapter_version,
                )
            )

    distinct_videos = sorted({occurrence.video_id for occurrence in occurrences})
    cursor.executemany(
        "insert into provenance.dataset_video_membership "
        "(dataset_id, dataset_version, video_id) values (%s,%s,%s) "
        "on conflict do nothing",
        [(contract.id, contract.version, video_id) for video_id in distinct_videos],
    )

    return len(occurrences)


def persist_rejects(
    cursor: psycopg.Cursor[tuple[object, ...]],
    *,
    contract: DatasetContract,
    rejects: list[Quarantined],
) -> int:
    """Append quarantined records with their non-sensitive reasons."""
    if not rejects:
        return 0

    cursor.executemany(
        "insert into provenance.extraction_reject "
        "(dataset_id, dataset_version, snapshot_digest, source_locator, "
        " adapter_version, rejection_reason) values (%s,%s,%s,%s,%s,%s)",
        [
            (
                contract.id,
                contract.version,
                contract.snapshot_digest,
                reject.locator,
                contract.adapter_version,
                reject.detail or reject.reason.value,
            )
            for reject in rejects
        ],
    )
    return len(rejects)


def persist_extraction_report(
    cursor: psycopg.Cursor[tuple[object, ...]], report: ExtractionReport
) -> None:
    """Record the extraction report.

    The database enforces the same conservation law the report checks, so a
    miscounted run is rejected here even if it somehow passed in-process.
    """
    cursor.execute(
        "insert into provenance.extraction_report "
        "(dataset_id, dataset_version, snapshot_digest, adapter_version, "
        " schema_version, records_examined, records_accepted, records_rejected, "
        " occurrences_emitted, expansion_count) "
        "values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            report.dataset_id,
            report.dataset_version,
            report.snapshot_digest,
            report.adapter_version,
            report.schema_version,
            report.records_examined,
            report.records_accepted,
            report.records_rejected,
            report.occurrences_emitted,
            report.expansion_count,
        ),
    )


@dataclass(frozen=True, slots=True)
class IngestionSummary:
    """What one ingestion run committed."""

    dataset_id: str
    dataset_version: str
    occurrences_persisted: int
    rejects_persisted: int
    records_examined: int

    def describe(self) -> str:
        return (
            f"{self.dataset_id}@{self.dataset_version}: "
            f"examined={self.records_examined} "
            f"occurrences={self.occurrences_persisted} "
            f"rejects={self.rejects_persisted}"
        )


def write_ingestion(
    cursor: psycopg.Cursor[tuple[object, ...]],
    *,
    contract: DatasetContract,
    occurrences: list[NormalizedOccurrence],
    rejects: list[Quarantined],
    report: ExtractionReport,
    storage_uri: str,
    byte_size: int,
    actor: str,
) -> IngestionSummary:
    """Write one extraction run's rows, without committing.

    Separated from the commit so a caller can compose this into a larger
    transaction, and so tests can exercise the real write path inside a
    transaction they roll back. The audit record is written here, on the
    same cursor, so it shares the caller's atomicity guarantee.
    """
    persist_snapshot(cursor, contract=contract, storage_uri=storage_uri, byte_size=byte_size)
    persisted = persist_occurrences(cursor, contract=contract, occurrences=occurrences)
    rejected = persist_rejects(cursor, contract=contract, rejects=rejects)
    persist_extraction_report(cursor, report)

    record_audit(
        cursor,
        AuditEntry(
            actor=actor,
            action="ingest_snapshot",
            resource_class="provenance.normalized_occurrence",
            outcome="success",
            detail={
                "datasetId": contract.id,
                "datasetVersion": contract.version,
                "recordsExamined": str(report.records_examined),
                "occurrencesEmitted": str(report.occurrences_emitted),
            },
        ),
    )

    return IngestionSummary(
        dataset_id=contract.id,
        dataset_version=contract.version,
        occurrences_persisted=persisted,
        rejects_persisted=rejected,
        records_examined=report.records_examined,
    )


def commit_ingestion(
    connection: psycopg.Connection[tuple[object, ...]],
    *,
    contract: DatasetContract,
    occurrences: list[NormalizedOccurrence],
    rejects: list[Quarantined],
    report: ExtractionReport,
    storage_uri: str,
    byte_size: int,
    actor: str,
) -> IngestionSummary:
    """Persist one extraction run atomically, with its audit record.

    Everything commits together or nothing does: occurrences, rejects, the
    report, and the audit entry share one transaction. An audit failure
    aborts the whole run (Requirement 15.21).
    """
    try:
        with connection.cursor() as cursor:
            summary = write_ingestion(
                cursor,
                contract=contract,
                occurrences=occurrences,
                rejects=rejects,
                report=report,
                storage_uri=storage_uri,
                byte_size=byte_size,
                actor=actor,
            )
    except Exception:
        connection.rollback()
        raise

    connection.commit()
    return summary


def utc_now() -> datetime:
    """Return the current instant in UTC."""
    return datetime.now(UTC)
