"""Compute the object-storage publication plan for a built release.

Usage:
    python -m creator_map_pipeline.cli_deliver --dir dist
    python -m creator_map_pipeline.cli_deliver --dir dist --release <id> --json plan.json
    python -m creator_map_pipeline.cli_deliver --dir dist --web apps/web/out

This reads the filesystem and nothing else. It deliberately takes no
database connection and no storage credential: the plan is a pure
function of built output, so it can be produced in CI, reviewed in a
pull request, and handed to a deploy step that holds the credential.
Requirement 15.2 wants the credential scoped to the workload that needs
it, and a build does not.

Requirement refs: 8.4, 8.7, 14.10, 15.2
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from creator_map_pipeline.release.delivery import (
    DeliveryObject,
    plan_delivery,
    plan_web_bundle,
    render_plan,
)


def _summarise(objects: tuple[DeliveryObject, ...], label: str) -> None:
    if not objects:
        print(f"{label}: none")
        return
    immutable = sum(1 for o in objects if o.is_immutable)
    total = sum(o.size for o in objects)
    print(f"{label}: {len(objects)} objects ({total:,} bytes), {immutable} immutable")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cli_deliver", description=__doc__)
    parser.add_argument("--dir", default="dist", help="Built release directory")
    parser.add_argument(
        "--release",
        default=None,
        help="Release id to publish; defaults to the one the local pointer names.",
    )
    parser.add_argument(
        "--web",
        default=None,
        help="Static web export directory to include in the plan.",
    )
    parser.add_argument("--json", default=None, help="Write the machine-readable plan here.")
    args = parser.parse_args(argv)

    try:
        plan = plan_delivery(Path(args.dir), release_id=args.release)
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(f"release: {plan.release_id}")
    _summarise(plan.artifacts, "artifacts")
    _summarise(plan.pointers, "pointers ")

    if args.web:
        try:
            web = plan_web_bundle(Path(args.web))
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        _summarise(web, "web      ")

    print()
    print("publish order: every artifact first, then the pointer.")
    print("  An artifact set with no pointer is unused; a pointer with")
    print("  missing artifacts is a broken release (Requirement 8.7).")

    if args.json:
        target = Path(args.json)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render_plan(plan), encoding="utf-8")
        print(f"\nplan written to {target}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
