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
  const datasets = manifest.datasets;
  const availableClasses = [
    ...new Set(datasets.map((d) => d.corpusClass)),
  ].sort();

  const toggleDataset = (datasetId: string, checked: boolean) => {
    const next = checked
      ? [...selectedDatasets, datasetId]
      : selectedDatasets.filter((id) => id !== datasetId);
    onDatasetsChange([...new Set(next)].sort());
  };

  const toggleClass = (corpusClass: string, checked: boolean) => {
    const next = checked
      ? [...selectedCorpusClasses, corpusClass]
      : selectedCorpusClasses.filter((c) => c !== corpusClass);
    onCorpusClassesChange([...new Set(next)].sort());
  };

  // An empty selection means "the release default", which is the whole
  // set. Rendering that as every box unchecked would misreport what is
  // being shown.
  const datasetChecked = (id: string) =>
    selectedDatasets.length === 0 || selectedDatasets.includes(id);
  const classChecked = (value: string) =>
    selectedCorpusClasses.length === 0 || selectedCorpusClasses.includes(value);

  return (
    <div className="filter-panel">
      <fieldset className="filter-panel__group">
        <legend className="filter-panel__legend">Datasets</legend>
        {datasets.map((dataset) => (
          <label className="filter-option" key={dataset.datasetId}>
            <input
              type="checkbox"
              checked={datasetChecked(dataset.datasetId)}
              onChange={(event) =>
                toggleDataset(dataset.datasetId, event.target.checked)
              }
            />
            <span className="filter-option__label">
              <span>{dataset.displayName}</span>
              <span className="filter-option__note">
                {dataset.version} · counts{" "}
                {dataset.occurrenceUnit.toLowerCase()}
                {dataset.corpusClass === "Comparison" && " · comparison corpus"}
              </span>
            </span>
          </label>
        ))}
      </fieldset>

      {availableClasses.length > 1 && (
        <fieldset className="filter-panel__group">
          <legend className="filter-panel__legend">Corpus class</legend>
          {availableClasses.map((corpusClass) => (
            <label className="filter-option" key={corpusClass}>
              <input
                type="checkbox"
                checked={classChecked(corpusClass)}
                onChange={(event) =>
                  toggleClass(corpusClass, event.target.checked)
                }
              />
              <span className="filter-option__label">
                <span>{corpusClass}</span>
                <span className="filter-option__note">
                  {corpusClass === "Comparison"
                    ? // Requirement 12.6: the label reflects documented
                      // provenance and says nothing about other corpora.
                      "Labelled from its own documented provenance and licence terms"
                    : "Corpora under examination in this project"}
                </span>
              </span>
            </label>
          ))}
        </fieldset>
      )}

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
