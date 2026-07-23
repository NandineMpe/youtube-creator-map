"""Adversarial tests for the security and publication boundaries.

Every other suite asks whether the system works. This one assumes an
attacker and asks whether it fails closed. The distinction matters
because most of these defences are invisible when they work: a
path-traversal check that silently stopped matching would break no test
and pass every review, right up until an archive wrote outside its root.

The organising principle is that each test states the attack in the terms
someone would actually attempt it, and asserts the specific safe outcome
rather than merely "an exception was raised" — an exception from the
wrong cause would satisfy a weaker assertion while leaving the hole open.

Requirement refs: 7.1-7.8, 8.2-8.12, 15.1-15.22
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
from creator_map_pipeline.aggregate.artifacts import (
    DisclosureViolation,
    GeneratedArtifact,
    find_prohibited_content,
)
from creator_map_pipeline.release.gates import (
    GateOutcome,
    ReleaseCandidate,
    gate_dependency_scan,
    gate_digests,
    gate_neutral_language,
    gate_signoff,
    run_gates,
)
from creator_map_pipeline.snapshot import (
    QuarantineReason,
    SnapshotLimits,
    is_formula,
    read_csv_records,
    safe_archive_members,
)

# --- Requirement 15.7: path traversal in archive members ------------------

TRAVERSAL_NAMES = (
    "../escape.csv",
    "../../etc/passwd",
    "data/../../escape.csv",
    "/absolute/escape.csv",
    "C:/Windows/System32/escape.csv",
    # Backslashes: a POSIX-only check would pass this straight through on
    # a Windows extraction root.
    "..\\escape.csv",
    "data\\..\\..\\escape.csv",
    "\\\\server\\share\\escape.csv",
)


@pytest.mark.parametrize("name", TRAVERSAL_NAMES)
def test_archive_member_escaping_the_root_is_quarantined(tmp_path: Path, name: str) -> None:
    """Writing outside the extraction root is the whole attack."""
    archive_path = tmp_path / "hostile.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(name, "video_id\nabc")

    with zipfile.ZipFile(archive_path) as archive:
        verdicts = [q for _, q in safe_archive_members(archive)]

    assert verdicts and verdicts[0] is not None
    assert verdicts[0].reason is QuarantineReason.PATH_TRAVERSAL


def test_a_benign_nested_path_is_not_quarantined(tmp_path: Path) -> None:
    """A traversal check that rejected ordinary nesting would be turned
    off by the first person who hit it."""
    archive_path = tmp_path / "ok.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("data/2026/part-0.csv", "video_id\nabc")

    with zipfile.ZipFile(archive_path) as archive:
        assert all(q is None for _, q in safe_archive_members(archive))


# --- Requirement 15.7: decompression bounds -------------------------------


def test_a_decompression_bomb_is_quarantined(tmp_path: Path) -> None:
    """Highly compressible content expanding far beyond its stored size
    is the signature. Extracting it exhausts disk or memory."""
    archive_path = tmp_path / "bomb.zip"
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("bomb.csv", "0" * (5 * 1024 * 1024))

    with zipfile.ZipFile(archive_path) as archive:
        verdicts = [q for _, q in safe_archive_members(archive) if q is not None]

    assert verdicts
    assert verdicts[0].reason is QuarantineReason.ARCHIVE_RATIO


def test_total_expansion_beyond_the_cap_stops_the_scan(tmp_path: Path) -> None:
    """Many individually-innocent members can exceed the budget together."""
    archive_path = tmp_path / "many.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        for index in range(4):
            archive.writestr(f"part-{index}.csv", "x" * 2048)

    limits = SnapshotLimits(max_uncompressed_bytes=4096)
    with zipfile.ZipFile(archive_path) as archive:
        verdicts = [q for _, q in safe_archive_members(archive, limits=limits) if q is not None]

    assert verdicts
    assert verdicts[0].reason is QuarantineReason.ARCHIVE_TOO_LARGE


def test_too_many_members_stops_the_scan(tmp_path: Path) -> None:
    archive_path = tmp_path / "swarm.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        for index in range(12):
            archive.writestr(f"m{index}.csv", "x")

    limits = SnapshotLimits(max_archive_members=5)
    with zipfile.ZipFile(archive_path) as archive:
        verdicts = [q for _, q in safe_archive_members(archive, limits=limits) if q is not None]

    assert verdicts[0].reason is QuarantineReason.ARCHIVE_TOO_MANY_MEMBERS


# --- Requirement 15.7: spreadsheet formula non-execution ------------------

FORMULA_PAYLOADS = (
    "=cmd|'/c calc'!A1",
    "+cmd|'/c calc'!A1",
    "-2+3+cmd|'/c calc'!A1",
    "@SUM(1+1)*cmd|'/c calc'!A1",
    "\t=1+1",
    "\r=1+1",
)


@pytest.mark.parametrize("payload", FORMULA_PAYLOADS)
def test_formula_content_is_recognised(payload: str) -> None:
    """CSV injection: a value a spreadsheet would execute on open."""
    assert is_formula(payload)


def test_a_hyphen_leading_video_id_is_not_a_formula() -> None:
    """The false positive that rejected ~2.4% of real YouTube ids.

    base64url includes "-", so a legitimate identifier can lead with one.
    A guard that quarantined those would discard real data on every run
    while looking like it was working.
    """
    assert not is_formula("-Ks8Zt3Vv0Q")
    assert not is_formula("_abc123XYZ-")


def test_formula_rows_are_quarantined_not_extracted() -> None:
    """Requirement 15.8: quarantine, with a reason, and keep the content
    out of extraction output."""
    csv_text = "video_id\n=cmd|'/c calc'!A1\ndQw4w9WgXcQ\n"

    outcome = read_csv_records(io.StringIO(csv_text), source_name="hostile")

    assert any(q.reason is QuarantineReason.FORMULA_CONTENT for q in outcome.quarantined)
    assert all("cmd" not in str(record.fields) for record in outcome.records)


def test_an_oversized_field_is_quarantined() -> None:
    csv_text = "video_id\n" + ("x" * 5000) + "\n"

    outcome = read_csv_records(
        io.StringIO(csv_text), source_name="big", limits=SnapshotLimits(max_field_bytes=100)
    )

    assert outcome.quarantined
    assert outcome.quarantined[0].reason in {
        QuarantineReason.FIELD_TOO_LARGE,
        QuarantineReason.RECORD_TOO_LARGE,
    }


def test_clean_records_survive_the_gauntlet() -> None:
    """Requirement 15.9 forbids precautionary quarantine of content that
    violates nothing. A reader that rejected everything would be safe and
    useless."""
    csv_text = "video_id\ndQw4w9WgXcQ\n-Ks8Zt3Vv0Q\n_abc123XYZ-\n"

    outcome = read_csv_records(io.StringIO(csv_text), source_name="clean")

    assert not outcome.quarantined
    assert len(outcome.records) == 3


# --- Requirement 15.3 / 7.5: credentials and restricted values ------------

RESTRICTED_PAYLOADS = (
    # A synthetic Google-key-shaped value. Never a real key: a real one
    # in a test payload is a real one in the repository, which the
    # credential scanner would (correctly) flag and which a public clone
    # would leak. The scanner keys on the shape, so a fake exercises it
    # exactly as well.
    {"apiKey": "AIza" + "F" * 35},
    # Nested three levels down, to prove the scan recurses.
    {"nested": [{"deep": {"key": "AIza" + "S" * 35}}]},
    {"channelId": "UCuAXFkgsw1L7xaCfnd5JJOw"},
    {"rows": [{"raw_channel_id": "UCuAXFkgsw1L7xaCfnd5JJOw"}]},
    {"connection": "postgresql://user:hunter2@host:5432/db"},
)


@pytest.mark.parametrize("payload", RESTRICTED_PAYLOADS)
def test_restricted_values_are_found_at_any_depth(payload: dict[str, object]) -> None:
    """Requirement 7.6 requires a recursive scan. A value reaching an
    artifact because it was nested three levels down is the failure."""
    assert find_prohibited_content(payload)


@pytest.mark.parametrize("payload", RESTRICTED_PAYLOADS)
def test_an_artifact_carrying_a_restricted_value_cannot_be_built(
    payload: dict[str, object],
) -> None:
    """Fail closed at construction, not at review time."""
    with pytest.raises(DisclosureViolation):
        GeneratedArtifact(path="releases/r1/overview.json", payload=payload).finalize()


def test_a_public_channel_key_is_not_mistaken_for_a_raw_id() -> None:
    """The published form must survive the guard, or nothing ships."""
    artifact = GeneratedArtifact(
        path="releases/r1/countries/ZA.json",
        payload={"rows": [{"publicChannelKey": "pk_" + "0" * 32, "displayName": "Example"}]},
    ).finalize()

    assert artifact.digest.startswith("sha256:")


def test_a_channel_named_like_a_video_id_still_publishes() -> None:
    """770 real channels have 11-character names matching the bare
    video-id shape. Blocking those blocks the build over a display name
    that is not an identifier at all."""
    artifact = GeneratedArtifact(
        path="releases/r1/countries/ZA.json",
        payload={"rows": [{"publicChannelKey": "pk_" + "0" * 32, "displayName": "101Treesrus"}]},
    ).finalize()

    assert artifact.digest


# --- Requirement 8.3: gates fail closed -----------------------------------


def candidate(**overrides: object) -> ReleaseCandidate:
    fields: dict[str, object] = {
        "release_id": "r1",
        "artifacts": (),
        "manifest": {},
    }
    fields.update(overrides)
    return ReleaseCandidate(**fields)  # type: ignore[arg-type]


def test_an_unrun_dependency_scan_blocks_activation() -> None:
    """Requirement 15.15: a scan that did not complete is not a clean
    scan. Defaulting to "clean" would invert the requirement."""
    result = gate_dependency_scan(candidate(vulnerability_scan=None))

    assert result.outcome is GateOutcome.INCOMPLETE
    assert result.blocks_activation


def test_a_scan_reporting_findings_blocks_activation() -> None:
    result = gate_dependency_scan(
        candidate(vulnerability_scan={"completed": True, "blockingFindings": 1})
    )

    assert result.blocks_activation


def test_an_unsigned_release_blocks_activation() -> None:
    assert gate_signoff(candidate()).blocks_activation


def test_a_tampered_artifact_fails_digest_verification() -> None:
    """Requirement 8.5/8.6: recompute and require equality. An artifact
    swapped after the manifest was written must not activate."""
    honest = GeneratedArtifact(
        path="releases/r1/overview.json", payload={"countries": []}
    ).finalize()
    tampered = GeneratedArtifact(
        path="releases/r1/overview.json", payload={"countries": [{"country": "ZA"}]}
    ).finalize()
    manifest = {"artifactDigests": {honest.path: honest.digest}}

    result = gate_digests(
        candidate(artifacts=(tampered,), manifest=manifest),
    )

    assert result.blocks_activation


def test_an_artifact_absent_from_the_manifest_blocks_activation() -> None:
    """An unlisted artifact has no recorded digest, so nothing verifies
    it. Publishing it anyway would defeat the manifest."""
    extra = GeneratedArtifact(path="releases/r1/extra.json", payload={"a": 1}).finalize()

    result = gate_digests(candidate(artifacts=(extra,), manifest={"artifactDigests": {}}))

    assert result.blocks_activation


def test_a_gate_that_raises_is_incomplete_not_skipped() -> None:
    """An exception in one check must not be mistaken for a pass, and
    must not stop the others reporting."""

    def exploding(_: ReleaseCandidate) -> GateOutcome:  # type: ignore[return-value]
        raise RuntimeError("boom")

    report = run_gates(candidate(), gates=[exploding])  # type: ignore[list-item]

    assert not report.passed
    assert report.results[0].outcome is GateOutcome.INCOMPLETE


def test_every_blocking_gate_is_reported_not_just_the_first() -> None:
    """A curator fixing a release should see the whole list."""
    report = run_gates(candidate())

    assert len(report.blocking) > 1


# --- Requirement 12.5: claims cannot be smuggled through ------------------


SMUGGLING_ATTEMPTS = (
    # A disclaimer followed by the assertion it disclaims.
    "This does not indicate training. The model was trained on these videos.",
    # A claim inside a deeply nested list of dicts.
    {"a": [{"b": [{"c": "this content was stolen from creators"}]}]},
    # A claim in a field nobody reviews by eye.
    {"internalNote": "the creator lives in Germany"},
)


@pytest.mark.parametrize("payload", SMUGGLING_ATTEMPTS)
def test_claims_cannot_be_smuggled_past_the_language_gate(payload: object) -> None:
    artifact = GeneratedArtifact(path="releases/r1/overview.json", payload=payload)
    object.__setattr__(artifact, "payload", payload)

    result = gate_neutral_language(candidate(artifacts=(artifact,)))

    assert result.blocks_activation


# --- Requirement 15.19: no anonymous mutation surface ---------------------


def test_the_published_application_is_a_static_export() -> None:
    """The strongest form of "no anonymous public mutation operation" is
    having no server to mutate. This pins that: a change to a rendered
    runtime would create an endpoint surface this argument no longer
    covers."""
    config = Path("apps/web/next.config.ts").read_text(encoding="utf-8")

    assert 'output: "export"' in config


def test_application_code_issues_no_mutating_request() -> None:
    """Requirement 15.19, checked at the source rather than the bundle.

    Scanning built chunks for `method:"POST"` does not work: Next.js
    ships its server-actions runtime into the framework chunk regardless
    of whether the app uses it, and in a static export there is no server
    for it to reach. Flagging that would be a false positive on a
    dependency's dead code, and the usual response to a false positive is
    to delete the test.

    What is actually checkable is that *our* code never constructs a
    mutating request, which is where such a call would have to originate.
    """
    offenders: list[str] = []
    for source in (*Path("apps/web").rglob("*.ts"), *Path("apps/web").rglob("*.tsx")):
        if "node_modules" in str(source) or ".next" in str(source):
            continue
        text = source.read_text(encoding="utf-8", errors="ignore").replace(" ", "")
        for verb in ('method:"POST"', 'method:"PUT"', 'method:"DELETE"', 'method:"PATCH"'):
            if verb in text:
                offenders.append(f"{source}: {verb}")

    assert not offenders, offenders
