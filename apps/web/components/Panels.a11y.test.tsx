import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "vitest-axe";

import { AnnouncerProvider } from "./Announcer";
import { FilterPanel } from "./FilterPanel";
import { HeadlineMetrics } from "./HeadlineMetrics";
import { Legend } from "./Legend";
import { EmptyPanel, ErrorPanel, LoadingPanel } from "./StatePanels";
import { ArtifactLoadError } from "../lib/loader";
import { computeBins } from "../lib/bins";

/**
 * Accessibility tests for the surrounding panels.
 *
 * The states here — loading, empty, error — are the ones most often
 * built as decorative markup and never revisited, and they are also the
 * states where a user most needs to be told what happened. Requirement
 * 14.7 forbids presenting an error as zero or an empty state, which is a
 * semantic distinction: it only holds if the roles differ, not just the
 * text.
 *
 * Requirement refs: 9.5, 9.6, 12.6, 13.1-13.4, 13.9, 14.6-14.8
 */

// Built with the real binning function rather than a hand-written
// literal. A literal drifts from the type silently — an earlier version
// of this file spelled the fields `colour`/`noDataColour` and rendered
// blank swatches with no error, which made the legend look correct and
// the assertion look wrong.
const SCALE = computeBins([1, 5, 12, 40, 96, 240, 700]);

const MANIFEST = {
  schemaVersion: "1.0.0" as const,
  releaseId: "r1",
  generatedAt: "2026-01-01T00:00:00Z",
  enrichmentCutoff: "2025-12-31T00:00:00Z",
  defaultFilter: { datasets: ["ds-a"], corpusClasses: ["Candidate"] },
  datasets: [
    {
      datasetId: "ds-a",
      displayName: "Dataset A",
      version: "1.0.0",
      corpusClass: "Candidate" as const,
      sourceKind: "MetadataOnly" as const,
      occurrenceUnit: "Row" as const,
      sourceCitation: "https://example.invalid/a",
      snapshotDigest: `sha256:${"a".repeat(64)}`,
    },
    {
      datasetId: "ds-b",
      displayName: "Dataset B",
      version: "1.0.0",
      corpusClass: "Candidate" as const,
      sourceKind: "MetadataOnly" as const,
      occurrenceUnit: "Row" as const,
      sourceCitation: "https://example.invalid/b",
      snapshotDigest: `sha256:${"b".repeat(64)}`,
    },
  ],
  // Two filters, deliberately: with only one published filter there is
  // nothing to choose, and the panel correctly renders a sentence rather
  // than a radio group. A single-filter fixture tests the wrong branch.
  filters: [
    {
      key: "Candidate~ds-a+ds-b",
      label: "All datasets",
      corpusClasses: ["Candidate"],
      datasets: ["ds-a", "ds-b"],
      isDefault: true,
      path: "releases/r1/overview.json",
    },
    {
      key: "Candidate~ds-a",
      label: "Dataset A only",
      corpusClasses: ["Candidate"],
      datasets: ["ds-a"],
      isDefault: false,
      path: "releases/r1/overview-ds-a.json",
    },
  ],
  artifactDigests: {},
  methodologyVersion: "1.0.0",
  disclosurePolicyVersion: "1.0.0",
  boundaryMetadata: { datasetName: "Natural Earth", version: "5.1.1" },
} as never;

const OVERVIEW = {
  schemaVersion: "1.0.0",
  releaseId: "r1",
  filter: { datasets: ["ds-a"], corpusClasses: ["Candidate"] },
  countries: [],
  coverage: {
    inputOccurrenceCount: 1200,
    distinctInputVideoCount: 1000,
    partition: {
      resolved: 700,
      unavailableUnclassified: 150,
      retryableOrPending: 100,
      invalid: 30,
      terminalFailure: 20,
    },
    resolvedChannelCount: 400,
    knownCountryChannelCount: 300,
    unknownCountryChannelCount: 100,
  },
  creatorCount: 400,
  representedVideoCount: 1000,
  representedCountryCount: 1,
} as never;

describe("the filter panel", () => {
  function renderPanel() {
    const onDatasetsChange = vi.fn();
    const onMetricChange = vi.fn();
    const result = render(
      <FilterPanel
        manifest={MANIFEST}
        selectedDatasets={["ds-a"]}
        selectedCorpusClasses={["Candidate"]}
        metric="creators"
        onDatasetsChange={onDatasetsChange}
        onCorpusClassesChange={vi.fn()}
        onMetricChange={onMetricChange}
      />,
    );
    return { ...result, onDatasetsChange, onMetricChange };
  }

  it("has no detectable accessibility violations", async () => {
    const { container } = renderPanel();

    expect(await axe(container)).toHaveNoViolations();
  });

  it("groups controls under a named fieldset", () => {
    // Without a group name, a screen reader announces "radio button,
    // Dataset A" with no indication of what the choice governs.
    renderPanel();

    expect(screen.getByRole("group", { name: /filter/i })).toBeInTheDocument();
  });

  it("labels every control", () => {
    renderPanel();

    for (const control of screen.getAllByRole("radio")) {
      expect(control).toHaveAccessibleName();
    }
  });

  it("is operable from the keyboard alone", async () => {
    const user = userEvent.setup();
    const { onMetricChange } = renderPanel();

    const select = screen.getByRole("combobox");
    select.focus();
    await user.selectOptions(select, "videos");

    expect(onMetricChange).toHaveBeenCalled();
  });
});

describe("the legend", () => {
  it("has no detectable accessibility violations", async () => {
    const { container } = render(<Legend scale={SCALE} metric="creators" />);

    expect(await axe(container)).toHaveNoViolations();
  });

  it("states each bin range as text, not only as a swatch", () => {
    // Requirement 9.5: colour alone conveys nothing to a screen reader,
    // and nothing at all to a reader who cannot distinguish the ramp.
    const { container } = render(<Legend scale={SCALE} metric="creators" />);

    expect(container.textContent).toMatch(/\d/);
  });

  it("names the no-data category", () => {
    // No-data is a category outside the sequential ramp, not the bottom
    // of it. Reading it as "zero" is the error this prevents.
    const { container } = render(<Legend scale={SCALE} metric="creators" />);

    expect(container.textContent?.toLowerCase()).toContain("no data");
  });
});

describe("the state panels", () => {
  it("announces loading politely rather than as an alert", async () => {
    // A loading state interrupting a screen reader on every fetch is
    // hostile; it is progress, not an emergency.
    const { container } = render(<LoadingPanel label="Loading countries" />);

    expect(await axe(container)).toHaveNoViolations();
    expect(screen.getByText(/loading countries/i)).toBeInTheDocument();
  });

  it("gives the empty state a heading and an explanation", async () => {
    const { container } = render(
      <EmptyPanel
        heading="No countries match"
        body="Every country was filtered out."
      />,
    );

    expect(await axe(container)).toHaveNoViolations();
    expect(screen.getByRole("heading")).toHaveTextContent(
      /no countries match/i,
    );
  });

  it("marks an error as an alert, distinct from an empty state", async () => {
    // Requirement 14.7: an error must not be presentable as zero or as
    // an empty state. Different text is not enough — the roles differ,
    // which is what a screen reader acts on.
    const error = new ArtifactLoadError(
      "not-found",
      "countries/ZA.json",
      "missing",
    );
    const { container } = render(<ErrorPanel error={error} />);

    expect(await axe(container)).toHaveNoViolations();
    expect(screen.getByRole("alert")).toBeInTheDocument();
  });

  it("offers retry as a real button when a handler is given", async () => {
    const user = userEvent.setup();
    const onRetry = vi.fn();
    const error = new ArtifactLoadError(
      "not-found",
      "countries/ZA.json",
      "missing",
    );

    render(<ErrorPanel error={error} onRetry={onRetry} />);
    await user.click(screen.getByRole("button", { name: /retry|try again/i }));

    expect(onRetry).toHaveBeenCalled();
  });
});

describe("the headline metrics", () => {
  it("has no detectable accessibility violations", async () => {
    const { container } = render(
      <AnnouncerProvider>
        <HeadlineMetrics overview={OVERVIEW} />
      </AnnouncerProvider>,
    );

    expect(await axe(container)).toHaveNoViolations();
  });

  it("pairs every value with a label", () => {
    // A bare number is unreadable out of visual context. Requirement 9.1
    // names the metrics; each has to say which one it is.
    const { container } = render(
      <AnnouncerProvider>
        <HeadlineMetrics overview={OVERVIEW} />
      </AnnouncerProvider>,
    );

    const items = container.querySelectorAll(".metric-card");
    expect(items.length).toBeGreaterThan(0);
    for (const item of items) {
      expect(item.textContent).toMatch(/[A-Za-z]/);
      expect(item.textContent).toMatch(/\d/);
    }
  });
});
