import { describe, expect, it } from "vitest";

import {
  ARTIFACT_SCHEMA_VERSION,
  UNKNOWN_COUNTRY,
  activeReleasePointer,
  countryBucket,
  countrySummary,
  coverageSummary,
  creatorRow,
  filter,
  overviewArtifact,
  publicChannelKey,
  releaseManifest,
} from "./artifacts";

const DIGEST = `sha256:${"a".repeat(64)}`;

function validCoverage() {
  return {
    inputOccurrenceCount: 120,
    distinctInputVideoCount: 100,
    partition: {
      resolved: 70,
      unavailableUnclassified: 15,
      retryableOrPending: 10,
      invalid: 3,
      terminalFailure: 2,
    },
    resolvedChannelCount: 40,
    knownCountryChannelCount: 30,
    unknownCountryChannelCount: 10,
  };
}

function validManifest() {
  return {
    schemaVersion: ARTIFACT_SCHEMA_VERSION,
    releaseId: "2026-01-15T12-00-00Z",
    generatedAt: "2026-01-15T12:00:00Z",
    enrichmentCutoff: "2026-01-15T11:00:00Z",
    defaultFilter: { datasets: ["panda-70m"], corpusClasses: ["Candidate"] },
    datasets: [
      {
        datasetId: "panda-70m",
        displayName: "Panda-70M",
        version: "2024.1",
        corpusClass: "Candidate",
        sourceKind: "MetadataOnly",
        occurrenceUnit: "Clip",
        sourceCitation: "https://example.invalid/panda70m",
        snapshotDigest: DIGEST,
      },
    ],
    artifactDigests: { "overview.json": DIGEST },
    methodologyVersion: "1.0.0",
    disclosurePolicyVersion: "1.0.0",
    filters: [],
    boundaryMetadata: {
      datasetName: "Natural Earth Admin 0",
      version: "5.1.1",
      license: "Public domain",
      attribution: "Made with Natural Earth",
      disputedTerritoryTreatment:
        "Presentation convention only; not evidence of location.",
    },
  };
}

describe("country bucket", () => {
  it("accepts ISO 3166 alpha-2 and the Unknown sentinel", () => {
    expect(countryBucket.parse("DE")).toBe("DE");
    expect(countryBucket.parse(UNKNOWN_COUNTRY)).toBe("XX");
  });

  it("rejects lowercase and malformed codes", () => {
    expect(() => countryBucket.parse("de")).toThrow();
    expect(() => countryBucket.parse("DEU")).toThrow();
    expect(() => countryBucket.parse("")).toThrow();
  });
});

describe("public channel key", () => {
  it("accepts a disclosure-approved key", () => {
    expect(publicChannelKey.parse("pk_a1b2c3d4e5")).toBe("pk_a1b2c3d4e5");
  });

  it("rejects a raw YouTube channel ID", () => {
    // Requirement 7.2: the public key must be distinct from the source ID.
    expect(() => publicChannelKey.parse("UC_x5XG1OV2P6uZZ5FSM9Ttw")).toThrow();
  });

  it("rejects a raw channel ID smuggled behind the pk_ prefix", () => {
    expect(() =>
      publicChannelKey.parse("pk_UC_x5XG1OV2P6uZZ5FSM9Tt"),
    ).toThrow();
  });
});

describe("filter", () => {
  it("requires sorted, unique members", () => {
    // Requirement 11.2: equivalent states must have one canonical form.
    expect(() =>
      filter.parse({ datasets: ["b", "a"], corpusClasses: ["Candidate"] }),
    ).toThrow(/sorted/);
    expect(() =>
      filter.parse({ datasets: ["a", "a"], corpusClasses: ["Candidate"] }),
    ).toThrow(/unique/);
  });

  it("rejects an empty selection", () => {
    expect(() =>
      filter.parse({ datasets: [], corpusClasses: ["Candidate"] }),
    ).toThrow();
  });
});

describe("coverage summary", () => {
  it("accepts a reconciling summary", () => {
    expect(coverageSummary.parse(validCoverage())).toBeTruthy();
  });

  it("rejects a partition that does not sum to distinct input videos", () => {
    // Requirement 6.4.
    const broken = validCoverage();
    broken.partition.resolved = 69;
    expect(() => coverageSummary.parse(broken)).toThrow(/partition sums to/);
  });

  it("rejects channel counts that do not reconcile", () => {
    // Requirement 6.5.
    const broken = validCoverage();
    broken.knownCountryChannelCount = 25;
    expect(() => coverageSummary.parse(broken)).toThrow(/known \+ unknown/);
  });

  it("rejects unknown fields", () => {
    expect(() =>
      coverageSummary.parse({
        ...validCoverage(),
        sourceLocator: "shard-0:row-1",
      }),
    ).toThrow();
  });
});

describe("creator row", () => {
  const row = {
    publicChannelKey: "pk_a1b2c3d4e5",
    displayName: "Example Channel",
    country: "DE",
    representedVideoCount: 12,
    datasetBreakdown: [{ datasetId: "panda-70m", representedVideoCount: 12 }],
    lastObservedAt: "2026-01-15",
  };

  it("accepts an approved row", () => {
    expect(creatorRow.parse(row)).toBeTruthy();
  });

  it("rejects a row carrying raw video identifiers", () => {
    // Requirement 7.5: strict() makes this structurally impossible.
    expect(() =>
      creatorRow.parse({ ...row, videoIds: ["dQw4w9WgXcQ"] }),
    ).toThrow();
  });

  it("rejects a row carrying a contact field", () => {
    expect(() => creatorRow.parse({ ...row, email: "a@b.invalid" })).toThrow();
  });

  it("rejects a row carrying a raw channel id", () => {
    expect(() =>
      creatorRow.parse({ ...row, channelId: "UC_x5XG1OV2P6uZZ5FSM9Ttw" }),
    ).toThrow();
  });
});

describe("release manifest", () => {
  it("accepts a complete manifest", () => {
    expect(releaseManifest.parse(validManifest())).toBeTruthy();
  });

  it("rejects a cutoff later than generation", () => {
    const broken = {
      ...validManifest(),
      enrichmentCutoff: "2026-01-15T13:00:00Z",
    };
    expect(() => releaseManifest.parse(broken)).toThrow(/enrichmentCutoff/);
  });

  it("rejects a malformed digest", () => {
    const broken = validManifest();
    broken.artifactDigests = { "overview.json": "not-a-digest" };
    expect(() => releaseManifest.parse(broken)).toThrow();
  });

  it("requires at least one dataset citation", () => {
    // Requirement 1.9: every included dataset must be citable.
    const broken = { ...validManifest(), datasets: [] };
    expect(() => releaseManifest.parse(broken)).toThrow();
  });

  it("requires boundary metadata", () => {
    // Requirement 12.10.
    const withoutBoundary: Record<string, unknown> = validManifest();
    delete withoutBoundary.boundaryMetadata;
    expect(() => releaseManifest.parse(withoutBoundary)).toThrow();
  });
});

describe("active release pointer", () => {
  it("stays minimal and separately refreshable", () => {
    const pointer = {
      schemaVersion: ARTIFACT_SCHEMA_VERSION,
      releaseId: "r1",
      manifestPath: "releases/r1/manifest.json",
      manifestDigest: DIGEST,
    };
    expect(activeReleasePointer.parse(pointer)).toBeTruthy();
    expect(() =>
      activeReleasePointer.parse({ ...pointer, countries: [] }),
    ).toThrow();
  });
});

describe("overview artifact", () => {
  const overview = {
    schemaVersion: ARTIFACT_SCHEMA_VERSION,
    releaseId: "r1",
    filter: { datasets: ["panda-70m"], corpusClasses: ["Candidate"] },
    countries: [
      {
        country: "DE",
        creatorCount: 3,
        representedVideoCount: 9,
        sourceOccurrenceCount: 14,
        resolvedVideoCount: 9,
        unavailableVideoCount: 0,
      },
    ],
    coverage: validCoverage(),
    creatorCount: 3,
    representedVideoCount: 9,
    representedCountryCount: 1,
  };

  it("accepts a valid overview", () => {
    expect(overviewArtifact.parse(overview)).toBeTruthy();
  });

  it("rejects duplicate country buckets", () => {
    // Requirement 9.3: map and table must not disagree.
    const broken = {
      ...overview,
      countries: [overview.countries[0], overview.countries[0]],
    };
    expect(() => overviewArtifact.parse(broken)).toThrow(/duplicate country/);
  });

  it("accepts the Unknown bucket alongside real countries", () => {
    // Requirement 6.8.
    const withUnknown = {
      ...overview,
      countries: [
        overview.countries[0],
        { ...overview.countries[0], country: UNKNOWN_COUNTRY },
      ],
    };
    expect(overviewArtifact.parse(withUnknown)).toBeTruthy();
  });
});

describe("country summary", () => {
  it("rejects negative and fractional counts", () => {
    const base = {
      country: "DE",
      creatorCount: 1,
      representedVideoCount: 1,
      sourceOccurrenceCount: 1,
      resolvedVideoCount: 1,
      unavailableVideoCount: 0,
    };
    expect(() => countrySummary.parse({ ...base, creatorCount: -1 })).toThrow();
    expect(() =>
      countrySummary.parse({ ...base, creatorCount: 1.5 }),
    ).toThrow();
  });
});
