import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { axe } from "vitest-axe";

import RootLayout from "./layout";

/**
 * The layout: disclaimers, landmarks, and the security policy.
 *
 * The text asserted here is the copy Requirement 12.4-12.5 *requires* —
 * the statements that keep an observation from reading as an accusation.
 * It is easy to lose in a redesign, and nothing else in the suite would
 * notice, because the page renders perfectly well without it.
 *
 * Requirement refs: 12.1-12.6, 13.1-13.3, 15.13
 */

function renderLayout() {
  return render(
    <RootLayout>
      <p>Page content</p>
    </RootLayout>,
  );
}

describe("the site layout", () => {
  it("has no detectable accessibility violations", async () => {
    const { container } = renderLayout();

    expect(await axe(container)).toHaveNoViolations();
  });

  // --- Requirement 12.5: what the data does not establish ---------------

  it("says the data does not establish that a model was trained", () => {
    const { container } = renderLayout();

    expect(container.textContent?.toLowerCase()).toContain(
      "does not establish that a model was trained",
    );
  });

  it("disclaims legality and consent alongside training", () => {
    // Requirement 12.5 names several classes, and a footer that covered
    // only training would leave the others to be inferred.
    const { container } = renderLayout();
    const text = container.textContent?.toLowerCase() ?? "";

    expect(text).toMatch(/lawful|unlawful/);
    expect(text).toMatch(/agreed|consent/);
  });

  it("keeps the methodology reachable from every view", () => {
    // Requirement 12.9. The country-declaration qualifier that
    // Requirement 12.4 asks for lives in the methodology copy rather
    // than the footer; this link is what makes it reachable from an
    // empty or error state, where no other route to it exists.
    renderLayout();

    // Linked from both the header nav and the footer, so the assertion
    // is that at least one exists and every one points at the right
    // route — not that there is exactly one.
    const links = screen.getAllByRole("link", { name: /methodology/i });

    expect(links.length).toBeGreaterThan(0);
    for (const link of links) {
      expect(link).toHaveAttribute("href", "/methodology");
    }
  });

  it("states the boundary convention is not a territorial position", () => {
    // Requirement 12.10. Borders are a cartographic choice, and saying
    // so is the difference between a presentation and an assertion.
    const { container } = renderLayout();
    const text = container.textContent?.toLowerCase() ?? "";

    expect(text).toContain("natural earth");
    expect(text).toMatch(/not a position/);
  });

  // --- Requirement 13.2: landmarks and skip link -------------------------

  it("offers a skip link as the first tab stop", () => {
    // A keyboard visitor should not traverse the header and filters to
    // reach the data on every page load.
    renderLayout();

    const skip = screen.getByRole("link", { name: /skip to main content/i });
    expect(skip).toHaveAttribute("href", "#main");
  });

  it("marks the main landmark the skip link points at", () => {
    // A skip link targeting nothing is worse than none: focus goes
    // nowhere and the visitor cannot tell.
    const { container } = renderLayout();

    expect(container.querySelector("#main")).not.toBeNull();
  });

  it("provides banner, main, and contentinfo landmarks", () => {
    renderLayout();

    expect(screen.getByRole("banner")).toBeInTheDocument();
    expect(screen.getByRole("main")).toBeInTheDocument();
    expect(screen.getByRole("contentinfo")).toBeInTheDocument();
  });

  it("renders the page content inside main", () => {
    renderLayout();

    expect(screen.getByRole("main").textContent).toContain("Page content");
  });

  // --- Requirement 15.13: the in-document policy -------------------------

  it("carries a restrictive Content Security Policy", () => {
    // Defence in depth under the real response headers. The directives
    // a meta tag cannot deliver — frame-ancestors, HSTS — are asserted
    // against the header module in the Python suite.
    const source = readFileSync(
      join(process.cwd(), "apps/web/app/layout.tsx"),
      "utf8",
    );

    expect(source).toContain("Content-Security-Policy");
    expect(source).toContain("default-src 'self'");
    expect(source).toContain("object-src 'none'");
  });

  it("does not permit inline script execution", () => {
    const source = readFileSync(
      join(process.cwd(), "apps/web/app/layout.tsx"),
      "utf8",
    );
    const scriptSrc = source
      .split("\n")
      .find((line) => line.includes("script-src"));

    expect(scriptSrc).toBeDefined();
    expect(scriptSrc).not.toContain("unsafe-inline");
    expect(scriptSrc).not.toContain("unsafe-eval");
  });
});
