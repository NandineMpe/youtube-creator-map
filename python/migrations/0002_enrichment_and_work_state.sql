-- Migration 0002: Append-only resolution observations, work items, leases,
-- checkpoints, and the quota ledger.
--
-- Requirement refs:
--   3.1, 3.2   At most one work item per distinct entity and policy version.
--   3.9        Observations are appended, never updated or deleted.
--   3.10-3.12  Cutoff exclusion and deterministic selection.
--   4.1-4.7    Unique work identity, leases, atomic checkpoints, replay safety.
--   4.14-4.16  Quota reserve accounting.

BEGIN;

CREATE SCHEMA IF NOT EXISTS enrichment;

CREATE TYPE enrichment.entity_kind AS ENUM ('Video', 'Channel');

CREATE TYPE enrichment.work_item_state AS ENUM (
    'Pending', 'Leased', 'Succeeded', 'RetryableFailure', 'TerminalFailure'
);

CREATE TYPE enrichment.video_resolution_status AS ENUM (
    'Resolved', 'UnavailableUnclassified', 'Invalid'
);

CREATE TYPE enrichment.channel_resolution_status AS ENUM (
    'Resolved', 'UnavailableUnclassified'
);

CREATE TYPE enrichment.job_state AS ENUM ('Running', 'OperatorHalt', 'Completed');

-- --------------------------------------------------------------------------
-- Resolution observations (append-only)
-- --------------------------------------------------------------------------

CREATE TABLE enrichment.video_observation (
    observation_id      bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    video_id            text        NOT NULL,
    status              enrichment.video_resolution_status NOT NULL,
    channel_id          text,
    observed_at         timestamptz NOT NULL,
    response_digest     text,
    policy_version      text        NOT NULL,

    -- Requirement 3.5/3.6: a resolved video carries a channel; an omitted or
    -- invalid one carries none, so no attribution can be inferred from it.
    CONSTRAINT video_observation_channel_matches_status CHECK (
        (status = 'Resolved' AND channel_id IS NOT NULL
            AND length(btrim(channel_id)) > 0)
        OR (status <> 'Resolved' AND channel_id IS NULL)
    )
);

COMMENT ON TABLE enrichment.video_observation IS
    'Append-only video enrichment observations (Requirement 3.9). A release selects one per identity by pinned policy and cutoff.';

CREATE RULE video_observation_no_update AS
    ON UPDATE TO enrichment.video_observation DO INSTEAD NOTHING;

CREATE RULE video_observation_no_delete AS
    ON DELETE TO enrichment.video_observation DO INSTEAD NOTHING;

-- Selection reads the newest eligible observation per identity at a cutoff.
-- Requirement 3.11 breaks ties on response_digest, so the index carries it as
-- the final ordering column and selection never depends on physical row order.
CREATE INDEX video_observation_selection_idx
    ON enrichment.video_observation (
        video_id, policy_version, observed_at DESC, response_digest DESC
    );

CREATE TABLE enrichment.channel_observation (
    observation_id      bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    channel_id          text        NOT NULL,
    status              enrichment.channel_resolution_status NOT NULL,
    display_name        text,
    -- Requirement 3.8 / Invariant 6: country comes only from the channel
    -- metadata field. NULL means Unknown; it is never backfilled by
    -- inference from language, locale, or any other signal.
    declared_country    char(2),
    observed_at         timestamptz NOT NULL,
    response_digest     text,
    policy_version      text        NOT NULL,

    CONSTRAINT channel_observation_country_shape CHECK (
        declared_country IS NULL OR declared_country ~ '^[A-Z]{2}$'
    ),

    -- An unavailable channel carries no display metadata to publish.
    CONSTRAINT channel_observation_unavailable_has_no_metadata CHECK (
        status <> 'UnavailableUnclassified'
        OR (display_name IS NULL AND declared_country IS NULL)
    )
);

CREATE RULE channel_observation_no_update AS
    ON UPDATE TO enrichment.channel_observation DO INSTEAD NOTHING;

CREATE RULE channel_observation_no_delete AS
    ON DELETE TO enrichment.channel_observation DO INSTEAD NOTHING;

CREATE INDEX channel_observation_selection_idx
    ON enrichment.channel_observation (
        channel_id, policy_version, observed_at DESC, response_digest DESC
    );

-- --------------------------------------------------------------------------
-- Jobs, work items, and leases
-- --------------------------------------------------------------------------

CREATE TABLE enrichment.job (
    job_id              text        NOT NULL PRIMARY KEY,
    entity_kind         enrichment.entity_kind NOT NULL,
    policy_version      text        NOT NULL,
    state               enrichment.job_state NOT NULL DEFAULT 'Running',
    halt_reason_class   text,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),

    -- Requirement 4.11: a halted job records why, in non-sensitive class form.
    CONSTRAINT job_halt_reason_present CHECK (
        state <> 'OperatorHalt' OR halt_reason_class IS NOT NULL
    )
);

CREATE TABLE enrichment.work_item (
    work_item_id        bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    job_id              text        NOT NULL REFERENCES enrichment.job (job_id),
    entity_kind         enrichment.entity_kind NOT NULL,
    entity_id           text        NOT NULL,
    policy_version      text        NOT NULL,
    state               enrichment.work_item_state NOT NULL DEFAULT 'Pending',
    attempts            integer     NOT NULL DEFAULT 0,
    next_attempt_at     timestamptz NOT NULL DEFAULT now(),
    lease_expires_at    timestamptz,
    lease_owner         text,
    last_error_class    text,
    updated_at          timestamptz NOT NULL DEFAULT now(),

    -- Requirements 3.1, 3.2, 4.1: exactly one work item per distinct entity
    -- and policy version. This unique identity is what makes claiming
    -- idempotent and prevents duplicate metadata requests for one identity.
    CONSTRAINT work_item_identity_unique
        UNIQUE (entity_kind, entity_id, policy_version),

    CONSTRAINT work_item_attempts_non_negative CHECK (attempts >= 0),

    -- Requirement 4.3: a leased item names its holder and expiry, so an
    -- expired lease is detectable without a background sweeper.
    CONSTRAINT work_item_lease_fields CHECK (
        (state = 'Leased'
            AND lease_expires_at IS NOT NULL AND lease_owner IS NOT NULL)
        OR (state <> 'Leased')
    ),

    -- Requirements 4.9, 4.10: terminal failures record the final error class.
    CONSTRAINT work_item_terminal_has_error CHECK (
        state <> 'TerminalFailure' OR last_error_class IS NOT NULL
    )
);

COMMENT ON CONSTRAINT work_item_identity_unique ON enrichment.work_item IS
    'Requirement 4.1: work identity is (entity_kind, entity_id, policy_version). Claims are idempotent against this key.';

-- Requirement 4.4: eligibility is computed from lease expiry rather than
-- stored state, so an item whose worker died is reclaimable even though its
-- state column still reads Leased. This partial index serves the claim query.
CREATE INDEX work_item_claimable_idx
    ON enrichment.work_item (entity_kind, policy_version, next_attempt_at)
    WHERE state IN ('Pending', 'RetryableFailure', 'Leased');

CREATE INDEX work_item_job_state_idx
    ON enrichment.work_item (job_id, state);

-- --------------------------------------------------------------------------
-- Checkpoints
-- --------------------------------------------------------------------------

CREATE TABLE enrichment.checkpoint (
    checkpoint_id       bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    job_id              text        NOT NULL REFERENCES enrichment.job (job_id),
    -- Requirement 4.7: replaying an already committed checkpoint must not
    -- duplicate effects. The caller supplies a deterministic batch key; the
    -- unique constraint turns a replay into a no-op conflict rather than a
    -- second application of the same outcomes.
    batch_key           text        NOT NULL,
    items_committed     integer     NOT NULL,
    quota_units_used    integer     NOT NULL,
    committed_at        timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT checkpoint_batch_unique UNIQUE (job_id, batch_key),

    CONSTRAINT checkpoint_counts_non_negative CHECK (
        items_committed >= 0 AND quota_units_used >= 0
    )
);

CREATE RULE checkpoint_no_update AS
    ON UPDATE TO enrichment.checkpoint DO INSTEAD NOTHING;

-- --------------------------------------------------------------------------
-- Quota ledger
-- --------------------------------------------------------------------------

CREATE TABLE enrichment.quota_ledger (
    ledger_date         date        NOT NULL,
    operation           text        NOT NULL,
    requests            bigint      NOT NULL DEFAULT 0,
    estimated_units     bigint      NOT NULL DEFAULT 0,
    updated_at          timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT quota_ledger_pkey PRIMARY KEY (ledger_date, operation),

    CONSTRAINT quota_ledger_non_negative CHECK (
        requests >= 0 AND estimated_units >= 0
    )
);

COMMENT ON TABLE enrichment.quota_ledger IS
    'Daily API usage. Requirement 4.14 stops claims with positive projected cost once remaining quota reaches the reserve.';

COMMIT;
