"""Tests for the deterministic synthetic release fixture.

The fixture's job is to let the cross-stack contract be checked without a
database, so what matters is that it is genuinely deterministic and that
it is a *valid* release rather than a plausible-looking one. A fixture
that failed the gates would send someone debugging the wrong thing.

Requirement refs: 8.1, 8.8
"""

from __future__ import annotations

from creator_map_pipeline.aggregate.fixtures import (
    FIXTURE_COUNTRIES,
    build_fixture_release,
)
from creator_map_pipeline.release.gates import GateOutcome, run_gates
from creator_map_pipeline.release.manager import candidate_from_artifacts


def candidate(fixture=None):  # type: ignore[no-untyped-def]
    fixture = fixture or build_fixture_release()
    return candidate_from_artifacts(
        fixture.release_id,
        list(fixture.artifacts),
        signoff_actor="fixture-curator",
        vulnerability_scan={"completed": True, "blockingFindings": 0},
    )


# --- Determinism -----------------------------------------------------------


def test_same_seed_produces_identical_bytes() -> None:
    """A fixture that drifted between runs would fail the contract test
    for reasons having nothing to do with the contract."""
    first = build_fixture_release()
    second = build_fixture_release()

    assert [a.path for a in first.artifacts] == [a.path for a in second.artifacts]
    assert [a.digest for a in first.artifacts] == [a.digest for a in second.artifacts]


def test_different_seeds_produce_different_data() -> None:
    """Otherwise the seed parameter is decoration."""
    default = build_fixture_release()
    other = build_fixture_release(seed="a-different-seed")

    assert default.by_path("overview.json").digest != other.by_path("overview.json").digest


def test_no_module_state_leaks_between_builds() -> None:
    """Built with digests rather than `random`, so interleaving two
    builds cannot change either one."""
    a = build_fixture_release()
    _ = build_fixture_release(seed="interleaved")
    b = build_fixture_release()

    assert a.by_path("manifest.json").digest == b.by_path("manifest.json").digest


# --- It is a valid release -------------------------------------------------


def test_the_fixture_passes_every_release_gate() -> None:
    """The point of the fixture. If it cannot pass the gates, the
    contract test is exercising something the pipeline would reject."""
    report = run_gates(candidate())

    assert report.passed, report.describe()


def test_arithmetic_reconciles_by_construction() -> None:
    result = next(r for r in run_gates(candidate()).results if r.name == "arithmetic")

    assert result.outcome is GateOutcome.PASSED


def test_disclosure_gate_finds_nothing_prohibited() -> None:
    """No raw channel ids, no restricted values."""
    result = next(r for r in run_gates(candidate()).results if r.name == "disclosure")

    assert result.outcome is GateOutcome.PASSED


# --- Shape -----------------------------------------------------------------


def test_every_country_gets_a_shard() -> None:
    fixture = build_fixture_release()
    paths = {a.path for a in fixture.artifacts}

    for country, _ in FIXTURE_COUNTRIES:
        assert any(p.endswith(f"countries/{country}.json") for p in paths), country


def test_the_unknown_bucket_uses_a_code_that_cannot_collide() -> None:
    """ "XX" is user-assigned in ISO 3166 and will never be issued, so it
    cannot be mistaken for a real country."""
    codes = {country for country, _ in FIXTURE_COUNTRIES}

    assert "XX" in codes
    assert "Unknown" not in codes


def test_counts_derive_from_the_rows_they_summarize() -> None:
    fixture = build_fixture_release()
    overview = fixture.by_path("overview.json").payload

    total = sum(int(c["creatorCount"]) for c in overview["countries"])
    assert overview["creatorCount"] == total
    assert total == sum(count for _, count in FIXTURE_COUNTRIES)


def test_dataset_breakdown_does_not_sum_to_the_represented_total() -> None:
    """A video seen in two datasets is one represented video but two
    dataset observations. A fixture where these summed cleanly would let
    a client-side sum look correct when it is not."""
    fixture = build_fixture_release()
    detail = fixture.by_path("countries/US.json").payload

    breakdown_total = sum(int(d["representedVideoCount"]) for d in detail["datasetBreakdown"])

    assert breakdown_total > int(detail["representedVideoCount"])


def test_pages_cover_every_row() -> None:
    fixture = build_fixture_release(page_size=10)
    detail = fixture.by_path("countries/US.json").payload

    for sort_key, paths in detail["pageIndex"].items():
        rows = 0
        for path in paths:
            # Exact match: pageIndex paths are release-rooted and must
            # resolve as published, not after trimming a prefix.
            page = next(a for a in fixture.artifacts if a.path == path)
            rows += len(page.payload["rows"])
        assert rows == detail["creatorCount"], sort_key


def test_creator_rows_are_ordered_as_claimed() -> None:
    fixture = build_fixture_release()
    page = fixture.by_path("countries/US/representedVideoCountDesc/page-0.json").payload

    counts = [int(r["representedVideoCount"]) for r in page["rows"]]
    assert counts == sorted(counts, reverse=True)


def test_no_raw_channel_id_reaches_a_published_row() -> None:
    """The disclosure property, checked on the fixture itself rather than
    only through the gate."""
    fixture = build_fixture_release()
    page = fixture.by_path("countries/ZA/representedVideoCountDesc/page-0.json").payload

    for row in page["rows"]:
        assert str(row["publicChannelKey"]).startswith("pk_")
        assert "UC" not in str(row["publicChannelKey"])


def test_manifest_digests_cover_every_artifact() -> None:
    fixture = build_fixture_release()
    manifest = fixture.by_path("manifest.json")

    recorded = manifest.payload["artifactDigests"]
    for artifact in fixture.artifacts:
        if artifact.path == manifest.path:
            continue
        assert recorded[artifact.path] == artifact.digest
