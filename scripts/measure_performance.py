"""Measure the delivery budgets Requirement 14 sets.

Two of the three bounds can be measured deterministically from build
output, and are:

  14.1  compressed manifest plus country summaries <= 250 KiB
  14.5  creator rows partitioned rather than shipped whole

The third cannot:

  14.3  Largest Contentful Paint <= 2.5s at the 75th percentile on a
        documented representative mobile profile

LCP is a field measurement over real page loads on real devices and
networks. Nothing computed here can establish it, and reporting a
transfer-size proxy as though it did would be worse than reporting
nothing — it would look like the requirement had been met. What this does
instead is measure the input that dominates LCP (bytes that must arrive
and parse before first contentful paint) and state plainly that the
requirement remains unverified until a field measurement exists.

Usage:
    python scripts/measure_performance.py <dist-dir> <web-out-dir>
"""

from __future__ import annotations

import gzip
import json
import sys
from dataclasses import dataclass
from pathlib import Path

#: Requirement 14.1, stated in KiB.
OVERVIEW_BUDGET_BYTES = 250 * 1024

#: The documented representative mobile profile. These are the conditions
#: a field measurement must be taken under for Requirement 14.3 to mean
#: anything; recording them here keeps the target from drifting.
REPRESENTATIVE_PROFILE = {
    "network": "4G, 9 Mbps down, 1.6 Mbps up, 170 ms RTT",
    "cpu": "4x slowdown against a 2024 reference laptop",
    "viewport": "412x915 CSS px, DPR 2.6",
    "cache": "cold, first visit",
    "percentile": "75th of at least 100 loads",
}


@dataclass(frozen=True, slots=True)
class Measurement:
    """One measured quantity and the bound it is checked against."""

    name: str
    requirement: str
    measured: int
    budget: int | None
    unit: str = "bytes"

    @property
    def within_budget(self) -> bool | None:
        """None when there is no budget to be within."""
        if self.budget is None:
            return None
        return self.measured <= self.budget

    def describe(self) -> str:
        if self.budget is None:
            return f"  {self.name}: {self.measured:,} {self.unit} (no bound)"
        verdict = "ok  " if self.within_budget else "OVER"
        pct = (self.measured / self.budget) * 100
        return (
            f"  [{verdict}] {self.name}: {self.measured:,} / {self.budget:,} "
            f"{self.unit} ({pct:.0f}%) — {self.requirement}"
        )


def gzipped_size(path: Path) -> int:
    """Transfer size a CDN would serve, at the compression it would use."""
    return len(gzip.compress(path.read_bytes(), 9))


def measure_overview_payload(dist: Path) -> Measurement:
    """Requirement 14.1: the default overview payload.

    Exactly what the requirement names — the manifest plus the country
    summaries — and nothing else. Country detail is excluded because 14.4
    defers it until requested, so counting it here would measure a payload
    no first visit downloads.
    """
    releases = sorted((dist / "releases").iterdir()) if (dist / "releases").is_dir() else []
    if not releases:
        return Measurement("overview payload", "14.1", 0, OVERVIEW_BUDGET_BYTES)

    release = releases[-1]
    total = 0
    for name in ("manifest.json", "overview.json"):
        target = release / name
        if target.is_file():
            total += gzipped_size(target)

    pointer = dist / "active-release.json"
    if pointer.is_file():
        total += gzipped_size(pointer)

    return Measurement("overview payload", "14.1", total, OVERVIEW_BUDGET_BYTES)


def measure_creator_pagination(dist: Path) -> list[Measurement]:
    """Requirement 14.5: creator rows partitioned, not shipped whole."""
    measurements: list[Measurement] = []
    releases = sorted((dist / "releases").iterdir()) if (dist / "releases").is_dir() else []
    if not releases:
        return measurements

    shards = sorted((releases[-1] / "countries").glob("*.json"))
    for shard in shards:
        payload = json.loads(shard.read_text(encoding="utf-8"))
        first_page = payload.get("firstPage", {})
        rows = len(first_page.get("rows", []))
        total_rows = int(first_page.get("totalRows", 0))

        # The shard must carry a page, not the whole set. A shard holding
        # every row would satisfy no bound and defeat the partitioning.
        measurements.append(
            Measurement(
                f"{payload.get('country', shard.stem)} first page rows",
                "14.5",
                rows,
                int(first_page.get("pageSize", 50)),
                unit="rows",
            )
        )
        if total_rows > rows:
            measurements.append(
                Measurement(
                    f"{payload.get('country', shard.stem)} shard bytes",
                    "14.5",
                    gzipped_size(shard),
                    None,
                    unit="bytes",
                )
            )
    return measurements


def measure_first_load_javascript(web_out: Path) -> Measurement:
    """The bytes that must arrive and parse before first contentful paint.

    This is *not* Requirement 14.3. It is the dominant input to it, and is
    reported so a regression is visible between field measurements —
    JavaScript growing by a megabyte will not improve LCP, and noticing
    that early is cheaper than noticing it in the field.
    """
    chunks = web_out / "_next" / "static" / "chunks"
    if not chunks.is_dir():
        return Measurement("first-load JavaScript", "14.3 (input)", 0, None)

    total = sum(gzipped_size(f) for f in chunks.rglob("*.js"))
    return Measurement("first-load JavaScript", "14.3 (input)", total, None)


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2

    dist, web_out = Path(argv[1]), Path(argv[2])

    print("Representative profile for any field measurement:")
    for key, value in REPRESENTATIVE_PROFILE.items():
        print(f"  {key:12} {value}")
    print()

    measurements = [
        measure_overview_payload(dist),
        measure_first_load_javascript(web_out),
        *measure_creator_pagination(dist),
    ]

    print("Measured:")
    for measurement in measurements:
        print(measurement.describe())

    over = [m for m in measurements if m.within_budget is False]

    print()
    print("Requirement 14.3 (LCP <= 2.5s at p75 mobile): NOT MEASURED HERE.")
    print("  LCP is a field measurement over real loads on real devices.")
    print("  No build-time number can establish it, and treating transfer")
    print("  size as a proxy would look like a pass without being one.")
    print("  It remains unverified until a field measurement exists under")
    print("  the profile above.")

    if over:
        print()
        print(f"FAILED: {len(over)} measurement(s) over budget")
        for measurement in over:
            print(f"  {measurement.name} ({measurement.requirement})")
        return 1

    print()
    print(f"PASSED: {len(measurements)} measurement(s), none over budget")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
