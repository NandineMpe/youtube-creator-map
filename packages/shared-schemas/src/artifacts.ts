/**
 * Versioned public artifact schemas.
 *
 * These describe everything that crosses the publication boundary. The
 * schemas are `.strict()` throughout: an artifact carrying a field the
 * schema does not name fails validation rather than being silently passed
 * through to the browser. That is what turns Requirement 7.5 (no raw video
 * identifier, source locator, contact field, or restricted provenance join
 * in a public artifact) into something the loader enforces rather than
 * something the generator is trusted to have honoured.
 *
 * Requirement refs: 7.2-7.7, 8.1, 8.8, 9.1-9.5, 10.2-10.7, 14.4, 14.5, 15.3
 */

import { z } from "zod";

/** Current public artifact schema version. */
export const ARTIFACT_SCHEMA_VERSION = "1.0.0" as const;

/**
 * The Unknown-country bucket.
 *
 * Requirement 6.8 places it outside the geographic choropleth, so it needs a
 * representation that cannot collide with a real ISO 3166 alpha-2 code.
 * "XX" is user-assigned in ISO 3166 and will never be issued to a country.
 */
export const UNKNOWN_COUNTRY = "XX" as const;

const iso3166Alpha2 = z
  .string()
  .regex(/^[A-Z]{2}$/, "country must be ISO 3166 alpha-2 uppercase");

/** A country bucket: a real country code, or the Unknown bucket. */
export const countryBucket = z.union([
  iso3166Alpha2,
  z.literal(UNKNOWN_COUNTRY),
]);

/** A non-negative count. Public counts are cardinalities, never estimates. */
const natural = z.number().int().nonnegative();

/** A digest recorded for artifact verification. */
const digest = z
  .string()
  .regex(/^sha256:[a-f0-9]{64}$/, "digest must be sha256:<64 lowercase hex>");

const isoInstant = z.string().datetime({ offset: true });
const isoDate = z.string().regex(/^\d{4}-\d{2}-\d{2}$/, "must be YYYY-MM-DD");

/**
 * A disclosure-approved public channel key.
 *
 * Requirement 7.2 requires this to be distinct from the raw source channel
 * identifier. YouTube channel IDs are 24 characters starting with "UC", so
 * the pattern here deliberately excludes that shape: a leaked raw ID fails
 * validation instead of being published.
 */
export const publicChannelKey = z
  .string()
  .regex(/^pk_[A-Za-z0-9_-]{8,64}$/, "public channel key must be pk_-prefixed")
  // A raw channel ID begins "UC"; any key carrying that prefix is treated as
  // a leaked source identifier regardless of the remaining length, rather
  // than only the exact 24-character form.
  .refine((value) => !/^pk_UC/i.test(value), {
    message: "public channel key must not embed a raw YouTube channel ID",
  });

export const corpusClass = z.enum(["Candidate", "Comparison"]);

export const occurrenceUnit = z.enum([
  "Clip",
  "Timestamp",
  "Segment",
  "Row",
  "Video",
]);

export const sourceKind = z.enum([
  "MetadataOnly",
  "MediaIndex",
  "SubtitleIndex",
]);

/** The five mutually exclusive video resolution states (Requirement 6.2). */
export const videoResolutionPartition = z
  .object({
    resolved: natural,
    unavailableUnclassified: natural,
    retryableOrPending: natural,
    invalid: natural,
    terminalFailure: natural,
  })
  .strict();

export type VideoResolutionPartition = z.infer<typeof videoResolutionPartition>;

/** Active filter: which datasets and corpus classes a view is derived from. */
export const filter = z
  .object({
    datasets: z.array(z.string().min(1)).min(1),
    corpusClasses: z.array(corpusClass).min(1),
  })
  .strict()
  .superRefine((value, ctx) => {
    // Requirement 11.2: states differing only in ordering must serialize to
    // one canonical URL, so the artifact's own ordering must be canonical.
    for (const [field, values] of [
      ["datasets", value.datasets],
      ["corpusClasses", value.corpusClasses],
    ] as const) {
      const sorted = [...values].every(
        (item, index) => index === 0 || values[index - 1] <= item,
      );
      if (!sorted) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: [field],
          message: `${field} must be sorted`,
        });
      }
      if (new Set(values).size !== values.length) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: [field],
          message: `${field} must be unique`,
        });
      }
    }
  });

export type Filter = z.infer<typeof filter>;

/**
 * Coverage counts published beside every headline total.
 *
 * Requirement 6.4 requires the partition to sum to the distinct input video
 * count, and 6.5 requires known + unknown channels to equal resolved
 * channels. Both are checked here so a miscomputed artifact is rejected at
 * load rather than rendered as though it reconciled.
 */
export const coverageSummary = z
  .object({
    inputOccurrenceCount: natural,
    distinctInputVideoCount: natural,
    partition: videoResolutionPartition,
    resolvedChannelCount: natural,
    knownCountryChannelCount: natural,
    unknownCountryChannelCount: natural,
  })
  .strict()
  .superRefine((value, ctx) => {
    const partitionTotal =
      value.partition.resolved +
      value.partition.unavailableUnclassified +
      value.partition.retryableOrPending +
      value.partition.invalid +
      value.partition.terminalFailure;
    if (partitionTotal !== value.distinctInputVideoCount) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["partition"],
        message: `resolution partition sums to ${partitionTotal}, expected ${value.distinctInputVideoCount}`,
      });
    }
    const channelTotal =
      value.knownCountryChannelCount + value.unknownCountryChannelCount;
    if (channelTotal !== value.resolvedChannelCount) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["resolvedChannelCount"],
        message: `known + unknown channels = ${channelTotal}, expected ${value.resolvedChannelCount}`,
      });
    }
  });

export type CoverageSummary = z.infer<typeof coverageSummary>;

/** Per-country aggregate powering both the choropleth and the country table. */
export const countrySummary = z
  .object({
    country: countryBucket,
    creatorCount: natural,
    representedVideoCount: natural,
    sourceOccurrenceCount: natural,
    resolvedVideoCount: natural,
    unavailableVideoCount: natural,
  })
  .strict();

export type CountrySummary = z.infer<typeof countrySummary>;

/** One dataset's contribution to a creator or country total. */
export const datasetBreakdownEntry = z
  .object({ datasetId: z.string().min(1), representedVideoCount: natural })
  .strict();

/**
 * A disclosure-approved creator row.
 *
 * Only the fields Requirement 7.2 permits appear here. There is deliberately
 * no field for raw channel ID, video IDs, source locators, contact details,
 * or subscriber metrics; `.strict()` means an artifact carrying one is
 * rejected rather than partially rendered.
 */
export const creatorRow = z
  .object({
    publicChannelKey,
    displayName: z.string().min(1),
    country: countryBucket,
    representedVideoCount: natural,
    datasetBreakdown: z.array(datasetBreakdownEntry),
    lastObservedAt: isoDate,
  })
  .strict();

export type CreatorRow = z.infer<typeof creatorRow>;

/** One cursor-addressed page of creator rows (Requirement 10.5). */
export const creatorPage = z
  .object({
    country: countryBucket,
    sortOrder: z.enum(["representedVideoCountDesc", "displayNameAsc"]),
    rows: z.array(creatorRow),
    nextCursor: z.string().min(1).nullable(),
    pageSize: z.number().int().positive(),
    totalRows: natural,
  })
  .strict();

export type CreatorPage = z.infer<typeof creatorPage>;

/** Country drill-down detail (Requirement 10.3). */
export const countryDetail = z
  .object({
    country: countryBucket,
    creatorCount: natural,
    representedVideoCount: natural,
    sourceOccurrenceCount: natural,
    coverage: coverageSummary,
    datasetBreakdown: z.array(datasetBreakdownEntry),
    firstPage: creatorPage,
  })
  .strict();

export type CountryDetail = z.infer<typeof countryDetail>;

/** A dataset cited in the methodology (Requirement 1.9). */
export const datasetCitation = z
  .object({
    datasetId: z.string().min(1),
    displayName: z.string().min(1),
    version: z.string().min(1),
    corpusClass,
    sourceKind,
    occurrenceUnit,
    sourceCitation: z.string().url(),
    snapshotDigest: digest,
  })
  .strict();

export type DatasetCitation = z.infer<typeof datasetCitation>;

/** Country-boundary provenance (Requirement 12.10). */
export const boundaryMetadata = z
  .object({
    datasetName: z.string().min(1),
    version: z.string().min(1),
    license: z.string().min(1),
    attribution: z.string().min(1),
    disputedTerritoryTreatment: z.string().min(1),
  })
  .strict();

/**
 * One supported filter combination and where its aggregates live.
 *
 * Requirement 9.6 requires a filter change to update every surface with
 * exact counts, and Requirement 5.12 forbids additive approximations.
 * Dataset overlap means the browser cannot derive a filtered total from
 * the default one, so each supported combination is published separately
 * and indexed here.
 */
export const filterIndexEntry = z
  .object({
    key: z.string().min(1),
    label: z.string().min(1),
    path: z.string().min(1),
    datasets: z.array(z.string().min(1)).min(1),
    corpusClasses: z.array(corpusClass).min(1),
    isDefault: z.boolean(),
  })
  .strict();

export type FilterIndexEntry = z.infer<typeof filterIndexEntry>;

/** The public release manifest (Requirement 8.1). */
export const releaseManifest = z
  .object({
    schemaVersion: z.literal(ARTIFACT_SCHEMA_VERSION),
    releaseId: z.string().min(1),
    generatedAt: isoInstant,
    enrichmentCutoff: isoInstant,
    defaultFilter: filter,
    datasets: z.array(datasetCitation).min(1),
    artifactDigests: z.record(z.string().min(1), digest),
    methodologyVersion: z.string().min(1),
    disclosurePolicyVersion: z.string().min(1),
    boundaryMetadata,
    // Required, and may be empty. The generator always emits it, and
    // making it optional would push an undefined check into every
    // consumer for a case the producer never creates.
    filters: z.array(filterIndexEntry),
  })
  .strict()
  .superRefine((value, ctx) => {
    // Requirement 3.10: a release selects observations at or before its
    // cutoff, so a cutoff after generation would describe an impossible run.
    if (Date.parse(value.enrichmentCutoff) > Date.parse(value.generatedAt)) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["enrichmentCutoff"],
        message: "enrichmentCutoff must not be later than generatedAt",
      });
    }
  });

export type ReleaseManifest = z.infer<typeof releaseManifest>;

/**
 * The separately refreshable active release pointer (Requirement 14.10).
 *
 * Kept minimal and distinct from the manifest so it can carry a short cache
 * lifetime while every artifact it references stays immutably cacheable.
 */
export const activeReleasePointer = z
  .object({
    schemaVersion: z.literal(ARTIFACT_SCHEMA_VERSION),
    releaseId: z.string().min(1),
    manifestPath: z.string().min(1),
    manifestDigest: digest,
  })
  .strict();

export type ActiveReleasePointer = z.infer<typeof activeReleasePointer>;

/** The default overview payload (Requirement 14.1 budgets this artifact). */
export const overviewArtifact = z
  .object({
    schemaVersion: z.literal(ARTIFACT_SCHEMA_VERSION),
    releaseId: z.string().min(1),
    filter,
    countries: z.array(countrySummary),
    coverage: coverageSummary,
    creatorCount: natural,
    representedVideoCount: natural,
    representedCountryCount: natural,
  })
  .strict()
  .superRefine((value, ctx) => {
    // Requirement 9.2/9.3: the choropleth and country table derive from these
    // same records, so a duplicated bucket would make the two disagree.
    const seen = new Set<string>();
    for (const summary of value.countries) {
      if (seen.has(summary.country)) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ["countries"],
          message: `duplicate country bucket: ${summary.country}`,
        });
      }
      seen.add(summary.country);
    }
  });

export type OverviewArtifact = z.infer<typeof overviewArtifact>;
