-- Migration 0003: Versioned policies, suppressions, release records, and the
-- durable audit log.
--
-- Requirement refs:
--   7.1, 7.4, 7.8   Versioned disclosure policy and exclusion records.
--   8.1, 8.7-8.9    Release manifests, atomic activation, historical immutability.
--   15.4-15.6       Role and resource-class separation.
--   15.20-15.22     Durable audit of restricted access and admin operations.

BEGIN;

CREATE SCHEMA IF NOT EXISTS governance;

CREATE TYPE governance.suppression_kind AS ENUM ('Correction', 'OptOut', 'Suppression');

CREATE TYPE governance.suppression_scope AS ENUM ('Full', 'Fields');

CREATE TYPE governance.release_state AS ENUM (
    'Candidate', 'Staged', 'Verified', 'Superseded', 'Rejected'
);

-- --------------------------------------------------------------------------
-- Versioned policies
-- --------------------------------------------------------------------------

-- Policies are stored as validated JSON documents rather than exploded
-- columns: their internal shape is versioned and expected to evolve, while
-- the release only ever pins them by (policy_id, version). Storing the exact
-- approved bytes also lets a historical release be revalidated against the
-- policy that actually governed it.
CREATE TABLE governance.disclosure_policy (
    policy_id           text        NOT NULL,
    version             text        NOT NULL,
    document            jsonb       NOT NULL,
    approved_at         timestamptz NOT NULL,
    approved_by         text        NOT NULL,
    recorded_at         timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT disclosure_policy_pkey PRIMARY KEY (policy_id, version),

    CONSTRAINT disclosure_policy_approver_present
        CHECK (length(btrim(approved_by)) > 0)
);

-- Requirement 7.1: an approved policy version is immutable. Changing rules
-- requires a new version so a published release's governing policy can
-- always be reconstructed.
CREATE RULE disclosure_policy_no_update AS
    ON UPDATE TO governance.disclosure_policy DO INSTEAD NOTHING;

CREATE RULE disclosure_policy_no_delete AS
    ON DELETE TO governance.disclosure_policy DO INSTEAD NOTHING;

CREATE TABLE governance.enrichment_policy (
    policy_id           text        NOT NULL,
    version             text        NOT NULL,
    document            jsonb       NOT NULL,
    approved_at         timestamptz NOT NULL,
    recorded_at         timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT enrichment_policy_pkey PRIMARY KEY (policy_id, version)
);

CREATE RULE enrichment_policy_no_update AS
    ON UPDATE TO governance.enrichment_policy DO INSTEAD NOTHING;

-- --------------------------------------------------------------------------
-- Corrections, opt-outs, and suppressions
-- --------------------------------------------------------------------------

CREATE TABLE governance.suppression_record (
    record_id           text        NOT NULL PRIMARY KEY,
    channel_id          text        NOT NULL,
    kind                governance.suppression_kind NOT NULL,
    scope               governance.suppression_scope NOT NULL,
    suppressed_fields   text[]      NOT NULL DEFAULT '{}',
    -- Requirement 7.8: the reason supports operator review and must never
    -- reach a public artifact. The column name marks it restricted so a
    -- field-enumerating serializer cannot mistake it for publishable.
    restricted_reason   text        NOT NULL,
    recorded_at         timestamptz NOT NULL DEFAULT now(),
    revoked_at          timestamptz,

    CONSTRAINT suppression_scope_fields_agree CHECK (
        (scope = 'Fields' AND array_length(suppressed_fields, 1) IS NOT NULL)
        OR (scope = 'Full' AND array_length(suppressed_fields, 1) IS NULL)
    ),

    CONSTRAINT suppression_reason_non_empty
        CHECK (length(btrim(restricted_reason)) > 0)
);

-- The disclosure engine looks up active records by channel on every build.
CREATE INDEX suppression_record_active_channel_idx
    ON governance.suppression_record (channel_id)
    WHERE revoked_at IS NULL;

-- --------------------------------------------------------------------------
-- Releases
-- --------------------------------------------------------------------------

CREATE TABLE governance.release (
    release_id          text        NOT NULL PRIMARY KEY,
    state               governance.release_state NOT NULL DEFAULT 'Candidate',
    manifest            jsonb       NOT NULL,
    -- Digest over the canonical manifest bytes. Requirement 8.9 requires a
    -- published release's manifest and counts to stay unchanged; storing the
    -- digest makes any drift detectable rather than merely discouraged.
    manifest_digest     text        NOT NULL,
    enrichment_cutoff   timestamptz NOT NULL,
    methodology_version text        NOT NULL,
    disclosure_policy_id      text  NOT NULL,
    disclosure_policy_version text  NOT NULL,
    generated_at        timestamptz NOT NULL,
    verified_at         timestamptz,

    CONSTRAINT release_policy_fkey
        FOREIGN KEY (disclosure_policy_id, disclosure_policy_version)
        REFERENCES governance.disclosure_policy (policy_id, version),

    CONSTRAINT release_verified_has_timestamp CHECK (
        state <> 'Verified' OR verified_at IS NOT NULL
    )
);

CREATE TABLE governance.release_artifact (
    release_id          text        NOT NULL REFERENCES governance.release (release_id),
    artifact_path       text        NOT NULL,
    artifact_digest     text        NOT NULL,
    byte_size           bigint      NOT NULL,

    CONSTRAINT release_artifact_pkey PRIMARY KEY (release_id, artifact_path),

    CONSTRAINT release_artifact_digest_non_empty
        CHECK (length(btrim(artifact_digest)) > 0),

    CONSTRAINT release_artifact_byte_size_non_negative CHECK (byte_size >= 0)
);

-- Requirement 8.9: once recorded, an artifact digest never changes.
CREATE RULE release_artifact_no_update AS
    ON UPDATE TO governance.release_artifact DO INSTEAD NOTHING;

-- Requirement 8.7 / Invariant 15: exactly one release is active at any
-- observable instant. A single-row table with a constant primary key makes
-- "two active releases" unrepresentable rather than merely avoided by
-- convention, and activation is a single-row update -- an atomic swap.
CREATE TABLE governance.active_release_pointer (
    pointer_id          boolean     NOT NULL PRIMARY KEY DEFAULT true,
    release_id          text        NOT NULL REFERENCES governance.release (release_id),
    activated_at        timestamptz NOT NULL DEFAULT now(),
    activated_by        text        NOT NULL,

    CONSTRAINT active_release_pointer_singleton CHECK (pointer_id = true)
);

COMMENT ON TABLE governance.active_release_pointer IS
    'Singleton pointer to the active verified release. The pointer_id check makes a second active release unrepresentable (Invariant 15).';

CREATE TABLE governance.release_gate_result (
    result_id           bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    release_id          text        NOT NULL REFERENCES governance.release (release_id),
    gate_name           text        NOT NULL,
    passed              boolean     NOT NULL,
    completed           boolean     NOT NULL,
    detail              jsonb       NOT NULL DEFAULT '{}'::jsonb,
    evaluated_at        timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX release_gate_result_release_idx
    ON governance.release_gate_result (release_id, gate_name);

-- --------------------------------------------------------------------------
-- Audit log
-- --------------------------------------------------------------------------

CREATE TABLE governance.audit_log (
    audit_id            bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    actor               text        NOT NULL,
    action              text        NOT NULL,
    resource_class      text        NOT NULL,
    outcome             text        NOT NULL,
    occurred_at         timestamptz NOT NULL DEFAULT now(),
    -- Non-sensitive structured context only. Requirement 15.11 forbids
    -- recording credentials or restricted data even on the denial path.
    detail              jsonb       NOT NULL DEFAULT '{}'::jsonb,

    CONSTRAINT audit_log_fields_non_empty CHECK (
        length(btrim(actor)) > 0
        AND length(btrim(action)) > 0
        AND length(btrim(resource_class)) > 0
        AND length(btrim(outcome)) > 0
    )
);

COMMENT ON TABLE governance.audit_log IS
    'Durable audit of restricted-data access and administrative operations (Requirement 15.20). Append-only: an operation whose audit record cannot be written must be denied or rolled back (Requirement 15.21).';

CREATE RULE audit_log_no_update AS
    ON UPDATE TO governance.audit_log DO INSTEAD NOTHING;

CREATE RULE audit_log_no_delete AS
    ON DELETE TO governance.audit_log DO INSTEAD NOTHING;

CREATE INDEX audit_log_actor_time_idx
    ON governance.audit_log (actor, occurred_at DESC);

CREATE INDEX audit_log_resource_time_idx
    ON governance.audit_log (resource_class, occurred_at DESC);

COMMIT;
