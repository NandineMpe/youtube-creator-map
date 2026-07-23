import fc from "fast-check";
import { describe, expect, it } from "vitest";

import {
  DisclosureViolationError,
  assertNoProhibitedContent,
  findProhibitedContent,
} from "./disclosure-guard";

/** A clean artifact shaped like a real overview payload. */
function cleanArtifact() {
  return {
    schemaVersion: "1.0.0",
    releaseId: "2026-01-15T12-00-00Z",
    countries: [
      {
        country: "DE",
        creatorCount: 3,
        representedVideoCount: 9,
      },
    ],
    creators: [
      {
        publicChannelKey: "pk_a1b2c3d4e5",
        displayName: "Example Channel",
        country: "DE",
        representedVideoCount: 12,
        lastObservedAt: "2026-01-15",
      },
    ],
  };
}

describe("clean artifacts", () => {
  it("reports no findings for a well-formed artifact", () => {
    expect(findProhibitedContent(cleanArtifact())).toEqual([]);
  });

  it("does not flag digests, dates, or ordinary prose", () => {
    const node = {
      digest: `sha256:${"b".repeat(64)}`,
      generatedAt: "2026-01-15T12:00:00Z",
      note: "Counts are distinct source-video identifiers within the active filter.",
    };
    expect(findProhibitedContent(node)).toEqual([]);
  });

  it("does not flag legitimate display names", () => {
    const node = { creators: [{ displayName: "Kurzgesagt" }] };
    expect(findProhibitedContent(node)).toEqual([]);
  });
});

describe("prohibited keys (Requirement 7.5)", () => {
  it.each([
    ["videoIds", ["dQw4w9WgXcQ"]],
    ["sourceLocator", "shard-0:row-17"],
    ["channelId", "some-value"],
    ["rawResponse", "{}"],
    ["email", "creator@example.invalid"],
    ["apiKey", "value"],
    ["suppressionReason", "privacy review"],
    ["restricted_reason", "privacy review"],
  ])("flags a %s field", (key, value) => {
    const findings = findProhibitedContent({ [key]: value });
    expect(findings.length).toBeGreaterThan(0);
    expect(findings[0].path).toBe(key);
  });

  it("flags prohibited keys nested at depth", () => {
    // Requirement 7.6: inspection is recursive, not top-level only.
    const node = { a: { b: { c: [{ sourceLocator: "shard-0:row-1" }] } } };
    const findings = findProhibitedContent(node);
    expect(findings).toHaveLength(1);
    expect(findings[0].path).toBe("a.b.c[0].sourceLocator");
  });
});

describe("prohibited values", () => {
  it("flags a raw channel ID in any field", () => {
    const findings = findProhibitedContent({
      note: "UC_x5XG1OV2P6uZZ5FSM9Ttw",
    });
    expect(findings[0].reason).toMatch(/channel identifier/);
  });

  it("flags a YouTube URL", () => {
    const findings = findProhibitedContent({
      note: "see https://youtube.com/watch?v=dQw4w9WgXcQ",
    });
    expect(findings.length).toBeGreaterThan(0);
  });

  it("flags a raw video ID in a free-text field", () => {
    const findings = findProhibitedContent({ note: "dQw4w9WgXcQ" });
    expect(findings[0].reason).toMatch(/video identifier/);
  });

  it.each([
    ["supabase secret", "sb_secret_abcdefghijklmnop"],
    ["google api key", `AIza${"a".repeat(35)}`],
    ["connection string", "postgresql://user:pass@host:5432/db"],
    ["private key", "-----BEGIN RSA PRIVATE KEY-----"],
  ])("flags a leaked %s (Requirement 15.3)", (_label, value) => {
    expect(findProhibitedContent({ config: value }).length).toBeGreaterThan(0);
  });
});

describe("prose fields keep detection without false positives", () => {
  it.each([
    ["Counts are distinct source-video identifiers within the active filter."],
    ["Boundaries are presentation conventions, not evidence of location."],
    ["Dataset subtotals are non-additive because of cross-dataset overlap."],
  ])("does not flag methodology prose: %s", (note) => {
    // Requirement 12 obliges publishing this copy; flagging it would block
    // every valid release.
    expect(findProhibitedContent({ note })).toEqual([]);
  });

  it.each([
    ["note", "dQw4w9WgXcQ"],
    ["label", "aB3_x9Zq-1p"],
    ["description", "9bZkp7q19f0"],
  ])("still flags a bare identifier under prose key %s", (key, value) => {
    // The prose exemption must not become a hiding place.
    const findings = findProhibitedContent({ [key]: value });
    expect(findings.length).toBeGreaterThan(0);
  });

  it("still flags a channel ID embedded in prose", () => {
    const findings = findProhibitedContent({
      note: "see UC_x5XG1OV2P6uZZ5FSM9Ttw for details",
    });
    expect(findings.length).toBeGreaterThan(0);
  });
});

describe("real channel names are not mistaken for identifiers", () => {
  it.each(["101Treesrus", "1BreezyLife", "_le__s__ya_", "1DeathEater"])(
    "accepts the channel name %s",
    (name) => {
      // Observed on live data: 770 channels have 11-character names, and
      // flagging them blocked the entire build. The shape heuristic cannot
      // tell a name from an identifier; the field's meaning already can.
      expect(findProhibitedContent({ displayName: name })).toEqual([]);
    },
  );

  it("still catches a credential or raw id in a name field", () => {
    // The name exemption covers the shape heuristic and public URLs, not
    // secrets or raw identifiers.
    expect(
      findProhibitedContent({ displayName: "UC_x5XG1OV2P6uZZ5FSM9Ttw" }).length,
    ).toBeGreaterThan(0);
    expect(
      findProhibitedContent({ displayName: "AIza" + "F".repeat(35) }).length,
    ).toBeGreaterThan(0);
  });

  it("allows a URL in a name but refuses it elsewhere", () => {
    // A channel named with a URL is publishing public metadata. The same
    // URL in an ordinary value has no legitimate reason to be there.
    // When every creator is listed, real URL-shaped names occur.
    expect(
      findProhibitedContent({ displayName: "youtube.com/@creator" }),
    ).toEqual([]);
    expect(
      findProhibitedContent({ note: "see youtube.com/watch?v=dQw4w9WgXcQ" })
        .length,
    ).toBeGreaterThan(0);
  });
});

describe("exempt fields", () => {
  it("does not flag release IDs that resemble identifiers", () => {
    // Release IDs are timestamps and can coincidentally be 11 chars.
    expect(findProhibitedContent({ releaseId: "20260115120" })).toEqual([]);
  });

  it("still flags credentials inside exempt fields", () => {
    // Exemption covers only the broad video-ID heuristic.
    const findings = findProhibitedContent({
      displayName: "UC_x5XG1OV2P6uZZ5FSM9Ttw",
    });
    expect(findings.length).toBeGreaterThan(0);
  });
});

describe("assertNoProhibitedContent", () => {
  it("passes a clean artifact", () => {
    expect(() => assertNoProhibitedContent(cleanArtifact())).not.toThrow();
  });

  it("throws with every finding attached", () => {
    const dirty = { sourceLocator: "a", videoIds: ["b"] };
    try {
      assertNoProhibitedContent(dirty);
      expect.unreachable("should have thrown");
    } catch (error) {
      expect(error).toBeInstanceOf(DisclosureViolationError);
      expect((error as DisclosureViolationError).findings.length).toBe(2);
    }
  });

  it("does not include the offending value in the message", () => {
    // The error is logged; it must not become the leak it is preventing.
    try {
      assertNoProhibitedContent({ apiKey: "sb_secret_supersecretvalue" });
      expect.unreachable("should have thrown");
    } catch (error) {
      expect((error as Error).message).not.toContain("supersecretvalue");
    }
  });
});

describe("Property 13: Disclosure Noninterference", () => {
  it("finds a suppressed identifier wherever it is nested", () => {
    // Validates Requirements 7.2-7.7: a prohibited value is detected at any
    // depth, under any key, inside arrays or objects.
    const rawChannelId = "UC_x5XG1OV2P6uZZ5FSM9Ttw";

    const nest = fc.letrec((tie) => ({
      node: fc.oneof(
        { depthSize: "small" },
        fc.constant<unknown>(null),
        fc.integer(),
        fc.boolean(),
        fc.string(),
        fc.array(tie("node"), { maxLength: 3 }),
        fc.dictionary(fc.stringMatching(/^[a-z]{1,8}$/), tie("node"), {
          maxKeys: 3,
        }),
      ),
    }));

    fc.assert(
      fc.property(
        nest.node,
        fc.array(fc.stringMatching(/^[a-z]{1,6}$/), {
          minLength: 1,
          maxLength: 4,
        }),
        (surrounding, path) => {
          // Bury the prohibited value at the end of a generated key path.
          let payload: unknown = rawChannelId;
          for (const key of [...path].reverse()) {
            payload = { [key]: payload };
          }
          const artifact = { surrounding, buried: payload };

          const findings = findProhibitedContent(artifact);
          return findings.some((f) => f.reason.includes("channel identifier"));
        },
      ),
      { numRuns: 200 },
    );
  });

  it("never reports a finding for artifacts built only from safe primitives", () => {
    // Guards against the scanner being so broad it rejects valid releases.
    fc.assert(
      fc.property(
        fc.dictionary(
          fc.stringMatching(/^(count|total|creatorCount|videoCount)$/),
          fc.nat(),
          { maxKeys: 5 },
        ),
        (counts) => findProhibitedContent(counts).length === 0,
      ),
      { numRuns: 100 },
    );
  });
});
