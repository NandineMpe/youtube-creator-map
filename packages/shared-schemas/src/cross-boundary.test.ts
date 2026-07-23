import { describe, expect, it } from "vitest";

import {
  activeReleasePointer,
  countryDetail,
  findProhibitedContent,
  overviewArtifact,
  releaseManifest,
} from "./index";

/**
 * The Python pipeline generates artifacts; the browser consumes them. These
 * tests pin the contract between the two using payloads shaped exactly as
 * the generator emits them, so a change on either side that breaks the
 * other fails here rather than at delivery time.
 *
 * The fixtures below are trimmed copies of real generator output.
 */

const DIGEST = `sha256:${"a".repeat(64)}`;

const overviewFixture = {
  schemaVersion: "1.0.0",
  releaseId: "2026-07-23T09-01-23Z",
  filter: { corpusClasses: ["Comparison"], datasets: ["youtube-commons"] },
  countries: [
    {
      country: "XX",
      creatorCount: 28998,
      representedVideoCount: 49363,
      sourceOccurrenceCount: 49967,
      resolvedVideoCount: 49363,
      unavailableVideoCount: 0,
    },
  ],
  coverage: {
    inputOccurrenceCount: 49967,
    distinctInputVideoCount: 49363,
    partition: {
      resolved: 49363,
      unavailableUnclassified: 0,
      retryableOrPending: 0,
      invalid: 0,
      terminalFailure: 0,
    },
    resolvedChannelCount: 28998,
    knownCountryChannelCount: 0,
    unknownCountryChannelCount: 28998,
  },
  creatorCount: 28998,
  representedVideoCount: 49363,
  representedCountryCount: 0,
};

const countryDetailFixture = {
  country: "XX",
  creatorCount: 28998,
  representedVideoCount: 49363,
  sourceOccurrenceCount: 49967,
  coverage: overviewFixture.coverage,
  datasetBreakdown: [
    { datasetId: "youtube-commons", representedVideoCount: 49363 },
  ],
  firstPage: {
    country: "XX",
    sortOrder: "representedVideoCountDesc",
    rows: [
      {
        publicChannelKey: "pk_9ef6e6c9a39b37556ff36cf522b8943a",
        displayName: "Roel Van de Paar",
        country: "XX",
        representedVideoCount: 985,
        datasetBreakdown: [
          { datasetId: "youtube-commons", representedVideoCount: 985 },
        ],
        lastObservedAt: "2026-07-23",
      },
    ],
    nextCursor: "eyJjIjozNSwiayI6InBrXzNk",
    pageSize: 50,
    totalRows: 1112,
  },
  pageIndex: {
    representedVideoCountDesc: [
      "releases/r1/countries/XX/representedVideoCountDesc/page-0.json",
    ],
  },
};

const manifestFixture = {
  schemaVersion: "1.0.0",
  releaseId: "2026-07-23T09-01-23Z",
  generatedAt: "2026-07-23T09:01:23Z",
  enrichmentCutoff: "2026-07-23T09:01:23Z",
  defaultFilter: overviewFixture.filter,
  datasets: [
    {
      datasetId: "youtube-commons",
      displayName: "YouTube-Commons (PleIAs)",
      version: "cctube_0-2024",
      corpusClass: "Comparison",
      sourceKind: "SubtitleIndex",
      occurrenceUnit: "Row",
      sourceCitation: "https://huggingface.co/datasets/PleIAs/YouTube-Commons",
      snapshotDigest: DIGEST,
    },
  ],
  artifactDigests: { "releases/r1/overview.json": DIGEST },
  methodologyVersion: "1.0.0",
  disclosurePolicyVersion: "0.1.0-dev",
  filters: [],
  boundaryMetadata: {
    datasetName: "Natural Earth Admin 0 - Countries",
    version: "5.1.1",
    license: "Public domain",
    attribution: "Made with Natural Earth",
    disputedTerritoryTreatment:
      "Boundaries are a presentation convention and are not evidence of channel location beyond declared country metadata.",
  },
};

const pointerFixture = {
  schemaVersion: "1.0.0",
  releaseId: "2026-07-23T09-01-23Z",
  manifestPath: "releases/2026-07-23T09-01-23Z/manifest.json",
  manifestDigest: DIGEST,
};

describe("generator output parses against the consumer schemas", () => {
  it("accepts a real overview payload", () => {
    expect(overviewArtifact.parse(overviewFixture)).toBeTruthy();
  });

  it("accepts a real country detail payload", () => {
    expect(countryDetail.parse(countryDetailFixture)).toBeTruthy();
  });

  it("accepts a real manifest payload", () => {
    expect(releaseManifest.parse(manifestFixture)).toBeTruthy();
  });

  it("accepts a real active pointer payload", () => {
    expect(activeReleasePointer.parse(pointerFixture)).toBeTruthy();
  });
});

describe("generator output carries no prohibited content", () => {
  it.each([
    ["overview", overviewFixture],
    ["country detail", countryDetailFixture],
    ["manifest", manifestFixture],
    ["pointer", pointerFixture],
  ])("%s is clean", (_label, payload) => {
    expect(findProhibitedContent(payload)).toEqual([]);
  });

  it("the boundary metadata prose is not mistaken for an identifier", () => {
    // This copy is required by Requirement 12.10 and must survive the scan.
    expect(
      findProhibitedContent({
        disputedTerritoryTreatment:
          manifestFixture.boundaryMetadata.disputedTerritoryTreatment,
      }),
    ).toEqual([]);
  });
});

describe("the consumer rejects what the generator must never emit", () => {
  it("rejects a country detail carrying a raw channel id", () => {
    const leaked = structuredClone(countryDetailFixture) as Record<
      string,
      unknown
    >;
    (
      (leaked.firstPage as Record<string, unknown>).rows as Record<
        string,
        unknown
      >[]
    )[0].channelId = "UC_x5XG1OV2P6uZZ5FSM9Ttw";

    expect(() => countryDetail.parse(leaked)).toThrow();
    expect(findProhibitedContent(leaked).length).toBeGreaterThan(0);
  });

  it("rejects an overview whose coverage does not reconcile", () => {
    const broken = structuredClone(overviewFixture);
    broken.coverage.partition.resolved = 1;
    expect(() => overviewArtifact.parse(broken)).toThrow(/partition sums to/);
  });

  it("rejects a manifest whose cutoff postdates generation", () => {
    const broken = structuredClone(manifestFixture);
    broken.enrichmentCutoff = "2026-07-24T00:00:00Z";
    expect(() => releaseManifest.parse(broken)).toThrow(/enrichmentCutoff/);
  });
});
