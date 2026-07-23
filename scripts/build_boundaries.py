"""Build the shipped country-boundary file from Natural Earth source.

The published Natural Earth GeoJSON is ~839 KB, most of which is metadata
the map never reads and coordinate precision no world-scale choropleth can
resolve. This strips both.

Coordinate precision is the significant reduction. Natural Earth stores
around 12 decimal places, which is sub-millimetre; at a world-map zoom one
screen pixel spans roughly 0.05 degrees, so anything past three decimals is
invisible. Rounding is lossy on purpose and the loss is below what any
viewer can perceive.

Requirement 12.10 requires the boundary dataset version, attribution,
license, and disputed-territory treatment to be documented, so those travel
with the data rather than living only in prose.

Usage:
    python scripts/build_boundaries.py <source.geojson> <output.json>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

#: Three decimals is ~110 m at the equator, well below one pixel at world
#: zoom. Going finer inflates the payload without changing any rendered
#: outline.
COORDINATE_PRECISION = 3

#: Natural Earth's ISO field. The _EH variant resolves several disputed
#: cases to the code the ISO standard actually assigns, which is what the
#: channel metadata will report.
ISO_FIELD = "ISO_A2_EH"

#: Provenance shipped with the geometry (Requirement 12.10).
BOUNDARY_METADATA = {
    "datasetName": "Natural Earth Admin 0 - Countries",
    "version": "5.1.1",
    "scale": "1:110m",
    "license": "Public domain",
    "attribution": "Made with Natural Earth",
    "sourceUrl": "https://www.naturalearthdata.com/",
    "disputedTerritoryTreatment": (
        "Boundaries and names follow Natural Earth's cartographic "
        "conventions. They are a presentation choice made for legibility, "
        "not a position on any territorial question, and they are not "
        "evidence of where a channel or its operator is located."
    ),
}


def round_coordinates(node: Any, precision: int) -> Any:
    """Recursively round every coordinate in a nested position array."""
    if isinstance(node, list):
        if node and isinstance(node[0], int | float):
            return [round(float(value), precision) for value in node]
        return [round_coordinates(item, precision) for item in node]
    return node


def simplify(source: dict[str, Any]) -> dict[str, Any]:
    """Strip properties and reduce precision, keeping geometry intact."""
    features: list[dict[str, Any]] = []
    skipped: list[str] = []

    for feature in source.get("features", []):
        properties = feature.get("properties", {})
        iso = properties.get(ISO_FIELD)
        name = properties.get("NAME")

        # Natural Earth marks entities with no assigned ISO code as "-99".
        # Rendering those would put a shape on the map that no channel
        # metadata could ever match, so they are excluded rather than
        # given an invented code.
        if not iso or iso == "-99" or len(iso) != 2:
            skipped.append(str(name))
            continue

        features.append(
            {
                "type": "Feature",
                # Only the two fields the map reads. Natural Earth carries
                # ~95 properties per feature; shipping them would add
                # hundreds of kilobytes the application never touches.
                "properties": {"iso": iso.upper(), "name": name},
                "geometry": {
                    "type": feature["geometry"]["type"],
                    "coordinates": round_coordinates(
                        feature["geometry"]["coordinates"], COORDINATE_PRECISION
                    ),
                },
            }
        )

    features.sort(key=lambda f: f["properties"]["iso"])

    return {
        "type": "FeatureCollection",
        "metadata": BOUNDARY_METADATA,
        "features": features,
    }, skipped


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2

    source_path, output_path = Path(argv[1]), Path(argv[2])
    source = json.loads(source_path.read_text(encoding="utf-8"))

    result, skipped = simplify(source)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(result, separators=(",", ":"), sort_keys=True),
        encoding="utf-8",
    )

    original = source_path.stat().st_size
    reduced = output_path.stat().st_size
    print(f"features:  {len(result['features'])}")
    print(f"skipped:   {len(skipped)} without an assigned ISO code")
    if skipped:
        print(f"           {', '.join(sorted(skipped)[:6])}")
    print(f"source:    {original:,} bytes")
    print(f"output:    {reduced:,} bytes ({reduced / original:.1%})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
