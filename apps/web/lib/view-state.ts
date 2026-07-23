/**
 * URL view-state codec.
 *
 * Requirement 11.2 is the demanding one: two states differing only in
 * set-member ordering or omitted defaults must serialize to the *same*
 * URL. That forces a canonical form — sorted sets, omitted defaults, fixed
 * parameter order — rather than simply reflecting whatever the user
 * clicked. Without it, two people exploring identically would produce
 * different links, and Requirement 11.6's round-trip identity would fail.
 *
 * Requirement refs: 9.6, 9.10-9.12, 10.8-10.10, 11.1-11.10
 */

import { UNKNOWN_COUNTRY } from "@creator-map/shared-schemas";

/** Bumped when the grammar changes incompatibly. */
export const VIEW_STATE_VERSION = "1";

export type CreatorSortOrder = "representedVideoCountDesc" | "displayNameAsc";

export const SORT_ORDERS: readonly CreatorSortOrder[] = [
  "representedVideoCountDesc",
  "displayNameAsc",
];

/** The default sort, omitted from a canonical URL. */
export const DEFAULT_SORT: CreatorSortOrder = "representedVideoCountDesc";

export type MetricKey = "creators" | "videos" | "occurrences";

export const METRICS: readonly MetricKey[] = [
  "creators",
  "videos",
  "occurrences",
];

/** The default metric, omitted from a canonical URL. */
export const DEFAULT_METRIC: MetricKey = "creators";

export type ViewMode = "map" | "table";

export const DEFAULT_VIEW: ViewMode = "map";

export interface ViewState {
  /** Release id, or null to follow the active pointer. */
  readonly release: string | null;
  /** Selected dataset ids. Empty means the release default filter. */
  readonly datasets: readonly string[];
  readonly corpusClasses: readonly string[];
  /** Selected country, the Unknown bucket, or null for the overview. */
  readonly country: string | null;
  readonly metric: MetricKey;
  readonly view: ViewMode;
  readonly sort: CreatorSortOrder;
  readonly cursor: string | null;
}

export const EMPTY_VIEW_STATE: ViewState = {
  release: null,
  datasets: [],
  corpusClasses: [],
  country: null,
  metric: DEFAULT_METRIC,
  view: DEFAULT_VIEW,
  sort: DEFAULT_SORT,
  cursor: null,
};

/** One component a fallback had to correct (Requirement 11.9). */
export interface Correction {
  readonly field: keyof ViewState;
  readonly rejected: string;
  readonly reason: string;
}

export interface ParseResult {
  readonly state: ViewState;
  /** Non-empty when the URL contained values that had to be discarded. */
  readonly corrections: readonly Correction[];
  /** Whether the result is the documented fallback rather than the request. */
  readonly usedFallback: boolean;
}

export interface ParseOptions {
  /** Dataset ids the release actually contains. */
  readonly knownDatasets?: readonly string[];
  readonly knownCorpusClasses?: readonly string[];
  /** Country buckets the release actually contains. */
  readonly knownCountries?: readonly string[];
  readonly knownReleases?: readonly string[];
}

const COUNTRY_PATTERN = /^[A-Z]{2}$/;
const CURSOR_PATTERN = /^[A-Za-z0-9_-]{1,512}$/;
const RELEASE_PATTERN = /^[A-Za-z0-9:_.-]{1,64}$/;
const DATASET_PATTERN = /^[a-z0-9][a-z0-9._-]{0,63}$/;

/** Sort and deduplicate, so equivalent selections share one form. */
function canonicalSet(values: readonly string[]): string[] {
  return [...new Set(values)].sort();
}

/**
 * Serialize a view state as its canonical URL.
 *
 * Parameters appear in a fixed order and defaults are omitted, so the same
 * state always produces byte-identical output (Requirements 11.1, 11.2).
 */
export function serialize(state: ViewState, basePath = "/"): string {
  const params = new URLSearchParams();

  // Fixed emission order. URLSearchParams preserves insertion order, so
  // this sequence *is* the canonical parameter order.
  if (state.release) params.set("release", state.release);

  const datasets = canonicalSet(state.datasets);
  if (datasets.length > 0) params.set("datasets", datasets.join(","));

  const classes = canonicalSet(state.corpusClasses);
  if (classes.length > 0) params.set("corpus", classes.join(","));

  if (state.country) params.set("country", state.country);
  if (state.metric !== DEFAULT_METRIC) params.set("metric", state.metric);
  if (state.view !== DEFAULT_VIEW) params.set("view", state.view);

  // Sort and cursor are meaningless without a country selection, so they
  // are omitted from the overview rather than carried as dead state.
  if (state.country) {
    if (state.sort !== DEFAULT_SORT) params.set("sort", state.sort);
    if (state.cursor) params.set("cursor", state.cursor);
  }

  // URLSearchParams percent-encodes the comma as %2C. That is valid but
  // unreadable, and these URLs are meant to be shared and recognised by
  // people. A comma is a sub-delimiter that RFC 3986 permits unencoded in
  // a query, so restoring it is both legal and stable — decode only the
  // separator, never arbitrary content.
  const query = params.toString().replaceAll("%2C", ",");
  return query ? `${basePath}?${query}` : basePath;
}

function parseList(raw: string | null): string[] {
  if (!raw) return [];
  return raw
    .split(",")
    .map((value) => value.trim())
    .filter((value) => value.length > 0);
}

/**
 * Parse a URL into exactly one view state.
 *
 * Requirement 11.7: an unsupported value yields a recoverable result and a
 * documented fallback, never an exception and never a silent acceptance.
 * Each discarded value is reported so the application can tell the visitor
 * what it corrected (Requirement 11.9).
 */
export function parse(url: string, options: ParseOptions = {}): ParseResult {
  const corrections: Correction[] = [];
  const query = url.includes("?") ? url.slice(url.indexOf("?") + 1) : "";
  const params = new URLSearchParams(query);

  const reject = (field: keyof ViewState, rejected: string, reason: string) => {
    corrections.push({ field, rejected, reason });
  };

  // -- release ---------------------------------------------------------
  let release: string | null = params.get("release");
  if (release !== null) {
    if (!RELEASE_PATTERN.test(release)) {
      reject("release", release, "not a valid release identifier");
      release = null;
    } else if (
      options.knownReleases &&
      !options.knownReleases.includes(release)
    ) {
      reject("release", release, "release is not available");
      release = null;
    }
  }

  // -- datasets --------------------------------------------------------
  const requestedDatasets = parseList(params.get("datasets"));
  const datasets: string[] = [];
  for (const dataset of requestedDatasets) {
    if (!DATASET_PATTERN.test(dataset)) {
      reject("datasets", dataset, "not a valid dataset identifier");
      continue;
    }
    if (options.knownDatasets && !options.knownDatasets.includes(dataset)) {
      reject("datasets", dataset, "dataset is not in this release");
      continue;
    }
    datasets.push(dataset);
  }

  // -- corpus classes --------------------------------------------------
  const requestedClasses = parseList(params.get("corpus"));
  const corpusClasses: string[] = [];
  for (const value of requestedClasses) {
    if (
      options.knownCorpusClasses &&
      !options.knownCorpusClasses.includes(value)
    ) {
      reject("corpusClasses", value, "unknown corpus class");
      continue;
    }
    if (
      !options.knownCorpusClasses &&
      value !== "Candidate" &&
      value !== "Comparison"
    ) {
      reject("corpusClasses", value, "unknown corpus class");
      continue;
    }
    corpusClasses.push(value);
  }

  // -- country ---------------------------------------------------------
  let country: string | null = params.get("country");
  if (country !== null) {
    const upper = country.toUpperCase();
    if (upper !== UNKNOWN_COUNTRY && !COUNTRY_PATTERN.test(upper)) {
      reject("country", country, "not an ISO 3166 alpha-2 code");
      country = null;
    } else if (
      options.knownCountries &&
      !options.knownCountries.includes(upper)
    ) {
      reject("country", country, "country has no data in this release");
      country = null;
    } else {
      country = upper;
    }
  }

  // -- metric ----------------------------------------------------------
  let metric: MetricKey = DEFAULT_METRIC;
  const rawMetric = params.get("metric");
  if (rawMetric !== null) {
    if ((METRICS as readonly string[]).includes(rawMetric)) {
      metric = rawMetric as MetricKey;
    } else {
      reject("metric", rawMetric, "unknown metric");
    }
  }

  // -- view ------------------------------------------------------------
  let view: ViewMode = DEFAULT_VIEW;
  const rawView = params.get("view");
  if (rawView !== null) {
    if (rawView === "map" || rawView === "table") {
      view = rawView;
    } else {
      reject("view", rawView, "unknown view mode");
    }
  }

  // -- sort ------------------------------------------------------------
  let sort: CreatorSortOrder = DEFAULT_SORT;
  const rawSort = params.get("sort");
  if (rawSort !== null) {
    if ((SORT_ORDERS as readonly string[]).includes(rawSort)) {
      sort = rawSort as CreatorSortOrder;
    } else {
      reject("sort", rawSort, "unknown sort order");
    }
  }

  // -- cursor ----------------------------------------------------------
  let cursor: string | null = params.get("cursor");
  if (cursor !== null && !CURSOR_PATTERN.test(cursor)) {
    reject("cursor", cursor, "not a valid page cursor");
    cursor = null;
  }

  // A cursor without a country selects nothing: Requirement 11.9 excludes
  // invalid values from data requests rather than carrying them forward.
  if (cursor !== null && country === null) {
    reject("cursor", cursor, "a page cursor requires a country selection");
    cursor = null;
  }

  return {
    state: {
      release,
      datasets: canonicalSet(datasets),
      corpusClasses: canonicalSet(corpusClasses),
      country,
      metric,
      view,
      sort,
      cursor,
    },
    corrections,
    usedFallback: corrections.length > 0,
  };
}

/** Parse and re-serialize, yielding the canonical URL for any input. */
export function canonicalize(url: string, options: ParseOptions = {}): string {
  const basePath = url.includes("?") ? url.slice(0, url.indexOf("?")) : url;
  return serialize(parse(url, options).state, basePath || "/");
}

/** Whether two states would produce the same canonical URL. */
export function equivalent(left: ViewState, right: ViewState): boolean {
  return serialize(left) === serialize(right);
}

/**
 * Apply a state change, resetting the components it invalidates.
 *
 * Changing the filter or country invalidates the page cursor: a cursor
 * names a position in one specific ordered result, so carrying it across a
 * change would silently skip rows (Requirement 10.6).
 */
export function withChange(
  state: ViewState,
  change: Partial<ViewState>,
): ViewState {
  const next = { ...state, ...change };

  const filterChanged =
    change.datasets !== undefined || change.corpusClasses !== undefined;
  const countryChanged =
    change.country !== undefined && change.country !== state.country;
  const sortChanged = change.sort !== undefined && change.sort !== state.sort;

  if (filterChanged || countryChanged || sortChanged) {
    return { ...next, cursor: change.cursor ?? null };
  }
  return next;
}
