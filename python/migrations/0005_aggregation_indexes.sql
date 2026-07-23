-- Migration 0005: Indexes supporting aggregation queries.
--
-- Aggregation joins video observations to channel observations and back to
-- occurrences. The selection indexes from 0002 serve the "one observation
-- per identity" lookup, but not the reverse join from a channel to the
-- videos attributing to it, which a country summary performs for every
-- bucket.
--
-- Measured need: the creator aggregate over ~29,000 channels and ~50,000
-- occurrences exceeded the statement timeout before these existed.
--
-- Requirement refs: 5.1-5.13, 14.2

BEGIN;

-- Attribution join: given a resolved video observation, find its channel.
-- Partial, because only resolved rows carry a channel and only those are
-- ever joined.
CREATE INDEX IF NOT EXISTS video_observation_channel_idx
    ON enrichment.video_observation (channel_id, policy_version)
    WHERE channel_id IS NOT NULL;

-- Country bucketing scans resolved channels and groups by declared country.
CREATE INDEX IF NOT EXISTS channel_observation_country_idx
    ON enrichment.channel_observation (policy_version, declared_country);

-- The occurrence -> video join in every filtered aggregate.
CREATE INDEX IF NOT EXISTS normalized_occurrence_video_dataset_idx
    ON provenance.normalized_occurrence (video_id, dataset_id);

-- Work-item lookup by entity during partition classification, which reads
-- by entity rather than by the claim path's (kind, policy, due) ordering.
CREATE INDEX IF NOT EXISTS work_item_entity_lookup_idx
    ON enrichment.work_item (entity_id, entity_kind, policy_version);

COMMIT;
