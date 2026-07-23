"use client";

import type { CountrySummary } from "@creator-map/shared-schemas";
import { useMemo, useState } from "react";

import type { BinScale } from "../lib/bins";
import { binFor } from "../lib/bins";
import {
  countryLabel,
  formatCount,
  isUnknownBucket,
  metricDefinition,
  metricValue,
  type MetricKey,
} from "../lib/format";

/**
 * The authoritative country table.
 *
 * This is not a fallback for the map — Requirement 9.9 makes it the
 * keyboard and screen-reader equivalent, and Requirement 9.3 requires both
 * to present identical values from the same records. Building the table
 * from the same `CountrySummary` array the map renders is what makes that
 * true by construction rather than by careful synchronisation.
 *
 * Every datum the map shows on hover is present here as text, which is how
 * Requirement 13.8 (no information available only on hover) is satisfied.
 *
 * Requirement refs: 6.8, 9.2, 9.3, 9.5, 9.9, 13.4, 13.8
 */

type SortColumn = "country" | "creators" | "videos" | "occurrences";
type SortDirection = "asc" | "desc";

export interface CountryTableProps {
  readonly countries: readonly CountrySummary[];
  readonly metric: MetricKey;
  readonly scale: BinScale;
  readonly selectedCountry: string | null;
  readonly onSelect: (country: string) => void;
}

function compare(
  a: CountrySummary,
  b: CountrySummary,
  column: SortColumn,
): number {
  switch (column) {
    case "country":
      return countryLabel(a.country).localeCompare(countryLabel(b.country));
    case "creators":
      return a.creatorCount - b.creatorCount;
    case "videos":
      return a.representedVideoCount - b.representedVideoCount;
    case "occurrences":
      return a.sourceOccurrenceCount - b.sourceOccurrenceCount;
  }
}

export function CountryTable({
  countries,
  metric,
  scale,
  selectedCountry,
  onSelect,
}: CountryTableProps) {
  const [column, setColumn] = useState<SortColumn>("creators");
  const [direction, setDirection] = useState<SortDirection>("desc");

  const sorted = useMemo(() => {
    const rows = [...countries];
    rows.sort((a, b) => {
      const primary = compare(a, b, column);
      if (primary !== 0) return direction === "asc" ? primary : -primary;
      // Country code as the tie-breaker, so the order is total and a
      // re-sort never reshuffles equal rows.
      return a.country.localeCompare(b.country);
    });
    return rows;
  }, [countries, column, direction]);

  const toggleSort = (next: SortColumn) => {
    if (next === column) {
      setDirection(direction === "asc" ? "desc" : "asc");
    } else {
      setColumn(next);
      setDirection(next === "country" ? "asc" : "desc");
    }
  };

  const ariaSort = (target: SortColumn) =>
    column === target
      ? direction === "asc"
        ? ("ascending" as const)
        : ("descending" as const)
      : ("none" as const);

  const definition = metricDefinition(metric);

  if (countries.length === 0) {
    return (
      <section aria-labelledby="country-table-heading">
        <h2 id="country-table-heading">Countries</h2>
        {/* Requirement 9.7: an empty filter is a valid state, presented
            distinctly from a delivery failure. */}
        <p role="status">
          No countries match the current filter. This is a complete result, not
          a loading or delivery problem — try selecting more datasets.
        </p>
      </section>
    );
  }

  return (
    <section aria-labelledby="country-table-heading">
      <h2 id="country-table-heading">Countries</h2>
      <p>
        Sorted by {definition.label.toLowerCase()} ({definition.unit}). Select a
        row to open that country&apos;s detail.
      </p>

      <div className="scroll-x">
        <table className="data-table">
          <caption className="visually-hidden">
            Countries with their creator, represented-video, and source
            occurrence counts. This table carries the same values as the map.
          </caption>
          <thead>
            <tr>
              <th scope="col" aria-sort={ariaSort("country")}>
                <button type="button" onClick={() => toggleSort("country")}>
                  Country
                </button>
              </th>
              <th scope="col">Scale</th>
              <th
                scope="col"
                className="numeric"
                aria-sort={ariaSort("creators")}
              >
                <button type="button" onClick={() => toggleSort("creators")}>
                  Creators
                </button>
              </th>
              <th
                scope="col"
                className="numeric"
                aria-sort={ariaSort("videos")}
              >
                <button type="button" onClick={() => toggleSort("videos")}>
                  Represented videos
                </button>
              </th>
              <th
                scope="col"
                className="numeric"
                aria-sort={ariaSort("occurrences")}
              >
                <button type="button" onClick={() => toggleSort("occurrences")}>
                  Source occurrences
                </button>
              </th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((summary) => {
              const value = metricValue(summary, metric);
              const bin = binFor(value, scale);
              const isSelected = summary.country === selectedCountry;

              return (
                <tr
                  key={summary.country}
                  aria-selected={isSelected}
                  className={isSelected ? "is-selected" : undefined}
                >
                  <th scope="row">
                    <button
                      type="button"
                      onClick={() => onSelect(summary.country)}
                      aria-current={isSelected ? "true" : undefined}
                    >
                      {countryLabel(summary.country)}
                    </button>
                    {isUnknownBucket(summary.country) && (
                      // Requirement 6.8: Unknown is shown, but marked as
                      // outside the geography rather than as a country.
                      <span className="badge"> not on map</span>
                    )}
                  </th>
                  <td>
                    {/* Requirement 9.5: the colour encoding is also
                        available as text, so the scale position survives
                        greyscale and colour vision deficiency. */}
                    <span className="bin-swatch" aria-hidden="true" />
                    <span>{bin ? bin.label : "No data"}</span>
                  </td>
                  <td className="numeric">
                    {formatCount(summary.creatorCount)}
                  </td>
                  <td className="numeric">
                    {formatCount(summary.representedVideoCount)}
                  </td>
                  <td className="numeric">
                    {formatCount(summary.sourceOccurrenceCount)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
