import { afterEach, expect } from "vitest";

/**
 * Shared test setup.
 *
 * Loaded for every environment, so anything DOM-specific is imported
 * lazily and guarded — a bare `import "@testing-library/jest-dom"` here
 * would throw in the node-environment suites, which are the majority.
 */

// The file-fetch tests serve artifacts same-origin and expect the loader
// to fetch relative paths. If a developer's shell (or an auto-loaded
// .env.local) has NEXT_PUBLIC_ARTIFACT_BASE_URL set for a real
// deployment, the loader would prepend that CDN origin and every one of
// those tests would fail with a confusing 404. Clearing it here makes the
// suite independent of the environment it runs in.
delete process.env.NEXT_PUBLIC_ARTIFACT_BASE_URL;

if (typeof document !== "undefined") {
  const [{ cleanup }, matchers, axeMatchers] = await Promise.all([
    import("@testing-library/react"),
    import("@testing-library/jest-dom/matchers"),
    import("vitest-axe/matchers"),
  ]);

  expect.extend(matchers as never);
  expect.extend(axeMatchers as never);

  // Without this, a component left mounted by one test is still in the
  // document for the next, and queries match the wrong element — which
  // reads as a mysterious duplicate-element failure far from the cause.
  afterEach(() => {
    cleanup();
  });

  // jsdom implements no layout engine, so `matchMedia` is absent. The
  // components read it for reduced-motion and breakpoint decisions;
  // leaving it undefined would make them throw rather than take a
  // branch, which is not the behaviour under test.
  if (!window.matchMedia) {
    Object.defineProperty(window, "matchMedia", {
      writable: true,
      value: (query: string) => ({
        matches: false,
        media: query,
        onchange: null,
        addEventListener: () => undefined,
        removeEventListener: () => undefined,
        addListener: () => undefined,
        removeListener: () => undefined,
        dispatchEvent: () => false,
      }),
    });
  }

  // MapLibre needs WebGL, which jsdom does not provide. The map is
  // aria-hidden and has a table equivalent, so the accessibility suite
  // tests the table; this stub keeps the canvas from throwing during
  // render rather than pretending the map is being exercised.
  if (!HTMLCanvasElement.prototype.getContext) {
    HTMLCanvasElement.prototype.getContext = (() => null) as never;
  }
}
