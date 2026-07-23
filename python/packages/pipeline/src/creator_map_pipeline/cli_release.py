"""Curator commands for release validation, activation, and rollback.

Usage:
    python -m creator_map_pipeline.cli_release validate --dir dist --actor nandi
    python -m creator_map_pipeline.cli_release activate --dir dist --actor nandi
    python -m creator_map_pipeline.cli_release status
    python -m creator_map_pipeline.cli_release rollback --to <release> --actor nandi

Activation is deliberately a separate command from build and validate: a
release becomes active only when an operator asks for it, never as a side
effect of producing artifacts.

Requirement refs: 8.1-8.12, 14.9-14.11, 15.16-15.22
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import psycopg

from creator_map_pipeline.aggregate.artifacts import GeneratedArtifact
from creator_map_pipeline.database import (
    DatabaseConfigError,
    redacted_target,
    resolve_database_url,
)
from creator_map_pipeline.release.gates import ReleaseCandidate
from creator_map_pipeline.release.manager import (
    ActivationError,
    ReleaseManager,
    candidate_from_artifacts,
)


def _load_candidate(
    directory: Path,
    *,
    signoff_actor: str | None,
    scan: dict[str, object] | None,
) -> ReleaseCandidate:
    """Rebuild a candidate from artifacts already written to disk."""
    releases = directory / "releases"
    if not releases.is_dir():
        msg = f"no releases directory under {directory}"
        raise ActivationError(msg)

    release_dirs = sorted(p for p in releases.iterdir() if p.is_dir())
    if not release_dirs:
        msg = f"no release found under {releases}"
        raise ActivationError(msg)
    release_dir = release_dirs[-1]
    release_id = release_dir.name

    artifacts: list[GeneratedArtifact] = []
    for path in sorted(release_dir.rglob("*.json")):
        relative = path.relative_to(directory).as_posix()
        payload = json.loads(path.read_text(encoding="utf-8"))
        artifacts.append(GeneratedArtifact(path=relative, payload=payload).finalize())

    return candidate_from_artifacts(
        release_id,
        artifacts,
        signoff_actor=signoff_actor,
        vulnerability_scan=scan,
    )


def _scan_argument(args: argparse.Namespace) -> dict[str, object] | None:
    """Interpret the dependency-scan flags.

    Absent by default: Requirement 15.15 treats an unrecorded scan as
    incomplete, and a default of "clean" would quietly invert that.
    """
    if not args.scan_completed:
        return None
    return {
        "completed": True,
        "blockingFindings": args.scan_blocking_findings,
    }


def _validate(args: argparse.Namespace, url: str) -> int:
    candidate = _load_candidate(
        Path(args.dir), signoff_actor=args.signoff, scan=_scan_argument(args)
    )

    with psycopg.connect(url) as connection:
        manager = ReleaseManager(connection, storage_root=Path(args.dir), actor=args.actor)
        manager.record_release(
            candidate,
            policy_id=args.policy_id,
            policy_version=str(candidate.manifest.get("disclosurePolicyVersion", "unknown")),
        )
        report = manager.validate(candidate)

    print(report.describe())
    return 0 if report.passed else 1


def _activate(args: argparse.Namespace, url: str) -> int:
    candidate = _load_candidate(
        Path(args.dir), signoff_actor=args.signoff, scan=_scan_argument(args)
    )

    with psycopg.connect(url) as connection:
        manager = ReleaseManager(connection, storage_root=Path(args.dir), actor=args.actor)

        manager.record_release(
            candidate,
            policy_id=args.policy_id,
            policy_version=str(candidate.manifest.get("disclosurePolicyVersion", "unknown")),
        )
        report = manager.validate(candidate)

        if not report.passed:
            # Requirement 8.3: the previous release keeps serving.
            manager.reject(candidate.release_id, [r.name for r in report.blocking])
            print(report.describe(), file=sys.stderr)
            print(
                "\nactivation refused; the previously active release is unchanged",
                file=sys.stderr,
            )
            return 1

        staged = manager.stage(candidate)
        problems = manager.verify_staged(candidate.release_id, candidate.manifest)
        if problems:
            manager.reject(candidate.release_id, problems)
            for problem in problems:
                print(f"  {problem}", file=sys.stderr)
            return 1

        manager.mark_verified(candidate.release_id)
        previous = manager.activate(candidate.release_id)
        manager.write_pointer(candidate.release_id, staged.manifest_digest)

    print(report.describe())
    print(f"\nactivated:  {candidate.release_id}")
    print(f"previous:   {previous or 'none'}")
    print(f"artifacts:  {staged.artifact_count} ({staged.total_bytes:,} bytes)")
    return 0


def _rollback(args: argparse.Namespace, url: str) -> int:
    with psycopg.connect(url) as connection:
        manager = ReleaseManager(connection, storage_root=Path(args.dir), actor=args.actor)
        previous = manager.rollback(args.to)

    print(f"rolled back to {args.to} (was {previous or 'none'})")
    return 0


def _status(args: argparse.Namespace, url: str) -> int:
    with psycopg.connect(url) as connection, connection.cursor() as cur:
        cur.execute(
            "select release_id, activated_at, activated_by "
            "from governance.active_release_pointer where pointer_id = true"
        )
        active = cur.fetchone()
        cur.execute(
            "select release_id, state, generated_at from governance.release "
            "order by generated_at desc limit 10"
        )
        recent = cur.fetchall()
        connection.rollback()

    if active:
        print(f"active:   {active[0]}  (by {active[2]} at {active[1]})")
    else:
        print("active:   none")

    print("\nrecent releases:")
    for release_id, state, generated in recent:
        marker = "*" if active and release_id == active[0] else " "
        print(f" {marker} {release_id}  {state}  {generated}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cli_release", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p: argparse.ArgumentParser, *, needs_actor: bool = True) -> None:
        p.add_argument("--dir", default="dist")
        if needs_actor:
            p.add_argument("--actor", required=True)
        p.add_argument("--policy-id", default="development-disclosure")
        p.add_argument(
            "--signoff",
            default=None,
            help="Curator approving dataset citations and terms review.",
        )
        p.add_argument(
            "--scan-completed",
            action="store_true",
            help="Record that the dependency scan ran to completion.",
        )
        p.add_argument("--scan-blocking-findings", type=int, default=0)

    validate = sub.add_parser("validate", help="Run every gate without activating")
    add_common(validate)
    validate.set_defaults(handler=_validate)

    activate = sub.add_parser("activate", help="Validate, stage, and activate")
    add_common(activate)
    activate.set_defaults(handler=_activate)

    rollback = sub.add_parser("rollback", help="Restore a prior verified release")
    rollback.add_argument("--dir", default="dist")
    rollback.add_argument("--actor", required=True)
    rollback.add_argument("--to", required=True)
    rollback.set_defaults(handler=_rollback)

    status = sub.add_parser("status", help="Show the active and recent releases")
    status.add_argument("--dir", default="dist")
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
    except ActivationError as exc:
        print(f"release error: {exc}", file=sys.stderr)
        return 1
    except psycopg.Error as exc:
        print(f"database error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
