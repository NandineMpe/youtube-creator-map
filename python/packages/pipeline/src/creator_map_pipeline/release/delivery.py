"""Delivery plan for publishing a release to object storage behind a CDN.

Requirement 14.10 splits published files into two classes with opposite
caching needs, and getting the split wrong breaks either correctness or
performance:

  Immutable artifacts live under ``releases/<release-id>/``. The release
  ID is in the path, so a given URL's bytes never change. These can be
  cached forever.

  The active-release pointer is one small file at a stable URL whose
  contents change on every activation. It must never be cached for long,
  or clients keep loading the previous release after a rollback.

Requirement 8.7 says every observable instant must show one complete
release. That is what forces the publication *order*: artifacts first,
pointer last. A pointer naming a release whose shards have not landed
would leave clients fetching 404s with no complete release to fall back
to. Publishing artifacts first is safe in a way the reverse is not,
because an unreferenced artifact set is merely unused.

This module computes the plan and does not perform uploads. The upload
credential belongs to a deploy workload, not to a build, and keeping the
two apart means the plan can be tested exhaustively without one.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

#: One year, the maximum any CDN honours in practice. Safe only because
#: the release ID appears in the path, so these bytes are addressed by
#: content lineage and a change produces a different URL.
IMMUTABLE_MAX_AGE = 31_536_000

#: The pointer is re-read to discover new releases. This bounds how long
#: a rollback takes to reach clients that already have it cached, so it
#: is deliberately short rather than zero: `no-store` would defeat
#: conditional requests and make every page load pay a full round trip.
POINTER_MAX_AGE = 60

#: Files whose extension is not in this map are refused rather than
#: guessed at. A wrong Content-Type on a JSON artifact can cause a
#: browser to decline to parse it, which surfaces as unexplained data
#: loss rather than an error.
CONTENT_TYPES = {
    ".json": "application/json",
    ".geojson": "application/geo+json",
    ".js": "text/javascript",
    ".css": "text/css",
    ".html": "text/html",
    ".svg": "image/svg+xml",
    ".woff2": "font/woff2",
    ".ico": "image/x-icon",
    ".txt": "text/plain",
    ".webmanifest": "application/manifest+json",
    ".map": "application/json",
    ".png": "image/png",
}


@dataclass(frozen=True, slots=True)
class DeliveryObject:
    """One file to upload, with the headers it must be served under."""

    #: Path relative to the storage bucket root. Forward slashes always,
    #: including when the plan is computed on Windows — these become URL
    #: paths, and a backslash in one is a broken link, not a separator.
    key: str
    source: Path
    content_type: str
    cache_control: str
    #: Hex SHA-256 of the bytes. Lets a deploy step verify what it
    #: uploaded matches what was planned, and lets re-runs skip
    #: unchanged objects without re-reading them from storage.
    digest: str
    size: int

    @property
    def is_immutable(self) -> bool:
        return "immutable" in self.cache_control


@dataclass(frozen=True, slots=True)
class DeliveryPlan:
    """An ordered publication plan for one release.

    The two tuples are separate because the order between them is
    load-bearing (Requirement 8.7), and a single list would let a caller
    iterate it in any order without noticing.
    """

    release_id: str
    #: Uploaded first. Immutable, safe to publish before activation.
    artifacts: tuple[DeliveryObject, ...]
    #: Uploaded last, and only after every artifact has landed.
    pointers: tuple[DeliveryObject, ...]

    @property
    def total_bytes(self) -> int:
        return sum(o.size for o in self.artifacts) + sum(o.size for o in self.pointers)

    def ordered(self) -> Iterator[DeliveryObject]:
        """Every object in the order it must be published."""
        yield from self.artifacts
        yield from self.pointers


def content_type_for(path: Path) -> str:
    """The Content-Type for one file, or a refusal.

    Raising beats defaulting to ``application/octet-stream``: an artifact
    served as a binary blob fails in the browser at load time, far from
    the build that introduced it.
    """
    suffix = path.suffix.lower()
    if suffix not in CONTENT_TYPES:
        msg = f"no Content-Type registered for {suffix!r} ({path.name}); refusing to guess"
        raise ValueError(msg)
    return CONTENT_TYPES[suffix]


def _digest_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        # Chunked so a large boundary file does not have to be resident.
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _object_for(path: Path, key: str, cache_control: str) -> DeliveryObject:
    return DeliveryObject(
        key=key,
        source=path,
        content_type=content_type_for(path),
        cache_control=cache_control,
        digest=_digest_of(path),
        size=path.stat().st_size,
    )


def plan_delivery(dist: Path, *, release_id: str | None = None) -> DeliveryPlan:
    """Compute the publication plan for a built release directory.

    ``release_id`` defaults to whatever the local pointer names. Passing
    it explicitly lets a deploy publish a release that was built but not
    activated, which is what Requirement 8.4 asks for: stage the whole
    artifact set, then change the pointer.
    """
    pointer_path = dist / "active-release.json"
    if release_id is None:
        if pointer_path.is_file():
            release_id = json.loads(pointer_path.read_text(encoding="utf-8"))["releaseId"]
        else:
            # A release built but never activated has no pointer, and
            # that is the normal state of the thing this function most
            # needs to plan: Requirement 8.4 stages the artifact set
            # *before* the pointer moves. Falling back to the newest
            # release on disk means an unactivated build is still
            # publishable; refusing would make staging impossible
            # without first activating, which is backwards.
            releases = dist / "releases"
            built = (
                sorted(p.name for p in releases.iterdir() if p.is_dir())
                if releases.is_dir()
                else []
            )
            if not built:
                msg = (
                    f"no release id given, no pointer at {pointer_path}, "
                    f"and no release under {releases}"
                )
                raise FileNotFoundError(msg)
            release_id = built[-1]

    release_dir = dist / "releases" / str(release_id)
    if not release_dir.is_dir():
        msg = f"release {release_id} not found at {release_dir}"
        raise FileNotFoundError(msg)

    artifacts = tuple(
        _object_for(
            path,
            key=f"releases/{release_id}/{path.relative_to(release_dir).as_posix()}",
            cache_control=f"public, max-age={IMMUTABLE_MAX_AGE}, immutable",
        )
        # Sorted so the plan is deterministic: two builds of the same
        # release produce identical plans, which makes a diff meaningful.
        for path in sorted(release_dir.rglob("*"))
        if path.is_file()
    )
    if not artifacts:
        msg = f"release {release_id} contains no files"
        raise ValueError(msg)

    pointers: tuple[DeliveryObject, ...] = ()
    if pointer_path.is_file():
        pointers = (
            _object_for(
                pointer_path,
                key="active-release.json",
                cache_control=(f"public, max-age={POINTER_MAX_AGE}, must-revalidate"),
            ),
        )

    return DeliveryPlan(release_id=str(release_id), artifacts=artifacts, pointers=pointers)


def plan_web_bundle(
    web_out: Path, *, prefix: str = "", include_html: bool = True
) -> tuple[DeliveryObject, ...]:
    """Plan the static web bundle.

    Next.js emits hashed filenames under ``_next/static``, so those are
    immutable by the same argument as release artifacts. Everything else
    — HTML entry points above all — is mutable at a stable URL and must
    revalidate, or a deploy leaves clients on the previous build's HTML
    pointing at chunks that no longer exist.

    ``include_html=False`` omits the ``.html`` entry points. Some object
    stores — Supabase Storage among them — refuse to serve user-uploaded
    HTML as ``text/html`` and force ``text/plain`` as a stored-XSS
    defence, which makes a browser download the page instead of rendering
    it. On those backends the HTML is hosted separately and only the
    data and hashed assets go to the store, which they serve correctly.
    """
    objects: list[DeliveryObject] = []
    for path in sorted(web_out.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(web_out).as_posix()
        if path.suffix.lower() not in CONTENT_TYPES:
            # Build output contains files the CDN never serves (e.g.
            # `.nft.json` trace files). Skipping is right here, unlike in
            # a release, where every file is meant to be public.
            continue
        if not include_html and path.suffix.lower() == ".html":
            continue
        hashed = relative.startswith("_next/static/")
        cache_control = (
            f"public, max-age={IMMUTABLE_MAX_AGE}, immutable"
            if hashed
            else "public, max-age=0, must-revalidate"
        )
        objects.append(_object_for(path, key=f"{prefix}{relative}", cache_control=cache_control))
    return tuple(objects)


def render_plan(plan: DeliveryPlan) -> str:
    """A machine-readable plan a deploy step can consume."""
    return json.dumps(
        {
            "releaseId": plan.release_id,
            "publishOrder": ["artifacts", "pointers"],
            "artifacts": [
                {
                    "key": o.key,
                    "contentType": o.content_type,
                    "cacheControl": o.cache_control,
                    "sha256": o.digest,
                    "bytes": o.size,
                }
                for o in plan.artifacts
            ],
            "pointers": [
                {
                    "key": o.key,
                    "contentType": o.content_type,
                    "cacheControl": o.cache_control,
                    "sha256": o.digest,
                    "bytes": o.size,
                }
                for o in plan.pointers
            ],
        },
        indent=2,
        sort_keys=True,
    )
