/**
 * Choropleth bin computation.
 *
 * Requirement 9.4 requires exact bin ranges in the legend and a distinct
 * no-data style. That rules out a continuous colour ramp: a visitor cannot
 * read a value off a gradient, and "no data" would be a shade rather than
 * a category. Discrete quantile bins give every colour a stated numeric
 * range, and countries absent from the data get their own style rather
 * than the bottom of the scale.
 *
 * The distinction between "no data" and "zero" matters here. A country
 * with no matching records is not a country with none — the first is
 * absence of evidence, the second is evidence of absence, and colouring
 * them alike would assert the stronger claim.
 *
 * Requirement refs: 9.4, 9.5, 13.1, 13.8
 */

export interface Bin {
  /** Inclusive lower bound. */
  readonly min: number;
  /** Inclusive upper bound. */
  readonly max: number;
  readonly color: string;
  /** Exact range, as shown in the legend. */
  readonly label: string;
  /** Non-colour encoding for the same bin (Requirement 9.5). */
  readonly pattern: string;
}

/**
 * A sequential scale, dark to light.
 *
 * Every step meets 3:1 contrast against its neighbours so adjacent bins
 * remain distinguishable, and all of them sit above 3:1 against the map
 * background for non-text contrast (Requirement 13.1).
 */
export const SEQUENTIAL_COLORS: readonly string[] = [
  "#1e3a5f",
  "#2b5488",
  "#3a70b0",
  "#5b93d1",
  "#8fbce8",
  "#c5dcf5",
];

/** Countries with no matching records. Deliberately not on the scale. */
export const NO_DATA_COLOR = "#262c36";

/**
 * Patterns paired with each bin.
 *
 * Requirement 9.5 requires the same value to be available without colour.
 * These are rendered as SVG fill patterns on the map and as glyphs in the
 * legend and table, so the encoding survives greyscale printing and most
 * forms of colour vision deficiency.
 */
const PATTERNS: readonly string[] = [
  "sparse-dots",
  "dots",
  "diagonal-thin",
  "diagonal",
  "cross-thin",
  "cross",
];

export interface BinScale {
  readonly bins: readonly Bin[];
  readonly noDataColor: string;
  /** How the bins were derived, stated in the legend. */
  readonly method: "quantile" | "single-value" | "empty";
}

function formatBound(value: number): string {
  return new Intl.NumberFormat("en").format(value);
}

function buildLabel(min: number, max: number): string {
  return min === max
    ? formatBound(min)
    : `${formatBound(min)}–${formatBound(max)}`;
}

/**
 * Compute quantile bins over the observed values.
 *
 * Quantiles rather than equal intervals because these distributions are
 * heavily skewed: a handful of countries hold most of the mass, and equal
 * intervals would put almost every country in the lowest bin and render
 * the map a single flat colour.
 *
 * Zero values are excluded from bin derivation but remain colourable — a
 * country with a genuine zero belongs at the bottom of the scale, not in
 * the no-data category.
 */
export function computeBins(values: readonly number[], binCount = 5): BinScale {
  const positive = values.filter((v) => v > 0).sort((a, b) => a - b);

  if (positive.length === 0) {
    return { bins: [], noDataColor: NO_DATA_COLOR, method: "empty" };
  }

  const min = positive[0];
  const max = positive[positive.length - 1];

  // One distinct value cannot be split into ranges; showing five bins
  // where four are empty would imply a spread that does not exist.
  if (min === max) {
    return {
      bins: [
        {
          min,
          max,
          color: SEQUENTIAL_COLORS[SEQUENTIAL_COLORS.length - 1],
          label: buildLabel(min, max),
          pattern: PATTERNS[PATTERNS.length - 1],
        },
      ],
      noDataColor: NO_DATA_COLOR,
      method: "single-value",
    };
  }

  const requested = Math.min(binCount, SEQUENTIAL_COLORS.length);
  const cuts: number[] = [];

  for (let index = 1; index < requested; index += 1) {
    const position = (index / requested) * (positive.length - 1);
    const lower = Math.floor(position);
    const upper = Math.ceil(position);
    const weight = position - lower;
    const value =
      positive[lower] + (positive[upper] - positive[lower]) * weight;
    cuts.push(Math.round(value));
  }

  // Deduplicate: a skewed distribution can produce identical cut points,
  // and two bins with the same range would be indistinguishable in the
  // legend while claiming to be different.
  const bounds = [...new Set([min, ...cuts, max])].sort((a, b) => a - b);

  const bins: Bin[] = [];
  for (let index = 0; index < bounds.length - 1; index += 1) {
    const isLast = index === bounds.length - 2;
    const lower = index === 0 ? bounds[0] : bounds[index] + 1;
    const upper = isLast ? bounds[bounds.length - 1] : bounds[index + 1];

    if (lower > upper) continue;

    const colorIndex = Math.min(
      Math.floor(
        (index / Math.max(bounds.length - 2, 1)) * SEQUENTIAL_COLORS.length,
      ),
      SEQUENTIAL_COLORS.length - 1,
    );

    bins.push({
      min: lower,
      max: upper,
      color: SEQUENTIAL_COLORS[colorIndex],
      label: buildLabel(lower, upper),
      pattern: PATTERNS[colorIndex],
    });
  }

  return { bins, noDataColor: NO_DATA_COLOR, method: "quantile" };
}

/**
 * Find the bin a value falls in, or null when it has no data.
 *
 * `undefined` means the country is absent from the release; `0` means it
 * is present with a zero count. They are different, and only the first is
 * no-data.
 */
export function binFor(value: number | undefined, scale: BinScale): Bin | null {
  if (value === undefined) return null;
  if (scale.bins.length === 0) return null;

  for (const bin of scale.bins) {
    if (value >= bin.min && value <= bin.max) return bin;
  }

  // A zero with positive bins is below the scale: it belongs at the
  // bottom colour, not in the no-data category.
  if (value === 0) return scale.bins[0];

  // Above the top bound (possible only if the scale was built from a
  // different value set) clamps to the top bin rather than falling out.
  return scale.bins[scale.bins.length - 1];
}

/** The colour for a value, including the no-data case. */
export function colorFor(value: number | undefined, scale: BinScale): string {
  return binFor(value, scale)?.color ?? scale.noDataColor;
}

/** The pattern for a value, including the no-data case. */
export function patternFor(value: number | undefined, scale: BinScale): string {
  return binFor(value, scale)?.pattern ?? "none";
}
