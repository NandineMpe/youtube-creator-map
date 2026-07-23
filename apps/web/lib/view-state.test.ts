import fc from "fast-check";
import { describe, expect, it } from "vitest";

import {
  DEFAULT_METRIC,
  DEFAULT_SORT,
  DEFAULT_VIEW,
  EMPTY_VIEW_STATE,
  METRICS,
  SORT_ORDERS,
  canonicalize,
  equivalent,
  parse,
  serialize,
  withChange,
  type ViewState,
} from "./view-state";

function state(overrides: Partial<ViewState> = {}): ViewState {
  return { ...EMPTY_VIEW_STATE, ...overrides };
}

// --- Requirement 11.1: serialization -------------------------------------

describe("serialize", () => {
  it("omits every default", () => {
    expect(serialize(EMPTY_VIEW_STATE)).toBe("/");
  });

  it("emits selected components", () => {
    const url = serialize(
      state({ datasets: ["ds-a"], country: "DE", metric: "videos" }),
    );
    expect(url).toBe("/?datasets=ds-a&country=DE&metric=videos");
  });

  it("omits sort and cursor without a country", () => {
    // They select nothing at the overview level, so carrying them would be
    // dead state that two equivalent views could disagree on.
    const url = serialize(state({ sort: "displayNameAsc", cursor: "abc" }));
    expect(url).toBe("/");
  });

  it("emits sort and cursor once a country is selected", () => {
    const url = serialize(
      state({ country: "DE", sort: "displayNameAsc", cursor: "abc" }),
    );
    expect(url).toContain("sort=displayNameAsc");
    expect(url).toContain("cursor=abc");
  });
});

// --- Requirement 11.2: one canonical form --------------------------------

describe("canonical form", () => {
  it("sorts dataset members", () => {
    const a = serialize(state({ datasets: ["zeta", "alpha", "mid"] }));
    const b = serialize(state({ datasets: ["mid", "zeta", "alpha"] }));
    expect(a).toBe(b);
    expect(a).toContain("datasets=alpha,mid,zeta");
  });

  it("deduplicates repeated members", () => {
    expect(serialize(state({ datasets: ["a", "a", "b"] }))).toBe(
      serialize(state({ datasets: ["a", "b"] })),
    );
  });

  it("treats an explicit default as an omitted one", () => {
    const explicit = state({
      metric: DEFAULT_METRIC,
      sort: DEFAULT_SORT,
      view: DEFAULT_VIEW,
    });
    expect(serialize(explicit)).toBe(serialize(EMPTY_VIEW_STATE));
  });

  it("emits parameters in a fixed order regardless of construction", () => {
    const url = serialize(
      state({
        cursor: "c",
        sort: "displayNameAsc",
        view: "table",
        metric: "videos",
        country: "DE",
        corpusClasses: ["Candidate"],
        datasets: ["ds"],
        release: "r1",
      }),
    );
    const order = url
      .slice(url.indexOf("?") + 1)
      .split("&")
      .map((pair) => pair.split("=")[0]);
    expect(order).toEqual([
      "release",
      "datasets",
      "corpus",
      "country",
      "metric",
      "view",
      "sort",
      "cursor",
    ]);
  });

  it("reports equivalent states as equivalent", () => {
    expect(
      equivalent(
        state({ datasets: ["b", "a"] }),
        state({ datasets: ["a", "b"] }),
      ),
    ).toBe(true);
  });
});

// --- Requirement 11.3, 11.4: parsing --------------------------------------

describe("parse", () => {
  it("parses a complete URL", () => {
    const result = parse(
      "/?release=r1&datasets=ds-a,ds-b&corpus=Candidate&country=DE&metric=videos&view=table&sort=displayNameAsc&cursor=abc",
    );

    expect(result.corrections).toEqual([]);
    expect(result.state).toMatchObject({
      release: "r1",
      datasets: ["ds-a", "ds-b"],
      corpusClasses: ["Candidate"],
      country: "DE",
      metric: "videos",
      view: "table",
      sort: "displayNameAsc",
      cursor: "abc",
    });
  });

  it("returns defaults for an empty URL", () => {
    const result = parse("/");
    expect(result.state).toEqual(EMPTY_VIEW_STATE);
    expect(result.usedFallback).toBe(false);
  });

  it("normalizes country case", () => {
    expect(parse("/?country=de").state.country).toBe("DE");
  });

  it("accepts the Unknown bucket", () => {
    expect(parse("/?country=XX").state.country).toBe("XX");
  });
});

// --- Requirement 11.7, 11.9: fallback and correction reporting -----------

describe("recoverable validation", () => {
  it.each([
    ["country", "/?country=Germany", "not an ISO"],
    ["metric", "/?metric=bogus", "unknown metric"],
    ["view", "/?view=globe", "unknown view mode"],
    ["sort", "/?country=DE&sort=random", "unknown sort order"],
    ["release", "/?release=../etc/passwd", "not a valid release"],
  ])("reports an unsupported %s", (field, url, reason) => {
    const result = parse(url);

    expect(result.usedFallback).toBe(true);
    expect(result.corrections).toHaveLength(1);
    expect(result.corrections[0].field).toBe(field);
    expect(result.corrections[0].reason).toContain(reason);
  });

  it("drops unknown datasets but keeps valid ones", () => {
    const result = parse("/?datasets=ds-a,ds-missing", {
      knownDatasets: ["ds-a"],
    });

    expect(result.state.datasets).toEqual(["ds-a"]);
    expect(result.corrections[0].rejected).toBe("ds-missing");
  });

  it("drops a country with no data in this release", () => {
    const result = parse("/?country=FR", { knownCountries: ["DE", "XX"] });
    expect(result.state.country).toBeNull();
    expect(result.corrections[0].reason).toContain("no data");
  });

  it("drops a cursor without a country", () => {
    // Requirement 11.9: invalid values are excluded from data requests.
    const result = parse("/?cursor=abc");
    expect(result.state.cursor).toBeNull();
    expect(result.corrections[0].field).toBe("cursor");
  });

  it("rejects a malformed cursor", () => {
    const result = parse(
      "/?country=DE&cursor=" + encodeURIComponent("../../etc"),
    );
    expect(result.state.cursor).toBeNull();
  });

  it("never throws on hostile input", () => {
    const hostile = [
      "/?country=" + "A".repeat(5000),
      "/?datasets=" + "x,".repeat(2000),
      "/?metric=%00%01%02",
      "/?" + "a=1&".repeat(1000),
    ];
    for (const url of hostile) {
      expect(() => parse(url)).not.toThrow();
    }
  });

  it("serializes a fallback as a valid canonical URL", () => {
    // Requirement 11.8.
    const result = parse("/?country=Germany&metric=bogus");
    const url = serialize(result.state);
    expect(url).toBe("/");
    expect(parse(url).corrections).toEqual([]);
  });
});

// --- Requirement 11.5, 11.6: round trips ---------------------------------

describe("round trips", () => {
  it("parse -> serialize -> parse is stable", () => {
    const url = "/?datasets=ds-a,ds-b&country=DE&metric=videos";
    const first = parse(url).state;
    const second = parse(serialize(first)).state;
    expect(second).toEqual(first);
  });

  it("a canonical URL survives parse and serialize unchanged", () => {
    // Requirement 11.6: the identity property.
    const canonical = "/?datasets=alpha,zeta&country=DE&metric=videos";
    expect(serialize(parse(canonical).state)).toBe(canonical);
  });

  it("canonicalize is idempotent", () => {
    const messy = "/?datasets=zeta,alpha,zeta&metric=creators&view=map";
    const once = canonicalize(messy);
    expect(canonicalize(once)).toBe(once);
    expect(once).toBe("/?datasets=alpha,zeta");
  });
});

// --- Cursor invalidation --------------------------------------------------

describe("withChange", () => {
  const base = state({ country: "DE", cursor: "page2", datasets: ["ds-a"] });

  it("clears the cursor when the filter changes", () => {
    // A cursor names a position in one ordered result; carrying it across
    // a filter change would skip rows (Requirement 10.6).
    expect(withChange(base, { datasets: ["ds-b"] }).cursor).toBeNull();
  });

  it("clears the cursor when the country changes", () => {
    expect(withChange(base, { country: "FR" }).cursor).toBeNull();
  });

  it("clears the cursor when the sort changes", () => {
    expect(withChange(base, { sort: "displayNameAsc" }).cursor).toBeNull();
  });

  it("keeps the cursor when only the view changes", () => {
    expect(withChange(base, { view: "table" }).cursor).toBe("page2");
  });

  it("keeps an explicitly supplied cursor", () => {
    expect(withChange(base, { country: "FR", cursor: "p1" }).cursor).toBe("p1");
  });
});

// --- Properties -----------------------------------------------------------

const arbState = fc.record({
  release: fc.option(fc.stringMatching(/^[a-z0-9-]{1,12}$/), { nil: null }),
  datasets: fc.array(fc.stringMatching(/^[a-z][a-z0-9-]{0,8}$/), {
    maxLength: 5,
  }),
  corpusClasses: fc.array(fc.constantFrom("Candidate", "Comparison"), {
    maxLength: 2,
  }),
  country: fc.option(fc.constantFrom("DE", "US", "JP", "XX"), { nil: null }),
  metric: fc.constantFrom(...METRICS),
  view: fc.constantFrom("map" as const, "table" as const),
  sort: fc.constantFrom(...SORT_ORDERS),
  cursor: fc.option(fc.stringMatching(/^[A-Za-z0-9_-]{1,20}$/), { nil: null }),
});

describe("codec properties", () => {
  it("serialize then parse preserves the canonical state", () => {
    // Requirement 11.4.
    fc.assert(
      fc.property(arbState, (candidate) => {
        const url = serialize(candidate as ViewState);
        const parsed = parse(url).state;
        return serialize(parsed) === url;
      }),
      { numRuns: 500 },
    );
  });

  it("serialization is idempotent under re-parsing", () => {
    // Requirement 11.5.
    fc.assert(
      fc.property(arbState, (candidate) => {
        const once = serialize(candidate as ViewState);
        const twice = serialize(parse(once).state);
        return once === twice;
      }),
      { numRuns: 500 },
    );
  });

  it("member ordering never changes the URL", () => {
    // Requirement 11.2.
    fc.assert(
      fc.property(
        fc.array(fc.stringMatching(/^[a-z][a-z0-9-]{0,8}$/), {
          minLength: 1,
          maxLength: 6,
        }),
        (datasets) => {
          const forward = serialize(state({ datasets }));
          const backward = serialize(
            state({ datasets: [...datasets].reverse() }),
          );
          return forward === backward;
        },
      ),
      { numRuns: 300 },
    );
  });

  it("parsing arbitrary text always yields a serializable state", () => {
    // Requirement 11.7: recoverable, never an exception.
    fc.assert(
      fc.property(fc.string({ maxLength: 200 }), (raw) => {
        const result = parse(`/?${raw}`);
        const url = serialize(result.state);
        return parse(url).corrections.length === 0;
      }),
      { numRuns: 500 },
    );
  });
});
