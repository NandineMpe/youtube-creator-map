"""End-to-end ingestion integration tests against a live PostgreSQL.

Skipped when DATABASE_URL is unset or unreachable.

Every test runs inside a transaction that is rolled back. The append-only
rules make DELETE a no-op on provenance and audit tables by design, so
rollback — not cleanup — is what keeps these tests from accumulating
residue. Tests therefore exercise `write_ingestion` (the real write path)
rather than `commit_ingestion`, which would escape the rollback.

Requirement refs: 1.1-1.9, 2.7-2.16, 15.20, 15.21
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime

import psycopg
import pytest
from creator_map_pipeline.database import DatabaseConfigError, resolve_database_url
from creator_map_pipeline.extraction import (
    RepeatedClipAdapter,
    VideoIdColumnAdapter,
    extract_records,
)
from creator_map_pipeline.registry import (
    DatasetContractKey,
    DatasetRegistry,
    RegistrationOutcome,
)
from creator_map_pipeline.repositories import (
    AuditEntry,
    AuditWriteFailure,
    PostgresDatasetRepository,
    record_audit,
    write_ingestion,
)
from creator_map_pipeline.snapshot import Quarantined, QuarantineReason, SourceRecord
from creator_map_schemas import (
    AccessStatus,
    CorpusClass,
    DatasetContract,
    OccurrenceUnit,
    SourceKind,
)

pytestmark = pytest.mark.integration

VALID_ID = "dQw4w9WgXcQ"
OTHER_ID = "9bZkp7q19f0"
DIGEST = "sha256:" + "e" * 64
INSTANT = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)


def contract(**overrides: object) -> DatasetContract:
    fields: dict[str, object] = {
        "id": "itest-ds",
        "display_name": "Integration DS",
        "version": "v1",
        "corpus_class": CorpusClass.CANDIDATE,
        "source_kind": SourceKind.METADATA_ONLY,
        "access_status": AccessStatus.APPROVED,
        "snapshot_digest": DIGEST,
        "adapter_version": "1.0.0",
        "occurrence_unit": OccurrenceUnit.CLIP,
        "source_citation": "https://example.invalid/itest",
        "terms_review_id": "r1",
    }
    fields.update(overrides)
    return DatasetContract.model_validate(fields)


def record(index: int, **fields: str) -> SourceRecord:
    return SourceRecord(locator=f"snap:row-{index}", fields=fields)


@pytest.fixture(scope="module")
def database_url() -> str:
    try:
        url = resolve_database_url()
    except DatabaseConfigError:
        pytest.skip("DATABASE_URL is not configured")
    try:
        with psycopg.connect(url, connect_timeout=10):
            pass
    except psycopg.Error as exc:
        pytest.skip(f"database unreachable: {type(exc).__name__}")
    return url


@pytest.fixture
def conn(database_url: str) -> Iterator[psycopg.Connection[tuple[object, ...]]]:
    """A connection whose work is always rolled back."""
    with psycopg.connect(database_url) as connection:
        yield connection
        connection.rollback()


@pytest.fixture
def cur(
    conn: psycopg.Connection[tuple[object, ...]],
) -> Iterator[psycopg.Cursor[tuple[object, ...]]]:
    with conn.cursor() as cursor:
        yield cursor


def _registry(cur: psycopg.Cursor[tuple[object, ...]]) -> DatasetRegistry:
    return DatasetRegistry(PostgresDatasetRepository(cur))


# --- Registry round trip --------------------------------------------------


def test_registry_round_trip(cur: psycopg.Cursor[tuple[object, ...]]) -> None:
    result = _registry(cur).register(contract())

    assert result.outcome is RegistrationOutcome.CREATED
    assert _registry(cur).get(DatasetContractKey("itest-ds", "v1")) == contract()


def test_identical_resubmission_is_idempotent(
    cur: psycopg.Cursor[tuple[object, ...]],
) -> None:
    """Requirement 1.3: no duplicate row, no state change."""
    registry = _registry(cur)
    registry.register(contract())
    second = registry.register(contract())

    assert second.outcome is RegistrationOutcome.ALREADY_REGISTERED
    cur.execute("select count(*) from provenance.dataset_contract where dataset_id='itest-ds'")
    row = cur.fetchone()
    assert row is not None and row[0] == 1


def test_conflicting_revision_preserves_stored(
    cur: psycopg.Cursor[tuple[object, ...]],
) -> None:
    """Requirement 1.4: the stored contract survives a conflicting submission."""
    registry = _registry(cur)
    registry.register(contract())
    result = registry.register(contract(display_name="Changed"))

    assert result.outcome is RegistrationOutcome.REJECTED_CONFLICT
    stored = registry.get(DatasetContractKey("itest-ds", "v1"))
    assert stored is not None
    assert stored.display_name == "Integration DS"


def test_new_version_coexists(cur: psycopg.Cursor[tuple[object, ...]]) -> None:
    """Requirement 1.8: revision means a new key, not a mutation."""
    registry = _registry(cur)
    registry.register(contract())
    registry.register(contract(version="v2", snapshot_digest="sha256:" + "f" * 64))

    cur.execute("select count(*) from provenance.dataset_contract where dataset_id='itest-ds'")
    row = cur.fetchone()
    assert row is not None and row[0] == 2


# --- Full ingestion write path -------------------------------------------


def test_ingestion_persists_occurrences_membership_and_report(
    cur: psycopg.Cursor[tuple[object, ...]],
) -> None:
    c = contract()
    _registry(cur).register(c)

    records = [
        record(0, video_id=VALID_ID),
        record(1, video_id=VALID_ID),  # duplicate evidence, retained
        record(2, video_id=OTHER_ID),
        record(3, video_id="not-an-id"),  # rejected
    ]
    extraction = extract_records(
        records, adapter=VideoIdColumnAdapter(), contract=c, extracted_at=INSTANT
    )
    assert extraction.report is not None

    summary = write_ingestion(
        cur,
        contract=c,
        occurrences=extraction.occurrences,
        rejects=extraction.rejects,
        report=extraction.report,
        storage_uri="file:///tmp/itest.csv",
        byte_size=100,
        actor="itest-curator",
    )

    assert summary.occurrences_persisted == 3
    assert summary.rejects_persisted == 1

    # Requirement 2.11: duplicate evidence retained.
    cur.execute(
        "select count(*) from provenance.normalized_occurrence "
        "where dataset_id='itest-ds' and video_id=%s",
        (VALID_ID,),
    )
    row = cur.fetchone()
    assert row is not None and row[0] == 2

    # Requirement 3.3: membership is the deduplicated projection.
    cur.execute(
        "select count(*) from provenance.dataset_video_membership where dataset_id='itest-ds'"
    )
    row = cur.fetchone()
    assert row is not None and row[0] == 2

    # Requirement 2.14: the report reconciles in the database too.
    cur.execute(
        "select records_examined, records_accepted, records_rejected, "
        "occurrences_emitted from provenance.extraction_report "
        "where dataset_id='itest-ds'"
    )
    row = cur.fetchone()
    assert row is not None
    examined, accepted, rejected, emitted = row
    assert accepted + rejected == examined
    assert emitted == 3

    # Requirement 15.20: the operation is audited.
    cur.execute(
        "select action, outcome from governance.audit_log "
        "where actor='itest-curator' order by audit_id desc limit 1"
    )
    row = cur.fetchone()
    assert row is not None
    assert row[0] == "ingest_snapshot"
    assert row[1] == "success"


def test_occurrence_provenance_is_complete(
    cur: psycopg.Cursor[tuple[object, ...]],
) -> None:
    """Requirement 2.7: every persisted occurrence carries full provenance."""
    c = contract()
    _registry(cur).register(c)
    extraction = extract_records(
        [record(0, video_id=VALID_ID)],
        adapter=VideoIdColumnAdapter(),
        contract=c,
        extracted_at=INSTANT,
    )
    assert extraction.report is not None
    write_ingestion(
        cur,
        contract=c,
        occurrences=extraction.occurrences,
        rejects=extraction.rejects,
        report=extraction.report,
        storage_uri="file:///tmp/itest.csv",
        byte_size=10,
        actor="itest-curator",
    )

    cur.execute(
        "select snapshot_digest, source_locator, adapter_version, occurrence_unit "
        "from provenance.normalized_occurrence where dataset_id='itest-ds'"
    )
    row = cur.fetchone()
    assert row is not None
    assert row[0] == DIGEST
    assert row[1] == "snap:row-0"
    assert row[2] == "1.0.0"
    assert row[3] == "Clip"


def test_expansion_is_persisted_and_reconciles(
    cur: psycopg.Cursor[tuple[object, ...]],
) -> None:
    """Requirement 2.13: one record emitting many occurrences reconciles."""
    c = contract()
    _registry(cur).register(c)

    extraction = extract_records(
        [record(0, video_id=VALID_ID, clips="0-5;5-10;10-15")],
        adapter=RepeatedClipAdapter(),
        contract=c,
        extracted_at=INSTANT,
    )
    assert extraction.report is not None

    write_ingestion(
        cur,
        contract=c,
        occurrences=extraction.occurrences,
        rejects=extraction.rejects,
        report=extraction.report,
        storage_uri="file:///tmp/itest.csv",
        byte_size=10,
        actor="itest-curator",
    )

    cur.execute(
        "select occurrences_emitted, expansion_count, records_accepted "
        "from provenance.extraction_report where dataset_id='itest-ds'"
    )
    row = cur.fetchone()
    assert row is not None
    assert tuple(row) == (3, 2, 1)

    cur.execute("select count(*) from provenance.normalized_occurrence where dataset_id='itest-ds'")
    row = cur.fetchone()
    assert row is not None and row[0] == 3


def test_clip_bounds_survive_the_round_trip(
    cur: psycopg.Cursor[tuple[object, ...]],
) -> None:
    c = contract()
    _registry(cur).register(c)
    extraction = extract_records(
        [record(0, video_id=VALID_ID, clip_start="1.5", clip_end="9.25")],
        adapter=VideoIdColumnAdapter(),
        contract=c,
        extracted_at=INSTANT,
    )
    assert extraction.report is not None
    write_ingestion(
        cur,
        contract=c,
        occurrences=extraction.occurrences,
        rejects=extraction.rejects,
        report=extraction.report,
        storage_uri="file:///tmp/itest.csv",
        byte_size=10,
        actor="itest-curator",
    )

    cur.execute(
        "select clip_start, clip_end from provenance.normalized_occurrence "
        "where dataset_id='itest-ds'"
    )
    row = cur.fetchone()
    assert row is not None
    assert (row[0], row[1]) == (1.5, 9.25)


def test_non_conserving_report_is_refused_by_the_database(
    cur: psycopg.Cursor[tuple[object, ...]],
) -> None:
    """The schema constraint is the last line of defence for Invariant 2."""
    _registry(cur).register(contract())

    with pytest.raises(psycopg.errors.CheckViolation):
        cur.execute(
            "insert into provenance.extraction_report "
            "(dataset_id, dataset_version, snapshot_digest, adapter_version, "
            " schema_version, records_examined, records_accepted, records_rejected, "
            " occurrences_emitted, expansion_count) "
            "values ('itest-ds','v1',%s,'1.0.0','1',10,5,4,5,0)",
            (DIGEST,),
        )


# --- Requirement 15.20 / 15.21: auditing ---------------------------------


def test_audit_failure_raises(conn: psycopg.Connection[tuple[object, ...]]) -> None:
    """An unwritable audit record must surface, not pass silently."""
    cursor = conn.cursor()
    cursor.close()

    with pytest.raises(AuditWriteFailure):
        record_audit(
            cursor,
            AuditEntry(
                actor="x",
                action="a",
                resource_class="r",
                outcome="success",
                detail={},
            ),
        )


def test_audit_detail_carries_no_restricted_values(
    cur: psycopg.Cursor[tuple[object, ...]],
) -> None:
    """Requirement 15.11: audit detail is non-sensitive."""
    record_audit(
        cur,
        AuditEntry(
            actor="itest-curator",
            action="ingest_snapshot",
            resource_class="provenance.normalized_occurrence",
            outcome="success",
            detail={"datasetId": "itest-ds", "recordsExamined": "10"},
        ),
    )
    cur.execute(
        "select detail from governance.audit_log "
        "where actor='itest-curator' order by audit_id desc limit 1"
    )
    row = cur.fetchone()
    assert row is not None
    detail = row[0] if isinstance(row[0], dict) else json.loads(str(row[0]))

    serialized = json.dumps(detail)
    assert "sourceLocator" not in detail
    assert VALID_ID not in serialized
    assert "snap:row-" not in serialized


# --- Reader-stage rejects are persisted ----------------------------------


def test_reader_quarantines_are_persisted_with_reasons(
    cur: psycopg.Cursor[tuple[object, ...]],
) -> None:
    c = contract()
    _registry(cur).register(c)

    prior = [
        Quarantined("snap:row-9", QuarantineReason.FORMULA_CONTENT, "field 'note'"),
        Quarantined("snap:row-10", QuarantineReason.FIELD_TOO_LARGE),
    ]
    extraction = extract_records(
        [record(0, video_id=VALID_ID)],
        adapter=VideoIdColumnAdapter(),
        contract=c,
        prior_quarantined=prior,
        extracted_at=INSTANT,
    )
    assert extraction.report is not None

    write_ingestion(
        cur,
        contract=c,
        occurrences=extraction.occurrences,
        rejects=extraction.rejects,
        report=extraction.report,
        storage_uri="file:///tmp/itest.csv",
        byte_size=10,
        actor="itest-curator",
    )

    cur.execute(
        "select rejection_reason from provenance.extraction_reject "
        "where dataset_id='itest-ds' order by reject_id"
    )
    reasons = [str(row[0]) for row in cur.fetchall()]

    assert len(reasons) == 2
    assert all(reason for reason in reasons)
