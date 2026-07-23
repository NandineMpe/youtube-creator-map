/**
 * Number and label formatting for public display.
 *
 * Counts are always rendered in full rather than abbreviated. "49,363" and
 * "49.4K" are not equivalent here: the whole point of the represented-video
 * count is that it is an exact distinct-set cardinality, and rounding it in
 * the presentation layer would quietly discard the precision the pipeline
 * works to preserve (Requirement 5.12).
 *
 * Requirement refs: 5.12, 9.4, 12.3, 12.4, 13.1
 */

import { UNKNOWN_COUNTRY } from "@creator-map/shared-schemas";

/** Format an exact count with thousands separators. */
export function formatCount(value: number): string {
  return new Intl.NumberFormat("en", { useGrouping: true }).format(value);
}

/**
 * Format a percentage for coverage display.
 *
 * Rounded to one decimal, but never to 0% or 100% unless the underlying
 * value is exactly that. A coverage figure of 99.97% shown as "100%" would
 * tell a visitor the data is complete when it is not.
 */
export function formatPercent(part: number, whole: number): string {
  if (whole === 0) return "—";
  const ratio = (part / whole) * 100;

  if (ratio > 0 && ratio < 0.1) return "<0.1%";
  if (ratio < 100 && ratio > 99.9) return ">99.9%";
  return `${ratio.toFixed(1)}%`;
}

/** Render an ISO instant as a readable UTC date and time. */
export function formatInstant(iso: string): string {
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return iso;
  return new Intl.DateTimeFormat("en", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "UTC",
  }).format(parsed);
}

/** Render an ISO date without a time component. */
export function formatDate(iso: string): string {
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return iso;
  return new Intl.DateTimeFormat("en", {
    dateStyle: "medium",
    timeZone: "UTC",
  }).format(parsed);
}

/**
 * The display name for a country bucket.
 *
 * The Unknown bucket gets a phrase rather than a code, because "XX" means
 * nothing to a visitor and the distinction it marks — resolved channel,
 * no declared country — is exactly what Requirement 6.8 wants surfaced.
 */
export function countryLabel(code: string): string {
  if (code === UNKNOWN_COUNTRY) return "Unknown country";

  const display = new Intl.DisplayNames(["en"], { type: "region" });
  try {
    return display.of(code) ?? code;
  } catch {
    // An unrecognised code is shown as-is rather than coerced to a nearby
    // country (Requirement 5.7 forbids guessing).
    return code;
  }
}

/** Whether a bucket is the Unknown bucket rather than a geography. */
export function isUnknownBucket(code: string): boolean {
  return code === UNKNOWN_COUNTRY;
}

/** Human-readable labels for the resolution partition states. */
export const PARTITION_LABELS: Record<string, string> = {
  resolved: "Resolved",
  unavailableUnclassified: "Unavailable",
  retryableOrPending: "Pending",
  invalid: "Invalid identifier",
  terminalFailure: "Could not resolve",
};

/**
 * Explanations for each partition state.
 *
 * "Unavailable" is the one that needs care: the metadata API does not say
 * why an identifier did not resolve, so Requirement 6.9 forbids labelling
 * it deleted or private. The copy has to convey "we don't know" without
 * sounding evasive.
 */
export const PARTITION_DESCRIPTIONS: Record<string, string> = {
  resolved: "The identifier resolved to a channel.",
  unavailableUnclassified:
    "The metadata API did not return this identifier and did not say why. " +
    "It may have been removed, made private, or never existed — the " +
    "response does not distinguish those, so neither do we.",
  retryableOrPending:
    "Not yet resolved at this release's cutoff. It may resolve in a later " +
    "release.",
  invalid: "The identifier did not match the supported identifier grammar.",
  terminalFailure:
    "Resolution was attempted and failed permanently under the retry policy.",
};

export type MetricKey = "creators" | "videos" | "occurrences";

export interface MetricDefinition {
  readonly key: MetricKey;
  readonly label: string;
  readonly unit: string;
  /** Shown wherever the metric is displayed (Requirement 12.8). */
  readonly definition: string;
}

/** The metrics a visitor can colour the map by. */
export const METRIC_DEFINITIONS: readonly MetricDefinition[] = [
  {
    key: "creators",
    label: "Creators",
    unit: "distinct channels",
    definition:
      "Distinct channels that videos in the active filter resolved to.",
  },
  {
    key: "videos",
    label: "Represented videos",
    unit: "distinct video identifiers",
    definition:
      "Distinct source-video identifiers within the active filter. A video " +
      "counts once however many times it appears.",
  },
  {
    key: "occurrences",
    label: "Source occurrences",
    unit: "retained source records",
    definition:
      "Retained source rows, clips, timestamps, or segments. Normally " +
      "larger than the represented-video count, because source material " +
      "often references the same video more than once.",
  },
];

export function metricDefinition(key: MetricKey): MetricDefinition {
  const found = METRIC_DEFINITIONS.find((m) => m.key === key);
  if (!found) {
    throw new Error(`unknown metric: ${key}`);
  }
  return found;
}

/** Read the selected metric from a country summary. */
export function metricValue(
  summary: {
    creatorCount: number;
    representedVideoCount: number;
    sourceOccurrenceCount: number;
  },
  metric: MetricKey,
): number {
  switch (metric) {
    case "creators":
      return summary.creatorCount;
    case "videos":
      return summary.representedVideoCount;
    case "occurrences":
      return summary.sourceOccurrenceCount;
  }
}
