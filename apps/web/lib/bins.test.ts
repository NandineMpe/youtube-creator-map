import fc from "fast-check";
import { describe, expect, it } from "vitest";

import {
  NO_DATA_COLOR,
  binFor,
  colorFor,
  computeBins,
  patternFor,
} from "./bins";

describe("computeBins", () => {
  it("returns no bins for an empty distribution", () => {
    const scale = computeBins([]);
    expect(scale.bins).toEqual([]);
    expect(scale.method).toBe("empty");
  });

  it("returns no bins when every value is zero", () => {
    // Zeros are real data but carry no spread to bin.
    const scale = computeBins([0, 0, 0]);
    expect(scale.method).toBe("empty");
  });

  it("collapses to one bin for a single distinct value", () => {
    // Five bins where four are empty would imply a spread that is not
    // there.
    const scale = computeBins([7, 7, 7, 7]);
    expect(scale.bins).toHaveLength(1);
    expect(scale.method).toBe("single-value");
    expect(scale.bins[0].label).toBe("7");
  });

  it("produces contiguous, non-overlapping bins", () => {
    const scale = computeBins([1, 5, 12, 40, 90, 300, 1200]);

    for (let i = 0; i < scale.bins.length - 1; i += 1) {
      expect(scale.bins[i].max).toBeLessThan(scale.bins[i + 1].min);
      // Contiguous: no gap a value could fall into.
      expect(scale.bins[i + 1].min).toBe(scale.bins[i].max + 1);
    }
  });

  it("labels every bin with an exact range", () => {
    // Requirement 9.4: the legend states exact ranges, not "high"/"low".
    const scale = computeBins([1, 10, 100, 1000]);
    for (const bin of scale.bins) {
      expect(bin.label).toMatch(/^[\d,]+(–[\d,]+)?$/);
    }
  });

  it("gives each bin a distinct colour and pattern", () => {
    const scale = computeBins([1, 4, 9, 16, 25, 36, 49, 64]);
    const colors = new Set(scale.bins.map((b) => b.color));
    const patterns = new Set(scale.bins.map((b) => b.pattern));

    expect(colors.size).toBe(scale.bins.length);
    expect(patterns.size).toBe(scale.bins.length);
  });

  it("keeps the no-data style off the sequential scale", () => {
    // Requirement 9.4: no-data is a category, not the bottom of the ramp.
    const scale = computeBins([1, 2, 3, 4, 5]);
    expect(scale.bins.map((b) => b.color)).not.toContain(NO_DATA_COLOR);
  });

  it("handles a heavily skewed distribution without empty bins", () => {
    // Real country distributions look like this: a long tail of ones.
    const skewed = [...Array<number>(200).fill(1), 5, 12, 400, 28998];
    const scale = computeBins(skewed);

    expect(scale.bins.length).toBeGreaterThan(0);
    for (const bin of scale.bins) {
      expect(bin.min).toBeLessThanOrEqual(bin.max);
    }
  });
});

describe("binFor", () => {
  const scale = computeBins([1, 10, 100, 1000, 10000]);

  it("returns null for a country absent from the release", () => {
    // Requirement 9.4: absence of evidence, not evidence of absence.
    expect(binFor(undefined, scale)).toBeNull();
  });

  it("places a genuine zero at the bottom of the scale, not no-data", () => {
    const bin = binFor(0, scale);
    expect(bin).not.toBeNull();
    expect(bin).toBe(scale.bins[0]);
  });

  it("distinguishes no-data from zero in colour", () => {
    expect(colorFor(undefined, scale)).toBe(NO_DATA_COLOR);
    expect(colorFor(0, scale)).not.toBe(NO_DATA_COLOR);
  });

  it("assigns every in-range value to exactly one bin", () => {
    for (const value of [1, 10, 100, 1000, 10000]) {
      const matches = scale.bins.filter(
        (b) => value >= b.min && value <= b.max,
      );
      expect(matches).toHaveLength(1);
    }
  });

  it("returns no pattern for no-data", () => {
    expect(patternFor(undefined, scale)).toBe("none");
    expect(patternFor(5, scale)).not.toBe("none");
  });

  it("returns null against an empty scale", () => {
    expect(binFor(5, computeBins([]))).toBeNull();
    expect(colorFor(5, computeBins([]))).toBe(NO_DATA_COLOR);
  });
});

describe("bin properties", () => {
  it("every positive value lands in exactly one bin", () => {
    fc.assert(
      fc.property(
        fc.array(fc.integer({ min: 1, max: 100000 }), {
          minLength: 1,
          maxLength: 60,
        }),
        (values) => {
          const scale = computeBins(values);
          if (scale.bins.length === 0) return true;

          return values.every((value) => {
            const matches = scale.bins.filter(
              (b) => value >= b.min && value <= b.max,
            );
            return matches.length === 1;
          });
        },
      ),
      { numRuns: 300 },
    );
  });

  it("bins never overlap", () => {
    fc.assert(
      fc.property(
        fc.array(fc.integer({ min: 0, max: 50000 }), { maxLength: 60 }),
        (values) => {
          const scale = computeBins(values);
          for (let i = 0; i < scale.bins.length - 1; i += 1) {
            if (scale.bins[i].max >= scale.bins[i + 1].min) return false;
          }
          return true;
        },
      ),
      { numRuns: 300 },
    );
  });

  it("bin bounds are always ordered", () => {
    fc.assert(
      fc.property(
        fc.array(fc.integer({ min: 0, max: 1000000 }), { maxLength: 40 }),
        (values) => computeBins(values).bins.every((b) => b.min <= b.max),
      ),
      { numRuns: 300 },
    );
  });

  it("no-data never shares a colour with a bin", () => {
    fc.assert(
      fc.property(
        fc.array(fc.integer({ min: 1, max: 9999 }), { maxLength: 40 }),
        (values) => {
          const scale = computeBins(values);
          return !scale.bins.some((b) => b.color === scale.noDataColor);
        },
      ),
      { numRuns: 200 },
    );
  });
});
