import { z } from "zod";

export const schemaVersion = z.literal("1.0.0");
export type SchemaVersion = z.infer<typeof schemaVersion>;

export {
  ARTIFACT_SCHEMA_VERSION,
  UNKNOWN_COUNTRY,
  activeReleasePointer,
  boundaryMetadata,
  corpusClass,
  countryBucket,
  countryDetail,
  countrySummary,
  coverageSummary,
  creatorPage,
  creatorRow,
  datasetBreakdownEntry,
  datasetCitation,
  filter,
  filterIndexEntry,
  occurrenceUnit,
  overviewArtifact,
  publicChannelKey,
  releaseManifest,
  sourceKind,
  videoResolutionPartition,
} from "./artifacts";

export type {
  ActiveReleasePointer,
  CountryDetail,
  CountrySummary,
  CoverageSummary,
  CreatorPage,
  CreatorRow,
  DatasetCitation,
  Filter,
  FilterIndexEntry,
  OverviewArtifact,
  ReleaseManifest,
  VideoResolutionPartition,
} from "./artifacts";

export {
  DisclosureViolationError,
  assertNoProhibitedContent,
  findProhibitedContent,
} from "./disclosure-guard";

export type { DisclosureFinding } from "./disclosure-guard";
