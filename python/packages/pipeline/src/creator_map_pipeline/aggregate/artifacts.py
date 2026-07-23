"""Disclosure-reviewed public artifact generation.

This is the publication boundary in code. Everything upstream operates on
restricted data; everything this module emits is intended for anonymous
public delivery.

Two independent checks guard the crossing, deliberately not sharing an
implementation. The disclosure engine decides *who* may appear and *which
fields*, working from an allowlist. The prohibited-content scan then reads
the finished bytes and looks for anything that should never be there at all
— a raw identifier, a locator, a credential. The first is a policy decision
and the second is a backstop against the first being wrong.

Requirement refs: 6.7-6.11, 7.2-7.7, 7.9-7.11, 10.3-10.7, 12.1-12.11
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from creator_map_schemas import UNKNOWN_COUNTRY, CountrySummary, CoverageSummary, Filter

from creator_map_pipeline.aggregate.builder import AggregateResult, CreatorAggregate
from creator_map_pipeline.aggregate.disclosure import (
    CreatorCandidate,
    DisclosureEngine,
)
from creator_map_pipeline.aggregate.pagination import (
    CreatorRow,
    CreatorSortOrder,
    paginate,
)

ARTIFACT_SCHEMA_VERSION = "1.0.0"

#: Field names that must never appear in a public artifact at any depth.
#: Mirrors the TypeScript guard so both sides of the boundary agree.
_PROHIBITED_KEYS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^videoIds?$", re.I), "raw video identifier field"),
    (re.compile(r"sourceLocator", re.I), "source locator field"),
    (re.compile(r"^channelId$", re.I), "raw channel identifier field"),
    (re.compile(r"rawResponse", re.I), "raw API response field"),
    (re.compile(r"responseDigest", re.I), "restricted provenance join"),
    (re.compile(r"^email$", re.I), "contact field"),
    (re.compile(r"restricted_?", re.I), "restricted-marked field"),
    (re.compile(r"apiKey|^secret|password|accessToken", re.I), "credential field"),
    (re.compile(r"suppressionReason", re.I), "suppression reason"),
    (re.compile(r"^acquisitionPath$|^termsReviewId$", re.I), "internal review field"),
)

#: Value patterns prohibited wherever they appear.
_PROHIBITED_VALUES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?<![\w-])UC[A-Za-z0-9_-]{22}(?![\w-])"), "raw channel identifier"),
    (re.compile(r"(?:youtube\.com|youtu\.be)/", re.I), "YouTube URL"),
    (re.compile(r"\bsb_secret_[A-Za-z0-9_-]+"), "Supabase secret key"),
    (re.compile(r"\bAIza[A-Za-z0-9_-]{35}\b"), "Google API key"),
    (re.compile(r"postgres(?:ql)?://[^\s]*:[^\s]*@", re.I), "connection string"),
)

#: Fields whose values are prose and may contain identifier-shaped words.
_PROSE_KEYS = re.compile(
    r"^(note|notes|description|summary|methodology|label|title"
    r"|attribution|license|disclaimer|caption|heading|text)$",
    re.I,
)

#: Fields exempt from the bare-video-id heuristic entirely.
#:
#: A channel display name comes from the channel metadata API and is a
#: name, not an identifier. Real names collide with the 11-character
#: base64url shape often enough to matter: 770 of the channels in the
#: current corpus have names like "101Treesrus" or "1BreezyLife", and
#: flagging them blocked the whole build.
#:
#: The exemption is narrow and safe. These fields remain subject to every
#: key check and to the channel-id, YouTube-URL, and credential patterns —
#: only the heuristic that guesses from shape alone is skipped, and that
#: heuristic can never be decisive for a field whose contents are, by
#: definition, whatever a human typed as their channel name.
_NAME_KEYS = re.compile(r"^(displayName|datasetName|channelName)$", re.I)

#: A value that is entirely a bare video identifier.
_BARE_VIDEO_ID = re.compile(
    r"^(?=[A-Za-z0-9_-]{11}$)(?:.*[0-9_-]|.*[a-z].*[A-Z]|.*[A-Z].*[a-z]).*$"
)

_DIGEST_SHAPE = re.compile(r"^sha256:[a-f0-9]{64}$")


@dataclass(frozen=True, slots=True)
class Finding:
    """One prohibited-content finding. Safe to log."""

    path: str
    reason: str


class DisclosureViolation(RuntimeError):
    """Raised when an artifact carries prohibited content."""

    def __init__(self, findings: list[Finding]) -> None:
        summary = "; ".join(f"{f.path}: {f.reason}" for f in findings[:5])
        extra = f" (+{len(findings) - 5} more)" if len(findings) > 5 else ""
        super().__init__(f"artifact failed disclosure inspection: {summary}{extra}")
        self.findings = findings


def find_prohibited_content(
    node: object, path: str = "$", findings: list[Finding] | None = None
) -> list[Finding]:
    """Recursively inspect a payload for prohibited keys and values.

    Requirement 7.6 requires keys, values, and embedded metadata to be
    inspected recursively; this returns every finding rather than the first
    so a release report can list them all.
    """
    collected = findings if findings is not None else []

    if isinstance(node, str):
        _inspect_value(node, None, path, collected)
    elif isinstance(node, list):
        for index, item in enumerate(node):
            find_prohibited_content(item, f"{path}[{index}]", collected)
    elif isinstance(node, dict):
        for key, value in node.items():
            child = key if path == "$" else f"{path}.{key}"
            _inspect_key(str(key), child, collected)
            if isinstance(value, str):
                _inspect_value(value, str(key), child, collected)
            else:
                find_prohibited_content(value, child, collected)

    return collected


def _inspect_key(key: str, path: str, findings: list[Finding]) -> None:
    for pattern, reason in _PROHIBITED_KEYS:
        if pattern.search(key):
            findings.append(Finding(path=path, reason=reason))
            return


def _inspect_value(value: str, key: str | None, path: str, findings: list[Finding]) -> None:
    for pattern, reason in _PROHIBITED_VALUES:
        if pattern.search(value):
            findings.append(Finding(path=path, reason=reason))
            return

    if _DIGEST_SHAPE.match(value):
        return

    # A name is a name. The shape heuristic cannot distinguish a channel
    # called "101Treesrus" from a video id, and the field's meaning
    # already settles it, so the guess is skipped rather than allowed to
    # veto real data.
    if key is not None and _NAME_KEYS.match(key):
        return

    # Under a prose key only a whole-string identifier is prohibited: an
    # 11-letter English word is not a leak, and rejecting one would block
    # the methodology copy Requirement 12 obliges us to publish.
    if key is not None and _PROSE_KEYS.match(key):
        if _BARE_VIDEO_ID.match(value):
            findings.append(Finding(path=path, reason="raw video identifier"))
        return

    if re.match(r"^[A-Za-z0-9_-]{11}$", value) and _BARE_VIDEO_ID.match(value):
        findings.append(Finding(path=path, reason="possible raw video identifier"))


def assert_publishable(payload: object) -> None:
    """Fail closed when a payload carries prohibited content."""
    findings = find_prohibited_content(payload)
    if findings:
        raise DisclosureViolation(findings)


def canonical_bytes(payload: object) -> bytes:
    """Serialize deterministically for digesting and delivery.

    Sorted keys and compact separators, so the same aggregate yields the
    same bytes on every build (Requirement 5.13) and its digest is
    meaningful.
    """
    return json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode(
        "utf-8"
    )


def digest_of(payload: object) -> str:
    """Return the artifact digest recorded in the manifest."""
    return f"sha256:{hashlib.sha256(canonical_bytes(payload)).hexdigest()}"


@dataclass(slots=True)
class GeneratedArtifact:
    """One public artifact, its bytes, and its digest."""

    path: str
    payload: dict[str, Any]
    content: bytes = b""
    digest: str = ""

    def finalize(self) -> GeneratedArtifact:
        """Inspect, serialize, and digest. Fails closed on any finding."""
        assert_publishable(self.payload)
        self.content = canonical_bytes(self.payload)
        self.digest = f"sha256:{hashlib.sha256(self.content).hexdigest()}"
        return self


@dataclass(slots=True)
class ArtifactSet:
    """Every artifact one release publishes."""

    artifacts: list[GeneratedArtifact] = field(default_factory=list)

    def add(self, path: str, payload: dict[str, Any]) -> GeneratedArtifact:
        artifact = GeneratedArtifact(path=path, payload=payload).finalize()
        self.artifacts.append(artifact)
        return artifact

    @property
    def digests(self) -> dict[str, str]:
        return {a.path: a.digest for a in self.artifacts}

    @property
    def total_bytes(self) -> int:
        return sum(len(a.content) for a in self.artifacts)


def _coverage_payload(coverage: CoverageSummary, partition: Any) -> dict[str, Any]:
    """Serialize coverage with the full resolution partition.

    Requirement 6.7 displays the partition beside headline totals, so the
    artifact must carry every state rather than a resolved/unresolved split.
    """
    return {
        "inputOccurrenceCount": coverage.input_occurrence_count,
        "distinctInputVideoCount": coverage.distinct_input_video_count,
        "partition": {
            "resolved": partition.resolved_count,
            "unavailableUnclassified": partition.unavailable_unclassified_count,
            "retryableOrPending": partition.retryable_or_pending_count,
            "invalid": partition.invalid_count,
            "terminalFailure": partition.terminal_failure_count,
        },
        "resolvedChannelCount": coverage.resolved_channel_count,
        "knownCountryChannelCount": coverage.known_country_channel_count,
        "unknownCountryChannelCount": coverage.unknown_country_channel_count,
    }


def _country_payload(summary: CountrySummary) -> dict[str, Any]:
    return {
        "country": summary.country,
        "creatorCount": summary.creator_count,
        "representedVideoCount": summary.represented_video_count,
        "sourceOccurrenceCount": summary.source_occurrence_count,
        "resolvedVideoCount": summary.resolved_video_count,
        "unavailableVideoCount": summary.unavailable_video_count,
    }


def _filter_payload(active: Filter) -> dict[str, Any]:
    return {
        "datasets": list(active.datasets),
        "corpusClasses": [c.value for c in active.corpus_classes],
    }


def approved_creator_rows(
    creators: list[CreatorAggregate],
    *,
    engine: DisclosureEngine,
    observed_at: str,
) -> list[CreatorRow]:
    """Apply the disclosure policy, returning only publishable rows.

    A creator failing any condition is simply absent. No placeholder, no
    marker, no count of how many were withheld — Requirement 7.8 treats the
    reason itself as disclosure.
    """
    rows: list[CreatorRow] = []

    for creator in creators:
        candidate = CreatorCandidate(
            channel_id=creator.channel_id,
            display_name=creator.display_name,
            represented_video_count=creator.represented_video_count,
            country=creator.country,
        )
        decision = engine.decide(candidate)
        if not decision.permitted:
            continue

        projected = engine.project(candidate, decision)
        display_name = projected.get("display_name")

        rows.append(
            CreatorRow(
                public_channel_key=engine.public_key_for(creator.channel_id),
                display_name=str(display_name) if display_name else "",
                country=creator.country,
                represented_video_count=creator.represented_video_count,
                dataset_breakdown=creator.dataset_breakdown,
                last_observed_at=observed_at,
            )
        )

    return rows


def _creator_row_payload(row: CreatorRow) -> dict[str, Any]:
    return {
        "publicChannelKey": row.public_channel_key,
        "displayName": row.display_name,
        "country": row.country,
        "representedVideoCount": row.represented_video_count,
        "datasetBreakdown": [
            {"datasetId": dataset, "representedVideoCount": count}
            for dataset, count in row.dataset_breakdown
        ],
        "lastObservedAt": row.last_observed_at,
    }


def build_overview(
    result: AggregateResult,
    *,
    release_id: str,
    active_filter: Filter,
) -> dict[str, Any]:
    """Build the default overview payload.

    Requirement 14.1 budgets this artifact, so it carries country summaries
    and coverage only — creator detail is deferred to per-country shards
    (Requirement 14.4).
    """
    if result.coverage is None or result.partition is None:
        msg = "aggregate result is incomplete; refusing to build an overview"
        raise ValueError(msg)

    return {
        "schemaVersion": ARTIFACT_SCHEMA_VERSION,
        "releaseId": release_id,
        "filter": _filter_payload(active_filter),
        "countries": [_country_payload(s) for s in result.countries],
        "coverage": _coverage_payload(result.coverage, result.partition),
        "creatorCount": result.creator_count,
        "representedVideoCount": result.represented_video_count,
        "representedCountryCount": result.represented_country_count,
    }


def creator_page_path(
    release_id: str, country: str, sort_order: CreatorSortOrder, index: int
) -> str:
    """Return the delivery path for one creator page beyond the first.

    Pages are addressed by ordinal within a sort order rather than by
    cursor: a cursor is an opaque position, so naming files after them
    would make the artifact set unlistable and undiagnosable. The client
    still traverses by cursor; this is only where the bytes live.
    """
    return f"releases/{release_id}/countries/{country}/{sort_order.value}/page-{index}.json"


def build_creator_pages(
    country: str,
    rows: list[CreatorRow],
    *,
    page_size: int,
    sort_order: CreatorSortOrder = CreatorSortOrder.VIDEO_COUNT_DESC,
) -> list[dict[str, Any]]:
    """Build every creator page for one country and sort order.

    Requirement 10.6 requires traversing all pages to present each
    approved creator exactly once without omission. A shard that
    advertises a next cursor but publishes no page for it cannot satisfy
    that — following the cursor would 404 — so every page the traversal
    can reach is emitted.
    """
    pages: list[dict[str, Any]] = []
    cursor: str | None = None
    # Bounded so a cursor that failed to advance surfaces as a build
    # failure rather than an infinite loop.
    max_pages = len(rows) // max(page_size, 1) + 2

    for _ in range(max_pages):
        page = paginate(rows, order=sort_order, page_size=page_size, cursor=cursor)
        pages.append(
            {
                "country": country,
                "sortOrder": sort_order.value,
                "rows": [_creator_row_payload(r) for r in page.rows],
                "nextCursor": page.next_cursor,
                "pageSize": page.page_size,
                "totalRows": page.total_rows,
            }
        )
        if page.next_cursor is None:
            return pages
        cursor = page.next_cursor

    msg = f"creator pagination for {country} did not terminate"
    raise ValueError(msg)


def build_country_detail(
    country: str,
    *,
    summary: CountrySummary,
    coverage: CoverageSummary,
    partition: Any,
    rows: list[CreatorRow],
    page_size: int,
    sort_order: CreatorSortOrder = CreatorSortOrder.VIDEO_COUNT_DESC,
) -> dict[str, Any]:
    """Build one country's detail shard with its first creator page."""
    page = paginate(rows, order=sort_order, page_size=page_size)

    dataset_totals: dict[str, int] = {}
    for row in rows:
        for dataset, count in row.dataset_breakdown:
            dataset_totals[dataset] = dataset_totals.get(dataset, 0) + count

    return {
        "country": country,
        "creatorCount": summary.creator_count,
        "representedVideoCount": summary.represented_video_count,
        "sourceOccurrenceCount": summary.source_occurrence_count,
        "coverage": _coverage_payload(coverage, partition),
        "datasetBreakdown": [
            {"datasetId": dataset, "representedVideoCount": count}
            for dataset, count in sorted(dataset_totals.items())
        ],
        "firstPage": {
            "country": country,
            "sortOrder": sort_order.value,
            "rows": [_creator_row_payload(r) for r in page.rows],
            "nextCursor": page.next_cursor,
            "pageSize": page.page_size,
            "totalRows": page.total_rows,
        },
    }


def build_manifest(
    *,
    release_id: str,
    generated_at: datetime,
    enrichment_cutoff: datetime,
    default_filter: Filter,
    datasets: list[dict[str, Any]],
    artifact_digests: dict[str, str],
    methodology_version: str,
    disclosure_policy_version: str,
    boundary_metadata: dict[str, str],
    filters: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the public release manifest (Requirement 8.1)."""
    return {
        "schemaVersion": ARTIFACT_SCHEMA_VERSION,
        "releaseId": release_id,
        "generatedAt": generated_at.isoformat().replace("+00:00", "Z"),
        "enrichmentCutoff": enrichment_cutoff.isoformat().replace("+00:00", "Z"),
        "defaultFilter": _filter_payload(default_filter),
        "datasets": datasets,
        "artifactDigests": dict(sorted(artifact_digests.items())),
        "methodologyVersion": methodology_version,
        "disclosurePolicyVersion": disclosure_policy_version,
        "boundaryMetadata": boundary_metadata,
        # The supported filter combinations and where their aggregates
        # live. Requirement 9.6 needs the client to reach an exact
        # per-filter artifact rather than approximate one locally.
        "filters": filters or [],
    }


def build_active_pointer(
    *, release_id: str, manifest_path: str, manifest_digest: str
) -> dict[str, Any]:
    """Build the separately refreshable active-release pointer.

    Kept minimal so it can carry a short cache lifetime while every artifact
    it references stays immutably cacheable (Requirement 14.10).
    """
    return {
        "schemaVersion": ARTIFACT_SCHEMA_VERSION,
        "releaseId": release_id,
        "manifestPath": manifest_path,
        "manifestDigest": manifest_digest,
    }


def country_shard_path(release_id: str, country: str) -> str:
    """Return the delivery path for one country detail shard."""
    safe = country if country == UNKNOWN_COUNTRY else country.upper()
    return f"releases/{release_id}/countries/{safe}.json"
