-- Migration 0001: Dataset registry, immutable provenance, and normalized occurrences.
--
-- Requirement refs:
--   1.3, 1.4  Idempotent registration; conflicting revisions rejected.
--   1.8       Revising an approved contract requires a new version key.
--   2.7       Accepted occurrences carry complete provenance.
--   3.3       Dataset membership is many-to-many without copying observations.
--
-- Immutability strategy: contracts and occurrences are append-only. Rules
-- reject UPDATE and DELETE at the database level so a compromised or buggy
-- application path cannot rewrite published provenance. Corrections happen
-- by inserting a new dataset version (Requirement 1.8), never by mutation.

BEGIN;

CREATE SCHEMA IF NOT EXISTS provenance;

-- --------------------------------------------------------------------------
-- Dataset registry
-- --------------------------------------------------------------------------

CREATE TYPE provenance.corpus_class AS ENUM ('Candidate', 'Comparison');

CREATE TYPE provenance.source_kind AS ENUM ('MetadataOnly', 'MediaIndex', 'SubtitleIndex');

CREATE TYPE provenance.access_status AS ENUM ('Proposed', 'Approved', 'Blocked');

CREATE TYPE provenance.occurrence_unit AS ENUM (
    'Clip', 'Timestamp', 'Segment', 'Row', 'Video'
);

CREATE TABLE provenance.dataset_contract (
    dataset_id          text        NOT NULL,
    dataset_version     text        NOT NULL,
    display_name        text        NOT NULL,
    corpus_class        provenance.corpus_class  NOT NULL,
    source_kind         provenance.source_kind   NOT NULL,
    access_status       provenance.access_status NOT NULL,
    snapshot_digest     text        NOT NULL,
    adapter_version     text        NOT NULL,
    occurrence_unit     provenance.occurrence_unit NOT NULL,
    source_citation     text        NOT NULL,
    acquisition_path    text        NOT NULL,
    terms_review_id     text        NOT NULL,
    registered_at       timestamptz NOT NULL DEFAULT now(),

    -- Dataset_Contract_Key (Requirement 1.1): the pair addresses one contract.
    CONSTRAINT dataset_contract_pkey PRIMARY KEY (dataset_id, dataset_version),

    -- Required metadata must be substantive, not empty strings that would
    -- satisfy NOT NULL while leaving provenance undocumented (Requirement 1.5).
    CONSTRAINT dataset_contract_fields_non_empty CHECK (
        length(btrim(dataset_id)) > 0
        AND length(btrim(dataset_version)) > 0
        AND length(btrim(display_name)) > 0
        AND length(btrim(snapshot_digest)) > 0
        AND length(btrim(adapter_version)) > 0
        AND length(btrim(source_citation)) > 0
        AND length(btrim(acquisition_path)) > 0
        AND length(btrim(terms_review_id)) > 0
    )
);

COMMENT ON TABLE provenance.dataset_contract IS
    'Immutable versioned dataset contracts. Revision requires a new dataset_version (Requirement 1.8).';

-- Requirement 1.4/1.8: a stored contract is never altered or removed. A
-- submission that differs for the same key must be rejected by the
-- application and can never overwrite the stored row here.
CREATE RULE dataset_contract_no_update AS
    ON UPDATE TO provenance.dataset_contract DO INSTEAD NOTHING;

CREATE RULE dataset_contract_no_delete AS
    ON DELETE TO provenance.dataset_contract DO INSTEAD NOTHING;

-- --------------------------------------------------------------------------
-- Source snapshots
-- --------------------------------------------------------------------------

CREATE TABLE provenance.source_snapshot (
    snapshot_digest     text        NOT NULL PRIMARY KEY,
    dataset_id          text        NOT NULL,
    dataset_version     text        NOT NULL,
    storage_uri         text        NOT NULL,
    byte_size           bigint      NOT NULL,
    recorded_at         timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT source_snapshot_contract_fkey
        FOREIGN KEY (dataset_id, dataset_version)
        REFERENCES provenance.dataset_contract (dataset_id, dataset_version),

    CONSTRAINT source_snapshot_byte_size_non_negative CHECK (byte_size >= 0)
);

CREATE RULE source_snapshot_no_update AS
    ON UPDATE TO provenance.source_snapshot DO INSTEAD NOTHING;

-- --------------------------------------------------------------------------
-- Normalized occurrences
-- --------------------------------------------------------------------------

CREATE TABLE provenance.normalized_occurrence (
    occurrence_id       bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    dataset_id          text        NOT NULL,
    dataset_version     text        NOT NULL,
    snapshot_digest     text        NOT NULL REFERENCES provenance.source_snapshot (snapshot_digest),
    source_locator      text        NOT NULL,
    video_id            text        NOT NULL,
    clip_start          double precision,
    clip_end            double precision,
    occurrence_unit     provenance.occurrence_unit NOT NULL,
    extracted_at        timestamptz NOT NULL,
    adapter_version     text        NOT NULL,

    CONSTRAINT normalized_occurrence_contract_fkey
        FOREIGN KEY (dataset_id, dataset_version)
        REFERENCES provenance.dataset_contract (dataset_id, dataset_version),

    -- Requirement 2.7: mandatory provenance for every accepted occurrence.
    CONSTRAINT normalized_occurrence_provenance_complete CHECK (
        length(btrim(source_locator)) > 0
        AND length(btrim(video_id)) > 0
        AND length(btrim(adapter_version)) > 0
    ),

    -- Requirement 2.9: clip bounds are valid only when 0 <= start < end, and
    -- are either both present or both absent.
    CONSTRAINT normalized_occurrence_clip_bounds CHECK (
        (clip_start IS NULL AND clip_end IS NULL)
        OR (clip_start IS NOT NULL AND clip_end IS NOT NULL
            AND clip_start >= 0 AND clip_start < clip_end)
    )
);

COMMENT ON TABLE provenance.normalized_occurrence IS
    'Append-only accepted occurrences. Requirement 2.11 forbids deduplicating source evidence, so repeated (dataset, video) rows are expected and carry no unique constraint.';

CREATE RULE normalized_occurrence_no_update AS
    ON UPDATE TO provenance.normalized_occurrence DO INSTEAD NOTHING;

CREATE RULE normalized_occurrence_no_delete AS
    ON DELETE TO provenance.normalized_occurrence DO INSTEAD NOTHING;

-- Aggregation reads occurrences by filter (dataset) and joins to resolutions
-- by video. Both access paths get an index.
CREATE INDEX normalized_occurrence_dataset_idx
    ON provenance.normalized_occurrence (dataset_id, dataset_version);

CREATE INDEX normalized_occurrence_video_idx
    ON provenance.normalized_occurrence (video_id);

-- Requirement 3.3: dataset membership is a many-to-many relation derived from
-- occurrences. Materializing the distinct pair keeps the enrichment planner
-- from scanning the full occurrence table, which is far larger because
-- repeated evidence is retained.
CREATE TABLE provenance.dataset_video_membership (
    dataset_id          text        NOT NULL,
    dataset_version     text        NOT NULL,
    video_id            text        NOT NULL,
    first_seen_at       timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT dataset_video_membership_pkey
        PRIMARY KEY (dataset_id, dataset_version, video_id),

    CONSTRAINT dataset_video_membership_contract_fkey
        FOREIGN KEY (dataset_id, dataset_version)
        REFERENCES provenance.dataset_contract (dataset_id, dataset_version)
);

CREATE INDEX dataset_video_membership_video_idx
    ON provenance.dataset_video_membership (video_id);

-- --------------------------------------------------------------------------
-- Rejects and extraction reports
-- --------------------------------------------------------------------------

CREATE TABLE provenance.extraction_reject (
    reject_id           bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    dataset_id          text        NOT NULL,
    dataset_version     text        NOT NULL,
    snapshot_digest     text        NOT NULL,
    source_locator      text        NOT NULL,
    adapter_version     text        NOT NULL,
    -- Requirement 2.6/2.15: a rejection always carries a non-empty reason.
    rejection_reason    text        NOT NULL,
    rejected_at         timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT extraction_reject_reason_non_empty
        CHECK (length(btrim(rejection_reason)) > 0)
);

CREATE INDEX extraction_reject_dataset_idx
    ON provenance.extraction_reject (dataset_id, dataset_version);

CREATE TABLE provenance.extraction_report (
    report_id           bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    dataset_id          text        NOT NULL,
    dataset_version     text        NOT NULL,
    snapshot_digest     text        NOT NULL,
    adapter_version     text        NOT NULL,
    schema_version      text        NOT NULL,
    records_examined    bigint      NOT NULL,
    records_accepted    bigint      NOT NULL,
    records_rejected    bigint      NOT NULL,
    occurrences_emitted bigint      NOT NULL,
    expansion_count     bigint      NOT NULL,
    completed_at        timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT extraction_report_contract_fkey
        FOREIGN KEY (dataset_id, dataset_version)
        REFERENCES provenance.dataset_contract (dataset_id, dataset_version),

    CONSTRAINT extraction_report_counts_non_negative CHECK (
        records_examined >= 0 AND records_accepted >= 0
        AND records_rejected >= 0 AND occurrences_emitted >= 0
        AND expansion_count >= 0
    ),

    -- Requirement 2.14 (Invariant 2): accepted + rejected = examined. The
    -- conservation law is enforced here so a miscounted run cannot be
    -- persisted and later mistaken for a valid extraction.
    CONSTRAINT extraction_report_conservation CHECK (
        records_accepted + records_rejected = records_examined
    ),

    -- One accepted record emits at least one occurrence; expansion accounts
    -- for the excess (Requirement 2.13).
    CONSTRAINT extraction_report_expansion CHECK (
        occurrences_emitted = records_accepted + expansion_count
    )
);

CREATE RULE extraction_report_no_update AS
    ON UPDATE TO provenance.extraction_report DO INSTEAD NOTHING;

CREATE INDEX extraction_report_dataset_idx
    ON provenance.extraction_report (dataset_id, dataset_version, completed_at DESC);

COMMIT;
