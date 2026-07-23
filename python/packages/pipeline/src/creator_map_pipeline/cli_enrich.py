"""Curator commands for enrichment.

Usage:
    # Resolve video->channel from a snapshot that already carries channel_id
    python -m creator_map_pipeline.cli_enrich videos-from-snapshot \\
        --snapshot data/snapshots/x.parquet --actor nandi

    # Resolve channel country through the approved metadata API
    python -m creator_map_pipeline.cli_enrich channels-from-api --actor nandi

    python -m creator_map_pipeline.cli_enrich status

Two resolution paths exist because they answer different questions. A
snapshot that publishes `channel_id` already documents video-to-channel
attribution, so no metadata request is needed. Declared_Country exists only
at the metadata API, so the country path always requires a key.

Requirement refs: 3.1-3.12, 4.1-4.18, 15.16-15.21
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import psycopg
from creator_map_schemas import (
    EnrichmentPolicy,
    ErrorClass,
    FailureDisposition,
    ObservationTieBreaker,
    RetryPolicy,
)

from creator_map_pipeline.database import (
    DatabaseConfigError,
    redacted_target,
    resolve_database_url,
)
from creator_map_pipeline.enrichment.resolver import (
    HuggingFaceChannelResolver,
    HuggingFaceDisplayNameResolver,
)
from creator_map_pipeline.enrichment.runner import (
    run_channel_enrichment,
    run_video_enrichment,
)
from creator_map_pipeline.enrichment.youtube import YouTubeMetadataResolver
from creator_map_pipeline.parquet_source import iter_parquet_column_pairs
from creator_map_pipeline.repositories import AuditEntry, record_audit

API_KEY_VAR = "YOUTUBE_API_KEY"

#: YouTube Data API v3 default daily allocation.
DEFAULT_DAILY_QUOTA = 10_000


def _default_policy() -> EnrichmentPolicy:
    """The approved enrichment policy used by these commands."""
    dispositions = {
        ErrorClass.RATE_LIMITED: FailureDisposition.RETRYABLE,
        ErrorClass.NETWORK: FailureDisposition.RETRYABLE,
        ErrorClass.SERVER: FailureDisposition.RETRYABLE,
        ErrorClass.TIMEOUT: FailureDisposition.RETRYABLE,
        ErrorClass.MALFORMED_RESPONSE: FailureDisposition.RETRYABLE,
        ErrorClass.NOT_FOUND: FailureDisposition.NON_RETRYABLE,
        ErrorClass.INVALID_REQUEST: FailureDisposition.NON_RETRYABLE,
        ErrorClass.INVALID_CREDENTIAL: FailureDisposition.OPERATOR_HALT,
        ErrorClass.POLICY_BLOCKED: FailureDisposition.OPERATOR_HALT,
    }
    return EnrichmentPolicy.model_validate(
        {
            "policy_id": "default-enrichment",
            "version": "1.0.0",
            "approved_at": datetime(2026, 1, 1, tzinfo=UTC),
            "freshness_seconds": 30 * 86_400,
            "video_fields": ("id", "snippet.channelId"),
            "channel_fields": ("id", "snippet.country", "snippet.title"),
            "tie_breaker": ObservationTieBreaker.LATEST_OBSERVED_THEN_DIGEST,
            "retry_policy": RetryPolicy.model_validate(
                {
                    "policy_id": "default-retry",
                    "version": "1.0.0",
                    "max_attempts": 5,
                    "initial_delay_seconds": 2.0,
                    "max_delay_seconds": 300.0,
                    "backoff_multiplier": 2.0,
                    "jitter_fraction": 0.2,
                    "dispositions": tuple(sorted(dispositions.items())),
                }
            ),
            "quota_reserve": 500,
            "max_batch_size": 50,
        }
    )


def _resolve_api_key() -> str | None:
    """Read the API key from the environment or the local env file."""
    from creator_map_pipeline.database import _LOCAL_ENV_FILE

    key = os.environ.get(API_KEY_VAR, "").strip()
    if key:
        return key
    if _LOCAL_ENV_FILE.is_file():
        for raw in _LOCAL_ENV_FILE.read_text(encoding="utf-8-sig").splitlines():
            line = raw.strip()
            if line.startswith(f"{API_KEY_VAR}="):
                return line.split("=", 1)[1].strip().strip("\"'")
    return None


def _videos_from_snapshot(args: argparse.Namespace, url: str) -> int:
    """Resolve video-to-channel from a snapshot carrying channel_id."""
    snapshot = Path(args.snapshot)
    policy = _default_policy()

    mapping = dict(
        iter_parquet_column_pairs(
            snapshot,
            key_column=args.video_column,
            value_column=args.channel_column,
            max_rows=args.max_rows,
        )
    )
    if not mapping:
        print("snapshot yielded no video->channel pairs", file=sys.stderr)
        return 1

    print(f"snapshot provides {len(mapping):,} video->channel pairs")

    with psycopg.connect(url) as connection:
        with connection.cursor() as cur:
            # Enrich only videos actually present in the provenance store,
            # so the run is scoped to ingested evidence rather than to the
            # whole snapshot.
            cur.execute("select distinct video_id from provenance.dataset_video_membership")
            known = tuple(str(row[0]) for row in cur.fetchall())
        connection.rollback()

        print(f"provenance store holds {len(known):,} distinct videos")

        resolver = HuggingFaceChannelResolver(mapping, snapshot_digest=args.snapshot_digest)
        summary = run_video_enrichment(
            connection,
            job_id=args.job_id,
            video_ids=known,
            resolver=resolver,
            policy=policy,
            daily_quota_limit=DEFAULT_DAILY_QUOTA,
            max_batches=args.max_batches,
        )

        with connection.cursor() as cur:
            record_audit(
                cur,
                AuditEntry(
                    actor=args.actor,
                    action="enrich_videos_from_snapshot",
                    resource_class="enrichment.video_observation",
                    outcome="success",
                    detail={
                        "jobId": args.job_id,
                        "observations": str(summary.observations_written),
                        "resolved": str(summary.resolved),
                    },
                ),
            )
        connection.commit()

    print(summary.describe())
    return 0


def _channels_from_snapshot(args: argparse.Namespace, url: str) -> int:
    """Resolve channel display names from a snapshot (no country)."""
    snapshot = Path(args.snapshot)
    policy = _default_policy()

    names = dict(
        iter_parquet_column_pairs(
            snapshot,
            key_column=args.channel_column,
            value_column=args.name_column,
            max_rows=args.max_rows,
        )
    )
    print(f"snapshot provides {len(names):,} channel display names")
    print(
        "note: this snapshot carries no country field, so every channel "
        "resolves to Unknown_Country until the metadata API supplies one"
    )

    with psycopg.connect(url) as connection:
        with connection.cursor() as cur:
            cur.execute(
                "select distinct channel_id from enrichment.video_observation "
                "where channel_id is not null"
            )
            known = tuple(str(row[0]) for row in cur.fetchall())
        connection.rollback()

        print(f"resolved videos reference {len(known):,} distinct channels")

        resolver = HuggingFaceDisplayNameResolver(names, snapshot_digest=args.snapshot_digest)
        summary = run_channel_enrichment(
            connection,
            job_id=args.job_id,
            channel_ids=known,
            resolver=resolver,
            policy=policy,
            daily_quota_limit=DEFAULT_DAILY_QUOTA,
            max_batches=args.max_batches,
        )

        with connection.cursor() as cur:
            record_audit(
                cur,
                AuditEntry(
                    actor=args.actor,
                    action="enrich_channels_from_snapshot",
                    resource_class="enrichment.channel_observation",
                    outcome="success",
                    detail={
                        "jobId": args.job_id,
                        "observations": str(summary.observations_written),
                    },
                ),
            )
        connection.commit()

    print(summary.describe())
    return 0


def _channels_from_api(args: argparse.Namespace, url: str) -> int:
    """Resolve Declared_Country through the approved metadata API."""
    api_key = _resolve_api_key()
    if not api_key:
        print(
            f"error: {API_KEY_VAR} is not set. Declared_Country is available "
            f"only from the YouTube Data API; no other field may substitute "
            f"for it. Add {API_KEY_VAR}=... to .env.local and retry.",
            file=sys.stderr,
        )
        return 2

    policy = _default_policy()

    with psycopg.connect(url) as connection:
        with connection.cursor() as cur:
            cur.execute(
                "select distinct channel_id from enrichment.video_observation "
                "where channel_id is not null"
            )
            channel_ids = tuple(str(row[0]) for row in cur.fetchall())
        connection.rollback()

        print(f"{len(channel_ids):,} distinct channels to resolve")
        print(f"daily quota {DEFAULT_DAILY_QUOTA:,}, reserve {policy.quota_reserve:,}")

        resolver = YouTubeMetadataResolver(api_key)
        summary = run_channel_enrichment(
            connection,
            job_id=args.job_id,
            channel_ids=channel_ids,
            resolver=resolver,
            policy=policy,
            daily_quota_limit=DEFAULT_DAILY_QUOTA,
            max_batches=args.max_batches,
        )

        with connection.cursor() as cur:
            record_audit(
                cur,
                AuditEntry(
                    actor=args.actor,
                    action="enrich_channels_from_api",
                    resource_class="enrichment.channel_observation",
                    outcome="halted" if summary.halted else "success",
                    detail={
                        "jobId": args.job_id,
                        "observations": str(summary.observations_written),
                        "quotaUnits": str(summary.quota_units_used),
                    },
                ),
            )
        connection.commit()

    print(summary.describe())
    return 1 if summary.halted else 0


def _status(_args: argparse.Namespace, url: str) -> int:
    """Report enrichment progress and coverage."""
    with psycopg.connect(url) as connection, connection.cursor() as cur:
        cur.execute("select count(*) from provenance.normalized_occurrence")
        occurrences = cur.fetchone()
        cur.execute("select count(distinct video_id) from provenance.dataset_video_membership")
        videos = cur.fetchone()
        cur.execute(
            "select status, count(*) from enrichment.video_observation "
            "group by status order by status"
        )
        video_states = cur.fetchall()
        cur.execute(
            "select count(distinct channel_id) from enrichment.video_observation "
            "where channel_id is not null"
        )
        channels = cur.fetchone()
        cur.execute(
            "select count(*) filter (where declared_country is not null), "
            "       count(*) filter (where declared_country is null) "
            "from enrichment.channel_observation"
        )
        countries = cur.fetchone()
        cur.execute(
            "select state, count(*) from enrichment.work_item group by state order by state"
        )
        work_states = cur.fetchall()
        cur.execute(
            "select operation, requests, estimated_units from enrichment.quota_ledger "
            "where ledger_date = current_date"
        )
        quota = cur.fetchall()
        connection.rollback()

    print(f"occurrences:      {occurrences[0] if occurrences else 0:,}")
    print(f"distinct videos:  {videos[0] if videos else 0:,}")
    print(f"video states:     {dict(video_states)}")
    print(f"distinct channels:{channels[0] if channels else 0:,}")
    if countries:
        print(f"with country:     {countries[0]:,}")
        print(f"unknown country:  {countries[1]:,}")
    print(f"work items:       {dict(work_states)}")
    print(f"quota today:      {quota or 'none used'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cli_enrich", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    from_snapshot = sub.add_parser(
        "videos-from-snapshot",
        help="Resolve video->channel from a snapshot carrying channel_id",
    )
    from_snapshot.add_argument("--snapshot", required=True)
    from_snapshot.add_argument("--snapshot-digest", default="sha256:unknown")
    from_snapshot.add_argument("--video-column", default="video_id")
    from_snapshot.add_argument("--channel-column", default="channel_id")
    from_snapshot.add_argument("--job-id", default="videos-from-snapshot")
    from_snapshot.add_argument("--actor", required=True)
    from_snapshot.add_argument("--max-rows", type=int, default=None)
    from_snapshot.add_argument("--max-batches", type=int, default=None)
    from_snapshot.set_defaults(handler=_videos_from_snapshot)

    names = sub.add_parser(
        "channels-from-snapshot",
        help="Resolve channel display names from a snapshot (no country)",
    )
    names.add_argument("--snapshot", required=True)
    names.add_argument("--snapshot-digest", default="sha256:unknown")
    names.add_argument("--channel-column", default="channel_id")
    names.add_argument("--name-column", default="channel")
    names.add_argument("--job-id", default="channels-from-snapshot")
    names.add_argument("--actor", required=True)
    names.add_argument("--max-rows", type=int, default=None)
    names.add_argument("--max-batches", type=int, default=None)
    names.set_defaults(handler=_channels_from_snapshot)

    api = sub.add_parser("channels-from-api", help="Resolve Declared_Country via the metadata API")
    api.add_argument("--job-id", default="channels-from-api")
    api.add_argument("--actor", required=True)
    api.add_argument("--max-batches", type=int, default=None)
    api.set_defaults(handler=_channels_from_api)

    status = sub.add_parser("status", help="Report enrichment progress")
    status.set_defaults(handler=_status)

    args = parser.parse_args(argv)

    try:
        url = resolve_database_url()
    except DatabaseConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.command != "status":
        print(f"target: {redacted_target(url)}", file=sys.stderr)

    try:
        handler = args.handler
        return int(handler(args, url))
    except psycopg.Error as exc:
        print(f"database error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
