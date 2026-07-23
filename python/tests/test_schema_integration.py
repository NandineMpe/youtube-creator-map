"""Integration tests verifying the applied schema enforces its invariants.

These require a live PostgreSQL reachable via DATABASE_URL and are skipped
otherwise, so the unit suite still runs on a machine with no database.

Every test runs inside a transaction that is rolled back, so the suite
leaves no residue and is safe to run against a shared development database.
"""

from __future__ import annotations

from collections.abc import Iterator

import psycopg
import pytest
from creator_map_pipeline.database import DatabaseConfigError, resolve_database_url

pytestmark = pytest.mark.integration


def _database_url() -> str | None:
    try:
        return resolve_database_url()
    except DatabaseConfigError:
        return None


@pytest.fixture(scope="module")
def database_url() -> str:
    url = _database_url()
    if url is None:
        pytest.skip("DATABASE_URL is not configured")
    try:
        with psycopg.connect(url, connect_timeout=10):
            pass
    except psycopg.Error as exc:
        pytest.skip(f"database unreachable: {type(exc).__name__}")
    return url


@pytest.fixture
def cur(database_url: str) -> Iterator[psycopg.Cursor[tuple[object, ...]]]:
    """Yield a cursor whose work is always rolled back."""
    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cursor:
            _seed_contract(cursor)
            yield cursor
        conn.rollback()


def _seed_contract(cur: psycopg.Cursor[tuple[object, ...]]) -> None:
    """Insert a contract and snapshot so occurrence rows have parents."""
    cur.execute(
        "insert into provenance.dataset_contract (dataset_id, dataset_version, "
        "display_name, corpus_class, source_kind, access_status, snapshot_digest, "
        "adapter_version, occurrence_unit, source_citation, acquisition_path, "
        "terms_review_id) values ('ds','v1','DS','Candidate','MetadataOnly',"
        "'Approved','sha256:x','1.0.0','Clip','https://e.invalid','permitted','r1')"
    )
    cur.execute(
        "insert into provenance.source_snapshot (snapshot_digest, dataset_id, "
        "dataset_version, storage_uri, byte_size) values "
        "('sha256:x','ds','v1','s3://b/k',1)"
    )


def _insert_occurrence(
    cur: psycopg.Cursor[tuple[object, ...]],
    *,
    video_id: str = "vid",
    clip_start: float | None = None,
    clip_end: float | None = None,
) -> None:
    cur.execute(
        "insert into provenance.normalized_occurrence (dataset_id, dataset_version, "
        "snapshot_digest, source_locator, video_id, clip_start, clip_end, "
        "occurrence_unit, extracted_at, adapter_version) values "
        "('ds','v1','sha256:x','loc',%s,%s,%s,'Clip',now(),'1.0.0')",
        (video_id, clip_start, clip_end),
    )


# --- Requirement 1.4 / 1.8: contracts are immutable -----------------------


def test_contract_update_is_rejected(cur: psycopg.Cursor[tuple[object, ...]]) -> None:
    """Migration 0004 raises rather than silently discarding the mutation."""
    with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
        cur.execute(
            "update provenance.dataset_contract set display_name='CHANGED' where dataset_id='ds'"
        )


def test_contract_delete_is_rejected(cur: psycopg.Cursor[tuple[object, ...]]) -> None:
    with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
        cur.execute("delete from provenance.dataset_contract where dataset_id='ds'")


# --- Requirement 2.9: clip bounds ----------------------------------------


@pytest.mark.parametrize(
    ("start", "end"),
    [(-1.0, 5.0), (5.0, 5.0), (9.0, 2.0), (1.0, None), (None, 1.0)],
)
def test_invalid_clip_bounds_rejected(
    cur: psycopg.Cursor[tuple[object, ...]], start: float | None, end: float | None
) -> None:
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert_occurrence(cur, clip_start=start, clip_end=end)


def test_valid_clip_bounds_accepted(cur: psycopg.Cursor[tuple[object, ...]]) -> None:
    _insert_occurrence(cur, clip_start=0.0, clip_end=12.5)


# --- Requirement 2.11: repeated evidence is retained ---------------------


def test_duplicate_occurrences_are_retained(
    cur: psycopg.Cursor[tuple[object, ...]],
) -> None:
    """Deduplicating here would destroy the source-occurrence count."""
    for _ in range(3):
        _insert_occurrence(cur, video_id="samevid")
    cur.execute("select count(*) from provenance.normalized_occurrence where video_id='samevid'")
    row = cur.fetchone()
    assert row is not None and row[0] == 3


# --- Requirement 2.14: extraction conservation ---------------------------


def test_non_conserving_extraction_report_rejected(
    cur: psycopg.Cursor[tuple[object, ...]],
) -> None:
    with pytest.raises(psycopg.errors.CheckViolation):
        cur.execute(
            "insert into provenance.extraction_report (dataset_id, dataset_version, "
            "snapshot_digest, adapter_version, schema_version, records_examined, "
            "records_accepted, records_rejected, occurrences_emitted, expansion_count) "
            "values ('ds','v1','sha256:x','1.0.0','1',10,5,4,5,0)"
        )


def test_conserving_extraction_report_accepted(
    cur: psycopg.Cursor[tuple[object, ...]],
) -> None:
    cur.execute(
        "insert into provenance.extraction_report (dataset_id, dataset_version, "
        "snapshot_digest, adapter_version, schema_version, records_examined, "
        "records_accepted, records_rejected, occurrences_emitted, expansion_count) "
        "values ('ds','v1','sha256:x','1.0.0','1',10,6,4,9,3)"
    )


# --- Requirement 3.5 / 3.6 / 3.9: observations ---------------------------


def test_resolved_video_without_channel_rejected(
    cur: psycopg.Cursor[tuple[object, ...]],
) -> None:
    with pytest.raises(psycopg.errors.CheckViolation):
        cur.execute(
            "insert into enrichment.video_observation (video_id, status, observed_at, "
            "policy_version) values ('v','Resolved',now(),'1')"
        )


def test_unavailable_video_with_channel_rejected(
    cur: psycopg.Cursor[tuple[object, ...]],
) -> None:
    """An omitted ID must not carry attribution (Requirement 3.6)."""
    with pytest.raises(psycopg.errors.CheckViolation):
        cur.execute(
            "insert into enrichment.video_observation (video_id, status, channel_id, "
            "observed_at, policy_version) values "
            "('v','UnavailableUnclassified','UC1',now(),'1')"
        )


def test_observation_update_and_delete_are_rejected(
    cur: psycopg.Cursor[tuple[object, ...]],
) -> None:
    """Requirement 3.9: observations are strictly append-only."""
    cur.execute(
        "insert into enrichment.video_observation (video_id, status, channel_id, "
        "observed_at, policy_version) values ('v2','Resolved','UC_A',now(),'1')"
    )
    cur.execute("savepoint sp")
    with pytest.raises(psycopg.errors.RestrictViolation):
        cur.execute("update enrichment.video_observation set channel_id='UC_B' where video_id='v2'")
    cur.execute("rollback to savepoint sp")

    with pytest.raises(psycopg.errors.RestrictViolation):
        cur.execute("delete from enrichment.video_observation where video_id='v2'")
    cur.execute("rollback to savepoint sp")

    # The original observation is intact.
    cur.execute("select channel_id from enrichment.video_observation where video_id='v2'")
    row = cur.fetchone()
    assert row is not None and row[0] == "UC_A"


# --- Requirement 3.8 / Invariant 6: no inferred country ------------------


def test_malformed_country_rejected(cur: psycopg.Cursor[tuple[object, ...]]) -> None:
    with pytest.raises((psycopg.errors.CheckViolation, psycopg.errors.StringDataRightTruncation)):
        cur.execute(
            "insert into enrichment.channel_observation (channel_id, status, "
            "declared_country, observed_at, policy_version) values "
            "('UC1','Resolved','zz',now(),'1')"
        )


def test_absent_country_stays_null(cur: psycopg.Cursor[tuple[object, ...]]) -> None:
    """Nothing backfills a country the API did not declare."""
    cur.execute(
        "insert into enrichment.channel_observation (channel_id, status, observed_at, "
        "policy_version) values ('UC_nc','Resolved',now(),'1')"
    )
    cur.execute(
        "select declared_country from enrichment.channel_observation where channel_id='UC_nc'"
    )
    row = cur.fetchone()
    assert row is not None and row[0] is None


# --- Requirement 4.1 / 4.3 / 4.7: work identity, leases, checkpoints -----


def _seed_job(cur: psycopg.Cursor[tuple[object, ...]]) -> None:
    cur.execute(
        "insert into enrichment.job (job_id, entity_kind, policy_version) values ('j1','Video','1')"
    )


def test_duplicate_work_identity_rejected(
    cur: psycopg.Cursor[tuple[object, ...]],
) -> None:
    """Requirement 4.1 is what makes claiming idempotent."""
    _seed_job(cur)
    cur.execute(
        "insert into enrichment.work_item (job_id, entity_kind, entity_id, policy_version) "
        "values ('j1','Video','vid1','1')"
    )
    with pytest.raises(psycopg.errors.UniqueViolation):
        cur.execute(
            "insert into enrichment.work_item (job_id, entity_kind, entity_id, "
            "policy_version) values ('j1','Video','vid1','1')"
        )


def test_same_entity_under_new_policy_is_distinct_work(
    cur: psycopg.Cursor[tuple[object, ...]],
) -> None:
    """A new policy version legitimately re-enriches the same entity."""
    _seed_job(cur)
    cur.execute(
        "insert into enrichment.work_item (job_id, entity_kind, entity_id, policy_version) "
        "values ('j1','Video','vid1','1')"
    )
    cur.execute(
        "insert into enrichment.work_item (job_id, entity_kind, entity_id, policy_version) "
        "values ('j1','Video','vid1','2')"
    )
    cur.execute("select count(*) from enrichment.work_item where entity_id='vid1'")
    row = cur.fetchone()
    assert row is not None and row[0] == 2


def test_leased_item_requires_owner_and_expiry(
    cur: psycopg.Cursor[tuple[object, ...]],
) -> None:
    """Requirement 4.3: an expired lease must be detectable."""
    _seed_job(cur)
    with pytest.raises(psycopg.errors.CheckViolation):
        cur.execute(
            "insert into enrichment.work_item (job_id, entity_kind, entity_id, "
            "policy_version, state) values ('j1','Video','vid9','1','Leased')"
        )


def test_replayed_checkpoint_is_rejected(
    cur: psycopg.Cursor[tuple[object, ...]],
) -> None:
    """Requirement 4.7: replay must not duplicate committed effects."""
    _seed_job(cur)
    cur.execute(
        "insert into enrichment.checkpoint (job_id, batch_key, items_committed, "
        "quota_units_used) values ('j1','batch-1',50,1)"
    )
    with pytest.raises(psycopg.errors.UniqueViolation):
        cur.execute(
            "insert into enrichment.checkpoint (job_id, batch_key, items_committed, "
            "quota_units_used) values ('j1','batch-1',50,1)"
        )


# --- Invariant 15: exactly one active release ----------------------------


def _seed_releases(cur: psycopg.Cursor[tuple[object, ...]]) -> None:
    cur.execute(
        "insert into governance.disclosure_policy (policy_id, version, document, "
        "approved_at, approved_by) values ('dp','1','{}'::jsonb, now(),'curator')"
    )
    for release_id in ("rel-a", "rel-b"):
        cur.execute(
            "insert into governance.release (release_id, manifest, manifest_digest, "
            "enrichment_cutoff, methodology_version, disclosure_policy_id, "
            "disclosure_policy_version, generated_at) values "
            "(%s,'{}'::jsonb,'sha256:m',now(),'1','dp','1',now())",
            (release_id,),
        )


def test_second_active_release_is_unrepresentable(
    cur: psycopg.Cursor[tuple[object, ...]],
) -> None:
    _seed_releases(cur)
    cur.execute(
        "insert into governance.active_release_pointer (release_id, activated_by) "
        "values ('rel-a','curator')"
    )
    with pytest.raises(psycopg.errors.UniqueViolation):
        cur.execute(
            "insert into governance.active_release_pointer (release_id, activated_by) "
            "values ('rel-b','curator')"
        )


def test_activation_is_an_atomic_single_row_swap(
    cur: psycopg.Cursor[tuple[object, ...]],
) -> None:
    """Requirement 8.7: no observable instant exposes a mixed release."""
    _seed_releases(cur)
    cur.execute(
        "insert into governance.active_release_pointer (release_id, activated_by) "
        "values ('rel-a','curator')"
    )
    cur.execute(
        "update governance.active_release_pointer set release_id='rel-b' where pointer_id = true"
    )
    cur.execute("select release_id from governance.active_release_pointer")
    rows = cur.fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "rel-b"


# --- Requirement 15.20: audit log is tamper-evident -----------------------


def test_audit_log_update_and_delete_are_rejected(
    cur: psycopg.Cursor[tuple[object, ...]],
) -> None:
    """Requirement 15.20: the audit log is tamper-evident."""
    cur.execute(
        "insert into governance.audit_log (actor, action, resource_class, outcome) "
        "values ('curator','migrate','schema','success')"
    )
    cur.execute("savepoint sp")
    with pytest.raises(psycopg.errors.RestrictViolation):
        cur.execute("update governance.audit_log set outcome='tampered' where actor='curator'")
    cur.execute("rollback to savepoint sp")

    with pytest.raises(psycopg.errors.RestrictViolation):
        cur.execute("delete from governance.audit_log where actor='curator'")
    cur.execute("rollback to savepoint sp")

    cur.execute("select outcome from governance.audit_log where actor='curator'")
    row = cur.fetchone()
    assert row is not None and row[0] == "success"
