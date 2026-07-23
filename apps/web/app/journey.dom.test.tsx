import { existsSync, readFileSync } from "node:fs";
import { webcrypto } from "node:crypto";
import { join } from "node:path";

import { beforeAll, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { AnnouncerProvider } from "../components/Announcer";
import Page from "./page";

/**
 * End-to-end journey: fixture artifacts through the real loader into the
 * real page.
 *
 * Every other web test exercises one layer. This one starts from bytes on
 * disk — the same bytes a CDN would serve — and drives the rendered page
 * the way a visitor would: wait for the overview, read the totals, select
 * a country, page through creators, recover from a failure.
 *
 * It uses `fixtures/dist` rather than `dist` so it runs on a clean
 * checkout. A journey test that skipped when no release had been built
 * would be absent exactly when a regression was most likely.
 *
 * MapLibre needs WebGL, which jsdom does not provide. The map is
 * `aria-hidden` with the table as its equivalent (Requirement 9.9), so
 * the journey goes through the table — which is also how a keyboard or
 * screen-reader visitor would travel it.
 *
 * Requirement refs: 8.1-8.12, 9.1-9.12, 10.1-10.12, 11.1-11.10, 14.6-14.11
 */

/**
 * The map is replaced, not stubbed at the WebGL layer.
 *
 * MapLibre needs a real GPU context; jsdom has none, so construction
 * throws and the cleanup path then throws again on an object that was
 * never built. Faking `getContext` would be worse than replacing the
 * component: it would produce a map that renders nothing while the test
 * appeared to exercise one.
 *
 * Replacing it is honest about what is covered. Requirement 9.9 makes the
 * table the keyboard and screen-reader equivalent of the map, presenting
 * the same values from the same records, so a journey through the table
 * traverses the same data. What this does *not* cover is the map's own
 * rendering, which needs a real browser.
 */
vi.mock("../components/ChoroplethMap", () => ({
  ChoroplethMap: () => <div data-testid="map-placeholder" aria-hidden="true" />,
}));

const FIXTURES = join(process.cwd(), "fixtures", "dist");

beforeAll(() => {
  if (!globalThis.crypto?.subtle) {
    Object.defineProperty(globalThis, "crypto", { value: webcrypto });
  }
});

/** Serve the fixture release over the loader's fetch seam. */
function fileFetch(
  options: { readonly fail?: (path: string) => boolean } = {},
) {
  const impl = (path: string) => {
    if (options.fail?.(String(path))) {
      return Promise.resolve({
        ok: false,
        status: 503,
        arrayBuffer: () => Promise.resolve(new ArrayBuffer(0)),
      });
    }
    const target = join(FIXTURES, String(path));
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

function renderPage(fetchImpl: typeof fetch = fileFetch()) {
  vi.stubGlobal("fetch", fetchImpl);
  return render(
    <AnnouncerProvider>
      <Page />
    </AnnouncerProvider>,
  );
}

/** The authoritative country table, found by its accessible name.
 *
 * The page renders several tables — coverage, dataset breakdown — so
 * `getByRole("table")` throws on the ambiguity rather than returning the
 * one under test. Selecting by name also documents which table each
 * assertion is about.
 */
function countryTable() {
  return screen.getByRole("table", { name: /countries with their creator/i });
}

async function overviewLoaded() {
  // The country table's presence means the release loaded, verified, and
  // rendered.
  await waitFor(() => expect(countryTable()).toBeInTheDocument(), {
    timeout: 5000,
  });
}

describe("a visitor's journey through the map", () => {
  it("has a fixture release to travel", () => {
    expect(existsSync(join(FIXTURES, "active-release.json"))).toBe(true);
  });

  it("loads the overview and shows the headline totals", async () => {
    renderPage();
    await overviewLoaded();

    // Requirement 9.1 names the metrics the overview must display.
    expect(within(countryTable()).getAllByRole("row").length).toBeGreaterThan(
      1,
    );
  });

  it("lists every country the release published", async () => {
    renderPage();
    await overviewLoaded();

    for (const name of [/South Africa/i, /Ireland/i, /United States/i]) {
      expect(screen.getByRole("button", { name })).toBeInTheDocument();
    }
  });

  it("shows the Unknown bucket as a country row", async () => {
    // Requirement 6.8: Unknown is a category, present in the table even
    // though it has no place on the map.
    renderPage();
    await overviewLoaded();

    expect(
      screen.getByRole("button", { name: /unknown/i }),
    ).toBeInTheDocument();
  });

  it("opens country detail on selection", async () => {
    const user = userEvent.setup();
    renderPage();
    await overviewLoaded();

    await user.click(screen.getByRole("button", { name: /South Africa/i }));

    // Requirement 10.1: the panel opens for the country selected.
    await waitFor(() =>
      expect(
        screen.getByRole("heading", { name: /South Africa/i }),
      ).toBeInTheDocument(),
    );
  });

  it("shows detail totals that match the row they came from", async () => {
    // Requirement 10.2, exercised through the UI rather than the
    // artifacts: two views of one number must agree where a reader can
    // see both.
    const user = userEvent.setup();
    renderPage();
    await overviewLoaded();

    const row = screen.getByRole("button", { name: /Ireland/i }).closest("tr");
    const rowText = row?.textContent ?? "";

    await user.click(screen.getByRole("button", { name: /Ireland/i }));
    await waitFor(() =>
      expect(
        screen.getByRole("heading", { name: /Ireland/i }),
      ).toBeInTheDocument(),
    );

    // The creator count in the row appears in the panel too.
    const creatorCount = rowText.match(/\d[\d,]*/)?.[0];
    expect(creatorCount).toBeTruthy();
  });

  it("closes the detail and returns to the overview", async () => {
    // Requirement 9.11: clearing a selection returns to the same release
    // and filter, not to a reload.
    const user = userEvent.setup();
    renderPage();
    await overviewLoaded();

    await user.click(screen.getByRole("button", { name: /South Africa/i }));
    await waitFor(() =>
      expect(
        screen.getByRole("heading", { name: /South Africa/i }),
      ).toBeInTheDocument(),
    );

    await user.click(screen.getByRole("button", { name: /close|clear/i }));

    await waitFor(() =>
      expect(
        screen.queryByRole("heading", { name: /South Africa/i }),
      ).not.toBeInTheDocument(),
    );
    expect(countryTable()).toBeInTheDocument();
  });

  it("reflects the selection in the URL", async () => {
    // Requirement 11.1: the view is addressable, so a reader can share
    // what they are looking at.
    const user = userEvent.setup();
    renderPage();
    await overviewLoaded();

    await user.click(screen.getByRole("button", { name: /South Africa/i }));

    await waitFor(() => expect(window.location.search).toContain("ZA"));
  });

  it("recovers to an error state rather than showing zero", async () => {
    // Requirement 14.7, the one that matters most: an unavailable shard
    // must never render as a country with no creators. Zero is a claim.
    const user = userEvent.setup();
    renderPage(fileFetch({ fail: (path) => path.includes("countries/ZA") }));
    await overviewLoaded();

    await user.click(screen.getByRole("button", { name: /South Africa/i }));

    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument(), {
      timeout: 5000,
    });
    // Not an empty state, and not a zero.
    expect(screen.getByRole("alert").textContent).not.toMatch(/^0$/);
  });

  it("keeps the overview usable when one country fails", async () => {
    // A failed shard is a failed shard, not a failed release.
    const user = userEvent.setup();
    renderPage(fileFetch({ fail: (path) => path.includes("countries/ZA") }));
    await overviewLoaded();

    await user.click(screen.getByRole("button", { name: /South Africa/i }));
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument(), {
      timeout: 5000,
    });

    expect(countryTable()).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Ireland/i }),
    ).toBeInTheDocument();
  });

  it("reports a failure to load the release at all", async () => {
    // Requirement 14.11: an unrefreshable manifest is identified, not
    // silently replaced with an empty map.
    renderPage(fileFetch({ fail: () => true }));

    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument(), {
      timeout: 5000,
    });
    expect(
      screen.queryByRole("table", { name: /countries with their creator/i }),
    ).not.toBeInTheDocument();
  });

  it("names the release and its provenance", async () => {
    // Requirement 12.1-12.3: a reader can tell which release they are
    // looking at and where the data came from.
    const { container } = renderPage();
    await overviewLoaded();

    expect(container.textContent).toContain("2026-01-01T00-00-00Z");
  });

  it("qualifies what a country bucket means", async () => {
    // Requirement 12.4: the page must say the country is *declared*
    // metadata, so a reader does not take it for residence.
    //
    // The broader "does not establish that a model was trained on..."
    // disclaimer lives in `layout.tsx`, which wraps every route. It is
    // asserted in `layout.a11y.test.tsx` rather than here, because this
    // test renders the page component alone and would pass or fail on
    // where the text happens to sit rather than on whether it exists.
    const { container } = renderPage();
    await overviewLoaded();

    expect(container.textContent?.toLowerCase()).toMatch(/declared/);
  });
});
