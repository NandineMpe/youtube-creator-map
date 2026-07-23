-- Curator sign-off as a durable record rather than a command-line flag.
--
-- Requirement 8.2 makes dataset-citation and terms review a release gate,
-- and task 8.3 requires the sign-off to be an authenticated versioned
-- record rather than a manual step. A `--signoff nandi` flag satisfies
-- neither: it records a string the invoker chose, proves nothing about
-- who ran it, and leaves nothing behind to audit afterwards.
--
-- What this table adds is durability and scope. A sign-off names the
-- exact release and the exact manifest digest it approved. If the
-- artifacts change, the digest changes and the sign-off no longer
-- applies — which is the property that makes it meaningful, since
-- otherwise a curator's approval of one set of numbers would silently
-- carry over to a different set.
--
-- Authentication itself belongs to the surrounding deployment (workload
-- identity, database roles). This table records *what was approved and
-- by whom*; it does not attempt to prove the actor's identity on its
-- own, and pretending it did would be worse than saying so here.

BEGIN;

CREATE TABLE governance.curator_signoff (
    signoff_id          bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    release_id          text        NOT NULL,

    -- Scopes the approval to specific bytes. A release rebuilt with
    -- different numbers produces a different digest, so an old sign-off
    -- cannot be reused to activate it.
    manifest_digest     text        NOT NULL,

    -- The curator, as authenticated by the surrounding deployment.
    actor               text        NOT NULL,

    -- What was reviewed. Requirement 8.2 names dataset citations and
    -- terms review; keeping them separate means a partial review cannot
    -- be recorded as a whole one.
    citations_reviewed  boolean     NOT NULL,
    terms_reviewed      boolean     NOT NULL,

    -- Free-text context from the reviewer. Non-sensitive by convention;
    -- this is a governance record, not a place for restricted data.
    note                text        NOT NULL DEFAULT '',

    -- The disclosure policy version in force when the review happened.
    -- A policy change after sign-off means the approval covered
    -- different publication rules than the ones being applied.
    policy_version      text        NOT NULL,

    signed_at           timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT curator_signoff_fields_non_empty CHECK (
        length(btrim(release_id)) > 0
        AND length(btrim(actor)) > 0
        AND length(btrim(policy_version)) > 0
    ),

    CONSTRAINT curator_signoff_digest_shape CHECK (
        manifest_digest ~ '^sha256:[a-f0-9]{64}$'
    ),

    -- One sign-off per curator per exact artifact set. Re-approving the
    -- same bytes is a no-op, not a second record, which keeps a repeated
    -- command from looking like independent review.
    CONSTRAINT curator_signoff_unique
        UNIQUE (release_id, manifest_digest, actor)
);

COMMENT ON TABLE governance.curator_signoff IS
    'Durable curator approval of dataset citations and terms review, scoped to one release manifest digest (Requirement 8.2, task 8.3).';

COMMENT ON COLUMN governance.curator_signoff.manifest_digest IS
    'Binds the approval to exact bytes. Rebuilt artifacts invalidate it.';

-- Append-only. A sign-off that could be edited afterwards is not a
-- record of what was approved, it is a record of what someone last
-- wanted it to say. Triggers rather than rules: a rule silently
-- discards the mutation, a trigger raises so the caller learns.
CREATE OR REPLACE FUNCTION governance.curator_signoff_immutable()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION
        'governance.curator_signoff is append-only; % is not permitted',
        TG_OP
        USING ERRCODE = 'restrict_violation';
END;
$$;

CREATE TRIGGER curator_signoff_no_update
    BEFORE UPDATE ON governance.curator_signoff
    FOR EACH ROW EXECUTE FUNCTION governance.curator_signoff_immutable();

CREATE TRIGGER curator_signoff_no_delete
    BEFORE DELETE ON governance.curator_signoff
    FOR EACH ROW EXECUTE FUNCTION governance.curator_signoff_immutable();

CREATE INDEX curator_signoff_release_idx
    ON governance.curator_signoff (release_id, signed_at DESC);

COMMIT;
