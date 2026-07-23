"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { ChoroplethMap } from "../components/ChoroplethMap";
import { CountryTable } from "../components/CountryTable";
import { ActiveFilterLabel, FilterPanel } from "../components/FilterPanel";
import { CoveragePanel, HeadlineMetrics } from "../components/HeadlineMetrics";
import { Legend } from "../components/Legend";
import { DatasetCitations, ReleaseContext } from "../components/ReleaseContext";
import {
  CorrectionNotice,
  EmptyPanel,
  ErrorPanel,
  LoadingPanel,
} from "../components/StatePanels";
import { computeBins } from "../lib/bins";
import { metricValue } from "../lib/format";
import {
  ArtifactLoadError,
  loadActiveRelease,
  type VerifiedRelease,
} from "../lib/loader";
import {
  parse,
  serialize,
  withChange,
  type ViewState,
  type Correction,
  type MetricKey,
} from "../lib/view-state";

/**
 * The overview: headline totals, filters, coverage, and the country table.
 *
 * All state lives here and flows down. Requirement 9.6 requires a filter
 * change to update every surface *and* the URL to the same resulting
 * filter, which is only guaranteed if there is exactly one place the
 * filter is stored. Components that held their own copy could drift.
 *
 * Requirement refs: 6.7-6.11, 9.1, 9.6-9.8, 9.12, 12.3, 12.4, 12.7-12.9
 */
export default function OverviewPage() {
  const [release, setRelease] = useState<VerifiedRelease | null>(null);
  const [error, setError] = useState<ArtifactLoadError | null>(null);
  const [loading, setLoading] = useState(true);
  const [view, setView] = useState<ViewState | null>(null);
  const [corrections, setCorrections] = useState<readonly Correction[]>([]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const loaded = await loadActiveRelease();
      setRelease(loaded);

      // Parse the URL only once the release is known, so unknown datasets
      // and countries can be validated against what actually exists
      // (Requirement 11.7).
      const parsed = parse(
        typeof window === "undefined" ? "/" : window.location.search,
        {
          knownDatasets: loaded.manifest.datasets.map((d) => d.datasetId),
          knownCountries: loaded.overview.countries.map((c) => c.country),
        },
      );
      setView(parsed.state);
      setCorrections(parsed.corrections);
    } catch (caught) {
      // Requirement 14.7: a failure leaves no figures on screen, rather
      // than zeros that would read as data.
      setError(
        caught instanceof ArtifactLoadError
          ? caught
          : new ArtifactLoadError(
              "network",
              "active-release.json",
              "unexpected failure",
            ),
      );
      setRelease(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  /** Apply a state change and mirror it into the URL. */
  const update = useCallback((change: Partial<ViewState>) => {
    setView((current) => {
      if (!current) return current;
      const next = withChange(current, change);
      if (typeof window !== "undefined") {
        window.history.replaceState(null, "", serialize(next));
      }
      return next;
    });
  }, []);

  const countries = release?.overview.countries ?? [];
  const metric: MetricKey = view?.metric ?? "creators";

  const scale = useMemo(
    () => computeBins(countries.map((c) => metricValue(c, metric))),
    [countries, metric],
  );

  if (loading) {
    return <LoadingPanel label="Verifying and loading the current release." />;
  }

  if (error) {
    return <ErrorPanel error={error} onRetry={() => void load()} />;
  }

  if (!release || !view) {
    return (
      <ErrorPanel
        error={
          new ArtifactLoadError(
            "network",
            "active-release.json",
            "no release loaded",
          )
        }
        onRetry={() => void load()}
      />
    );
  }

  const { manifest, overview } = release;

  return (
    <>
      <ReleaseContext
        manifest={manifest}
        datasetCount={manifest.datasets.length}
      />

      <CorrectionNotice corrections={corrections} />

      <HeadlineMetrics overview={overview} />

      <FilterPanel
        manifest={manifest}
        selectedDatasets={view.datasets}
        selectedCorpusClasses={view.corpusClasses}
        metric={metric}
        onDatasetsChange={(datasets) => update({ datasets })}
        onCorpusClassesChange={(corpusClasses) => update({ corpusClasses })}
        onMetricChange={(next) => update({ metric: next })}
      />

      <ActiveFilterLabel
        manifest={manifest}
        selectedDatasets={view.datasets}
        countryCount={countries.length}
      />

      {countries.length === 0 ? (
        // Requirement 9.7 / 6.11: a zero-input filter is an empty state,
        // presented distinctly from a delivery or digest failure.
        <EmptyPanel
          heading="No countries match this filter"
          body="The selected datasets contain no records that resolved to a country."
        />
      ) : (
        <>
          <div className="control-row">
            {/* Requirement 9.10: switching views preserves the release,
                filter, selected country, metric, and values. Only the
                presentation changes, so the toggle writes one field. */}
            <span id="view-toggle-label">View</span>
            <div
              className="view-toggle"
              role="group"
              aria-labelledby="view-toggle-label"
            >
              <button
                type="button"
                aria-pressed={view.view === "map"}
                onClick={() => update({ view: "map" })}
              >
                Map
              </button>
              <button
                type="button"
                aria-pressed={view.view === "table"}
                onClick={() => update({ view: "table" })}
              >
                Table only
              </button>
            </div>
          </div>

          <div className="explore-layout">
            <div>
              {view.view === "map" && (
                <ChoroplethMap
                  countries={countries}
                  metric={metric}
                  scale={scale}
                  selectedCountry={view.country}
                  onSelect={(country) => update({ country })}
                />
              )}
              {/* The table renders in both modes. Requirement 13.4 makes
                  it the keyboard and screen-reader route to every country,
                  so hiding it in map mode would remove that route. */}
              <CountryTable
                countries={countries}
                metric={metric}
                scale={scale}
                selectedCountry={view.country}
                onSelect={(country) => update({ country })}
              />
            </div>
            <Legend scale={scale} metric={metric} />
          </div>
        </>
      )}

      <CoveragePanel coverage={overview.coverage} />

      <DatasetCitations manifest={manifest} />
    </>
  );
}
