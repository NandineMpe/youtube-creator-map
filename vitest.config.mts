import { defineConfig } from "vitest/config";

/**
 * Test environments.
 *
 * Most suites are pure logic — loaders, codecs, binning, artifact
 * contracts — and run fastest in node. Only the component and
 * accessibility tests need a DOM, and giving *every* test a jsdom
 * environment would slow the whole suite to pay for a handful of files.
 *
 * `environmentMatchGlobs` is deprecated in favour of per-file
 * annotations, but the annotation form requires touching every file and
 * is easy to forget on a new one — a component test silently running in
 * node fails with an opaque "document is not defined" rather than
 * anything that points at the cause. The glob keeps the rule in one
 * place.
 */
export default defineConfig({
  // The automatic runtime, matching the app's tsconfig. Without it, JSX
  // compiles to `React.createElement` and every component test fails with
  // "React is not defined" — a confusing error for a file that never
  // mentions React.
  esbuild: { jsx: "automatic" },
  test: {
    environment: "node",
    environmentMatchGlobs: [
      ["**/*.dom.test.{ts,tsx}", "jsdom"],
      ["**/*.a11y.test.{ts,tsx}", "jsdom"],
      ["apps/web/components/**/*.test.tsx", "jsdom"],
    ],
    setupFiles: ["./vitest.setup.ts"],
    // The default `**/*.test.*` also walks node_modules and build output,
    // where a dependency's own tests would run as if they were ours.
    exclude: ["**/node_modules/**", "**/.next/**", "**/out/**", "**/dist/**"],
  },
});
