import type { BinScale } from "../lib/bins";
import { metricDefinition, type MetricKey } from "../lib/format";

/**
 * The choropleth legend.
 *
 * Requirement 9.4 requires exact bin ranges and a distinct no-data style.
 * "Low" to "high" would be unfalsifiable; a reader could not check whether
 * a country belongs where it sits. Stating the numeric range for every
 * colour makes the encoding legible and auditable.
 *
 * Requirement refs: 9.4, 9.5, 13.1, 13.8
 */
export function Legend({
  scale,
  metric,
}: {
  readonly scale: BinScale;
  readonly metric: MetricKey;
}) {
  const definition = metricDefinition(metric);

  if (scale.bins.length === 0) {
    return (
      <div className="legend" role="note">
        <p className="legend__title">
          No {definition.label.toLowerCase()} to display for this filter.
        </p>
      </div>
    );
  }

  return (
    <div className="legend">
      <h3 className="legend__title" id="legend-heading">
        {definition.label}
      </h3>
      <p className="legend__unit">{definition.unit}</p>

      <ul className="legend__scale" aria-labelledby="legend-heading">
        {scale.bins.map((bin) => (
          <li key={`${bin.min}-${bin.max}`} className="legend__item">
            <span
              className="legend__swatch"
              style={{ background: bin.color }}
              aria-hidden="true"
            />
            <span className="legend__label">{bin.label}</span>
          </li>
        ))}
        <li className="legend__item">
          {/* No-data is a category, deliberately outside the sequential
              ramp: a country absent from the release is not a country with
              a low count. */}
          <span
            className="legend__swatch legend__swatch--no-data"
            style={{ background: scale.noDataColor }}
            aria-hidden="true"
          />
          <span className="legend__label">No data in this release</span>
        </li>
      </ul>

      {scale.method === "quantile" && (
        <p className="legend__method">
          Ranges are quantiles of the values in this filter, so they change when
          the filter changes.
        </p>
      )}
    </div>
  );
}
