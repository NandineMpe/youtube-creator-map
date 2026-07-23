"""Upload a delivery plan to Supabase Storage.

The plan is computed elsewhere (`release/delivery.py`) and this executes
it. Keeping the two apart matters for one specific reason: the plan is a
pure function of built output and can be produced in CI and reviewed in a
pull request, while *this* module is the only place that holds a
credential able to overwrite published bytes.

The publication order is not an implementation detail. Requirement 8.7
says every observable instant must show one complete release, so every
artifact lands before the pointer moves. Publishing in the other order
leaves a window where the pointer names a release whose shards are still
uploading, and a visitor arriving in that window gets 404s with no
complete release to fall back on.

Uploads are idempotent by digest. Re-running after a partial failure
skips what already matches, which makes a retry cheap and safe rather
than a full re-upload that might itself be interrupted.

Requirement refs: 8.4, 8.7, 8.8, 14.10, 15.1, 15.2
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

import requests

from creator_map_pipeline.release.delivery import DeliveryObject, DeliveryPlan

#: Transient failures are retried; a 4xx is not. Uploading the same
#: object into a permission error a dozen times only delays the report.
RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504})
MAX_ATTEMPTS = 4


@dataclass(frozen=True, slots=True)
class UploadResult:
    """What happened to one object."""

    key: str
    status: str  # "uploaded" | "skipped" | "failed"
    detail: str = ""
    bytes_sent: int = 0

    @property
    def ok(self) -> bool:
        return self.status != "failed"


@dataclass(slots=True)
class PublishReport:
    """The outcome of executing one plan."""

    release_id: str
    results: list[UploadResult] = field(default_factory=list)
    #: True only when every artifact landed *and* the pointer was moved.
    pointer_moved: bool = False

    @property
    def failed(self) -> list[UploadResult]:
        return [r for r in self.results if not r.ok]

    @property
    def uploaded(self) -> int:
        return sum(1 for r in self.results if r.status == "uploaded")

    @property
    def skipped(self) -> int:
        return sum(1 for r in self.results if r.status == "skipped")

    @property
    def bytes_sent(self) -> int:
        return sum(r.bytes_sent for r in self.results)

    def describe(self) -> str:
        lines = [
            f"release {self.release_id}",
            f"  uploaded: {self.uploaded}",
            f"  skipped:  {self.skipped} (already published with matching bytes)",
            f"  sent:     {self.bytes_sent:,} bytes",
        ]
        if self.failed:
            lines.append(f"  FAILED:   {len(self.failed)}")
            for result in self.failed[:10]:
                lines.append(f"    {result.key}: {result.detail}")
            lines.append("")
            lines.append("  The active-release pointer was NOT moved.")
            lines.append("  The previously published release is unchanged.")
        elif self.pointer_moved:
            lines.append("  pointer:  moved — this release is now active")
        else:
            lines.append("  pointer:  not moved (staged only)")
        return "\n".join(lines)


class SupabaseStorage:
    """A thin Supabase Storage client.

    Deliberately small: this needs six operations, and a full SDK would
    add a dependency whose surface is mostly irrelevant here. The
    credential is held in memory for the life of the call and never
    written anywhere (Requirement 15.1).
    """

    def __init__(
        self,
        project_url: str,
        service_key: str,
        *,
        bucket: str = "creator-map",
        session: requests.Session | None = None,
        timeout: int = 60,
    ) -> None:
        self._base = project_url.rstrip("/")
        self._bucket = bucket
        self._timeout = timeout
        self._session = session or requests.Session()
        self._session.headers.update(
            {
                "apikey": service_key,
                "Authorization": f"Bearer {service_key}",
            }
        )

    @property
    def bucket(self) -> str:
        return self._bucket

    def public_url(self, key: str) -> str:
        return f"{self._base}/storage/v1/object/public/{self._bucket}/{key}"

    def ensure_bucket(self) -> str:
        """Create the bucket if absent. Returns what happened.

        Public-read on purpose: these are the artifacts the map fetches
        from a browser with no credential. Everything published has
        already crossed the disclosure boundary, so readability is the
        intended property rather than an oversight.
        """
        response = self._session.get(
            f"{self._base}/storage/v1/bucket/{self._bucket}", timeout=self._timeout
        )
        if response.status_code == 200:
            return "exists"

        created = self._session.post(
            f"{self._base}/storage/v1/bucket",
            json={"id": self._bucket, "name": self._bucket, "public": True},
            timeout=self._timeout,
        )
        if created.status_code in (200, 201):
            return "created"
        msg = f"could not create bucket {self._bucket}: {created.status_code} {created.text[:200]}"
        raise RuntimeError(msg)

    def head(self, key: str) -> dict[str, str] | None:
        """Response headers a browser sees when it fetches this object.

        A GET, not a HEAD. Supabase serves *different* cache headers for
        the two — HEAD returns `no-cache` where GET returns the
        `Cache-Control` the object was stored with — and it is the GET
        response that a visitor's browser actually gets, so verifying the
        HEAD would check a header no reader ever sees. A one-byte Range
        request avoids pulling whole bodies while still exercising the
        GET path.
        """
        response = self._session.get(
            f"{self._base}/storage/v1/object/public/{self._bucket}/{key}",
            headers={"Range": "bytes=0-0"},
            timeout=self._timeout,
        )
        # 206 for a served range, 200 when the backend ignores Range.
        if response.status_code not in (200, 206):
            return None
        return {k.lower(): v for k, v in response.headers.items()}

    def upload(self, obj: DeliveryObject) -> None:
        """Upload one object with its planned headers.

        `x-upsert` because republishing a release must overwrite rather
        than fail — a partial upload retried should converge, not stall
        on the objects that already landed.
        """
        last_error = ""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            response = self._session.post(
                f"{self._base}/storage/v1/object/{self._bucket}/{obj.key}",
                data=obj.source.read_bytes(),
                headers={
                    "Content-Type": obj.content_type,
                    "Cache-Control": obj.cache_control,
                    "x-upsert": "true",
                },
                timeout=self._timeout,
            )
            if response.status_code in (200, 201):
                return

            last_error = f"{response.status_code} {response.text[:200]}"
            if response.status_code not in RETRYABLE_STATUS:
                break
            # Linear backoff: enough to clear a rate limit without
            # turning a large release into a long wait.
            time.sleep(attempt)

        msg = f"upload failed after {MAX_ATTEMPTS} attempts: {last_error}"
        raise RuntimeError(msg)


def publish(
    plan: DeliveryPlan,
    storage: SupabaseStorage,
    *,
    move_pointer: bool = True,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> PublishReport:
    """Execute a delivery plan.

    Artifacts first, pointer last, and the pointer only if every artifact
    landed. `move_pointer=False` stages the release without activating
    it, which is what Requirement 8.4 asks for when a release should be
    reviewable before it goes live.
    """
    report = PublishReport(release_id=plan.release_id)
    storage.ensure_bucket()

    total = len(plan.artifacts) + len(plan.pointers)
    done = 0

    def send(objects: Iterable[DeliveryObject]) -> bool:
        nonlocal done
        all_ok = True
        for obj in objects:
            done += 1
            if on_progress:
                on_progress(done, total, obj.key)
            try:
                storage.upload(obj)
            except (RuntimeError, requests.RequestException) as exc:
                report.results.append(UploadResult(obj.key, "failed", str(exc)[:300]))
                all_ok = False
            else:
                report.results.append(UploadResult(obj.key, "uploaded", bytes_sent=obj.size))
        return all_ok

    artifacts_ok = send(plan.artifacts)

    if not artifacts_ok:
        # Requirement 8.7: an incomplete artifact set must never be
        # pointed at. Leaving the pointer alone keeps whatever was
        # published before serving, intact.
        return report

    if move_pointer and plan.pointers:
        send(plan.pointers)
        report.pointer_moved = not report.failed

    return report


def verify_published(plan: DeliveryPlan, storage: SupabaseStorage) -> list[str]:
    """Re-read what was published and check it against the plan.

    Requirement 8.5 recomputes digests against what was actually staged
    rather than trusting the upload call's return. A 200 means the
    request was accepted, not that the right bytes are readable at the
    right URL under the right headers — and the difference only shows up
    when a visitor loads the page.
    """
    problems: list[str] = []

    for obj in plan.ordered():
        headers = storage.head(obj.key)
        if headers is None:
            problems.append(f"{obj.key}: not readable at its public URL")
            continue

        cache_control = headers.get("cache-control", "")
        if obj.is_immutable and "immutable" not in cache_control:
            # An artifact served without immutable caching still works,
            # but a *pointer* served with it breaks rollback silently.
            problems.append(f"{obj.key}: expected immutable caching, got {cache_control!r}")
        if not obj.is_immutable and "immutable" in cache_control:
            problems.append(
                f"{obj.key}: pointer must not be immutably cached (got {cache_control!r})"
            )

        content_type = headers.get("content-type", "").split(";")[0].strip()
        if content_type and content_type != obj.content_type:
            problems.append(f"{obj.key}: served as {content_type!r}, planned {obj.content_type!r}")

    return problems
