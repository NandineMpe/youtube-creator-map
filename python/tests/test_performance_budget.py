"""Tests for the delivery-budget measurements.

The point of these is to check that the measurement itself is honest:
that it measures what the requirement names, that it fails when a budget
is exceeded, and — most importantly — that it does not claim to have
verified Requirement 14.3 when it has not.

Requirement refs: 14.1, 14.3, 14.5
"""

from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2] / "scripts"))

from measure_performance import (  # noqa: E402
    OVERVIEW_BUDGET_BYTES,
    REPRESENTATIVE_PROFILE,
    Measurement,
    gzipped_size,
    main,
    measure_creator_pagination,
    measure_overview_payload,
)


def write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def build_dist(tmp_path: Path, *, rows: int = 50, total_rows: int = 500) -> Path:
    dist = tmp_path / "dist"
    release = dist / "releases" / "r1"

    write(release / "manifest.json", {"releaseId": "r1", "artifactDigests": {}})
    write(release / "overview.json", {"releaseId": "r1", "countries": []})
    write(dist / "active-release.json", {"releaseId": "r1"})
    write(
        release / "countries" / "DE.json",
        {
            "country": "DE",
            "firstPage": {
                "rows": [{"publicChannelKey": f"pk_{i}"} for i in range(rows)],
                "pageSize": 50,
                "totalRows": total_rows,
            },
        },
    )
    return dist


# --- Requirement 14.1 -----------------------------------------------------


def test_overview_payload_measures_manifest_and_summaries(tmp_path: Path) -> None:
    dist = build_dist(tmp_path)
    measurement = measure_overview_payload(dist)

    assert measurement.requirement == "14.1"
    assert measurement.budget == OVERVIEW_BUDGET_BYTES
    assert measurement.measured > 0
    assert measurement.within_budget is True


def test_overview_payload_excludes_country_detail(tmp_path: Path) -> None:
    """Requirement 14.4 defers detail, so counting it would measure a
    payload no first visit downloads."""
    small = build_dist(tmp_path / "a")
    large = build_dist(tmp_path / "b", rows=50, total_rows=500)

    # Inflate only the country shard.
    shard = large / "releases" / "r1" / "countries" / "DE.json"
    payload = json.loads(shard.read_text(encoding="utf-8"))
    payload["padding"] = "x" * 400_000
    shard.write_text(json.dumps(payload), encoding="utf-8")

    assert measure_overview_payload(small).measured == measure_overview_payload(large).measured


def test_oversized_overview_is_reported_over_budget(tmp_path: Path) -> None:
    import secrets

    dist = build_dist(tmp_path)
    overview = dist / "releases" / "r1" / "overview.json"
    # Incompressible, so gzip cannot mask the size.
    write(overview, {"countries": [secrets.token_hex(32) for _ in range(12_000)]})

    measurement = measure_overview_payload(dist)
    assert measurement.within_budget is False


def test_missing_release_does_not_crash(tmp_path: Path) -> None:
    measurement = measure_overview_payload(tmp_path / "empty")
    assert measurement.measured == 0


# --- Requirement 14.5 -----------------------------------------------------


def test_pagination_measures_first_page_against_page_size(tmp_path: Path) -> None:
    dist = build_dist(tmp_path, rows=50, total_rows=500)
    measurements = measure_creator_pagination(dist)

    page = next(m for m in measurements if "first page rows" in m.name)
    assert page.requirement == "14.5"
    assert page.measured == 50
    assert page.within_budget is True


def test_shipping_every_row_in_the_shard_is_over_budget(tmp_path: Path) -> None:
    """A shard carrying all 500 rows defeats the partitioning entirely."""
    dist = build_dist(tmp_path, rows=500, total_rows=500)
    measurements = measure_creator_pagination(dist)

    page = next(m for m in measurements if "first page rows" in m.name)
    assert page.within_budget is False


# --- Honesty about what is not measured -----------------------------------


def test_lcp_is_not_reported_as_measured(tmp_path: Path, capsys) -> None:
    """The one that matters.

    Requirement 14.3 is a field measurement. A build-time script that
    printed a pass for it would be worse than one that printed nothing,
    because it would look like the requirement had been met.
    """
    dist = build_dist(tmp_path)
    web_out = tmp_path / "out"
    web_out.mkdir()

    main(["measure_performance.py", str(dist), str(web_out)])
    output = capsys.readouterr().out

    assert "NOT MEASURED HERE" in output
    assert "remains unverified" in output

    # The passing tally must not imply 14.3 was among what passed. It
    # counts only measurements with a budget, and 14.3 has none here.
    passed_line = next(line for line in output.splitlines() if line.startswith("PASSED"))
    assert "14.3" not in passed_line


def test_javascript_size_carries_no_budget(tmp_path: Path, capsys) -> None:
    """Reported to catch regressions, not to certify a bound it cannot."""
    dist = build_dist(tmp_path)
    web_out = tmp_path / "out"
    (web_out / "_next" / "static" / "chunks").mkdir(parents=True)
    (web_out / "_next" / "static" / "chunks" / "a.js").write_text("x" * 5000)

    main(["measure_performance.py", str(dist), str(web_out)])
    output = capsys.readouterr().out

    assert "first-load JavaScript" in output
    assert "no bound" in output


def test_representative_profile_is_documented() -> None:
    """Requirement 14.3 names a documented profile; an undocumented one
    lets the target drift until any number passes."""
    for key in ("network", "cpu", "viewport", "cache", "percentile"):
        assert key in REPRESENTATIVE_PROFILE
        assert REPRESENTATIVE_PROFILE[key]


# --- Exit behaviour --------------------------------------------------------


def test_exits_nonzero_when_over_budget(tmp_path: Path) -> None:
    import secrets

    dist = build_dist(tmp_path)
    write(
        dist / "releases" / "r1" / "overview.json",
        {"countries": [secrets.token_hex(32) for _ in range(12_000)]},
    )
    web_out = tmp_path / "out"
    web_out.mkdir()

    assert main(["measure_performance.py", str(dist), str(web_out)]) == 1


def test_exits_zero_when_within_budget(tmp_path: Path) -> None:
    dist = build_dist(tmp_path)
    web_out = tmp_path / "out"
    web_out.mkdir()

    assert main(["measure_performance.py", str(dist), str(web_out)]) == 0


def test_measurement_without_a_budget_is_neither_pass_nor_fail() -> None:
    measurement = Measurement("x", "14.3 (input)", 100, None)
    assert measurement.within_budget is None
    assert "no bound" in measurement.describe()


def test_gzipped_size_reflects_transfer_not_disk(tmp_path: Path) -> None:
    target = tmp_path / "f.json"
    target.write_text("a" * 10_000, encoding="utf-8")

    assert gzipped_size(target) < 10_000
    assert gzipped_size(target) == len(gzip.compress(target.read_bytes(), 9))
