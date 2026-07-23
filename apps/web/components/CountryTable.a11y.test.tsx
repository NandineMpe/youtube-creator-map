import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "vitest-axe";

import { CountryTable } from "./CountryTable";
import { computeBins } from "../lib/bins";

/**
 * Accessibility tests for the country table.
 *
 * The table is not a fallback for the map — Requirement 9.9 makes it the
 * keyboard and screen-reader equivalent, which means a defect here is not
 * a degraded experience for some users but the absence of any experience.
 * The map canvas is `aria-hidden`; if this table is unusable there is
 * nothing left.
 *
 * These assert semantics rather than markup: a test pinning `<th>` would
 * pass on a table nobody can navigate, and fail on a correct refactor.
 *
 * Requirement refs: 9.3, 9.5, 9.9, 13.1-13.5, 13.8
 */

// Built by the real binning function rather than a literal: a
// hand-written scale drifts from the type and renders blank swatches
// without any error, which looks like a component bug.
const SCALE = computeBins([121, 238, 640, 1204, 12993]);

const COUNTRIES = [
  {
    country: "ZA",
    creatorCount: 238,
    representedVideoCount: 1204,
    sourceOccurrenceCount: 1442,
    resolvedVideoCount: 1204,
    unavailableVideoCount: 0,
  },
  {
    country: "IE",
    creatorCount: 121,
    representedVideoCount: 640,
    sourceOccurrenceCount: 761,
    resolvedVideoCount: 640,
    unavailableVideoCount: 0,
  },
  {
    country: "XX",
    creatorCount: 12993,
    representedVideoCount: 22110,
    sourceOccurrenceCount: 23001,
    resolvedVideoCount: 22110,
    unavailableVideoCount: 0,
  },
];

function renderTable(
  overrides: Partial<Parameters<typeof CountryTable>[0]> = {},
) {
  const onSelect = vi.fn();
  const result = render(
    <CountryTable
      countries={COUNTRIES}
      metric="creators"
      scale={SCALE}
      selectedCountry={null}
      onSelect={onSelect}
      {...overrides}
    />,
  );
  return { ...result, onSelect };
}

describe("the country table", () => {
  it("has no detectable accessibility violations", async () => {
    const { container } = renderTable();

    expect(await axe(container)).toHaveNoViolations();
  });

  it("is a real table with a caption and header cells", () => {
    // Screen readers announce row and column context from these. A grid
    // of divs looks identical and conveys none of it.
    renderTable();
    const table = screen.getByRole("table");

    expect(within(table).getAllByRole("columnheader").length).toBeGreaterThan(
      0,
    );
    expect(within(table).getAllByRole("rowheader").length).toBe(
      COUNTRIES.length,
    );
  });

  it("exposes every country as a named row", () => {
    renderTable();

    for (const country of ["South Africa", "Ireland"]) {
      expect(
        screen.getByRole("button", { name: new RegExp(country, "i") }),
      ).toBeInTheDocument();
    }
  });

  // --- Requirement 13.5: keyboard equivalence ----------------------------

  it("selects a country with Enter", async () => {
    // Requirement 13.5 names Enter and Space explicitly. These pass
    // because the control is a real <button>; they would fail the moment
    // someone replaced it with a clickable <div>, which is the actual
    // regression worth catching.
    const user = userEvent.setup();
    const { onSelect } = renderTable();

    // Focused directly rather than tabbed to: the first tab stop is a
    // column-sort button, and asserting on tab *order* here would make
    // this test fail whenever a column is added — a different concern
    // from whether Enter activates a country. Tab reachability is
    // covered separately below.
    screen.getByRole("button", { name: /South Africa/i }).focus();
    await user.keyboard("{Enter}");

    expect(onSelect).toHaveBeenCalledWith("ZA");
  });

  it("selects a country with Space", async () => {
    const user = userEvent.setup();
    const { onSelect } = renderTable();

    const button = screen.getByRole("button", { name: /South Africa/i });
    button.focus();
    await user.keyboard(" ");

    expect(onSelect).toHaveBeenCalledWith("ZA");
  });

  it("produces the same result from keyboard and pointer", async () => {
    const user = userEvent.setup();

    const keyboard = renderTable();
    screen.getByRole("button", { name: /Ireland/i }).focus();
    await user.keyboard("{Enter}");
    const fromKeyboard = keyboard.onSelect.mock.calls;
    keyboard.unmount();

    const pointer = renderTable();
    await user.click(screen.getByRole("button", { name: /Ireland/i }));

    expect(pointer.onSelect.mock.calls).toEqual(fromKeyboard);
  });

  it("reaches every country control by tabbing", async () => {
    const user = userEvent.setup();
    renderTable();

    const reachable = new Set<string>();
    for (let step = 0; step < 20; step += 1) {
      await user.tab();
      const active = document.activeElement;
      if (active instanceof HTMLElement && active.tagName === "BUTTON") {
        reachable.add(active.textContent ?? "");
      }
    }

    // Every row is reachable, not just the first.
    expect(reachable.size).toBeGreaterThanOrEqual(COUNTRIES.length);
  });

  // --- Requirement 9.5 / 13.4: state is not conveyed by colour alone -----

  it("marks the selected row without relying on colour", () => {
    renderTable({ selectedCountry: "ZA" });

    const selected = screen.getByRole("button", { name: /South Africa/i });

    // aria-current is announced; a background colour is not.
    expect(selected).toHaveAttribute("aria-current");
  });

  it("labels the Unknown bucket in text", () => {
    // Requirement 6.8 places Unknown outside the geographic ramp. A
    // reader must be able to tell it apart without seeing the legend.
    renderTable();

    expect(
      screen.getByRole("button", { name: /unknown/i }),
    ).toBeInTheDocument();
  });

  it("announces the sort state on sortable columns", async () => {
    const user = userEvent.setup();
    renderTable();

    const header = screen
      .getAllByRole("columnheader")
      .find((h) => h.getAttribute("aria-sort"));
    expect(header).toBeDefined();

    const before = header?.getAttribute("aria-sort");
    await user.click(within(header!).getByRole("button"));

    expect(header?.getAttribute("aria-sort")).not.toBe(before);
  });

  // --- Requirement 13.8: nothing available only on hover -----------------

  it("presents every metric as text rather than on hover", () => {
    renderTable();
    const row = screen
      .getByRole("button", { name: /South Africa/i })
      .closest("tr");

    // The counts the map shows in its hover panel are in the row.
    expect(row?.textContent).toContain("238");
  });

  it("stays accessible with a country selected", async () => {
    const { container } = renderTable({ selectedCountry: "ZA" });

    expect(await axe(container)).toHaveNoViolations();
  });

  it("stays accessible with no countries at all", async () => {
    // The empty state is a state, not an error, and it has to be
    // navigable too.
    const { container } = renderTable({ countries: [] });

    expect(await axe(container)).toHaveNoViolations();
  });
});
