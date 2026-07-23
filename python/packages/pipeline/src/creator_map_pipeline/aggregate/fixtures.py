"""Deterministic synthetic release fixtures.

The real-artifact contract test reads `dist/`, which means it skips on a
clean checkout and in any CI job that has no database. A test that skips
silently is a test that proves nothing, and the drift it exists to catch
— Python writing artifacts the TypeScript loader cannot read — is exactly
the kind that goes unnoticed until a browser hits it.

This generates a complete, valid release from a fixed seed, with no
database and no network. Same seed, same bytes, always: the counts are
derived arithmetically rather than sampled, so the reconciliation the
release gates check holds by construction rather than by luck.

Nothing here is real. Channel ids and display names are synthesized, and
the country assignments are arbitrary. It would be a mistake to read any
of it as an observation about a real creator.

Requirement refs: 8.1, 8.8, 14.6-14.11
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TypedDict

from creator_map_pipeline.aggregate.artifacts import GeneratedArtifact


class DatasetCount(TypedDict):
    """One dataset's contribution to a creator's or country's total."""

    datasetId: str
    representedVideoCount: int


class CreatorRow(TypedDict):
    """One published creator row, shaped as the artifact contract wants.

    A TypedDict rather than a bare dict so the sort keys and the
    arithmetic below are checked: the counts here feed the reconciliation
    the release gates enforce, and an untyped `object` there silently
    permits a row that would fail them at build time.
    """

    publicChannelKey: str
    displayName: str
    representedVideoCount: int
    country: str
    #: Per-dataset counts. These do not sum to representedVideoCount: a
    #: video observed in two datasets is one represented video but two
    #: dataset observations, and that non-additivity is a real property
    #: of the corpus rather than an inconsistency to smooth over.
    datasetBreakdown: list[DatasetCount]
    lastObservedAt: str


class CountrySummary(TypedDict):
    """One country's row in the overview."""

    country: str
    creatorCount: int
    representedVideoCount: int
    sourceOccurrenceCount: int
    resolvedVideoCount: int
    unavailableVideoCount: int


#: Fixed so two runs on two machines produce identical bytes.
DEFAULT_SEED = "creator-map-fixture-v1"

#: Enough countries to exercise binning, the no-data category, and the
#: Unknown bucket without generating a payload nobody wants to read.
FIXTURE_COUNTRIES: tuple[tuple[str, int], ...] = (
    ("US", 40),
    ("GB", 25),
    ("ZA", 18),
    ("IE", 12),
    ("DE", 9),
    ("BR", 6),
    ("JP", 3),
    # "XX" is user-assigned in ISO 3166 and will never be issued to a
    # country, so the Unknown bucket cannot collide with a real code.
    ("XX", 7),
)


@dataclass(frozen=True, slots=True)
class FixtureRelease:
    """A complete synthetic release ready to write to disk."""

    release_id: str
    artifacts: tuple[GeneratedArtifact, ...]

    def by_path(self, suffix: str) -> GeneratedArtifact:
        for artifact in self.artifacts:
            if artifact.path.endswith(suffix):
                return artifact
        msg = f"no fixture artifact ending in {suffix!r}"
        raise KeyError(msg)


def _stable_int(seed: str, *parts: str, modulo: int) -> int:
    """A deterministic pseudo-random integer.

    Uses a digest rather than `random` so the value depends only on the
    inputs — no module-level state, no ordering effects, and identical
    across Python versions.
    """
    digest = hashlib.sha256("|".join((seed, *parts)).encode()).digest()
    return int.from_bytes(digest[:8], "big") % modulo


def _channel_id(seed: str, index: int) -> str:
    """A synthetic channel id with the right shape but no real referent."""
    digest = hashlib.sha256(f"{seed}|channel|{index}".encode()).hexdigest()
    # Real YouTube channel ids are "UC" plus 22 base64url characters.
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    body = "".join(alphabet[int(digest[i : i + 2], 16) % len(alphabet)] for i in range(0, 44, 2))
    return f"UC{body}"


def _public_key(seed: str, channel_id: str) -> str:
    """A stable public key. The fixture uses a published secret on
    purpose: these are synthetic channels, and using a real secret here
    would put it in the repository."""
    digest = hashlib.sha256(f"{seed}|pk|{channel_id}".encode()).hexdigest()
    return f"pk_{digest[:32]}"


def build_fixture_release(
    *,
    seed: str = DEFAULT_SEED,
    release_id: str = "2026-01-01T00-00-00Z",
    page_size: int = 50,
) -> FixtureRelease:
    """Generate a complete synthetic release.

    Counts are computed from the creator rows rather than chosen
    independently, so the arithmetic gates hold by construction. A
    fixture whose totals did not reconcile would fail the gates for a
    reason that has nothing to do with the code under test.
    """
    artifacts: list[GeneratedArtifact] = []

    # Build creators per country first; every published count derives
    # from these rows.
    creators: dict[str, list[CreatorRow]] = {}
    index = 0
    for country, count in FIXTURE_COUNTRIES:
        rows: list[CreatorRow] = []
        for _ in range(count):
            channel_id = _channel_id(seed, index)
            videos = 1 + _stable_int(seed, "videos", channel_id, modulo=180)
            # Split across two datasets with deliberate overlap, so the
            # per-dataset counts sum to more than the represented total.
            in_a = 1 + _stable_int(seed, "ds-a", channel_id, modulo=videos)
            in_b = videos - in_a + _stable_int(seed, "ds-b", channel_id, modulo=in_a + 1)
            rows.append(
                CreatorRow(
                    publicChannelKey=_public_key(seed, channel_id),
                    displayName=f"Fixture Channel {index:03d}",
                    representedVideoCount=videos,
                    country=country,
                    datasetBreakdown=[
                        DatasetCount(datasetId="fixture-a", representedVideoCount=in_a),
                        DatasetCount(datasetId="fixture-b", representedVideoCount=max(in_b, 0)),
                    ],
                    lastObservedAt="2026-01-01",
                )
            )
            index += 1
        # Same ordering the real builder uses.
        rows.sort(key=lambda r: (-r["representedVideoCount"], r["publicChannelKey"]))
        creators[country] = rows

    country_summaries: list[CountrySummary] = []
    for country, rows in creators.items():
        represented = sum(r["representedVideoCount"] for r in rows)
        country_summaries.append(
            CountrySummary(
                country=country,
                creatorCount=len(rows),
                representedVideoCount=represented,
                # Occurrences are at least represented videos: a video
                # can appear in more than one dataset row.
                sourceOccurrenceCount=represented + len(rows),
                resolvedVideoCount=represented,
                unavailableVideoCount=0,
            )
        )

    total_creators = sum(len(rows) for rows in creators.values())
    total_videos = sum(c["representedVideoCount"] for c in country_summaries)
    total_occurrences = sum(c["sourceOccurrenceCount"] for c in country_summaries)

    # The resolution partition must sum to the distinct input count, so
    # the remainder is assigned to the non-resolved buckets rather than
    # picked independently.
    unresolved = 240
    distinct_inputs = total_videos + unresolved

    unknown_creators = len(creators.get("XX", []))

    coverage = {
        "inputOccurrenceCount": total_occurrences + unresolved,
        "distinctInputVideoCount": distinct_inputs,
        "partition": {
            "resolved": total_videos,
            "unavailableUnclassified": 120,
            "retryableOrPending": 60,
            "invalid": 40,
            "terminalFailure": 20,
        },
        "resolvedChannelCount": total_creators,
        "knownCountryChannelCount": total_creators - unknown_creators,
        "unknownCountryChannelCount": unknown_creators,
    }

    default_filter = {"datasets": ["fixture-a", "fixture-b"], "corpusClasses": ["Candidate"]}

    overview = {
        "schemaVersion": "1.0.0",
        "releaseId": release_id,
        "filter": default_filter,
        "countries": sorted(country_summaries, key=lambda c: c["country"]),
        "coverage": coverage,
        "creatorCount": total_creators,
        "representedVideoCount": total_videos,
        "representedCountryCount": len(country_summaries),
    }
    artifacts.append(
        GeneratedArtifact(path=f"releases/{release_id}/overview.json", payload=overview).finalize()
    )

    # Country shards, with paged creator rows in both sort orders.
    for country, rows in creators.items():
        for sort_key, ordered in (
            (
                "representedVideoCountDesc",
                rows,
            ),
            (
                "displayNameAsc",
                sorted(rows, key=lambda r: r["displayName"]),
            ),
        ):
            for page_number in range((len(ordered) + page_size - 1) // page_size):
                window = ordered[page_number * page_size : (page_number + 1) * page_size]
                artifacts.append(
                    GeneratedArtifact(
                        path=(
                            f"releases/{release_id}/countries/{country}/"
                            f"{sort_key}/page-{page_number}.json"
                        ),
                        payload={
                            "country": country,
                            "sortOrder": sort_key,
                            "pageSize": page_size,
                            "totalRows": len(ordered),
                            "rows": window,
                            # Keyset cursor, not an offset: exactly-once
                            # traversal under a stable sort.
                            "nextCursor": (
                                window[-1]["publicChannelKey"]
                                if (page_number + 1) * page_size < len(ordered)
                                else None
                            ),
                        },
                    ).finalize()
                )

        summary = next(c for c in country_summaries if c["country"] == country)
        first_page = rows[:page_size]
        artifacts.append(
            GeneratedArtifact(
                path=f"releases/{release_id}/countries/{country}.json",
                payload={
                    "country": country,
                    "creatorCount": summary["creatorCount"],
                    "representedVideoCount": summary["representedVideoCount"],
                    "sourceOccurrenceCount": summary["sourceOccurrenceCount"],
                    "coverage": coverage,
                    "datasetBreakdown": [
                        {
                            "datasetId": "fixture-a",
                            "representedVideoCount": sum(
                                r["datasetBreakdown"][0]["representedVideoCount"] for r in rows
                            ),
                        },
                        {
                            "datasetId": "fixture-b",
                            "representedVideoCount": sum(
                                r["datasetBreakdown"][1]["representedVideoCount"] for r in rows
                            ),
                        },
                    ],
                    "firstPage": {
                        "country": country,
                        "sortOrder": "representedVideoCountDesc",
                        "pageSize": page_size,
                        "totalRows": len(rows),
                        "rows": first_page,
                        "nextCursor": (
                            first_page[-1]["publicChannelKey"] if len(rows) > page_size else None
                        ),
                    },
                    "pageIndex": {
                        # Release-rooted, matching the real builder: the
                        # loader resolves these against the storage root,
                        # not against the shard's own directory.
                        "representedVideoCountDesc": [
                            f"releases/{release_id}/countries/{country}"
                            f"/representedVideoCountDesc/page-{n}.json"
                            for n in range((len(rows) + page_size - 1) // page_size)
                        ],
                        "displayNameAsc": [
                            f"releases/{release_id}/countries/{country}"
                            f"/displayNameAsc/page-{n}.json"
                            for n in range((len(rows) + page_size - 1) // page_size)
                        ],
                    },
                },
            ).finalize()
        )

    manifest = {
        "schemaVersion": "1.0.0",
        "releaseId": release_id,
        "generatedAt": datetime(2026, 1, 1, tzinfo=UTC).isoformat().replace("+00:00", "Z"),
        "enrichmentCutoff": datetime(2025, 12, 31, tzinfo=UTC).isoformat().replace("+00:00", "Z"),
        "defaultFilter": default_filter,
        "datasets": [
            {
                "datasetId": f"fixture-{suffix}",
                "displayName": f"Fixture Dataset {suffix.upper()}",
                "version": "1.0.0",
                "corpusClass": "Candidate",
                "sourceKind": "MetadataOnly",
                "occurrenceUnit": "Row",
                "sourceCitation": f"https://example.invalid/fixture-{suffix}",
                "snapshotDigest": "sha256:"
                + hashlib.sha256(f"{seed}|dataset|{suffix}".encode()).hexdigest(),
            }
            for suffix in ("a", "b")
        ],
        # The filter index the application reads to resolve a selection
        # to a precomputed overview. One entry here, because a fixture
        # with a single filter still exercises the resolution path — the
        # non-additivity that motivated precomputation is a property of
        # real data, not something a fixture should pretend to model.
        "filters": [
            {
                # The key uses the same storage-safe separators the real
                # builder produces (`_` within a group, `__` between
                # groups), so the fixture cannot drift into a format that
                # would fail to publish.
                "key": "Candidate__fixture-a_fixture-b",
                "label": "All datasets",
                "corpusClasses": ["Candidate"],
                "datasets": ["fixture-a", "fixture-b"],
                "isDefault": True,
                "path": f"releases/{release_id}/overview.json",
            }
        ],
        "artifactDigests": {a.path: a.digest for a in artifacts},
        "methodologyVersion": "1.0.0",
        "disclosurePolicyVersion": "1.0.0-fixture",
        "boundaryMetadata": {
            "datasetName": "Natural Earth Admin 0 - Countries",
            "version": "5.1.1",
            "license": "Public domain",
            "attribution": "Made with Natural Earth",
            "disputedTerritoryTreatment": (
                "Boundaries follow Natural Earth's cartographic conventions. "
                "They are a presentation choice made for legibility, not a "
                "position on any territorial question."
            ),
        },
    }
    artifacts.append(
        GeneratedArtifact(path=f"releases/{release_id}/manifest.json", payload=manifest).finalize()
    )

    return FixtureRelease(release_id=release_id, artifacts=tuple(artifacts))
