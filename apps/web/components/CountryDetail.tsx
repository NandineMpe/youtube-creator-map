"use client";

import type {
  CountryDetail as CountryDetailArtifact,
  CountrySummary,
  CreatorPage,
  ReleaseManifest,
} from "@creator-map/shared-schemas";
import { useCallback, useEffect, useRef, useState } from "react";

import {
  ArtifactLoadError,
  creatorPageCount,
  loadCountryDetail,
  loadCreatorPage,
} from "../lib/loader";
import { countryLabel, formatCount, formatDate } from "../lib/format";
import { ErrorPanel, LoadingPanel } from "./StatePanels";

/**
 * One country's drill-down.
 *
 * Requirement 10.2 requires the detail totals to equal the corresponding
 * summary values. Rather than trusting that, this asserts it: the shard is
 * built from the same aggregate as the summary, so a mismatch means the
 * artifacts disagree and the figures should not be shown at all
 * (Requirement 10.10 forbids presenting partial data as valid).
 *
 * Requirement refs: 10.1-10.12, 12.8, 14.4, 14.6-14.8
 */

export interface CountryDetailPanelProps {
  readonly manifest: ReleaseManifest;
  readonly summary: CountrySummary;
  readonly sortOrder: string;
  readonly onSortChange: (order: string) => void;
  readonly onClose: () => void;
}

export function CountryDetailPanel({
  manifest,
  summary,
  sortOrder,
  onSortChange,
  onClose,
}: CountryDetailPanelProps) {
  const [detail, setDetail] = useState<CountryDetailArtifact | null>(null);
  const [page, setPage] = useState<CreatorPage | null>(null);
  const [pageIndex, setPageIndex] = useState(0);
  const [error, setError] = useState<ArtifactLoadError | null>(null);
  const [loading, setLoading] = useState(true);
  const heading = useRef<HTMLHeadingElement | null>(null);

  const country = summary.country;

  // Requirement 14.4: the shard is fetched only when a country is
  // actually selected, not with the overview.
  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const loaded = await loadCountryDetail(manifest, country);

      // Requirement 10.2: the detail must agree with the summary. If it
      // does not, the artifacts contradict each other and neither figure
      // can be trusted.
      if (
        loaded.creatorCount !== summary.creatorCount ||
        loaded.representedVideoCount !== summary.representedVideoCount
      ) {
        throw new ArtifactLoadError(
          "mixed-release",
          country,
          "the country detail disagrees with the overview totals",
        );
      }

      setDetail(loaded);
      setPage(loaded.firstPage);
      setPageIndex(0);
    } catch (caught) {
      setError(
        caught instanceof ArtifactLoadError
          ? caught
          : new ArtifactLoadError("network", country, "detail failed to load"),
      );
      setDetail(null);
      setPage(null);
    } finally {
      setLoading(false);
    }
  }, [manifest, country, summary.creatorCount, summary.representedVideoCount]);

  useEffect(() => {
    void load();
  }, [load]);

  // Requirement 10.9: a country change moves focus to the new heading, so
  // a screen-reader user is told what opened rather than left where they
  // were in the table.
  useEffect(() => {
    if (detail && heading.current) heading.current.focus();
  }, [detail]);

  const goToPage = useCallback(
    async (index: number) => {
      if (!detail) return;
      setLoading(true);
      try {
        const loaded = await loadCreatorPage(
          manifest,
          detail,
          sortOrder,
          index,
        );
        setPage(loaded);
        setPageIndex(index);
        setError(null);
      } catch (caught) {
        // Requirement 10.10: a failed page keeps the country and release
        // selected and shows no partial rows.
        setError(
          caught instanceof ArtifactLoadError
            ? caught
            : new ArtifactLoadError("network", country, "page failed"),
        );
      } finally {
        setLoading(false);
      }
    },
    [manifest, detail, sortOrder, country],
  );

  if (loading && !detail) {
    return <LoadingPanel label={`Loading ${countryLabel(country)}.`} />;
  }

  if (error && !detail) {
    return <ErrorPanel error={error} onRetry={() => void load()} />;
  }

  if (!detail) return null;

  const totalPages = creatorPageCount(detail, sortOrder);

  return (
    <section className="detail-panel" aria-labelledby="detail-heading">
      <div className="detail-panel__header">
        <h2 id="detail-heading" ref={heading} tabIndex={-1}>
          {countryLabel(country)}
        </h2>
        <button type="button" onClick={onClose}>
          Close
        </button>
      </div>

      {/* Requirement 10.3: totals, coverage, and release context together,
          so a figure is never shown without what qualifies it. */}
      <ul className="metric-grid metric-grid--compact">
        <li className="metric-card">
          <span className="metric-card__value">
            {formatCount(detail.creatorCount)}
          </span>
          <span className="metric-card__label">Creators</span>
        </li>
        <li className="metric-card">
          <span className="metric-card__value">
            {formatCount(detail.representedVideoCount)}
          </span>
          <span className="metric-card__label">Represented videos</span>
        </li>
        <li className="metric-card">
          <span className="metric-card__value">
            {formatCount(detail.sourceOccurrenceCount)}
          </span>
          <span className="metric-card__label">Source occurrences</span>
        </li>
      </ul>

      <p className="detail-panel__context">
        Channel metadata read {formatDate(manifest.enrichmentCutoff)}. Release{" "}
        <span className="release-context__value">{manifest.releaseId}</span>.
      </p>

      {detail.datasetBreakdown.length > 0 && (
        <div className="scroll-x">
          <table className="data-table">
            <caption>Represented videos by dataset</caption>
            <thead>
              <tr>
                <th scope="col">Dataset</th>
                <th scope="col" className="numeric">
                  Videos
                </th>
              </tr>
            </thead>
            <tbody>
              {detail.datasetBreakdown.map((entry) => (
                <tr key={entry.datasetId}>
                  <th scope="row">{entry.datasetId}</th>
                  <td className="numeric">
                    {formatCount(entry.representedVideoCount)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <CreatorList
        page={page}
        pageIndex={pageIndex}
        totalPages={totalPages}
        sortOrder={sortOrder}
        loading={loading}
        error={error}
        onSortChange={onSortChange}
        onPage={(index) => void goToPage(index)}
      />
    </section>
  );
}

/**
 * The paginated creator list.
 *
 * Requirement 10.4 limits rows to policy-permitted fields. The row shape
 * comes from the artifact, which the disclosure engine already filtered,
 * so this renders what it is given rather than deciding again — two places
 * deciding what is publishable is one place too many.
 */
function CreatorList({
  page,
  pageIndex,
  totalPages,
  sortOrder,
  loading,
  error,
  onSortChange,
  onPage,
}: {
  readonly page: CreatorPage | null;
  readonly pageIndex: number;
  readonly totalPages: number;
  readonly sortOrder: string;
  readonly loading: boolean;
  readonly error: ArtifactLoadError | null;
  readonly onSortChange: (order: string) => void;
  readonly onPage: (index: number) => void;
}) {
  if (!page) return null;

  return (
    <section aria-labelledby="creators-heading">
      <h3 id="creators-heading">Creators</h3>

      <div className="control-row">
        <label htmlFor="creator-sort">Sort by</label>
        <select
          id="creator-sort"
          value={sortOrder}
          onChange={(event) => onSortChange(event.target.value)}
        >
          <option value="representedVideoCountDesc">
            Represented videos, most first
          </option>
          <option value="displayNameAsc">Channel name, A to Z</option>
        </select>
      </div>

      {page.totalRows === 0 ? (
        // Requirement 10.11: a validated shard with no publishable rows is
        // an empty state, and the aggregate totals above still stand.
        <div className="state-panel state-panel--empty" role="status">
          <h4>No creators are published for this country</h4>
          <p>
            The totals above still hold. Individual creators appear only when
            they meet the release&apos;s disclosure policy, so a country can
            have counted creators and no listed ones.
          </p>
        </div>
      ) : (
        <>
          <p role="status" aria-live="polite">
            {loading
              ? "Loading creators…"
              : `Showing ${formatCount(page.rows.length)} of ${formatCount(page.totalRows)} published creators` +
                (totalPages > 1
                  ? `, page ${pageIndex + 1} of ${totalPages}`
                  : "")}
          </p>

          {error && (
            <div className="state-panel state-panel--error" role="alert">
              <p>
                That page could not be loaded. The creators below are from the
                last page that was verified.
              </p>
            </div>
          )}

          <div className="scroll-x">
            <table className="data-table">
              <caption className="visually-hidden">
                Published creators for this country, with their represented
                video counts
              </caption>
              <thead>
                <tr>
                  <th scope="col">Channel</th>
                  <th scope="col" className="numeric">
                    Represented videos
                  </th>
                  <th scope="col">Datasets</th>
                  <th scope="col">Metadata read</th>
                </tr>
              </thead>
              <tbody>
                {page.rows.map((row) => (
                  <tr key={row.publicChannelKey}>
                    <th scope="row">{row.displayName}</th>
                    <td className="numeric">
                      {formatCount(row.representedVideoCount)}
                    </td>
                    <td>
                      {row.datasetBreakdown
                        .map(
                          (d) => `${d.datasetId} (${d.representedVideoCount})`,
                        )
                        .join(", ")}
                    </td>
                    <td>{formatDate(row.lastObservedAt)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {totalPages > 1 && (
            <nav className="pagination" aria-label="Creator pages">
              <button
                type="button"
                onClick={() => onPage(pageIndex - 1)}
                disabled={pageIndex === 0 || loading}
              >
                Previous
              </button>
              <span aria-current="page">
                Page {pageIndex + 1} of {totalPages}
              </span>
              <button
                type="button"
                onClick={() => onPage(pageIndex + 1)}
                disabled={pageIndex >= totalPages - 1 || loading}
              >
                Next
              </button>
            </nav>
          )}
        </>
      )}
    </section>
  );
}
