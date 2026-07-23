-- Widen the curator sign-off uniqueness key to include what was reviewed.
--
-- The original key was (release_id, manifest_digest, actor), which made a
-- curator's *first* answer permanent. A reviewer who checked citations,
-- came back, and then confirmed terms would have the second record
-- silently discarded by ON CONFLICT DO NOTHING — leaving an incomplete
-- sign-off that could never be completed, and no error to explain why.
-- Reviewing in two sittings is ordinary, so the schema should allow it.
--
-- Including the review flags in the key keeps the original intent (an
-- identical repeated command is still a no-op, so re-running a script
-- cannot manufacture the appearance of independent review) while letting
-- a genuinely different answer be recorded. The table stays append-only,
-- so the earlier partial record remains as evidence of what was known
-- when: `approving_actor` looks for a complete record and ignores the
-- superseded partial one.

BEGIN;

ALTER TABLE governance.curator_signoff
    DROP CONSTRAINT curator_signoff_unique;

ALTER TABLE governance.curator_signoff
    ADD CONSTRAINT curator_signoff_unique
    UNIQUE (release_id, manifest_digest, actor, citations_reviewed, terms_reviewed);

COMMENT ON CONSTRAINT curator_signoff_unique ON governance.curator_signoff IS
    'An identical repeated sign-off is a no-op; a changed review scope is a new record. Reviewing citations and terms in separate sittings must not be blocked by the first partial record.';

COMMIT;
