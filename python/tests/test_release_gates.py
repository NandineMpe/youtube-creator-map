"""Tests for the release validation gates.

Requirement refs: 8.1-8.3, 12.12, 12.13, 14.1, 15.14, 15.15
"""

from __future__ import annotations

from typing import Any

import pytest
from creator_map_pipeline.aggregate.artifacts import GeneratedArtifact
from creator_map_pipeline.release.gates import (
    GateOutcome,
    ReleaseCandidate,
    gate_arithmetic,
    gate_creator_pagination,
    gate_dependency_scan,
    gate_digests,
    gate_disclosure,
    gate_manifest,
    gate_neutral_language,
    gate_payload_budget,
    gate_provenance,
    gate_signoff,
    run_gates,
)

DIGEST = "sha256:" + "a" * 64


def coverage_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "inputOccurrenceCount": 120,
        "distinctInputVideoCount": 100,
        "partition": {
            "resolved": 70,
            "unavailableUnclassified": 15,
            "retryableOrPending": 10,
            "invalid": 3,
            "terminalFailure": 2,
        },
        "resolvedChannelCount": 40,
        "knownCountryChannelCount": 30,
        "unknownCountryChannelCount": 10,
    }
    payload.update(overrides)
    return payload


def overview_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schemaVersion": "1.0.0",
        "releaseId": "r1",
        "filter": {"datasets": ["ds-a"], "corpusClasses": ["Candidate"]},
        "countries": [
            {
                "country": "DE",
                "creatorCount": 20,
                "representedVideoCount": 60,
                "sourceOccurrenceCount": 70,
                "resolvedVideoCount": 60,
                "unavailableVideoCount": 0,
            }
        ],
        "coverage": coverage_payload(),
        "creatorCount": 40,
        "representedVideoCount": 100,
        "representedCountryCount": 1,
    }
    payload.update(overrides)
    return payload


def manifest_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schemaVersion": "1.0.0",
        "releaseId": "r1",
        "generatedAt": "2026-01-15T12:00:00Z",
        "enrichmentCutoff": "2026-01-15T11:00:00Z",
        "defaultFilter": {"datasets": ["ds-a"], "corpusClasses": ["Candidate"]},
        "datasets": [
            {
                "datasetId": "ds-a",
                "displayName": "DS A",
                "version": "v1",
                "corpusClass": "Candidate",
                "sourceKind": "MetadataOnly",
                "occurrenceUnit": "Row",
                "sourceCitation": "https://example.invalid/ds",
                "snapshotDigest": DIGEST,
            }
        ],
        "artifactDigests": {},
        "methodologyVersion": "1.0.0",
        "disclosurePolicyVersion": "1.0.0",
        "boundaryMetadata": {"datasetName": "Natural Earth", "version": "5.1.1"},
    }
    payload.update(overrides)
    return payload


def candidate(
    *,
    overview: dict[str, Any] | None = None,
    manifest: dict[str, Any] | None = None,
    extra: list[GeneratedArtifact] | None = None,
    signoff: str | None = "curator",
    scan: dict[str, Any] | None = None,
) -> ReleaseCandidate:
    overview_artifact = GeneratedArtifact(
        path="releases/r1/overview.json", payload=overview or overview_payload()
    ).finalize()

    artifacts = [overview_artifact, *(extra or [])]

    resolved_manifest = manifest if manifest is not None else manifest_payload()
    if "artifactDigests" in resolved_manifest and not resolved_manifest["artifactDigests"]:
        resolved_manifest = {
            **resolved_manifest,
            "artifactDigests": {a.path: a.digest for a in artifacts},
        }

    manifest_artifact = GeneratedArtifact(
        path="releases/r1/manifest.json", payload=resolved_manifest
    ).finalize()

    return ReleaseCandidate(
        release_id="r1",
        artifacts=(*artifacts, manifest_artifact),
        manifest=resolved_manifest,
        signoff_actor=signoff,
        vulnerability_scan=scan if scan is not None else {"completed": True, "blockingFindings": 0},
    )


# --- Requirement 8.2: arithmetic reconciliation ---------------------------


def test_arithmetic_passes_on_consistent_counts() -> None:
    assert gate_arithmetic(candidate()).outcome is GateOutcome.PASSED


def test_arithmetic_rejects_a_partition_that_does_not_sum() -> None:
    broken = overview_payload(
        coverage=coverage_payload(
            partition={
                "resolved": 69,
                "unavailableUnclassified": 15,
                "retryableOrPending": 10,
                "invalid": 3,
                "terminalFailure": 2,
            }
        )
    )
    result = gate_arithmetic(candidate(overview=broken))
    assert result.outcome is GateOutcome.FAILED
    assert any("partition sums" in r for r in result.reasons)


def test_arithmetic_rejects_channel_counts_that_do_not_split() -> None:
    broken = overview_payload(coverage=coverage_payload(knownCountryChannelCount=25))
    result = gate_arithmetic(candidate(overview=broken))
    assert result.outcome is GateOutcome.FAILED
    assert any("channels !=" in r for r in result.reasons)


def test_arithmetic_rejects_country_subtotals_exceeding_the_corpus() -> None:
    """The bug that shipped a country with more occurrences than existed."""
    broken = overview_payload(
        countries=[
            {
                "country": "DE",
                "creatorCount": 20,
                "representedVideoCount": 60,
                "sourceOccurrenceCount": 51183,
                "resolvedVideoCount": 60,
                "unavailableVideoCount": 0,
            }
        ]
    )
    result = gate_arithmetic(candidate(overview=broken))
    assert result.outcome is GateOutcome.FAILED
    assert any("exceeding" in r for r in result.reasons)


def test_arithmetic_rejects_more_distinct_videos_than_occurrences() -> None:
    """Every distinct video is backed by at least one occurrence."""
    broken = overview_payload(coverage=coverage_payload(inputOccurrenceCount=50))
    result = gate_arithmetic(candidate(overview=broken))
    assert result.outcome is GateOutcome.FAILED


def test_arithmetic_is_incomplete_without_an_overview() -> None:
    """Requirement 8.3: a check that cannot run does not pass."""
    empty = ReleaseCandidate(release_id="r1", artifacts=(), manifest={})
    assert gate_arithmetic(empty).outcome is GateOutcome.INCOMPLETE


# --- Requirement 8.2 / 1.9: provenance ------------------------------------


def test_provenance_requires_complete_citations() -> None:
    broken = manifest_payload(datasets=[{"datasetId": "ds-a", "displayName": "DS A"}])
    result = gate_provenance(candidate(manifest=broken))
    assert result.outcome is GateOutcome.FAILED
    assert any("missing" in r for r in result.reasons)


def test_provenance_rejects_an_unusable_digest() -> None:
    entry = dict(manifest_payload()["datasets"][0])
    entry["snapshotDigest"] = "not-a-digest"
    result = gate_provenance(candidate(manifest=manifest_payload(datasets=[entry])))
    assert result.outcome is GateOutcome.FAILED


def test_provenance_rejects_a_manifest_citing_nothing() -> None:
    result = gate_provenance(candidate(manifest=manifest_payload(datasets=[])))
    assert result.outcome is GateOutcome.FAILED


# --- Requirement 7.5: disclosure ------------------------------------------


def test_disclosure_passes_on_clean_artifacts() -> None:
    assert gate_disclosure(candidate()).outcome is GateOutcome.PASSED


def test_disclosure_rejects_a_leaked_identifier() -> None:
    leaked = GeneratedArtifact(
        path="releases/r1/countries/DE.json",
        payload={"rows": [{"note": "UC_x5XG1OV2P6uZZ5FSM9Ttw"}]},
    )
    # Bypass finalize's own check to simulate a generator that failed.
    leaked.content = b"{}"
    leaked.digest = DIGEST

    result = gate_disclosure(candidate(extra=[leaked]))
    assert result.outcome is GateOutcome.FAILED


def test_disclosure_requires_a_policy_version() -> None:
    """Requirement 7.1: no version means no governing policy."""
    broken = manifest_payload(disclosurePolicyVersion="")
    result = gate_disclosure(candidate(manifest=broken))
    assert result.outcome is GateOutcome.FAILED


# --- Requirement 12.12: neutral language ----------------------------------


def test_neutral_language_passes_on_observational_copy() -> None:
    fine = GeneratedArtifact(
        path="releases/r1/methodology.json",
        payload={
            "note": "Identifiers were observed in a named, versioned snapshot.",
            "description": "Country is declared channel metadata at the cutoff.",
        },
    ).finalize()
    assert gate_neutral_language(candidate(extra=[fine])).outcome is GateOutcome.PASSED


@pytest.mark.parametrize(
    "copy",
    [
        "This creator's work was stolen for training.",
        "The dataset pirated these videos.",
        "This was an illegal use of the material.",
        "The model infringed on this content.",
        "The model was trained on these videos.",
        "Used without their consent.",
        "This creator lives in Germany.",
        "The creator's nationality is German.",
    ],
)
def test_neutral_language_rejects_unsupported_claims(copy: str) -> None:
    """Requirement 12.5 forbids exactly these assertions."""
    offending = GeneratedArtifact(path="releases/r1/copy.json", payload={"note": copy}).finalize()
    result = gate_neutral_language(candidate(extra=[offending]))
    assert result.outcome is GateOutcome.FAILED, f"should reject: {copy}"


def test_neutral_language_inspects_nested_copy() -> None:
    nested = GeneratedArtifact(
        path="releases/r1/copy.json",
        payload={"sections": [{"body": {"text": "This use was illegal."}}]},
    ).finalize()
    assert gate_neutral_language(candidate(extra=[nested])).outcome is GateOutcome.FAILED


def test_neutral_language_permits_the_approved_boundary_disclaimer() -> None:
    """The required disclaimer must not trip the gate meant to protect it."""
    approved = GeneratedArtifact(
        path="releases/r1/copy.json",
        payload={
            "disputedTerritoryTreatment": (
                "Boundaries are a presentation convention and are not evidence "
                "of channel location beyond declared country metadata."
            )
        },
    ).finalize()
    assert gate_neutral_language(candidate(extra=[approved])).outcome is GateOutcome.PASSED


# --- Requirement 8.5: digests ---------------------------------------------


def test_digests_pass_when_every_artifact_matches() -> None:
    assert gate_digests(candidate()).outcome is GateOutcome.PASSED


def test_digests_reject_a_mismatch() -> None:
    subject = candidate()
    tampered = {**subject.manifest}
    tampered["artifactDigests"] = {"releases/r1/overview.json": "sha256:" + "b" * 64}
    result = gate_digests(
        ReleaseCandidate(
            release_id="r1",
            artifacts=subject.artifacts,
            manifest=tampered,
            signoff_actor="curator",
        )
    )
    assert result.outcome is GateOutcome.FAILED
    assert any("mismatch" in r for r in result.reasons)


def test_digests_reject_an_unlisted_artifact() -> None:
    subject = candidate()
    result = gate_digests(
        ReleaseCandidate(
            release_id="r1",
            artifacts=subject.artifacts,
            manifest={**subject.manifest, "artifactDigests": {}},
            signoff_actor="curator",
        )
    )
    assert result.outcome is GateOutcome.FAILED


def test_digests_reject_a_listed_but_missing_artifact() -> None:
    subject = candidate()
    result = gate_digests(
        ReleaseCandidate(
            release_id="r1",
            artifacts=subject.artifacts,
            manifest={
                **subject.manifest,
                "artifactDigests": {
                    **subject.manifest["artifactDigests"],
                    "releases/r1/countries/FR.json": DIGEST,
                },
            },
            signoff_actor="curator",
        )
    )
    assert result.outcome is GateOutcome.FAILED
    assert any("not staged" in r for r in result.reasons)


# --- Requirement 14.1: payload budget -------------------------------------


def test_payload_budget_passes_for_a_small_release() -> None:
    result = gate_payload_budget(candidate())
    assert result.outcome is GateOutcome.PASSED
    assert int(result.detail["compressedBytes"]) < 250 * 1024


def test_payload_budget_rejects_an_oversized_overview() -> None:
    # Incompressible content, so gzip cannot mask the size.
    import secrets

    bulky = overview_payload(
        countries=[
            {
                "country": "DE",
                "creatorCount": 1,
                "representedVideoCount": 1,
                "sourceOccurrenceCount": 1,
                "resolvedVideoCount": 1,
                "unavailableVideoCount": 0,
                "padding": secrets.token_hex(200),
            }
            for _ in range(2000)
        ]
    )
    result = gate_payload_budget(candidate(overview=bulky))
    assert result.outcome is GateOutcome.FAILED
    assert any("over the" in r for r in result.reasons)


# --- Requirement 8.1: manifest completeness -------------------------------


def test_manifest_gate_requires_every_field() -> None:
    result = gate_manifest(candidate(manifest=manifest_payload(methodologyVersion="")))
    assert result.outcome is GateOutcome.FAILED


def test_manifest_gate_rejects_a_cutoff_after_generation() -> None:
    broken = manifest_payload(enrichmentCutoff="2026-01-15T13:00:00Z")
    result = gate_manifest(candidate(manifest=broken))
    assert result.outcome is GateOutcome.FAILED
    assert any("postdates" in r for r in result.reasons)


def test_manifest_gate_rejects_a_release_id_mismatch() -> None:
    result = gate_manifest(candidate(manifest=manifest_payload(releaseId="other")))
    assert result.outcome is GateOutcome.FAILED


# --- Requirement 14.5 / 10.6: creator pagination --------------------------


def _shard(country: str, *, rows: int, total: int, pages: int) -> GeneratedArtifact:
    return GeneratedArtifact(
        path=f"releases/r1/countries/{country}.json",
        payload={
            "country": country,
            "firstPage": {
                "rows": [{"publicChannelKey": f"pk_{i:04d}"} for i in range(rows)],
                "pageSize": 50,
                "totalRows": total,
            },
            "pageIndex": {
                "representedVideoCountDesc": [
                    f"releases/r1/countries/{country}/x/page-{i}.json" for i in range(pages)
                ]
            },
        },
    ).finalize()


def test_pagination_passes_when_every_page_is_published() -> None:
    subject = candidate(extra=[_shard("DE", rows=50, total=120, pages=3)])
    assert gate_creator_pagination(subject).outcome is GateOutcome.PASSED


def test_pagination_fails_when_pages_are_missing() -> None:
    """The defect this gate exists for: a cursor leading nowhere."""
    subject = candidate(extra=[_shard("DE", rows=50, total=2747, pages=1)])
    result = gate_creator_pagination(subject)

    assert result.outcome is GateOutcome.FAILED
    assert any("expected" in r for r in result.reasons)


def test_pagination_fails_when_no_index_is_published() -> None:
    shard = GeneratedArtifact(
        path="releases/r1/countries/DE.json",
        payload={
            "country": "DE",
            "firstPage": {"rows": [], "pageSize": 50, "totalRows": 900},
        },
    ).finalize()
    result = gate_creator_pagination(candidate(extra=[shard]))

    assert result.outcome is GateOutcome.FAILED
    assert any("no page index" in r for r in result.reasons)


def test_pagination_fails_when_the_first_page_exceeds_the_page_size() -> None:
    # Requirement 14.5: rows are partitioned, not shipped whole.
    subject = candidate(extra=[_shard("DE", rows=500, total=500, pages=1)])
    result = gate_creator_pagination(subject)

    assert result.outcome is GateOutcome.FAILED
    assert any("over the" in r for r in result.reasons)


def test_pagination_accepts_a_country_that_fits_one_page() -> None:
    subject = candidate(extra=[_shard("DE", rows=12, total=12, pages=1)])
    assert gate_creator_pagination(subject).outcome is GateOutcome.PASSED


def test_pagination_is_incomplete_without_shards() -> None:
    """Requirement 8.3: an unrun check is not a pass."""
    assert gate_creator_pagination(candidate()).outcome is GateOutcome.INCOMPLETE


# --- Requirement 15.14/15.15: dependency scanning -------------------------


def test_absent_scan_is_incomplete_not_passing() -> None:
    """Requirement 15.15: an unrun scan blocks activation."""
    result = gate_dependency_scan(candidate(scan=None))
    # `scan=None` means "not recorded" only when explicitly absent.
    subject = ReleaseCandidate(release_id="r1", artifacts=(), manifest={}, vulnerability_scan=None)
    assert gate_dependency_scan(subject).outcome is GateOutcome.INCOMPLETE
    assert result.outcome is GateOutcome.PASSED


def test_incomplete_scan_blocks() -> None:
    result = gate_dependency_scan(candidate(scan={"completed": False, "blockingFindings": 0}))
    assert result.outcome is GateOutcome.INCOMPLETE


def test_blocking_vulnerabilities_fail() -> None:
    result = gate_dependency_scan(candidate(scan={"completed": True, "blockingFindings": 3}))
    assert result.outcome is GateOutcome.FAILED


# --- Requirement 8.2: curator sign-off ------------------------------------


def test_missing_signoff_is_incomplete() -> None:
    assert gate_signoff(candidate(signoff=None)).outcome is GateOutcome.INCOMPLETE


def test_recorded_signoff_passes() -> None:
    result = gate_signoff(candidate(signoff="nandi"))
    assert result.outcome is GateOutcome.PASSED
    assert result.detail["actor"] == "nandi"


# --- Running the full set -------------------------------------------------


def test_a_complete_candidate_passes_every_gate() -> None:
    # A complete candidate now includes a country shard: without one the
    # pagination gate is INCOMPLETE, which is correct rather than a
    # nuisance — a release with no shards has published no detail.
    report = run_gates(candidate(extra=[_shard("DE", rows=50, total=120, pages=3)]))
    assert report.passed, report.describe()
    assert len(report.results) == 10


def test_report_lists_every_blocking_gate() -> None:
    """A curator fixing a release should see the whole list at once."""
    report = run_gates(candidate(signoff=None, scan={"completed": False}))
    assert not report.passed
    names = {r.name for r in report.blocking}
    assert "curator-signoff" in names
    assert "dependency-scan" in names


def test_a_raising_gate_is_incomplete_not_passing() -> None:
    """An exception must never be mistaken for a pass."""

    def gate_explodes(_c: ReleaseCandidate) -> None:
        msg = "boom"
        raise RuntimeError(msg)

    report = run_gates(candidate(), gates=[gate_explodes])  # type: ignore[list-item]
    assert not report.passed
    assert report.results[0].outcome is GateOutcome.INCOMPLETE


def test_report_describes_itself_readably() -> None:
    text = run_gates(candidate(extra=[_shard("DE", rows=50, total=120, pages=3)])).describe()
    assert "PASS" in text
    assert "arithmetic" in text
