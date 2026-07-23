import { describe, expect, it } from "vitest";

import { resolveFilterEntry } from "./loader";

/**
 * Choosing which published aggregate serves a selection.
 *
 * Requirement 5.12 forbids approximating a filtered count, so this must
 * either find an exact published artifact or say plainly that it fell
 * back. Quietly serving the default while the controls show a narrower
 * selection would present one filter's numbers as another's.
 *
 * Requirement refs: 5.12, 9.6, 9.12
 */

type Manifest = Parameters<typeof resolveFilterEntry>[0];

function manifest(
  filters: {
    key: string;
    label: string;
    path: string;
    datasets: string[];
    corpusClasses: ("Candidate" | "Comparison")[];
    isDefault: boolean;
  }[],
): Manifest {
  return { releaseId: "r1", filters } as unknown as Manifest;
}

const full = manifest([
  {
    key: "Candidate+Comparison~ds-a+ds-b",
    label: "All datasets",
    path: "releases/r1/overview.json",
    datasets: ["ds-a", "ds-b"],
    corpusClasses: ["Candidate", "Comparison"],
    isDefault: true,
  },
  {
    key: "Candidate~ds-a+ds-b",
    label: "Candidate corpora",
    path: "releases/r1/overviews/Candidate~ds-a+ds-b.json",
    datasets: ["ds-a", "ds-b"],
    corpusClasses: ["Candidate"],
    isDefault: false,
  },
  {
    key: "Candidate+Comparison~ds-a",
    label: "ds-a",
    path: "releases/r1/overviews/Candidate+Comparison~ds-a.json",
    datasets: ["ds-a"],
    corpusClasses: ["Candidate", "Comparison"],
    isDefault: false,
  },
]);

describe("resolveFilterEntry", () => {
  it("returns the default for an empty selection", () => {
    const { entry, exact } = resolveFilterEntry(full, [], []);
    expect(entry?.isDefault).toBe(true);
    expect(exact).toBe(true);
  });

  it("finds an exact dataset match", () => {
    const { entry, exact } = resolveFilterEntry(full, ["ds-a"], []);
    expect(entry?.key).toBe("Candidate+Comparison~ds-a");
    expect(exact).toBe(true);
  });

  it("finds an exact corpus-class match", () => {
    const { entry, exact } = resolveFilterEntry(full, [], ["Candidate"]);
    expect(entry?.key).toBe("Candidate~ds-a+ds-b");
    expect(exact).toBe(true);
  });

  it("matches regardless of member ordering", () => {
    // The canonical form is order-independent (Requirement 11.2), so the
    // artifact lookup must be too.
    const forward = resolveFilterEntry(full, ["ds-a", "ds-b"], []);
    const backward = resolveFilterEntry(full, ["ds-b", "ds-a"], []);
    expect(forward.entry?.key).toBe(backward.entry?.key);
  });

  it("falls back and says so for an unpublished combination", () => {
    // Requirement 5.12: no artifact means no exact answer, and the caller
    // must be able to tell the visitor rather than show the default's
    // numbers under the requested label.
    const { entry, exact } = resolveFilterEntry(full, ["ds-b"], ["Candidate"]);
    expect(entry?.isDefault).toBe(true);
    expect(exact).toBe(false);
  });

  it("falls back for a dataset the release does not contain", () => {
    const { entry, exact } = resolveFilterEntry(full, ["ds-absent"], []);
    expect(entry?.isDefault).toBe(true);
    expect(exact).toBe(false);
  });

  it("reports no entry when a release publishes no index", () => {
    const bare = manifest([]);
    const { entry, exact } = resolveFilterEntry(bare, ["ds-a"], []);
    expect(entry).toBeNull();
    expect(exact).toBe(false);
  });

  it("uses the first entry when none is marked default", () => {
    const headless = manifest([
      {
        key: "Candidate~ds-a",
        label: "ds-a",
        path: "p.json",
        datasets: ["ds-a"],
        corpusClasses: ["Candidate"],
        isDefault: false,
      },
    ]);
    const { entry } = resolveFilterEntry(headless, ["ds-zzz"], []);
    expect(entry?.key).toBe("Candidate~ds-a");
  });
});
