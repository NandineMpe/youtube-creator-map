import { describe, expect, it } from "vitest";

import { schemaVersion } from "./index";

describe("shared schema boundary", () => {
  it("accepts only the scaffolded schema version", () => {
    expect(schemaVersion.parse("1.0.0")).toBe("1.0.0");
    expect(() => schemaVersion.parse("2.0.0")).toThrow();
  });
});
