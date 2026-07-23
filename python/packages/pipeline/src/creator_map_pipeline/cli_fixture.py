"""Write a deterministic synthetic release to disk.

Usage:
    python -m creator_map_pipeline.cli_fixture --out fixtures/dist

No database, no network, no credentials. The output is a complete valid
release that the TypeScript loader can read and the release gates accept,
so the cross-stack contract can be checked on a clean checkout — which is
the case where the real-artifact test skips and therefore proves nothing.

Requirement refs: 8.1, 8.8
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from creator_map_pipeline.aggregate.artifacts import canonical_bytes
from creator_map_pipeline.aggregate.fixtures import DEFAULT_SEED, build_fixture_release


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cli_fixture", description=__doc__)
    parser.add_argument("--out", default="fixtures/dist")
    parser.add_argument("--seed", default=DEFAULT_SEED)
    parser.add_argument("--release-id", default="2026-01-01T00-00-00Z")
    parser.add_argument("--page-size", type=int, default=50)
    parser.add_argument(
        "--activate",
        action="store_true",
        help="Also write the active-release pointer, so loaders find it.",
    )
    args = parser.parse_args(argv)

    out = Path(args.out)
    fixture = build_fixture_release(
        seed=args.seed, release_id=args.release_id, page_size=args.page_size
    )

    # Regenerating must not leave stale artifacts from a previous shape
    # behind, since a loader would happily read one and the digests
    # would still verify.
    release_dir = out / "releases" / fixture.release_id
    if release_dir.exists():
        shutil.rmtree(release_dir)

    for artifact in fixture.artifacts:
        target = out / artifact.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(artifact.content)

    if args.activate:
        # The pointer carries the manifest's path and digest, not just an
        # id: that is what lets a loader verify the whole chain from one
        # fetch without trusting the directory listing.
        manifest = fixture.by_path("manifest.json")
        (out / "active-release.json").write_bytes(
            canonical_bytes(
                {
                    "schemaVersion": "1.0.0",
                    "releaseId": fixture.release_id,
                    "manifestPath": manifest.path,
                    "manifestDigest": manifest.digest,
                }
            )
        )

    print(f"release:   {fixture.release_id}")
    print(f"artifacts: {len(fixture.artifacts)}")
    print(f"written to {out}")
    print("\nSynthetic data. Nothing here describes a real creator or channel.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:] or None))
