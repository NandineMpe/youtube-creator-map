import { createHash } from "node:crypto";
import { webcrypto } from "node:crypto";

import { beforeAll, describe, expect, it, vi } from "vitest";

import {
  ArtifactLoadError,
  describeFailure,
  digestBytes,
  loadActiveRelease,
  loadCountryDetail,
  loadVerified,
} from "./loader";
import { overviewArtifact } from "@creator-map/shared-schemas";

beforeAll(() => {
  // Node exposes SubtleCrypto under webcrypto; the browser has it globally.
  if (!globalThis.crypto?.subtle) {
    Object.defineProperty(globalThis, "crypto", { value: webcrypto });
  }
});

const DIGEST = `sha256:${"a".repeat(64)}`;

function digestOf(text: string): string {
  return `sha256:${createHash("sha256").update(text).digest("hex")}`;
}

function bytes(text: string): ArrayBuffer {
  const encoded = new TextEncoder().encode(text);
  return encoded.buffer.slice(
    encoded.byteOffset,
    encoded.byteOffset + encoded.byteLength,
  );
}

interface FakeResponse {
  ok: boolean;
  status: number;
  arrayBuffer: () => Promise<ArrayBuffer>;
}

function respond(status: number, body: string): FakeResponse {
  return {
    ok: status >= 200 && status < 300,
    status,
    arrayBuffer: () => Promise.resolve(bytes(body)),
  };
}

/** A fetch double serving a fixed path -> body map. */
function fakeFetch(
  files: Record<string, string>,
  options: { failTimes?: number; status?: number } = {},
) {
  let failures = options.failTimes ?? 0;
  const calls: string[] = [];

  // The loader always calls fetch with a string path, so the double takes
  // one. Accepting the full RequestInfo union would force a stringify that
  // silently yields "[object Object]" for a Request and mismatch every path.
  const impl = vi.fn((path: string): Promise<FakeResponse> => {
    calls.push(path);

    if (failures > 0) {
      failures -= 1;
      return Promise.resolve(respond(503, ""));
    }

    const body = files[path];
    if (body === undefined) {
      return Promise.resolve(respond(404, ""));
    }
    return Promise.resolve(respond(options.status ?? 200, body));
  });

  return { impl: impl as unknown as typeof fetch, calls };
}

const noSleep = (): Promise<void> => Promise.resolve();

const overviewPayload = {
  schemaVersion: "1.0.0" as const,
  releaseId: "r1",
  filter: { datasets: ["ds-a"], corpusClasses: ["Candidate" as const] },
  countries: [
    {
      country: "DE",
      creatorCount: 2,
      representedVideoCount: 10,
      sourceOccurrenceCount: 12,
      resolvedVideoCount: 10,
      unavailableVideoCount: 0,
    },
  ],
  coverage: {
    inputOccurrenceCount: 12,
    distinctInputVideoCount: 10,
    partition: {
      resolved: 10,
      unavailableUnclassified: 0,
      retryableOrPending: 0,
      invalid: 0,
      terminalFailure: 0,
    },
    resolvedChannelCount: 2,
    knownCountryChannelCount: 2,
    unknownCountryChannelCount: 0,
  },
  creatorCount: 2,
  representedVideoCount: 10,
  representedCountryCount: 1,
};

function releaseFiles(overrides: Record<string, string> = {}) {
  const overview = JSON.stringify(overviewPayload);
  const overviewPath = "releases/r1/overview.json";

  const manifest = JSON.stringify({
    schemaVersion: "1.0.0",
    releaseId: "r1",
    generatedAt: "2026-01-15T12:00:00Z",
    enrichmentCutoff: "2026-01-15T11:00:00Z",
    defaultFilter: { datasets: ["ds-a"], corpusClasses: ["Candidate"] },
    datasets: [
      {
        datasetId: "ds-a",
        displayName: "DS A",
        version: "v1",
        corpusClass: "Candidate",
        sourceKind: "MetadataOnly",
        occurrenceUnit: "Row",
        sourceCitation: "https://example.invalid/ds",
        snapshotDigest: DIGEST,
      },
    ],
    artifactDigests: { [overviewPath]: digestOf(overview) },
    methodologyVersion: "1.0.0",
    disclosurePolicyVersion: "1.0.0",
    filters: [],
    boundaryMetadata: {
      datasetName: "Natural Earth",
      version: "5.1.1",
      license: "Public domain",
      attribution: "Made with Natural Earth",
      disputedTerritoryTreatment: "Presentation convention only.",
    },
  });

  const pointer = JSON.stringify({
    schemaVersion: "1.0.0",
    releaseId: "r1",
    manifestPath: "releases/r1/manifest.json",
    manifestDigest: digestOf(manifest),
  });

  return {
    "active-release.json": pointer,
    "releases/r1/manifest.json": manifest,
    [overviewPath]: overview,
    ...overrides,
  };
}

describe("digestBytes", () => {
  it("matches the manifest digest format", async () => {
    const value = await digestBytes(bytes("hello"));
    expect(value).toBe(digestOf("hello"));
  });
});

describe("loadVerified", () => {
  it("returns parsed data when the digest matches", async () => {
    const body = JSON.stringify(overviewPayload);
    const { impl } = fakeFetch({ "o.json": body });

    const result = await loadVerified(
      "o.json",
      digestOf(body),
      overviewArtifact,
      { fetchImpl: impl, sleep: noSleep },
    );
    expect(result.releaseId).toBe("r1");
  });

  it("rejects a payload whose digest does not match", async () => {
    // Requirement 14.7: corrupt data is refused, not rendered.
    const body = JSON.stringify(overviewPayload);
    const { impl } = fakeFetch({ "o.json": body });

    await expect(
      loadVerified("o.json", DIGEST, overviewArtifact, {
        fetchImpl: impl,
        sleep: noSleep,
      }),
    ).rejects.toMatchObject({ kind: "digest-mismatch" });
  });

  it("reports a digest failure before a schema failure", async () => {
    // Corrupt bytes would also fail schema parsing; reporting the digest
    // first tells the operator what actually went wrong.
    const { impl } = fakeFetch({ "o.json": "{}" });

    await expect(
      loadVerified("o.json", DIGEST, overviewArtifact, {
        fetchImpl: impl,
        sleep: noSleep,
      }),
    ).rejects.toMatchObject({ kind: "digest-mismatch" });
  });

  it("rejects valid JSON that fails the schema", async () => {
    const body = JSON.stringify({ schemaVersion: "1.0.0", releaseId: "r1" });
    const { impl } = fakeFetch({ "o.json": body });

    await expect(
      loadVerified("o.json", digestOf(body), overviewArtifact, {
        fetchImpl: impl,
        sleep: noSleep,
      }),
    ).rejects.toMatchObject({ kind: "schema-invalid" });
  });

  it("rejects non-JSON that happens to digest correctly", async () => {
    const body = "not json at all";
    const { impl } = fakeFetch({ "o.json": body });

    await expect(
      loadVerified("o.json", digestOf(body), overviewArtifact, {
        fetchImpl: impl,
        sleep: noSleep,
      }),
    ).rejects.toMatchObject({ kind: "schema-invalid" });
  });
});

describe("retry policy", () => {
  it("retries transient network failures", async () => {
    const body = JSON.stringify(overviewPayload);
    const { impl, calls } = fakeFetch({ "o.json": body }, { failTimes: 2 });

    const result = await loadVerified(
      "o.json",
      digestOf(body),
      overviewArtifact,
      { fetchImpl: impl, sleep: noSleep },
    );

    expect(result.releaseId).toBe("r1");
    expect(calls).toHaveLength(3);
  });

  it("gives up after the configured attempts", async () => {
    const { impl, calls } = fakeFetch({}, { failTimes: 99 });

    await expect(
      loadVerified("o.json", DIGEST, overviewArtifact, {
        fetchImpl: impl,
        sleep: noSleep,
        retry: { maxAttempts: 2, baseDelayMs: 1, maxDelayMs: 2 },
      }),
    ).rejects.toMatchObject({ kind: "network" });
    expect(calls).toHaveLength(2);
  });

  it("does not retry a missing artifact", async () => {
    // An immutable artifact that is absent stays absent; retrying only
    // delays reporting an incomplete release.
    const { impl, calls } = fakeFetch({});

    await expect(
      loadVerified("gone.json", DIGEST, overviewArtifact, {
        fetchImpl: impl,
        sleep: noSleep,
      }),
    ).rejects.toMatchObject({ kind: "not-found" });
    expect(calls).toHaveLength(1);
  });

  it("does not retry a digest mismatch", async () => {
    const body = JSON.stringify(overviewPayload);
    const { impl, calls } = fakeFetch({ "o.json": body });

    await expect(
      loadVerified("o.json", DIGEST, overviewArtifact, {
        fetchImpl: impl,
        sleep: noSleep,
      }),
    ).rejects.toMatchObject({ kind: "digest-mismatch" });
    expect(calls).toHaveLength(1);
  });
});

describe("loadActiveRelease", () => {
  it("loads a complete, self-consistent release", async () => {
    const { impl } = fakeFetch(releaseFiles());

    const release = await loadActiveRelease({
      fetchImpl: impl,
      sleep: noSleep,
    });

    expect(release.pointer.releaseId).toBe("r1");
    expect(release.manifest.releaseId).toBe("r1");
    expect(release.overview.representedVideoCount).toBe(10);
  });

  it("rejects a manifest belonging to another release", async () => {
    // Requirement 14.11: a stale cache must not pair artifacts across
    // releases.
    const files = releaseFiles();
    const manifest = JSON.parse(files["releases/r1/manifest.json"]) as {
      releaseId: string;
    };
    manifest.releaseId = "r2";
    const rewritten = JSON.stringify(manifest);

    const pointer = JSON.parse(files["active-release.json"]) as {
      manifestDigest: string;
    };
    pointer.manifestDigest = digestOf(rewritten);

    const { impl } = fakeFetch({
      ...files,
      "active-release.json": JSON.stringify(pointer),
      "releases/r1/manifest.json": rewritten,
    });

    await expect(
      loadActiveRelease({ fetchImpl: impl, sleep: noSleep }),
    ).rejects.toMatchObject({ kind: "mixed-release" });
  });

  it("rejects a tampered manifest", async () => {
    const files = releaseFiles();
    const { impl } = fakeFetch({
      ...files,
      "releases/r1/manifest.json": files["releases/r1/manifest.json"].replace(
        '"methodologyVersion":"1.0.0"',
        '"methodologyVersion":"9.9.9"',
      ),
    });

    await expect(
      loadActiveRelease({ fetchImpl: impl, sleep: noSleep }),
    ).rejects.toMatchObject({ kind: "digest-mismatch" });
  });

  it("reports an unreadable pointer distinctly", async () => {
    const { impl } = fakeFetch({ "active-release.json": "<html>oops</html>" });

    await expect(
      loadActiveRelease({ fetchImpl: impl, sleep: noSleep }),
    ).rejects.toMatchObject({ kind: "pointer-refresh" });
  });

  it("rejects a manifest that lists no overview", async () => {
    const files = releaseFiles();
    const manifest = JSON.parse(files["releases/r1/manifest.json"]) as {
      artifactDigests: Record<string, string>;
    };
    manifest.artifactDigests = { "releases/r1/other.json": DIGEST };
    const rewritten = JSON.stringify(manifest);
    const pointer = JSON.parse(files["active-release.json"]) as {
      manifestDigest: string;
    };
    pointer.manifestDigest = digestOf(rewritten);

    const { impl } = fakeFetch({
      ...files,
      "active-release.json": JSON.stringify(pointer),
      "releases/r1/manifest.json": rewritten,
    });

    await expect(
      loadActiveRelease({ fetchImpl: impl, sleep: noSleep }),
    ).rejects.toMatchObject({ kind: "not-found" });
  });
});

describe("loadCountryDetail", () => {
  const detailPayload = {
    country: "DE",
    creatorCount: 2,
    representedVideoCount: 10,
    sourceOccurrenceCount: 12,
    coverage: overviewPayload.coverage,
    datasetBreakdown: [{ datasetId: "ds-a", representedVideoCount: 10 }],
    firstPage: {
      country: "DE",
      sortOrder: "representedVideoCountDesc" as const,
      rows: [],
      nextCursor: null,
      pageSize: 50,
      totalRows: 0,
    },
  };

  it("loads a verified shard", async () => {
    const body = JSON.stringify(detailPayload);
    const path = "releases/r1/countries/DE.json";
    const { impl } = fakeFetch({ [path]: body });

    const manifest = {
      releaseId: "r1",
      artifactDigests: { [path]: digestOf(body) },
    } as never;

    const detail = await loadCountryDetail(manifest, "DE", {
      fetchImpl: impl,
      sleep: noSleep,
    });
    expect(detail.country).toBe("DE");
  });

  it("rejects a shard the manifest does not list", async () => {
    const { impl } = fakeFetch({});
    const manifest = { releaseId: "r1", artifactDigests: {} } as never;

    await expect(
      loadCountryDetail(manifest, "FR", { fetchImpl: impl, sleep: noSleep }),
    ).rejects.toMatchObject({ kind: "not-found" });
  });

  it("rejects a shard reporting a different country", async () => {
    const body = JSON.stringify({ ...detailPayload, country: "FR" });
    const path = "releases/r1/countries/DE.json";
    const { impl } = fakeFetch({ [path]: body });
    const manifest = {
      releaseId: "r1",
      artifactDigests: { [path]: digestOf(body) },
    } as never;

    await expect(
      loadCountryDetail(manifest, "DE", { fetchImpl: impl, sleep: noSleep }),
    ).rejects.toMatchObject({ kind: "mixed-release" });
  });
});

describe("failure descriptions", () => {
  it.each([
    "network",
    "not-found",
    "digest-mismatch",
    "schema-invalid",
    "mixed-release",
    "pointer-refresh",
  ] as const)("describes %s without claiming there is no data", (kind) => {
    const message = describeFailure(
      new ArtifactLoadError(kind, "p.json", "internal"),
    );

    expect(message.length).toBeGreaterThan(0);
    // Requirement 14.7: a failure must never read as an empty result.
    expect(message.toLowerCase()).not.toContain("no data");
    expect(message).not.toContain("0");
  });

  it("never leaks the internal message", () => {
    const message = describeFailure(
      new ArtifactLoadError("network", "p.json", "ECONNREFUSED 10.0.0.1:5432"),
    );
    expect(message).not.toContain("10.0.0.1");
  });
});
