import { existsSync, readFileSync } from "node:fs";
import { webcrypto } from "node:crypto";
import { join } from "node:path";

import { beforeAll, describe, expect, it } from "vitest";

import {
  loadActiveRelease,
  loadCountryDetail,
  loadCreatorPage,
} from "./loader";
import { parse, serialize } from "./view-state";

/**
 * End-to-end checks for specific regions.
 *
 * These name South Africa and Ireland because they were asked for
 * directly. The value is not that these two are special to the pipeline —
 * they are not — but that a named region gives a concrete anchor for
 * "does selecting a country actually work", covering the URL, the shard,
 * the totals, and the creator pages in one path.
 *
 * Skipped when no release has been built.
 *
 * Requirement refs: 10.1-10.7, 11.3, 11.6
 */

const DIST = join(process.cwd(), "dist");
const hasRelease = existsSync(join(DIST, "active-release.json"));

beforeAll(() => {
  if (!globalThis.crypto?.subtle) {
    Object.defineProperty(globalThis, "crypto", { value: webcrypto });
  }
});

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

const REGIONS = [
  ["ZA", "South Africa"],
  ["IE", "Ireland"],
] as const;

describe.skipIf(!hasRelease)("named regions", () => {
  it.each(REGIONS)("%s (%s) appears in the overview", async (code) => {
    const { overview } = await loadActiveRelease({ fetchImpl: fileFetch() });
    const summary = overview.countries.find((c) => c.country === code);

    expect(summary, `${code} missing from overview`).toBeDefined();
    expect(summary!.creatorCount).toBeGreaterThan(0);
    expect(summary!.representedVideoCount).toBeGreaterThan(0);
  });

  it.each(REGIONS)("%s detail agrees with its summary", async (code) => {
    // Requirement 10.2.
    const release = await loadActiveRelease({ fetchImpl: fileFetch() });
    const summary = release.overview.countries.find((c) => c.country === code)!;
    const detail = await loadCountryDetail(release.manifest, code, {
      fetchImpl: fileFetch(),
    });

    expect(detail.creatorCount).toBe(summary.creatorCount);
    expect(detail.representedVideoCount).toBe(summary.representedVideoCount);
  });

  it.each(REGIONS)(
    "%s creator pages all load and are disjoint",
    async (code) => {
      // Requirement 10.6: exactly once, no omission.
      const release = await loadActiveRelease({ fetchImpl: fileFetch() });
      const detail = await loadCountryDetail(release.manifest, code, {
        fetchImpl: fileFetch(),
      });

      const sort = "representedVideoCountDesc";
      const seen = new Set<string>();
      let rows = 0;

      for (const _ of detail.pageIndex[sort] ?? []) {
        const index = detail.pageIndex[sort].indexOf(_);
        const page = await loadCreatorPage(
          release.manifest,
          detail,
          sort,
          index,
          {
            fetchImpl: fileFetch(),
          },
        );
        for (const row of page.rows) {
          seen.add(row.publicChannelKey);
          rows += 1;
        }
      }

      expect(rows).toBe(detail.firstPage.totalRows);
      expect(seen.size).toBe(rows);
    },
  );

  it.each(REGIONS)("%s survives a URL round trip", (code) => {
    // Requirement 11.6: the shareable link is stable.
    const url = `/?country=${code}`;
    const parsed = parse(url);

    expect(parsed.state.country).toBe(code);
    expect(parsed.corrections).toEqual([]);
    expect(serialize(parsed.state)).toBe(url);
  });

  it.each(REGIONS)("%s has a boundary shape to render", (code) => {
    const geo = JSON.parse(
      readFileSync(
        join(process.cwd(), "apps/web/public/boundaries/countries.json"),
        "utf8",
      ),
    ) as { features: { properties: { iso: string } }[] };

    expect(geo.features.some((f) => f.properties.iso === code)).toBe(true);
  });

  it("publishes only creators meeting the disclosure threshold", async () => {
    // The gap between counted and listed creators is the policy working,
    // not data loss. Asserting it keeps a future change to the threshold
    // from silently publishing everyone.
    const release = await loadActiveRelease({ fetchImpl: fileFetch() });

    for (const [code] of REGIONS) {
      const detail = await loadCountryDetail(release.manifest, code, {
        fetchImpl: fileFetch(),
      });
      expect(detail.firstPage.totalRows).toBeLessThanOrEqual(
        detail.creatorCount,
      );
      for (const row of detail.firstPage.rows) {
        expect(row.representedVideoCount).toBeGreaterThanOrEqual(5);
      }
    }
  });
});
