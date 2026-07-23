import { createHash, webcrypto } from "node:crypto";

import { beforeAll, describe, expect, it } from "vitest";

import { creatorPageCount, loadCreatorPage } from "./loader";

/**
 * Traversing published creator pages.
 *
 * Requirement 10.6 requires traversal to present each approved creator
 * exactly once without omission, which is only possible if every page the
 * cursors can reach actually exists. These tests cover the failure modes
 * that would silently break that: a page that was never published, a page
 * belonging to another country, and one whose digest does not match.
 *
 * Requirement refs: 10.5-10.7, 10.10
 */

beforeAll(() => {
  if (!globalThis.crypto?.subtle) {
    Object.defineProperty(globalThis, "crypto", { value: webcrypto });
  }
});

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

function fakeFetch(files: Record<string, string>): typeof fetch {
  const impl = (path: string) => {
    const body = files[path];
    if (body === undefined) {
      return Promise.resolve({
        ok: false,
        status: 404,
        arrayBuffer: () => Promise.resolve(bytes("")),
      });
    }
    return Promise.resolve({
      ok: true,
      status: 200,
      arrayBuffer: () => Promise.resolve(bytes(body)),
    });
  };
  return impl as unknown as typeof fetch;
}

function page(country: string, index: number, sortOrder: string) {
  return JSON.stringify({
    country,
    sortOrder,
    rows: [
      {
        publicChannelKey: `pk_page${index}key0000`,
        displayName: `Channel ${index}`,
        country,
        representedVideoCount: 10 - index,
        datasetBreakdown: [{ datasetId: "ds-a", representedVideoCount: 1 }],
        lastObservedAt: "2026-07-23",
      },
    ],
    nextCursor: index < 2 ? `cursor-${index}` : null,
    pageSize: 1,
    totalRows: 3,
  });
}

const SORT = "representedVideoCountDesc";

function fixture() {
  const paths = [0, 1, 2].map(
    (i) => `releases/r1/countries/DE/${SORT}/page-${i}.json`,
  );
  const files: Record<string, string> = {};
  const digests: Record<string, string> = {};

  paths.forEach((path, index) => {
    const body = page("DE", index, SORT);
    files[path] = body;
    digests[path] = digestOf(body);
  });

  const manifest = {
    releaseId: "r1",
    artifactDigests: digests,
  } as never;

  const detail = {
    country: "DE",
    pageIndex: { [SORT]: paths },
  } as never;

  return { files, manifest, detail, paths };
}

describe("creatorPageCount", () => {
  it("reports how many pages a sort order publishes", () => {
    const { detail } = fixture();
    expect(creatorPageCount(detail, SORT)).toBe(3);
  });

  it("reports zero for an unpublished sort order", () => {
    const { detail } = fixture();
    expect(creatorPageCount(detail, "displayNameAsc")).toBe(0);
  });
});

describe("loadCreatorPage", () => {
  it("loads each published page", async () => {
    const { files, manifest, detail } = fixture();

    for (let index = 0; index < 3; index += 1) {
      const loaded = await loadCreatorPage(manifest, detail, SORT, index, {
        fetchImpl: fakeFetch(files),
      });
      expect(loaded.country).toBe("DE");
      expect(loaded.rows[0].displayName).toBe(`Channel ${index}`);
    }
  });

  it("traverses every page exactly once", async () => {
    // Requirement 10.6, over the loader rather than the raw artifacts.
    const { files, manifest, detail } = fixture();
    const seen = new Set<string>();
    let total = 0;

    for (let index = 0; index < creatorPageCount(detail, SORT); index += 1) {
      const loaded = await loadCreatorPage(manifest, detail, SORT, index, {
        fetchImpl: fakeFetch(files),
      });
      for (const row of loaded.rows) {
        seen.add(row.publicChannelKey);
        total += 1;
      }
    }

    expect(total).toBe(3);
    expect(seen.size).toBe(3);
  });

  it("refuses a page index beyond what was published", async () => {
    // Better a clear error than a 404 the caller has to interpret.
    const { files, manifest, detail } = fixture();

    await expect(
      loadCreatorPage(manifest, detail, SORT, 9, {
        fetchImpl: fakeFetch(files),
      }),
    ).rejects.toMatchObject({ kind: "not-found" });
  });

  it("refuses a negative page index", async () => {
    const { files, manifest, detail } = fixture();
    await expect(
      loadCreatorPage(manifest, detail, SORT, -1, {
        fetchImpl: fakeFetch(files),
      }),
    ).rejects.toMatchObject({ kind: "not-found" });
  });

  it("refuses a sort order the release does not publish", async () => {
    const { files, manifest, detail } = fixture();

    await expect(
      loadCreatorPage(manifest, detail, "displayNameAsc", 0, {
        fetchImpl: fakeFetch(files),
      }),
    ).rejects.toMatchObject({ kind: "not-found" });
  });

  it("refuses a page whose digest does not match", async () => {
    // Requirement 10.10: a corrupt page shows no rows rather than
    // partial ones.
    const { files, manifest, detail, paths } = fixture();
    const tampered = { ...files, [paths[1]]: page("DE", 99, SORT) };

    await expect(
      loadCreatorPage(manifest, detail, SORT, 1, {
        fetchImpl: fakeFetch(tampered),
      }),
    ).rejects.toMatchObject({ kind: "digest-mismatch" });
  });

  it("refuses a page belonging to another country", async () => {
    // Mixing countries into a traversal would break exactly-once.
    const path = `releases/r1/countries/DE/${SORT}/page-0.json`;
    const body = page("FR", 0, SORT);

    const manifest = {
      releaseId: "r1",
      artifactDigests: { [path]: digestOf(body) },
    } as never;
    const detail = { country: "DE", pageIndex: { [SORT]: [path] } } as never;

    await expect(
      loadCreatorPage(manifest, detail, SORT, 0, {
        fetchImpl: fakeFetch({ [path]: body }),
      }),
    ).rejects.toMatchObject({ kind: "mixed-release" });
  });

  it("refuses a page the manifest does not list", async () => {
    const path = `releases/r1/countries/DE/${SORT}/page-0.json`;
    const manifest = { releaseId: "r1", artifactDigests: {} } as never;
    const detail = { country: "DE", pageIndex: { [SORT]: [path] } } as never;

    await expect(
      loadCreatorPage(manifest, detail, SORT, 0, { fetchImpl: fakeFetch({}) }),
    ).rejects.toMatchObject({ kind: "not-found" });
  });
});
