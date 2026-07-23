"""Composable release validation gates.

Requirement 8.2 enumerates the checks activation must pass; Requirement 8.3
requires a failed *or incomplete* gate to reject activation and keep the
previous release serving. The distinction matters: a gate that could not run
is not a gate that passed, so `GateOutcome` separates them and both block.

Gates are pure functions over a candidate. They return results rather than
raising, so one run reports every problem instead of stopping at the first —
a curator fixing a release should see the whole list.

Requirement refs: 8.2, 8.3, 12.12, 12.13, 14.1, 15.3, 15.14, 15.15
"""

from __future__ import annotations

import gzip
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum, unique
from typing import Any

from creator_map_pipeline.aggregate.artifacts import (
    GeneratedArtifact,
    canonical_bytes,
    find_prohibited_content,
)
from creator_map_pipeline.release.headers import SECURITY_HEADERS, check_headers

#: Requirement 14.1 budgets the compressed manifest plus country summaries.
OVERVIEW_BUDGET_BYTES = 250 * 1024


@unique
class GateOutcome(StrEnum):
    """Whether a gate passed, failed, or could not complete.

    INCOMPLETE is not a soft pass. Requirement 8.3 blocks activation on a
    gate that cannot complete just as firmly as on one that fails, because
    an unrun check provides no assurance.
    """

    PASSED = "passed"
    FAILED = "failed"
    INCOMPLETE = "incomplete"


@dataclass(frozen=True, slots=True)
class GateResult:
    """One gate's verdict and its reasons."""

    name: str
    outcome: GateOutcome
    reasons: tuple[str, ...] = ()
    detail: dict[str, str] = field(default_factory=dict)

    @property
    def blocks_activation(self) -> bool:
        return self.outcome is not GateOutcome.PASSED


@dataclass(frozen=True, slots=True)
class ReleaseCandidate:
    """Everything a gate may inspect."""

    release_id: str
    artifacts: tuple[GeneratedArtifact, ...]
    manifest: dict[str, Any]
    #: Present only when the curator has recorded sign-off.
    signoff_actor: str | None = None
    #: Why no sign-off was found, when one was looked for and missed.
    #: A gate that reports only "absent" when the real cause is a policy
    #: change since approval sends the curator to the wrong place.
    signoff_detail: str | None = None
    #: Dependency scan result, absent when the scan did not run.
    vulnerability_scan: dict[str, Any] | None = None

    def artifact(self, suffix: str) -> GeneratedArtifact | None:
        for candidate in self.artifacts:
            if candidate.path.endswith(suffix):
                return candidate
        return None


@dataclass(slots=True)
class ValidationReport:
    """The full result of running every gate."""

    release_id: str
    results: list[GateResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """Whether activation may proceed."""
        return all(not r.blocks_activation for r in self.results)

    @property
    def blocking(self) -> list[GateResult]:
        return [r for r in self.results if r.blocks_activation]

    def describe(self) -> str:
        lines = [f"release {self.release_id}: {'PASS' if self.passed else 'BLOCKED'}"]
        for result in self.results:
            marker = {
                GateOutcome.PASSED: "ok  ",
                GateOutcome.FAILED: "FAIL",
                GateOutcome.INCOMPLETE: "????",
            }[result.outcome]
            lines.append(f"  [{marker}] {result.name}")
            for reason in result.reasons:
                lines.append(f"           {reason}")
        return "\n".join(lines)


Gate = Callable[[ReleaseCandidate], GateResult]


# --- Requirement 8.2: arithmetic reconciliation ---------------------------


def gate_arithmetic(candidate: ReleaseCandidate) -> GateResult:
    """Verify the published counts reconcile with each other.

    Checks the relationships a reader would reasonably assume: the coverage
    partition sums to the distinct input count, channel coverage splits
    exactly, and country subtotals never exceed the corpus.
    """
    overview = candidate.artifact("overview.json")
    if overview is None:
        return GateResult(
            "arithmetic",
            GateOutcome.INCOMPLETE,
            ("no overview artifact to reconcile",),
        )

    payload = overview.payload
    reasons: list[str] = []

    coverage = payload.get("coverage", {})
    partition = coverage.get("partition", {})
    partition_total = sum(int(v) for v in partition.values())
    distinct = int(coverage.get("distinctInputVideoCount", 0))
    if partition_total != distinct:
        reasons.append(
            f"resolution partition sums to {partition_total}, "
            f"expected {distinct} distinct input videos"
        )

    known = int(coverage.get("knownCountryChannelCount", 0))
    unknown = int(coverage.get("unknownCountryChannelCount", 0))
    resolved = int(coverage.get("resolvedChannelCount", 0))
    if known + unknown != resolved:
        reasons.append(f"known ({known}) + unknown ({unknown}) channels != resolved ({resolved})")

    countries = payload.get("countries", [])
    country_videos = sum(int(c.get("representedVideoCount", 0)) for c in countries)
    if country_videos > distinct:
        reasons.append(f"country video subtotals sum to {country_videos}, exceeding {distinct}")

    occurrences = int(coverage.get("inputOccurrenceCount", 0))
    country_occurrences = sum(int(c.get("sourceOccurrenceCount", 0)) for c in countries)
    if country_occurrences > occurrences:
        reasons.append(
            f"country occurrence subtotals sum to {country_occurrences}, exceeding {occurrences}"
        )

    # A distinct video count above the occurrence count is impossible: every
    # distinct video is backed by at least one occurrence.
    if distinct > occurrences:
        reasons.append(f"distinct videos ({distinct}) exceeds occurrences ({occurrences})")

    return GateResult(
        "arithmetic",
        GateOutcome.FAILED if reasons else GateOutcome.PASSED,
        tuple(reasons),
    )


# --- Requirement 8.2: provenance completeness -----------------------------


def gate_provenance(candidate: ReleaseCandidate) -> GateResult:
    """Every included dataset must be fully citable (Requirement 1.9)."""
    datasets = candidate.manifest.get("datasets", [])
    if not datasets:
        return GateResult("provenance", GateOutcome.FAILED, ("manifest cites no datasets",))

    required = (
        "datasetId",
        "displayName",
        "version",
        "sourceKind",
        "occurrenceUnit",
        "sourceCitation",
        "snapshotDigest",
    )
    reasons: list[str] = []
    for entry in datasets:
        missing = [f for f in required if not entry.get(f)]
        if missing:
            reasons.append(
                f"dataset {entry.get('datasetId', '?')} is missing: {', '.join(missing)}"
            )
        digest = str(entry.get("snapshotDigest", ""))
        if digest and not re.match(r"^sha256:[a-f0-9]{64}$", digest):
            reasons.append(f"dataset {entry.get('datasetId', '?')} has an unusable digest")

    return GateResult(
        "provenance",
        GateOutcome.FAILED if reasons else GateOutcome.PASSED,
        tuple(reasons),
    )


# --- Requirement 8.2 / 7.5: disclosure compliance -------------------------


def gate_disclosure(candidate: ReleaseCandidate) -> GateResult:
    """No artifact may carry prohibited content."""
    reasons: list[str] = []
    for artifact in candidate.artifacts:
        for finding in find_prohibited_content(artifact.payload):
            reasons.append(f"{artifact.path} :: {finding.path}: {finding.reason}")

    if not candidate.manifest.get("disclosurePolicyVersion"):
        # Requirement 7.1: no policy version means no governing policy.
        reasons.append("manifest records no disclosure policy version")

    return GateResult(
        "disclosure",
        GateOutcome.FAILED if reasons else GateOutcome.PASSED,
        tuple(reasons[:20]),
        detail={"findings": str(len(reasons))},
    )


# --- Requirement 12.12: neutral language ----------------------------------

#: Claims public copy may never make (Requirement 12.5). Matched
#: word-boundary so ordinary words containing them do not trip the gate.
#: Negations that turn a prohibited phrase into the disclaimer
#: Requirement 12.5 actually wants.
#:
#: Without this, the gate flags the project's own required copy: "this
#: does not indicate whether any model was trained on a video" contains
#: "was trained on" and would block publication of the very sentence that
#: makes the claim neutral. A property test found it before the copy
#: moved into an artifact, which is where it would have become a release
#: that could not ship without deleting its own disclaimer.
#:
#: Scope: the sentence containing the phrase. A negation anywhere earlier
#: in the same sentence is treated as governing it.
#:
#: Two failure modes bound this, and both were found by the property test
#: rather than reasoned out in advance:
#:
#:   Too narrow (a fixed character window) misses the real disclaimer
#:   "does not establish that any model was trained on the video, that
#:   any use was unlawful" — one "does not" governs a list of clauses,
#:   and the last one is far from it.
#:
#:   Too wide (the whole string) lets a paragraph opening with any "not"
#:   exempt every claim after it, which turns the negation handling into
#:   a bypass.
#:
#: Sentence scope is not a parser and will not resolve every
#: construction. The gate is a backstop under human review of public
#: copy, not a substitute for it — a claim written to evade this is a
#: review failure, not a regex failure.
_SENTENCE_BREAK = re.compile(r"[.!?]\s")
_NEGATION = re.compile(
    r"\b(?:not|never|no|cannot|neither|nor|nothing)\b",
    re.I,
)


def _is_negated(text: str, start: int) -> bool:
    """Whether a negation earlier in the same sentence governs `start`."""
    sentence_start = 0
    for match in _SENTENCE_BREAK.finditer(text, 0, start):
        sentence_start = match.end()
    return _NEGATION.search(text, sentence_start, start) is not None


_PROHIBITED_CLAIMS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bstole|\bstolen\b|\btheft\b", re.I), "theft claim"),
    (re.compile(r"\bpirat(ed|es|ing)\b", re.I), "piracy claim"),
    (re.compile(r"\billegal(ly)?\b|\bunlawful", re.I), "legality claim"),
    (re.compile(r"\binfring(ed|es|ing|ement)\b", re.I), "infringement claim"),
    (re.compile(r"\bwas trained on\b|\btrained their model", re.I), "training claim"),
    (re.compile(r"\bwithout (?:their )?(?:consent|permission)\b", re.I), "consent claim"),
    (re.compile(r"\blives in\b|\bresides? in\b|\bresidency\b", re.I), "residence claim"),
    (re.compile(r"\bnationality\b|\bis (?:a )?citizen", re.I), "nationality claim"),
)


def gate_neutral_language(candidate: ReleaseCandidate) -> GateResult:
    """Reject copy that converts observation into a claim.

    Requirement 12.5 forbids asserting confirmed training, infringement,
    illegality, consent status, residence, or nationality. This lints the
    prose actually shipped in artifacts, so unreviewed copy cannot reach
    publication through a data field.
    """
    reasons: list[str] = []

    def inspect(node: object, path: str) -> None:
        if isinstance(node, str):
            for pattern, label in _PROHIBITED_CLAIMS:
                # finditer, not search: a paragraph may disclaim a class
                # in one sentence and assert it in the next. Checking
                # only the first occurrence let exactly that through —
                # "this does not indicate whether any model was trained
                # on a video. The model was trained on these videos."
                # passed, because the first match was negated and the
                # loop moved on to the next pattern.
                for match in pattern.finditer(node):
                    # A negated phrase is a disclaimer, not an assertion.
                    # Requirement 12.5 requires those disclaimers, so
                    # flagging them would make the gate reject its own
                    # required copy.
                    if _is_negated(node, match.start()):
                        continue
                    reasons.append(f"{path}: {label}")
                    return
        elif isinstance(node, dict):
            for key, value in node.items():
                inspect(value, f"{path}.{key}" if path else str(key))
        elif isinstance(node, list):
            for index, item in enumerate(node):
                inspect(item, f"{path}[{index}]")

    for artifact in candidate.artifacts:
        inspect(artifact.payload, artifact.path)

    return GateResult(
        "neutral-language",
        GateOutcome.FAILED if reasons else GateOutcome.PASSED,
        tuple(reasons[:20]),
    )


# --- Requirement 8.2 / 8.5: digest verification ---------------------------


def gate_digests(candidate: ReleaseCandidate) -> GateResult:
    """Every artifact digest must match its recomputed content.

    Requirement 8.5 recomputes rather than trusting the recorded value,
    which is the only way a corrupted artifact is detectable.
    """
    recorded = candidate.manifest.get("artifactDigests", {})
    reasons: list[str] = []

    for artifact in candidate.artifacts:
        if artifact.path.endswith("manifest.json"):
            # The manifest cannot record its own digest.
            continue
        expected = recorded.get(artifact.path)
        if expected is None:
            reasons.append(f"{artifact.path} is absent from the manifest")
            continue
        actual = artifact.finalize().digest
        if actual != expected:
            reasons.append(f"{artifact.path} digest mismatch")

    listed = {p for p in recorded if not p.endswith("manifest.json")}
    present = {a.path for a in candidate.artifacts}
    for missing in sorted(listed - present):
        reasons.append(f"{missing} is listed in the manifest but was not staged")

    return GateResult(
        "digests",
        GateOutcome.FAILED if reasons else GateOutcome.PASSED,
        tuple(reasons[:20]),
    )


# --- Requirement 14.1: payload budget -------------------------------------


def gate_payload_budget(candidate: ReleaseCandidate) -> GateResult:
    """The compressed overview plus manifest must fit the delivery budget."""
    overview = candidate.artifact("overview.json")
    manifest = candidate.artifact("manifest.json")
    if overview is None or manifest is None:
        return GateResult(
            "payload-budget",
            GateOutcome.INCOMPLETE,
            ("overview or manifest is missing; cannot measure the budget",),
        )

    compressed = len(gzip.compress(overview.content, 9)) + len(gzip.compress(manifest.content, 9))
    if compressed > OVERVIEW_BUDGET_BYTES:
        return GateResult(
            "payload-budget",
            GateOutcome.FAILED,
            (
                f"compressed overview + manifest is {compressed:,} bytes, "
                f"over the {OVERVIEW_BUDGET_BYTES:,} byte budget",
            ),
            detail={"compressedBytes": str(compressed)},
        )

    return GateResult(
        "payload-budget",
        GateOutcome.PASSED,
        detail={"compressedBytes": str(compressed)},
    )


# --- Requirement 8.1: manifest completeness -------------------------------


def gate_manifest(candidate: ReleaseCandidate) -> GateResult:
    """The manifest must record everything Requirement 8.1 enumerates."""
    required = (
        "releaseId",
        "generatedAt",
        "enrichmentCutoff",
        "defaultFilter",
        "datasets",
        "artifactDigests",
        "methodologyVersion",
        "disclosurePolicyVersion",
        "boundaryMetadata",
    )
    reasons = [f"manifest is missing {f}" for f in required if not candidate.manifest.get(f)]

    if candidate.manifest.get("releaseId") != candidate.release_id:
        reasons.append("manifest release id does not match the candidate")

    generated = str(candidate.manifest.get("generatedAt", ""))
    cutoff = str(candidate.manifest.get("enrichmentCutoff", ""))
    if generated and cutoff and cutoff > generated:
        reasons.append("enrichment cutoff postdates generation")

    return GateResult(
        "manifest",
        GateOutcome.FAILED if reasons else GateOutcome.PASSED,
        tuple(reasons),
    )


# --- Requirement 15.14 / 15.15: dependency scanning -----------------------


def gate_dependency_scan(candidate: ReleaseCandidate) -> GateResult:
    """A completed vulnerability scan is a precondition for activation.

    Requirement 15.15 blocks a release when scanning finds a prohibited
    vulnerability *or cannot complete*, so an absent scan is INCOMPLETE
    rather than a pass.
    """
    scan = candidate.vulnerability_scan
    if scan is None:
        return GateResult(
            "dependency-scan",
            GateOutcome.INCOMPLETE,
            ("no dependency scan result was recorded",),
        )
    if not scan.get("completed"):
        return GateResult(
            "dependency-scan",
            GateOutcome.INCOMPLETE,
            ("the dependency scan did not complete",),
        )

    blocking = scan.get("blockingFindings", 0)
    if blocking:
        return GateResult(
            "dependency-scan",
            GateOutcome.FAILED,
            (f"{blocking} prohibited vulnerabilities remain unresolved",),
        )
    return GateResult("dependency-scan", GateOutcome.PASSED)


# --- Requirement 14.5: creator rows are partitioned -----------------------


def gate_creator_pagination(candidate: ReleaseCandidate) -> GateResult:
    """Creator rows must be paginated, not shipped whole.

    Requirement 14.5 partitions creator rows according to the page-size
    policy rather than including every row in the default payload. A shard
    that advertises more rows than it carries must also publish the pages
    that hold the rest, or Requirement 10.6's exactly-once traversal
    cannot complete — the cursor would lead nowhere.
    """
    reasons: list[str] = []
    checked = 0

    for artifact in candidate.artifacts:
        if "/countries/" not in artifact.path or artifact.path.endswith(
            tuple(f"page-{n}.json" for n in range(10))
        ):
            continue

        payload = artifact.payload
        first_page = payload.get("firstPage")
        if not isinstance(first_page, dict):
            continue

        checked += 1
        rows = len(first_page.get("rows", []))
        page_size = int(first_page.get("pageSize", 0) or 0)
        total = int(first_page.get("totalRows", 0) or 0)
        country = payload.get("country", artifact.path)

        if page_size and rows > page_size:
            reasons.append(
                f"{country}: first page carries {rows} rows, over the {page_size} page size"
            )

        # Every page the traversal can reach must exist.
        index = payload.get("pageIndex")
        if total > rows:
            if not isinstance(index, dict) or not index:
                reasons.append(
                    f"{country}: {total} rows but no page index, so "
                    f"traversal cannot reach past the first page"
                )
            else:
                for order, paths in index.items():
                    expected = -(-total // page_size) if page_size else 0
                    if expected and len(paths) < expected:
                        reasons.append(
                            f"{country}/{order}: {len(paths)} pages published "
                            f"for {total} rows, expected {expected}"
                        )

    if checked == 0:
        return GateResult(
            "creator-pagination",
            GateOutcome.INCOMPLETE,
            ("no country shards to check",),
        )

    return GateResult(
        "creator-pagination",
        GateOutcome.FAILED if reasons else GateOutcome.PASSED,
        tuple(reasons[:20]),
        detail={"shardsChecked": str(checked)},
    )


# --- Requirement 8.2: curator sign-off ------------------------------------


def gate_signoff(candidate: ReleaseCandidate) -> GateResult:
    """Dataset citations and terms review require a recorded approver."""
    if not candidate.signoff_actor:
        return GateResult(
            "curator-signoff",
            GateOutcome.INCOMPLETE,
            (
                candidate.signoff_detail
                or "no curator has signed off on dataset citations and terms review",
            ),
        )
    return GateResult(
        "curator-signoff",
        GateOutcome.PASSED,
        detail={"actor": candidate.signoff_actor},
    )


# --- Requirement 15.13: security response headers -------------------------


def gate_security_headers(candidate: ReleaseCandidate) -> GateResult:
    """The headers the hosting layer must apply are internally valid.

    This checks the policy this release would be served under, not the
    live response — nothing at build time can observe a CDN. That limit
    is worth stating: the gate catches a policy weakened in source, not
    a host misconfigured to drop the headers. Verifying the latter needs
    a probe against the deployed origin, which belongs to deployment.

    What it does catch is the failure that is otherwise invisible:
    `frame-ancestors` or HSTS quietly removed, leaving a policy string
    that still reads as protective.
    """
    problems = check_headers(dict(SECURITY_HEADERS))
    if problems:
        return GateResult(
            "security-headers",
            GateOutcome.FAILED,
            tuple(f"{p.header}: {p.detail}" for p in problems),
        )
    return GateResult(
        "security-headers",
        GateOutcome.PASSED,
        detail={"headers": str(len(SECURITY_HEADERS))},
    )


#: The gates every activation must pass, in reporting order.
DEFAULT_GATES: tuple[Gate, ...] = (
    gate_manifest,
    gate_arithmetic,
    gate_provenance,
    gate_disclosure,
    gate_neutral_language,
    gate_digests,
    gate_payload_budget,
    gate_creator_pagination,
    gate_dependency_scan,
    gate_security_headers,
    gate_signoff,
)


def run_gates(candidate: ReleaseCandidate, gates: Sequence[Gate] | None = None) -> ValidationReport:
    """Run every gate, collecting all results.

    A gate that raises is recorded as INCOMPLETE rather than propagating:
    an exception in one check must not prevent the others from reporting,
    and it certainly must not be mistaken for a pass.
    """
    report = ValidationReport(release_id=candidate.release_id)

    for gate in gates or DEFAULT_GATES:
        name = getattr(gate, "__name__", "gate").removeprefix("gate_")
        try:
            report.results.append(gate(candidate))
        except Exception as exc:  # noqa: BLE001 - a failing gate must not pass
            report.results.append(
                GateResult(
                    name,
                    GateOutcome.INCOMPLETE,
                    (f"gate raised {type(exc).__name__}",),
                )
            )

    return report


def canonical_report_bytes(report: ValidationReport) -> bytes:
    """Serialize a report for the durable internal record."""
    return canonical_bytes(
        {
            "releaseId": report.release_id,
            "passed": report.passed,
            "results": [
                {
                    "name": r.name,
                    "outcome": r.outcome.value,
                    "reasons": list(r.reasons),
                    "detail": r.detail,
                }
                for r in report.results
            ],
        }
    )
