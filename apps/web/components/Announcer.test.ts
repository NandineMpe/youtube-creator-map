import { describe, expect, it } from "vitest";

import { describeFilterChange } from "./Announcer";

/**
 * What a screen reader is told when the filter changes.
 *
 * Requirement 13.3 names four things the announcement must convey: the
 * resulting filter, loading status, completion status, and the summary
 * update. These tests pin the phrasing of the summary half, because an
 * announcement that omits the counts leaves a screen-reader user knowing
 * something changed but not what it changed to.
 *
 * Requirement refs: 13.3, 13.9
 */

describe("describeFilterChange", () => {
  it("names the filter and the resulting counts", () => {
    const message = describeFilterChange({
      datasetLabel: "all 2 datasets",
      creators: 49061,
      videos: 97400,
      countries: 1,
      exact: true,
    });

    expect(message).toContain("all 2 datasets");
    expect(message).toContain("49,061 creators");
    expect(message).toContain("97,400 represented videos");
  });

  it("groups digits, because unseparated figures are misread aloud", () => {
    const message = describeFilterChange({
      datasetLabel: "one dataset",
      creators: 1234567,
      videos: 1,
      countries: 1,
      exact: true,
    });

    expect(message).toContain("1,234,567");
    expect(message).not.toContain("1234567");
  });

  it("uses singular and plural correctly", () => {
    const one = describeFilterChange({
      datasetLabel: "d",
      creators: 1,
      videos: 1,
      countries: 1,
      exact: true,
    });
    const many = describeFilterChange({
      datasetLabel: "d",
      creators: 1,
      videos: 1,
      countries: 5,
      exact: true,
    });

    expect(one).toContain("1 country bucket.");
    expect(many).toContain("5 country buckets.");
  });

  it("says so when the figures are not the requested combination", () => {
    // Requirement 5.12: a fallback must be stated, not implied by numbers
    // that quietly disagree with the controls.
    const message = describeFilterChange({
      datasetLabel: "one dataset",
      creators: 10,
      videos: 20,
      countries: 2,
      exact: false,
    });

    expect(message).toContain("does not publish that exact combination");
  });

  it("says nothing about fallbacks when the match is exact", () => {
    const message = describeFilterChange({
      datasetLabel: "one dataset",
      creators: 10,
      videos: 20,
      countries: 2,
      exact: true,
    });

    expect(message).not.toContain("exact combination");
    expect(message).not.toContain("full release");
  });

  it("reads as sentences rather than a field dump", () => {
    // A screen reader speaks this; "creators: 5, videos: 9" is a table
    // read aloud, not a sentence.
    const message = describeFilterChange({
      datasetLabel: "all 2 datasets",
      creators: 5,
      videos: 9,
      countries: 3,
      exact: true,
    });

    expect(message).not.toContain(":");
    expect(message.endsWith(".")).toBe(true);
  });
});
