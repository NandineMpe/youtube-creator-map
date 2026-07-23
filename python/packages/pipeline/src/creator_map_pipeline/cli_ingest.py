"""Curator commands for governed ingestion.

Usage:
    python -m creator_map_pipeline.cli_ingest register --contract contract.json
    python -m creator_map_pipeline.cli_ingest ingest --contract c.json --snapshot s.csv
    python -m creator_map_pipeline.cli_ingest list

Every mutating command requires an actor identity and produces an audit
record. No command downloads media: ingestion reads local snapshot metadata
only (Requirement 15.12).

Requirement refs: 1.1-1.9, 2.7-2.16, 15.12, 15.16-15.21
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import psycopg
from creator_map_schemas import DatasetContract

from creator_map_pipeline.database import (
    DatabaseConfigError,
    redacted_target,
    resolve_database_url,
)
from creator_map_pipeline.extraction import (
    RepeatedClipAdapter,
    SchemaDriftError,
    SourceAdapter,
    VideoIdColumnAdapter,
    extract_records,
)
from creator_map_pipeline.parquet_source import read_parquet_records
from creator_map_pipeline.registry import DatasetRegistry
from creator_map_pipeline.repositories import (
    AuditEntry,
    PostgresDatasetRepository,
    commit_ingestion,
    record_audit,
)
from creator_map_pipeline.snapshot import (
    SnapshotLimits,
    compute_digest,
    read_csv_records,
    read_jsonl_records,
    validate_snapshot,
)

_ADAPTERS: dict[str, type[SourceAdapter]] = {
    "video-id-column": VideoIdColumnAdapter,
    "repeated-clip": RepeatedClipAdapter,
}


def _load_contract(path: Path) -> DatasetContract:
    return DatasetContract.model_validate_json(path.read_bytes())


def _register(args: argparse.Namespace, url: str) -> int:
    contract = _load_contract(Path(args.contract))

    with psycopg.connect(url) as connection, connection.cursor() as cursor:
        registry = DatasetRegistry(
            PostgresDatasetRepository(cursor, acquisition_path=args.acquisition_path)
        )
        result = registry.register(contract)

        record_audit(
            cursor,
            AuditEntry(
                actor=args.actor,
                action="register_dataset_contract",
                resource_class="provenance.dataset_contract",
                outcome="success" if result.accepted else "denied",
                detail={
                    "datasetId": contract.id,
                    "datasetVersion": contract.version,
                    "outcome": result.outcome.value,
                },
            ),
        )

        if not result.accepted:
            connection.rollback()
            print(f"rejected: {result.outcome.value}", file=sys.stderr)
            for reason in result.reasons:
                print(f"  - {reason}", file=sys.stderr)
            return 1

        connection.commit()

    print(f"{result.outcome.value}: {result.key}")
    return 0


def _ingest(args: argparse.Namespace, url: str) -> int:
    contract = _load_contract(Path(args.contract))
    snapshot_path = Path(args.snapshot)

    # Requirement 1.6: the digest gate runs before anything is read.
    report = validate_snapshot(snapshot_path, contract)
    if not report.ok:
        print("snapshot validation failed:", file=sys.stderr)
        for reason in report.reasons:
            print(f"  - {reason}", file=sys.stderr)
        if report.actual_digest:
            print(f"  expected {report.expected_digest}", file=sys.stderr)
            print(f"  actual   {report.actual_digest}", file=sys.stderr)
        return 1

    limits = SnapshotLimits()
    adapter = _ADAPTERS[args.adapter]()

    if snapshot_path.suffix == ".parquet":
        # Project only the columns the adapter declares. YouTube-Commons
        # carries full transcripts; reading them would pull licensed content
        # into the pipeline for no purpose (Requirement 15.12).
        outcome = read_parquet_records(
            snapshot_path,
            columns=tuple(sorted(adapter.required_fields())),
            optional_columns=tuple(sorted(adapter.optional_fields())),
            limits=limits,
            max_rows=args.max_rows,
        )
    else:
        reader = read_jsonl_records if snapshot_path.suffix == ".jsonl" else read_csv_records
        with snapshot_path.open("r", encoding="utf-8", newline="") as handle:
            outcome = reader(handle, source_name=snapshot_path.name, limits=limits)

    try:
        extraction = extract_records(
            outcome.records,
            adapter=adapter,
            contract=contract,
            prior_quarantined=outcome.quarantined,
        )
    except SchemaDriftError as exc:
        # Requirement 2.16: fail closed, publish nothing, leave prior
        # outputs untouched.
        print(f"schema drift: {exc}", file=sys.stderr)
        print(
            "no occurrences were published; a versioned adapter update is required", file=sys.stderr
        )
        return 1

    if extraction.report is None:  # pragma: no cover - defensive
        print("extraction produced no report", file=sys.stderr)
        return 1

    if args.dry_run:
        print("dry run — nothing persisted")
        print(f"  examined:    {extraction.report.records_examined}")
        print(f"  accepted:    {extraction.report.records_accepted}")
        print(f"  rejected:    {extraction.report.records_rejected}")
        print(f"  occurrences: {extraction.report.occurrences_emitted}")
        print(f"  expansion:   {extraction.report.expansion_count}")
        return 0

    with psycopg.connect(url) as connection:
        summary = commit_ingestion(
            connection,
            contract=contract,
            occurrences=extraction.occurrences,
            rejects=extraction.rejects,
            report=extraction.report,
            storage_uri=str(snapshot_path.resolve().as_uri()),
            byte_size=snapshot_path.stat().st_size,
            actor=args.actor,
        )

    print(summary.describe())
    return 0


def _list(_args: argparse.Namespace, url: str) -> int:
    with psycopg.connect(url) as connection, connection.cursor() as cursor:
        registry = DatasetRegistry(PostgresDatasetRepository(cursor))
        entries = registry.methodology_entries()
        connection.rollback()

    print(json.dumps(list(entries), indent=2, sort_keys=True))
    return 0


def _digest(args: argparse.Namespace, _url: str) -> int:
    """Print a snapshot's content address, for authoring a contract."""
    print(compute_digest(Path(args.snapshot)))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cli_ingest", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    register = sub.add_parser("register", help="Register a dataset contract")
    register.add_argument("--contract", required=True)
    register.add_argument("--actor", required=True)
    register.add_argument("--acquisition-path", default="documented")
    register.set_defaults(handler=_register)

    ingest = sub.add_parser("ingest", help="Extract and persist a snapshot")
    ingest.add_argument("--contract", required=True)
    ingest.add_argument("--snapshot", required=True)
    ingest.add_argument("--adapter", choices=sorted(_ADAPTERS), default="video-id-column")
    ingest.add_argument("--actor", required=True)
    ingest.add_argument("--dry-run", action="store_true")
    ingest.add_argument(
        "--max-rows",
        type=int,
        default=None,
        help="Bound a development run over a large shard (Parquet only)",
    )
    ingest.set_defaults(handler=_ingest)

    listing = sub.add_parser("list", help="List approved dataset citations")
    listing.set_defaults(handler=_list)

    digest = sub.add_parser("digest", help="Compute a snapshot content address")
    digest.add_argument("--snapshot", required=True)
    digest.set_defaults(handler=_digest)

    args = parser.parse_args(argv)

    # The digest command is local-only and needs no database.
    if args.command == "digest":
        return _digest(args, "")

    try:
        url = resolve_database_url()
    except DatabaseConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.command != "list":
        print(f"target: {redacted_target(url)}", file=sys.stderr)

    try:
        handler = args.handler
        return int(handler(args, url))
    except FileNotFoundError as exc:
        print(f"file not found: {exc.filename}", file=sys.stderr)
        return 1
    except psycopg.Error as exc:
        print(f"database error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
