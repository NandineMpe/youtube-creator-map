"""Publish a built release to Supabase Storage.

Usage:
    python -m creator_map_pipeline.cli_publish --dir dist
    python -m creator_map_pipeline.cli_publish --dir dist --web apps/web/out
    python -m creator_map_pipeline.cli_publish --dir dist --stage-only
    python -m creator_map_pipeline.cli_publish --dir dist --verify-only

Credentials come from the environment, never the command line: an
argument lands in shell history and in the process list, where anything
on the machine can read it (Requirement 15.1).

    SUPABASE_URL          https://<project>.supabase.co
    SUPABASE_SERVICE_KEY  the service role key (never the publishable one)

The publishable key cannot write to storage — that is the point of it —
so a publish attempt with it fails on a row-level security policy rather
than doing anything dangerous.

Requirement refs: 8.4, 8.7, 8.8, 14.10, 15.1, 15.2
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from creator_map_pipeline.release.delivery import (
    DeliveryPlan,
    plan_delivery,
    plan_web_bundle,
)
from creator_map_pipeline.release.publisher import (
    SupabaseStorage,
    publish,
    verify_published,
)


def _resolve_credentials() -> tuple[str, str]:
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()

    missing = [
        name for name, value in (("SUPABASE_URL", url), ("SUPABASE_SERVICE_KEY", key)) if not value
    ]
    if missing:
        msg = f"missing environment variable(s): {', '.join(missing)}"
        raise RuntimeError(msg)

    # A publishable key here is a mistake worth catching before the
    # upload fails halfway through with an opaque RLS error.
    if key.startswith("sb_publishable_"):
        msg = (
            "SUPABASE_SERVICE_KEY holds a publishable key, which cannot write "
            "to storage. Use the service role key from Project Settings > API."
        )
        raise RuntimeError(msg)

    return url, key


def _progress(done: int, total: int, key: str) -> None:
    # Rewritten in place: 600 lines of "uploaded x" buries the summary
    # that actually matters.
    trimmed = key if len(key) <= 58 else f"...{key[-55:]}"
    print(f"\r  [{done:>4}/{total}] {trimmed:<58}", end="", file=sys.stderr)
    if done == total:
        print(file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cli_publish", description=__doc__)
    parser.add_argument("--dir", default="dist", help="Built release directory")
    parser.add_argument("--release", default=None, help="Release id; defaults to the pointer's")
    parser.add_argument("--web", default=None, help="Static web export to publish alongside")
    parser.add_argument("--bucket", default="creator-map")
    parser.add_argument(
        "--stage-only",
        action="store_true",
        help="Upload artifacts without moving the active-release pointer.",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Check what is already published against the plan; upload nothing.",
    )
    args = parser.parse_args(argv)

    try:
        url, key = _resolve_credentials()
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        plan = plan_delivery(Path(args.dir), release_id=args.release)
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.web:
        # The web bundle rides along as extra artifacts. It is not part
        # of the release manifest — the app is code, the release is data
        # — but both have to be on the CDN for the site to work.
        web = plan_web_bundle(Path(args.web))
        plan = DeliveryPlan(
            release_id=plan.release_id,
            artifacts=(*plan.artifacts, *web),
            pointers=plan.pointers,
        )

    storage = SupabaseStorage(url, key, bucket=args.bucket)
    print(f"project: {url}")
    print(f"bucket:  {args.bucket}")
    print(f"release: {plan.release_id}")
    print(f"objects: {len(plan.artifacts)} artifacts + {len(plan.pointers)} pointer(s)")
    print(f"bytes:   {plan.total_bytes:,}")
    print()

    if args.verify_only:
        problems = verify_published(plan, storage)
        if problems:
            print(f"FAILED: {len(problems)} problem(s)", file=sys.stderr)
            for problem in problems[:20]:
                print(f"  {problem}", file=sys.stderr)
            return 1
        print(f"verified {len(plan.artifacts) + len(plan.pointers)} published object(s)")
        return 0

    try:
        report = publish(
            plan,
            storage,
            move_pointer=not args.stage_only,
            on_progress=_progress,
        )
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(report.describe())

    if report.failed:
        return 1

    # Requirement 8.5: confirm the bytes are readable at their public
    # URLs under the planned headers, rather than trusting that a 200 on
    # upload means the object is correctly served.
    print()
    print("verifying published objects...")
    problems = verify_published(plan, storage)
    if problems:
        print(f"FAILED: {len(problems)} problem(s) after upload", file=sys.stderr)
        for problem in problems[:20]:
            print(f"  {problem}", file=sys.stderr)
        return 1

    print("all published objects verified")
    print()
    print(f"pointer: {storage.public_url('active-release.json')}")
    if args.web:
        print(f"site:    {storage.public_url('index.html')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
