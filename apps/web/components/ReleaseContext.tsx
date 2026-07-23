import type { ReleaseManifest } from "@creator-map/shared-schemas";

import { formatInstant } from "../lib/format";

/**
 * Release and filter context, shown with every set of figures.
 *
 * Requirement 12.8 requires every public aggregate to identify its release,
 * active filter, counting unit, and methodology version. Figures without
 * that context are unfalsifiable: a reader cannot tell which snapshot they
 * describe or when the channel metadata was read, and the enrichment cutoff
 * is precisely what makes a country figure a claim about a moment rather
 * than about the present.
 *
 * Requirement refs: 9.1, 12.2, 12.8
 */
export function ReleaseContext({
  manifest,
  datasetCount,
}: {
  readonly manifest: ReleaseManifest;
  readonly datasetCount: number;
}) {
  return (
    <div className="release-context">
      <span>
        Release{" "}
        <span className="release-context__value">{manifest.releaseId}</span>
      </span>
      <span>
        Published{" "}
        <span className="release-context__value">
          {formatInstant(manifest.generatedAt)}
        </span>
      </span>
      <span>
        Channel metadata read{" "}
        <span className="release-context__value">
          {formatInstant(manifest.enrichmentCutoff)}
        </span>
      </span>
      <span>
        Datasets <span className="release-context__value">{datasetCount}</span>
      </span>
      <span>
        Methodology{" "}
        <span className="release-context__value">
          {manifest.methodologyVersion}
        </span>
      </span>
    </div>
  );
}

/**
 * The dataset citations backing a release.
 *
 * Requirement 1.9 names exactly which fields must be displayed for each
 * included dataset. The snapshot digest is included because it is what
 * makes "observed in a named, versioned snapshot" checkable rather than
 * merely asserted.
 *
 * Requirement refs: 1.9, 12.1, 12.7
 */
export function DatasetCitations({
  manifest,
}: {
  readonly manifest: ReleaseManifest;
}) {
  return (
    <section aria-labelledby="datasets-heading">
      <h2 id="datasets-heading">Datasets in this release</h2>
      <p>
        Each entry below is a specific, digest-pinned snapshot. A video appears
        in this map because its identifier was observed in one of them.
      </p>

      <div className="scroll-x">
        <table className="data-table">
          <caption className="visually-hidden">
            Dataset citations with version, source kind, occurrence unit, and
            snapshot digest
          </caption>
          <thead>
            <tr>
              <th scope="col">Dataset</th>
              <th scope="col">Version</th>
              <th scope="col">Corpus</th>
              <th scope="col">Source kind</th>
              <th scope="col">Counts</th>
              <th scope="col">Snapshot</th>
            </tr>
          </thead>
          <tbody>
            {manifest.datasets.map((dataset) => (
              <tr key={`${dataset.datasetId}@${dataset.version}`}>
                <th scope="row">
                  <a
                    href={dataset.sourceCitation}
                    rel="noreferrer noopener"
                    target="_blank"
                  >
                    {dataset.displayName}
                  </a>
                </th>
                <td>{dataset.version}</td>
                <td>{dataset.corpusClass}</td>
                <td>{dataset.sourceKind}</td>
                <td>{dataset.occurrenceUnit}</td>
                <td>
                  <code className="digest">
                    {dataset.snapshotDigest.replace("sha256:", "").slice(0, 12)}
                  </code>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Requirement 12.7: subtotals overlap and must not be added. */}
      <p className="callout">
        The same video can appear in several datasets. It counts once in a
        combined total but once in each dataset it belongs to, so adding the
        per-dataset figures together will overshoot the combined number.
      </p>
    </section>
  );
}
