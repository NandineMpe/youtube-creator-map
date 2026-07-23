"use client";

import type { CountrySummary } from "@creator-map/shared-schemas";
import {
  Map as MapLibreMap,
  type ErrorEvent,
  type MapLayerMouseEvent,
  type StyleSpecification,
} from "maplibre-gl";
import { useEffect, useRef, useState } from "react";

import { binFor, type BinScale } from "../lib/bins";
import {
  countryLabel,
  formatCount,
  metricValue,
  type MetricKey,
} from "../lib/format";

/**
 * The choropleth.
 *
 * MapLibre renders to a WebGL canvas, which assistive technology cannot
 * read and a keyboard cannot traverse. Requirement 13.4 anticipates this:
 * country discovery and selection must be available through the country
 * table, and the table is authoritative rather than a fallback. This
 * component therefore carries `aria-hidden` on the canvas and points to the
 * table instead of attempting a synthetic focus layer over pixels, which
 * would announce coordinates rather than countries.
 *
 * Everything the map shows on hover is present in the table as text
 * (Requirement 13.8), so nothing here is the only route to a datum.
 *
 * Requirement refs: 6.8, 9.2-9.5, 9.9, 12.10, 12.11, 13.4, 13.8
 */

export interface ChoroplethMapProps {
  readonly countries: readonly CountrySummary[];
  readonly metric: MetricKey;
  readonly scale: BinScale;
  readonly selectedCountry: string | null;
  readonly onSelect: (country: string) => void;
  readonly boundariesUrl?: string;
}

interface BoundaryMetadata {
  readonly datasetName: string;
  readonly version: string;
  readonly attribution: string;
  readonly license: string;
  readonly disputedTerritoryTreatment: string;
}

/**
 * Read the ISO code from a clicked or hovered feature.
 *
 * GeoJSON properties are untyped by construction, so the value is narrowed
 * here rather than trusted. A feature without a well-formed code is treated
 * as no selection, which is the correct outcome: the boundary build already
 * excludes entities with no assigned ISO code, so anything else reaching
 * this point is a malformed source file rather than a country.
 */
function isoFromFeature(
  features: MapLayerMouseEvent["features"],
): string | null {
  const raw: unknown = features?.[0]?.properties?.iso;
  return typeof raw === "string" && /^[A-Z]{2}$/.test(raw) ? raw : null;
}

/** A minimal style: no basemap tiles, no external hosts. */
function emptyStyle(): StyleSpecification {
  return {
    version: 8,
    // No glyph or sprite URLs: both would be external requests the CSP
    // would have to allow, and neither is needed for flat country fills.
    sources: {},
    layers: [
      {
        id: "background",
        type: "background",
        paint: { "background-color": "#0a0c10" },
      },
    ],
  };
}

export function ChoroplethMap({
  countries,
  metric,
  scale,
  selectedCountry,
  onSelect,
  // The boundaries file is the app's own asset, so it lives under the
  // deployment base path. On GitHub Pages the app is served from a
  // project subpath, and a bare "/boundaries/..." would resolve against
  // the domain root and 404. `basePath` from next.config is inlined here
  // at build time; empty for a root deployment.
  boundariesUrl = `${process.env.NEXT_PUBLIC_BASE_PATH ?? ""}/boundaries/countries.json`,
}: ChoroplethMapProps) {
  const container = useRef<HTMLDivElement | null>(null);
  const map = useRef<MapLibreMap | null>(null);
  const [ready, setReady] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);
  const [metadata, setMetadata] = useState<BoundaryMetadata | null>(null);
  const [hovered, setHovered] = useState<string | null>(null);

  // Latest callback and data, read inside long-lived map handlers without
  // re-registering them on every render.
  const selectRef = useRef(onSelect);
  selectRef.current = onSelect;

  useEffect(() => {
    if (!container.current || map.current) return;

    const instance = new MapLibreMap({
      container: container.current,
      style: emptyStyle(),
      center: [0, 20],
      zoom: 1,
      minZoom: 0.5,
      maxZoom: 6,
      attributionControl: false,
      // The canvas is decorative here; the table is the accessible route,
      // so it should not become a tab stop that traps keyboard users on a
      // surface they cannot read.
      keyboard: false,
    });

    map.current = instance;

    instance.on("error", (event: ErrorEvent) => {
      setFailed(event.error?.message ?? "the map failed to initialise");
    });

    instance.on("load", () => {
      void (async () => {
        try {
          const response = await fetch(boundariesUrl);
          if (!response.ok) {
            throw new Error(`boundaries unavailable (${response.status})`);
          }
          const data = (await response.json()) as {
            metadata?: BoundaryMetadata;
            features: unknown[];
          };

          if (data.metadata) setMetadata(data.metadata);

          instance.addSource("countries", {
            type: "geojson",
            data: data as unknown as GeoJSON.FeatureCollection,
            // Feature state needs a stable id. Without promoteId, MapLibre
            // assigns indices that change between source updates, so a
            // colour set on one render would land on a different country
            // after the next filter change.
            promoteId: "iso",
          });

          instance.addLayer({
            id: "country-fill",
            type: "fill",
            source: "countries",
            paint: {
              "fill-color": ["coalesce", ["feature-state", "color"], "#262c36"],
              "fill-opacity": 1,
            },
          });

          instance.addLayer({
            id: "country-border",
            type: "line",
            source: "countries",
            paint: { "line-color": "#3d4757", "line-width": 0.5 },
          });

          instance.addLayer({
            id: "country-selected",
            type: "line",
            source: "countries",
            paint: {
              "line-color": "#ffd166",
              "line-width": [
                "case",
                ["boolean", ["feature-state", "selected"], false],
                2.5,
                0,
              ],
            },
          });

          instance.on("click", "country-fill", (event: MapLayerMouseEvent) => {
            const iso = isoFromFeature(event.features);
            if (iso) selectRef.current(iso);
          });

          instance.on(
            "mousemove",
            "country-fill",
            (event: MapLayerMouseEvent) => {
              setHovered(isoFromFeature(event.features));
              instance.getCanvas().style.cursor = "pointer";
            },
          );

          instance.on("mouseleave", "country-fill", () => {
            setHovered(null);
            instance.getCanvas().style.cursor = "";
          });

          setReady(true);
        } catch (error) {
          setFailed(
            error instanceof Error
              ? error.message
              : "boundaries failed to load",
          );
        }
      })();
    });

    return () => {
      instance.remove();
      map.current = null;
    };
  }, [boundariesUrl]);

  // Repaint on data, metric, or scale change. Feature state rather than a
  // style rebuild, so a filter change is a paint rather than a reload.
  useEffect(() => {
    const instance = map.current;
    if (!instance || !ready) return;

    // Driven from the country list rather than from rendered features:
    // querySourceFeatures only returns what is currently in the viewport,
    // so a country scrolled off-screen would keep a stale colour until it
    // happened to be panned back into view.
    for (const summary of countries) {
      const bin = binFor(metricValue(summary, metric), scale);
      instance.setFeatureState(
        { source: "countries", id: summary.country },
        {
          color: bin?.color ?? scale.noDataColor,
          selected: summary.country === selectedCountry,
        },
      );
    }

    // Countries absent from this release keep the no-data fill, which the
    // paint expression already supplies as its coalesce fallback. Only the
    // selection flag needs clearing, in case the selection moved away.
    if (selectedCountry) {
      instance.setFeatureState(
        { source: "countries", id: selectedCountry },
        { selected: true },
      );
    }
  }, [countries, metric, scale, selectedCountry, ready]);

  const hoveredSummary = hovered
    ? countries.find((c) => c.country === hovered)
    : undefined;

  return (
    <div className="map-panel">
      <div
        ref={container}
        className="map-canvas"
        // The canvas conveys nothing a screen reader can use; the country
        // table carries the same values as text (Requirement 13.8).
        aria-hidden="true"
      />

      {failed && (
        <div className="map-panel__notice" role="status">
          <p>
            The map could not be drawn ({failed}). The country table below
            carries the same figures.
          </p>
        </div>
      )}

      {/* Hover preview. Duplicated from the table rather than exclusive to
          it, so no information lives only behind a pointer. */}
      {hoveredSummary && (
        <div className="map-hover" aria-hidden="true">
          <strong>{countryLabel(hoveredSummary.country)}</strong>
          <span>
            {formatCount(metricValue(hoveredSummary, metric))}{" "}
            {metric === "creators"
              ? "creators"
              : metric === "videos"
                ? "represented videos"
                : "source occurrences"}
          </span>
        </div>
      )}

      <p className="visually-hidden">
        This map is a visual summary. Every country and value it shows is listed
        in the country table, which supports keyboard and screen reader use.
      </p>

      {metadata && (
        // Requirement 12.10/12.11: boundary provenance travels with the
        // map, and states plainly that borders are not location evidence.
        <p className="map-attribution">
          {metadata.attribution} · {metadata.datasetName} {metadata.version} ·{" "}
          {metadata.license}. {metadata.disputedTerritoryTreatment}
        </p>
      )}
    </div>
  );
}
