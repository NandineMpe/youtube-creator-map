import { describe, expect, it } from "vitest";

import * as methodology from "./methodology";

/**
 * The methodology copy carries the qualifications that keep the map's
 * numbers from reading as accusations. It is prose, so nothing else in
 * the suite touches it — and prose is exactly what gets trimmed in a
 * redesign for being long.
 *
 * These assert meaning, not wording: each checks that a specific
 * qualification is present somewhere in the copy, so it can be rewritten
 * but not dropped.
 *
 * Requirement refs: 12.1-12.8, 12.11
 */

/** Every string the module exports, flattened. */
function allCopy(): string {
  const seen: string[] = [];
  const walk = (node: unknown): void => {
    if (typeof node === "string") {
      seen.push(node);
    } else if (Array.isArray(node)) {
      node.forEach(walk);
    } else if (node && typeof node === "object") {
      Object.values(node).forEach(walk);
    }
  };
  walk(methodology);
  return seen.join(" ").toLowerCase();
}

describe("the methodology copy", () => {
  const copy = allCopy();

  it("is not empty", () => {
    // A guard on the guard: if the module's shape changes so that
    // `allCopy` collects nothing, every assertion below would pass
    // vacuously.
    expect(copy.length).toBeGreaterThan(500);
  });

  // --- Requirement 12.4: country is declared metadata --------------------

  it("says country is what a channel declared, not where anyone is", () => {
    expect(copy).toContain("declared");
    expect(copy).toMatch(/self-declared|declared in its own|channel declared/);
  });

  it("explains what the Unknown bucket contains", () => {
    // Requirement 6.8: Unknown is a real category. Left unexplained it
    // reads as missing data, which invites subtracting it out.
    expect(copy).toMatch(/no supported country|missing for many|declared no/);
  });

  // --- Requirement 12.5: what the data does not establish ----------------

  it("disclaims training", () => {
    expect(copy).toMatch(/was trained on|trained on the video/);
    expect(copy).toMatch(/does not|not establish/);
  });

  it("disclaims legality", () => {
    expect(copy).toMatch(/lawful|unlawful|legal/);
  });

  it("disclaims consent", () => {
    // "agree" rather than "consent": the copy says "the creator did or
    // did not agree to it", which is the same qualification in plainer
    // words. Matching only the legal term would fail on better writing.
    expect(copy).toMatch(/consent|agree/);
  });

  // --- Requirement 12.7: counting units are distinguished ----------------

  it("distinguishes the three counting units", () => {
    // Source occurrences, represented videos, and creators are different
    // numbers, and a reader who conflates them will conclude the totals
    // do not add up.
    expect(copy).toMatch(/occurrence/);
    expect(copy).toMatch(/distinct/);
    expect(copy).toMatch(/channel|creator/);
  });

  it("explains why per-dataset counts do not sum", () => {
    // The non-additivity is real: ~8,800 channels appear in both
    // datasets in the live corpus, so summing overstates by ~18%.
    // Unexplained, it looks like an arithmetic error.
    expect(copy).toMatch(/more than one|both|overlap|repeat|same video/);
  });

  // --- Requirement 12.11: limits are stated, not implied -----------------

  it("states that coverage is partial", () => {
    expect(copy).toMatch(
      /not (?:a )?complete|partial|only the datasets|subset/,
    );
  });

  // --- Neutral language --------------------------------------------------

  it("uses observational verbs rather than accusatory ones", () => {
    for (const accusation of [
      "stolen",
      "theft",
      "pirated",
      "infringing",
      "infringement",
    ]) {
      expect(copy).not.toContain(accusation);
    }
  });
});
