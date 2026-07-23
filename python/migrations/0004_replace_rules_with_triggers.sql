-- Migration 0004: Replace append-only RULEs with BEFORE triggers.
--
-- Migrations 0001-0003 enforced append-only semantics with RULEs. PostgreSQL
-- rejects `INSERT ... ON CONFLICT` against any table carrying an INSERT or
-- UPDATE rule ("FeatureNotSupported"), which makes idempotent upserts
-- impossible on those tables. Idempotent inserts are required for resumable
-- ingestion (a re-run must not fail on an already-registered snapshot) and
-- for the membership projection of Requirement 3.3.
--
-- BEFORE triggers provide the same guarantee without that interaction:
-- returning NULL from a BEFORE row trigger suppresses the operation. Unlike
-- rules, triggers do not rewrite the query tree, so ON CONFLICT continues to
-- work normally.
--
-- Behaviour is deliberately strengthened at the same time: the previous
-- rules silently discarded an UPDATE or DELETE, which meant a buggy caller
-- could believe it had mutated a record. The triggers raise instead, so an
-- attempt to mutate immutable provenance is a loud error rather than a
-- silent no-op.
--
-- Requirement refs: 1.4, 1.8, 3.9, 8.9, 15.20

BEGIN;

-- --------------------------------------------------------------------------
-- Drop the rules installed by 0001-0003
-- --------------------------------------------------------------------------

DROP RULE IF EXISTS dataset_contract_no_update ON provenance.dataset_contract;
DROP RULE IF EXISTS dataset_contract_no_delete ON provenance.dataset_contract;
DROP RULE IF EXISTS source_snapshot_no_update ON provenance.source_snapshot;
DROP RULE IF EXISTS normalized_occurrence_no_update ON provenance.normalized_occurrence;
DROP RULE IF EXISTS normalized_occurrence_no_delete ON provenance.normalized_occurrence;
DROP RULE IF EXISTS extraction_report_no_update ON provenance.extraction_report;
DROP RULE IF EXISTS video_observation_no_update ON enrichment.video_observation;
DROP RULE IF EXISTS video_observation_no_delete ON enrichment.video_observation;
DROP RULE IF EXISTS channel_observation_no_update ON enrichment.channel_observation;
DROP RULE IF EXISTS channel_observation_no_delete ON enrichment.channel_observation;
DROP RULE IF EXISTS checkpoint_no_update ON enrichment.checkpoint;
DROP RULE IF EXISTS disclosure_policy_no_update ON governance.disclosure_policy;
DROP RULE IF EXISTS disclosure_policy_no_delete ON governance.disclosure_policy;
DROP RULE IF EXISTS enrichment_policy_no_update ON governance.enrichment_policy;
DROP RULE IF EXISTS release_artifact_no_update ON governance.release_artifact;
DROP RULE IF EXISTS audit_log_no_update ON governance.audit_log;
DROP RULE IF EXISTS audit_log_no_delete ON governance.audit_log;

-- --------------------------------------------------------------------------
-- Append-only enforcement
-- --------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION provenance.reject_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION
        'relation %.% is append-only; % is not permitted',
        TG_TABLE_SCHEMA, TG_TABLE_NAME, TG_OP
        USING ERRCODE = 'restrict_violation';
END;
$$;

COMMENT ON FUNCTION provenance.reject_mutation() IS
    'Raises on UPDATE/DELETE against append-only relations. Used by BEFORE triggers so ON CONFLICT remains usable (unlike RULEs).';

-- Contracts: immutable once stored (Requirements 1.4, 1.8).
CREATE TRIGGER dataset_contract_append_only
    BEFORE UPDATE OR DELETE ON provenance.dataset_contract
    FOR EACH ROW EXECUTE FUNCTION provenance.reject_mutation();

-- Snapshots: content-addressed, so an update would break the address.
-- DELETE stays permitted: an unreferenced snapshot record may be retracted
-- before any occurrence cites it.
CREATE TRIGGER source_snapshot_no_update
    BEFORE UPDATE ON provenance.source_snapshot
    FOR EACH ROW EXECUTE FUNCTION provenance.reject_mutation();

-- Occurrences: append-only evidence (Requirement 2.11).
CREATE TRIGGER normalized_occurrence_append_only
    BEFORE UPDATE OR DELETE ON provenance.normalized_occurrence
    FOR EACH ROW EXECUTE FUNCTION provenance.reject_mutation();

CREATE TRIGGER extraction_report_no_update
    BEFORE UPDATE ON provenance.extraction_report
    FOR EACH ROW EXECUTE FUNCTION provenance.reject_mutation();

-- Observations: append-only history (Requirement 3.9).
CREATE TRIGGER video_observation_append_only
    BEFORE UPDATE OR DELETE ON enrichment.video_observation
    FOR EACH ROW EXECUTE FUNCTION provenance.reject_mutation();

CREATE TRIGGER channel_observation_append_only
    BEFORE UPDATE OR DELETE ON enrichment.channel_observation
    FOR EACH ROW EXECUTE FUNCTION provenance.reject_mutation();

CREATE TRIGGER checkpoint_no_update
    BEFORE UPDATE ON enrichment.checkpoint
    FOR EACH ROW EXECUTE FUNCTION provenance.reject_mutation();

-- Policies: an approved version is immutable (Requirement 7.1).
CREATE TRIGGER disclosure_policy_append_only
    BEFORE UPDATE OR DELETE ON governance.disclosure_policy
    FOR EACH ROW EXECUTE FUNCTION provenance.reject_mutation();

CREATE TRIGGER enrichment_policy_no_update
    BEFORE UPDATE ON governance.enrichment_policy
    FOR EACH ROW EXECUTE FUNCTION provenance.reject_mutation();

-- Published artifact digests never change (Requirement 8.9).
CREATE TRIGGER release_artifact_no_update
    BEFORE UPDATE ON governance.release_artifact
    FOR EACH ROW EXECUTE FUNCTION provenance.reject_mutation();

-- Audit log is tamper-evident (Requirement 15.20).
CREATE TRIGGER audit_log_append_only
    BEFORE UPDATE OR DELETE ON governance.audit_log
    FOR EACH ROW EXECUTE FUNCTION provenance.reject_mutation();

COMMIT;
