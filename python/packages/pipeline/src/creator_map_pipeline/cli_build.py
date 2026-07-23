"""Curator command for building public artifacts from the provenance store.

Usage:
    python -m creator_map_pipeline.cli_build --out dist/ --actor nandi

Builds an *inactive* artifact set: nothing is activated and no pointer is
moved. Requirement 8.4 stages a complete set before activation, and the
release commands handle that separately.

Requirement refs: 5.1-5.13, 6.1-6.11, 7.1-7.11, 8.1
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

import psycopg
from creator_map_schemas import DisclosurePolicy

from creator_map_pipeline.aggregate.artifacts import (
    ArtifactSet,
    DisclosureViolation,
    approved_creator_rows,
    build_active_pointer,
    build_country_detail,
    build_manifest,
    build_overview,
    country_shard_path,
)
from creator_map_pipeline.aggregate.builder import (
    AggregateInputs,
    build_aggregates,
    default_filter,
)
from creator_map_pipeline.aggregate.disclosure import DisclosureEngine
from creator_map_pipeline.database import (
    DatabaseConfigError,
    redacted_target,
    resolve_database_url,
)
from creator_map_pipeline.registry import DatasetRegistry
from creator_map_pipeline.repositories import (
    AuditEntry,
    PostgresDatasetRepository,
    record_audit,
)

#: Default disclosure policy for development builds.
#:
#: The design requires small-group thresholds to be chosen through privacy
#: review before launch, and the system fails closed until they are. This
#: value is a development placeholder, not that review's outcome.
_DEV_POLICY = DisclosurePolicy.model_validate(
    {
        "policy_id": "development-disclosure",
        "version": "0.1.0-dev",
        "approved_at": datetime(2026, 1, 1, tzinfo=UTC),
        "min_represented_video_count": 5,
        "allowed_fields": ("display_name", "represented_video_count"),
    }
)

_BOUNDARY_METADATA = {
    "datasetName": "Natural Earth Admin 0 - Countries",
    "version": "5.1.1",
    "license": "Public domain",
    "attribution": "Made with Natural Earth",
    "disputedTerritoryTreatment": (
        "Boundaries are a presentation convention and are not evidence of "
        "channel location beyond declared country metadata."
    ),
}


def _build(args: argparse.Namespace, url: str) -> int:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    generated_at = datetime.now(UTC)
    release_id = args.release_id or generated_at.strftime("%Y-%m-%dT%H-%M-%SZ")
    cutoff = generated_at

    secret = args.public_key_secret
    if not secret:
        print(
            "error: --public-key-secret is required. Public channel keys are "
            "derived with it, and an empty secret would make them reversible "
            "by enumeration.",
            file=sys.stderr,
        )
        return 2

    with psycopg.connect(url) as connection, connection.cursor() as cur:
        active = default_filter(cur)
        if not active.datasets:
            print("no approved datasets to build from", file=sys.stderr)
            return 1

        inputs = AggregateInputs(
            enrichment_cutoff=cutoff,
            policy_version=args.policy_version,
            active_filter=active,
        )

        print(f"filter: {list(active.datasets)}")
        result = build_aggregates(cur, inputs, creator_limit=args.creator_limit)

        if result.coverage is None or result.partition is None:
            print("aggregation produced no coverage", file=sys.stderr)
            return 1

        registry = DatasetRegistry(PostgresDatasetRepository(cur))
        citations = [
            {
                "datasetId": entry["datasetId"],
                "displayName": entry["displayName"],
                "version": entry["version"],
                "corpusClass": entry["corpusClass"],
                "sourceKind": entry["sourceKind"],
                "occurrenceUnit": entry["occurrenceUnit"],
                "sourceCitation": entry["sourceCitation"],
                "snapshotDigest": entry["snapshotDigest"],
            }
            for entry in registry.methodology_entries()
        ]

        engine = DisclosureEngine(_DEV_POLICY, public_key_secret=secret)
        observed = cutoff.date().isoformat()

        artifacts = ArtifactSet()

        overview = build_overview(result, release_id=release_id, active_filter=active)
        artifacts.add(f"releases/{release_id}/overview.json", overview)

        # Group approved creators by country so each shard carries only its
        # own rows (Requirement 14.4 defers detail until requested).
        by_country: dict[str, list[object]] = {}
        for aggregate in result.creators:
            by_country.setdefault(aggregate.country, []).append(aggregate)

        shard_count = 0
        for country_summary in result.countries:
            country = country_summary.country
            aggregates = by_country.get(country, [])
            rows = approved_creator_rows(
                aggregates,  # type: ignore[arg-type]
                engine=engine,
                observed_at=observed,
            )
            detail = build_country_detail(
                country,
                summary=country_summary,
                coverage=result.coverage,
                partition=result.partition,
                rows=rows,
                page_size=args.page_size,
            )
            artifacts.add(country_shard_path(release_id, country), detail)
            shard_count += 1

        manifest = build_manifest(
            release_id=release_id,
            generated_at=generated_at,
            enrichment_cutoff=cutoff,
            default_filter=active,
            datasets=citations,
            artifact_digests=artifacts.digests,
            methodology_version="1.0.0",
            disclosure_policy_version=_DEV_POLICY.version,
            boundary_metadata=_BOUNDARY_METADATA,
        )
        manifest_artifact = artifacts.add(f"releases/{release_id}/manifest.json", manifest)

        pointer = build_active_pointer(
            release_id=release_id,
            manifest_path=f"releases/{release_id}/manifest.json",
            manifest_digest=manifest_artifact.digest,
        )

        for artifact in artifacts.artifacts:
            target = out / artifact.path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(artifact.content)

        # The pointer is written but not activated: staging and activation
        # are separate operations (Requirement 8.4).
        pointer_path = out / "active-release.candidate.json"
        from creator_map_pipeline.aggregate.artifacts import canonical_bytes

        pointer_path.write_bytes(canonical_bytes(pointer))

        record_audit(
            cur,
            AuditEntry(
                actor=args.actor,
                action="build_release_candidate",
                resource_class="public.artifact",
                outcome="success",
                detail={
                    "releaseId": release_id,
                    "artifactCount": str(len(artifacts.artifacts)),
                    "countryShards": str(shard_count),
                },
            ),
        )
        connection.commit()

    overview_bytes = next(a for a in artifacts.artifacts if a.path.endswith("overview.json"))
    print(f"release:   {release_id}")
    print(f"artifacts: {len(artifacts.artifacts)} ({artifacts.total_bytes:,} bytes)")
    print(f"overview:  {len(overview_bytes.content):,} bytes uncompressed")
    print(f"creators:  {result.creator_count:,}")
    print(f"videos:    {result.represented_video_count:,}")
    print(f"countries: {shard_count} shards")
    print(f"written to {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cli_build", description=__doc__)
    parser.add_argument("--out", default="dist")
    parser.add_argument("--actor", required=True)
    parser.add_argument("--release-id", default=None)
    parser.add_argument("--policy-version", default="1.0.0")
    parser.add_argument("--page-size", type=int, default=50)
    parser.add_argument("--creator-limit", type=int, default=None)
    parser.add_argument(
        "--public-key-secret",
        default="",
        help="Secret used to derive public channel keys (restricted).",
    )
    args = parser.parse_args(argv)

    try:
        url = resolve_database_url()
    except DatabaseConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(f"target: {redacted_target(url)}", file=sys.stderr)

    try:
        return _build(args, url)
    except DisclosureViolation as exc:
        # Requirement 7.5: a prohibited value blocks the whole build.
        print(f"disclosure violation: {exc}", file=sys.stderr)
        return 1
    except psycopg.Error as exc:
        print(f"database error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
