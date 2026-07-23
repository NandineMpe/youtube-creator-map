"use client";

import type { ReleaseManifest } from "@creator-map/shared-schemas";

import { METRIC_DEFINITIONS, type MetricKey } from "../lib/format";

/**
 * Dataset, corpus-class, and metric controls.
 *
 * Requirement 9.6 requires a filter change to update the map, table,
 * headline summaries, coverage, filter label, and URL to the *same*
 * resulting filter. That is why this component owns no state: it reports
 * changes upward, and every surface reads from the one state the page
 * holds. Local state here would be a second source of truth and the
 * surfaces could disagree.
 *
 * Requirement refs: 9.6, 9.8, 12.6, 13.2, 13.3
 */
export interface FilterPanelProps {
  readonly manifest: ReleaseManifest;
  readonly selectedDatasets: readonly string[];
  readonly selectedCorpusClasses: readonly string[];
  readonly metric: MetricKey;
  readonly onDatasetsChange: (datasets: string[]) => void;
  readonly onCorpusClassesChange: (classes: string[]) => void;
  readonly onMetricChange: (metric: MetricKey) => void;
}

export function FilterPanel({
  manifest,
  selectedDatasets,
  selectedCorpusClasses,
  metric,
  onDatasetsChange,
  onCorpusClassesChange,
  onMetricChange,
}: FilterPanelProps) {
  // Only combinations the release actually published can be offered.
  // Requirement 5.12 forbids approximating a filtered count, so a control
  // that produced no artifact would either lie or fall back silently.
  const published = manifest.filters;
  const byDataset = new Map(
    manifest.datasets.map((d) => [d.datasetId, d] as const),
  );

  const selectedKey =
    selectedDatasets.length === 0 && selectedCorpusClasses.length === 0
      ? (published.find((e) => e.isDefault)?.key ?? "")
      : `${[...selectedCorpusClasses].sort().join("+")}~${[...selectedDatasets].sort().join("+")}`;

  if (published.length <= 1) {
    // A single-filter release: offering a choice would imply alternatives
    // that do not exist.
    return (
      <div className="filter-panel">
        <p className="filter-option__note">
          This release publishes one filter, covering{" "}
          {manifest.datasets.length === 1
            ? "its single dataset"
            : `all ${manifest.datasets.length} datasets`}
          .
        </p>
        <MetricSelect metric={metric} onMetricChange={onMetricChange} />
      </div>
    );
  }

  return (
    <div className="filter-panel">
      <fieldset className="filter-panel__group">
        <legend className="filter-panel__legend">Filter</legend>
        {published.map((entry) => {
          const dataset =
            entry.datasets.length === 1
              ? byDataset.get(entry.datasets[0])
              : undefined;

          return (
            <label className="filter-option" key={entry.key}>
              <input
                type="radio"
                name="published-filter"
                checked={entry.key === selectedKey}
                onChange={() => {
                  if (entry.isDefault) {
                    // The default is expressed as an empty selection, so
                    // its URL stays clean (Requirement 11.2).
                    onDatasetsChange([]);
                    onCorpusClassesChange([]);
                  } else {
                    onDatasetsChange([...entry.datasets]);
                    onCorpusClassesChange([...entry.corpusClasses]);
                  }
                }}
              />
              <span className="filter-option__label">
                <span>{dataset ? dataset.displayName : entry.label}</span>
                <span className="filter-option__note">
                  {dataset
                    ? `${dataset.version} · counts ${dataset.occurrenceUnit.toLowerCase()}${
                        dataset.corpusClass === "Comparison"
                          ? " · comparison corpus"
                          : ""
                      }`
                    : entry.corpusClasses.length === 1 &&
                        entry.corpusClasses[0] === "Comparison"
                      ? // Requirement 12.6: the label reflects this
                        // corpus's own documented provenance and says
                        // nothing about any other.
                        "Labelled from its own documented provenance and licence terms"
                      : `${entry.datasets.length} dataset${entry.datasets.length === 1 ? "" : "s"}`}
                </span>
              </span>
            </label>
          );
        })}
      </fieldset>

      <MetricSelect metric={metric} onMetricChange={onMetricChange} />
    </div>
  );
}

function MetricSelect({
  metric,
  onMetricChange,
}: {
  readonly metric: MetricKey;
  readonly onMetricChange: (metric: MetricKey) => void;
}) {
  return (
    <div className="control-row">
      <label htmlFor="metric-select">Colour the map by</label>
      <select
        id="metric-select"
        value={metric}
        onChange={(event) => onMetricChange(event.target.value as MetricKey)}
      >
        {METRIC_DEFINITIONS.map((definition) => (
          <option key={definition.key} value={definition.key}>
            {definition.label}
          </option>
        ))}
      </select>
    </div>
  );
}

/**
 * A plain-language summary of what is currently being shown.
 *
 * Requirement 9.6 requires the active-filter label to update with the
 * filter, and 13.3 requires the change to be announced. The live region
 * here is what makes a filter change perceivable to a screen reader
 * rather than a silent repaint.
 */
export function ActiveFilterLabel({
  manifest,
  selectedDatasets,
  countryCount,
}: {
  readonly manifest: ReleaseManifest;
  readonly selectedDatasets: readonly string[];
  readonly countryCount: number;
}) {
  const total = manifest.datasets.length;
  const selected =
    selectedDatasets.length === 0 ? total : selectedDatasets.length;

  const datasetPhrase =
    selected === total
      ? `all ${total} dataset${total === 1 ? "" : "s"}`
      : `${selected} of ${total} datasets`;

  return (
    <p role="status" aria-live="polite" className="active-filter">
      Showing {datasetPhrase}, across {countryCount} country bucket
      {countryCount === 1 ? "" : "s"}.
    </p>
  );
}
