import type {
  CoverageSummary,
  OverviewArtifact,
} from "@creator-map/shared-schemas";

import { formatCount, formatPercent } from "../lib/format";

/**
 * Headline totals with their counting units and coverage context.
 *
 * Requirement 12.8 requires every public aggregate to carry its counting
 * unit and coverage context, which is why each card states what it counts
 * rather than leaving "49,363" to be interpreted. A number without its unit
 * invites the reader to supply one, and the unit is precisely what
 * distinguishes represented videos from source occurrences.
 *
 * Requirement refs: 9.1, 12.3, 12.4, 12.8, 13.1
 */
export function HeadlineMetrics({
  overview,
}: {
  readonly overview: OverviewArtifact;
}) {
  const { coverage } = overview;

  return (
    <section aria-labelledby="headline-heading">
      <h2 id="headline-heading" className="visually-hidden">
        Headline totals
      </h2>

      <ul className="metric-grid">
        <li className="metric-card">
          <span className="metric-card__value">
            {formatCount(overview.creatorCount)}
          </span>
          <span className="metric-card__label">Creators</span>
          <span className="metric-card__note">
            Distinct channels resolved within this filter
          </span>
        </li>

        <li className="metric-card">
          <span className="metric-card__value">
            {formatCount(overview.representedVideoCount)}
          </span>
          <span className="metric-card__label">Represented videos</span>
          <span className="metric-card__note">
            Distinct source-video identifiers, counted once each
          </span>
        </li>

        <li className="metric-card">
          <span className="metric-card__value">
            {formatCount(coverage.inputOccurrenceCount)}
          </span>
          <span className="metric-card__label">Source occurrences</span>
          <span className="metric-card__note">
            Retained source records; higher than the video count because sources
            repeat
          </span>
        </li>

        <li className="metric-card">
          <span className="metric-card__value">
            {formatCount(overview.representedCountryCount)}
          </span>
          <span className="metric-card__label">Countries represented</span>
          <span className="metric-card__note">
            {coverage.unknownCountryChannelCount > 0
              ? `Excludes ${formatCount(coverage.unknownCountryChannelCount)} channels with no declared country`
              : "Channels with no declared country are excluded"}
          </span>
        </li>
      </ul>
    </section>
  );
}

/**
 * The resolution partition, shown beside the headline totals.
 *
 * Requirement 6.7 requires every partition state to appear with the
 * totals, not behind a disclosure. Publishing only the resolved count
 * would let a visitor read partial coverage as complete coverage.
 *
 * Requirement refs: 6.2-6.4, 6.7, 6.9
 */
export function CoveragePanel({
  coverage,
}: {
  readonly coverage: CoverageSummary;
}) {
  const { partition } = coverage;
  const total = coverage.distinctInputVideoCount;

  const states = [
    {
      key: "resolved",
      label: "Resolved",
      value: partition.resolved,
      note: "Matched to a channel.",
    },
    {
      key: "unavailableUnclassified",
      label: "Unavailable",
      value: partition.unavailableUnclassified,
      // Requirement 6.9: the API does not say why, so neither do we.
      note: "The metadata API did not return these and did not say why.",
    },
    {
      key: "retryableOrPending",
      label: "Pending",
      value: partition.retryableOrPending,
      note: "Not yet resolved at this release's cutoff.",
    },
    {
      key: "invalid",
      label: "Invalid identifier",
      value: partition.invalid,
      note: "Did not match the supported identifier grammar.",
    },
    {
      key: "terminalFailure",
      label: "Could not resolve",
      value: partition.terminalFailure,
      note: "Resolution failed permanently under the retry policy.",
    },
  ];

  return (
    <section aria-labelledby="coverage-heading">
      <h2 id="coverage-heading">Coverage</h2>
      <p>
        Of {formatCount(total)} distinct video identifiers in this filter, each
        falls into exactly one state below. Unresolved identifiers are counted
        here but are not attributed to any country.
      </p>

      <div className="scroll-x">
        <table className="data-table">
          <caption>Resolution state of every distinct video identifier</caption>
          <thead>
            <tr>
              <th scope="col">State</th>
              <th scope="col" className="numeric">
                Videos
              </th>
              <th scope="col" className="numeric">
                Share
              </th>
              <th scope="col">What it means</th>
            </tr>
          </thead>
          <tbody>
            {states.map((state) => (
              <tr key={state.key}>
                <th scope="row">{state.label}</th>
                <td className="numeric">{formatCount(state.value)}</td>
                <td className="numeric">{formatPercent(state.value, total)}</td>
                <td>{state.note}</td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr>
              <th scope="row">Total</th>
              <td className="numeric">{formatCount(total)}</td>
              <td className="numeric">100%</td>
              <td>Every identifier is counted exactly once.</td>
            </tr>
          </tfoot>
        </table>
      </div>

      <h3>Channels</h3>
      <div className="scroll-x">
        <table className="data-table">
          <caption>
            Whether resolved channels declared a country in their metadata
          </caption>
          <thead>
            <tr>
              <th scope="col">Channels</th>
              <th scope="col" className="numeric">
                Count
              </th>
              <th scope="col" className="numeric">
                Share
              </th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <th scope="row">Declared a country</th>
              <td className="numeric">
                {formatCount(coverage.knownCountryChannelCount)}
              </td>
              <td className="numeric">
                {formatPercent(
                  coverage.knownCountryChannelCount,
                  coverage.resolvedChannelCount,
                )}
              </td>
            </tr>
            <tr>
              <th scope="row">No declared country</th>
              <td className="numeric">
                {formatCount(coverage.unknownCountryChannelCount)}
              </td>
              <td className="numeric">
                {formatPercent(
                  coverage.unknownCountryChannelCount,
                  coverage.resolvedChannelCount,
                )}
              </td>
            </tr>
          </tbody>
          <tfoot>
            <tr>
              <th scope="row">Resolved channels</th>
              <td className="numeric">
                {formatCount(coverage.resolvedChannelCount)}
              </td>
              <td className="numeric">100%</td>
            </tr>
          </tfoot>
        </table>
      </div>
    </section>
  );
}
