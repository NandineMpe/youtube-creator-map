/**
 * Approved public copy.
 *
 * Every string here is subject to the neutral-language gate: Requirement
 * 12.5 forbids claiming confirmed training, infringement, illegality,
 * consent status, residence, or nationality. The wording is deliberately
 * observational throughout — "observed in", "declared at", "reported as" —
 * because the system's entire epistemic claim is that an identifier
 * appeared in a named snapshot and a channel declared a country at a
 * particular time.
 *
 * Requirement refs: 12.1-12.11
 */

export const METHODOLOGY_VERSION = "1.0.0";

/** What dataset membership does and does not mean (Requirement 12.1). */
export const MEMBERSHIP_STATEMENT =
  "A video appears here because its identifier was observed in a named, " +
  "versioned snapshot of dataset source material. That observation does not " +
  "establish that any model was trained on the video, that any use was " +
  "lawful or unlawful, or that the creator did or did not agree to it.";

/** What country means (Requirement 12.2, 12.11). */
export const COUNTRY_STATEMENT =
  "Country is the value a channel declared in its own YouTube metadata, read " +
  "at the enrichment cutoff shown with each release. It is not a statement " +
  "about where a creator lives or what passport they hold, and a channel may " +
  "change or remove it at any time.";

/** The counting units (Requirement 12.3, 12.4). */
export const COUNT_DEFINITIONS = [
  {
    term: "Represented videos",
    definition:
      "Distinct source-video identifiers within the active filter. A video " +
      "counts once however many times it appears.",
  },
  {
    term: "Source occurrences",
    definition:
      "Retained source rows, clips, timestamps, or segments. This is a " +
      "secondary count and is normally larger than the represented-video " +
      "count, because source material often references the same video more " +
      "than once.",
  },
  {
    term: "Creators",
    definition:
      "Distinct channels that videos in the active filter resolved to. A " +
      "channel counts once regardless of how many of its videos appear.",
  },
  {
    term: "Unknown country",
    definition:
      "Channels that resolved successfully but declared no supported country. " +
      "They are counted and shown, but they are not placed on the map.",
  },
] as const;

/** Why dataset subtotals do not add up (Requirement 12.7). */
export const OVERLAP_STATEMENT =
  "Datasets overlap. The same video can appear in several of them, so it " +
  "counts once in a combined total but once in each dataset it belongs to. " +
  "Adding the per-dataset numbers together will therefore overshoot the " +
  "combined figure.";

/** Boundary provenance (Requirement 12.10, 12.11). */
export const BOUNDARY_STATEMENT =
  "Country shapes come from Natural Earth Admin 0 (public domain). Borders " +
  "and names are a presentation convention chosen for legibility. They are " +
  "not a position on any territorial question, and they are not evidence of " +
  "where a channel or its operator is located.";

/** Comparison corpora (Requirement 12.6). */
export const COMPARISON_STATEMENT =
  "Some corpora are labelled comparison rather than candidate. That label " +
  "reflects their own documented provenance and licence terms. It is not a " +
  "judgement about any other corpus, and it does not imply that material " +
  "elsewhere was gathered improperly.";

/** Coverage (Requirement 6.7-6.10). */
export const COVERAGE_STATEMENT =
  "Not every observed identifier resolves. Some are unavailable through the " +
  "metadata API, and the API does not say why — so those are reported as " +
  "unavailable rather than described as deleted or private, which would be " +
  "a guess. Counts of every resolution state are published alongside the " +
  "headline totals.";

/** The correction and opt-out path (Requirement 7.9). */
export const CORRECTION_PATH = {
  heading: "Corrections and removal",
  body:
    "If you are a creator and want an entry corrected or removed, or you " +
    "believe something here is wrong, the review path below applies. " +
    "Requests are handled under the published disclosure policy, and " +
    "removals take effect from the next release onward.",
  contactLabel: "Correction and opt-out requests",
  // Deliberately a route rather than an address: Requirement 7.3 keeps
  // contact fields out of generated artifacts, and a mailto here would be
  // a contact field on every page.
  href: "/methodology#corrections",
} as const;

/** Limitations a reader needs in order to interpret the map (12.9). */
export const LIMITATIONS = [
  "Membership means an identifier was observed in a snapshot, not that a " +
    "model was trained on it.",
  "Country is self-declared channel metadata and is missing for many " +
    "channels.",
  "Coverage is partial: identifiers that do not resolve are reported, not " +
    "hidden, but they are also not attributed to any country.",
  "Dataset subtotals overlap and are not additive.",
  "Every figure describes one release at one enrichment cutoff. Later " +
    "observations do not change a published release.",
] as const;

export interface CountDefinition {
  readonly term: string;
  readonly definition: string;
}
