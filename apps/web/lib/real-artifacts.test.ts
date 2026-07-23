import { existsSync, readFileSync, readdirSync } from "node:fs";
import { webcrypto } from "node:crypto";
import { join } from "node:path";

import { beforeAll, describe, expect, it } from "vitest";

import { loadActiveRelease, loadCountryDetail } from "./loader";

/**
 * Load a real generated release through the real loader.
 *
 * The unit tests exercise the loader against synthetic payloads, which
 * proves the logic but not that the Python generator and the TypeScript
 * consumer agree about digests, paths, and shapes. This reads the actual
 * `dist/` output — the same bytes a CDN would serve — so a drift between
 * the two sides fails here rather than in a browser.
 *
 * Skipped when no release has been built, so a clean checkout still runs
 * the suite.
 *
 * Requirement refs: 8.8, 14.6-14.11
 */

const DIST = join(process.cwd(), "dist");
const hasRelease = existsSync(join(DIST, "active-release.json"));

beforeAll(() => {
  if (!globalThis.crypto?.subtle) {
    Object.defineProperty(globalThis, "crypto", { value: webcrypto });
  }
});

/** Serve `dist/` from the filesystem through the loader's fetch seam. */
function fileFetch(): typeof fetch {
  const impl = (path: string) => {
    const target = join(DIST, path);
    if (!existsSync(target)) {
      return Promise.resolve({
        ok: false,
        status: 404,
        arrayBuffer: () => Promise.resolve(new ArrayBuffer(0)),
      });
    }
    const buffer = readFileSync(target);
    return Promise.resolve({
      ok: true,
      status: 200,
      arrayBuffer: () =>
        Promise.resolve(
          buffer.buffer.slice(
            buffer.byteOffset,
            buffer.byteOffset + buffer.byteLength,
          ),
        ),
    });
  };
  return impl as unknown as typeof fetch;
}

describe.skipIf(!hasRelease)("a real generated release", () => {
  it("loads through the full verified chain", async () => {
    const release = await loadActiveRelease({ fetchImpl: fileFetch() });

    expect(release.manifest.releaseId).toBe(release.pointer.releaseId);
    expect(release.overview.releaseId).toBe(release.manifest.releaseId);
    expect(release.manifest.datasets.length).toBeGreaterThan(0);
  });

  it("has a coverage partition that reconciles", async () => {
    const { overview } = await loadActiveRelease({ fetchImpl: fileFetch() });
    const { coverage } = overview;
    const partitionTotal =
      coverage.partition.resolved +
      coverage.partition.unavailableUnclassified +
      coverage.partition.retryableOrPending +
      coverage.partition.invalid +
      coverage.partition.terminalFailure;

    expect(partitionTotal).toBe(coverage.distinctInputVideoCount);
    expect(
      coverage.knownCountryChannelCount + coverage.unknownCountryChannelCount,
    ).toBe(coverage.resolvedChannelCount);
  });

  it("reports more occurrences than distinct videos", async () => {
    // The counting-unit distinction, on real data: sources repeat, so
    // occurrences exceed distinct identifiers.
    const { overview } = await loadActiveRelease({ fetchImpl: fileFetch() });
    expect(overview.coverage.inputOccurrenceCount).toBeGreaterThanOrEqual(
      overview.coverage.distinctInputVideoCount,
    );
  });

  it("keeps country subtotals within the corpus totals", async () => {
    const { overview } = await loadActiveRelease({ fetchImpl: fileFetch() });
    const countryVideos = overview.countries.reduce(
      (sum, c) => sum + c.representedVideoCount,
      0,
    );
    expect(countryVideos).toBeLessThanOrEqual(
      overview.coverage.distinctInputVideoCount,
    );
  });

  it("loads every country shard the manifest lists", async () => {
    const release = await loadActiveRelease({ fetchImpl: fileFetch() });

    for (const summary of release.overview.countries) {
      const detail = await loadCountryDetail(
        release.manifest,
        summary.country,
        { fetchImpl: fileFetch() },
      );

      // Requirement 10.2: detail totals equal the summary totals.
      expect(detail.creatorCount).toBe(summary.creatorCount);
      expect(detail.representedVideoCount).toBe(summary.representedVideoCount);
    }
  });

  it("carries no raw channel identifier in any artifact", () => {
    // The publication boundary, checked against the bytes on disk rather
    // than the in-memory payload.
    const rawIdPattern = /(?<![\w-])UC[A-Za-z0-9_-]{22}(?![\w-])/;

    function walk(dir: string): string[] {
      return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
        const path = join(dir, entry.name);
        if (entry.isDirectory()) return walk(path);
        return entry.name.endsWith(".json") ? [path] : [];
      });
    }

    const files = walk(DIST);
    expect(files.length).toBeGreaterThan(0);

    for (const file of files) {
      const text = readFileSync(file, "utf8");
      expect(rawIdPattern.test(text), `raw channel id in ${file}`).toBe(false);
    }
  });

  it("uses disclosure-approved public keys for every creator row", async () => {
    const release = await loadActiveRelease({ fetchImpl: fileFetch() });

    for (const summary of release.overview.countries) {
      const detail = await loadCountryDetail(
        release.manifest,
        summary.country,
        { fetchImpl: fileFetch() },
      );
      for (const row of detail.firstPage.rows) {
        expect(row.publicChannelKey).toMatch(/^pk_[A-Za-z0-9_-]{8,64}$/);
        expect(row.publicChannelKey.startsWith("pk_UC")).toBe(false);
      }
    }
  });
});
