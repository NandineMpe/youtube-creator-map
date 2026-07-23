"""Tests for the Supabase Storage publisher.

The property worth defending is the publication order. Requirement 8.7
requires one complete release at every observable instant, and the only
way to break it is to move the pointer before every artifact has landed —
which produces a window where visitors get 404s and nothing in the upload
log looks wrong.

A fake transport rather than a mocked client: the failure modes that
matter are HTTP-shaped (a 500 mid-upload, a 403 on the bucket, a header
the server did not apply), and a mock of the client's own methods would
assert only that the code called itself.

Requirement refs: 8.4, 8.5, 8.7, 14.10, 15.1
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import requests
from creator_map_pipeline.release.delivery import plan_delivery
from creator_map_pipeline.release.publisher import (
    SupabaseStorage,
    publish,
    verify_published,
)


class FakeResponse:
    def __init__(self, status_code: int, headers: dict[str, str] | None = None) -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self.text = "" if status_code < 400 else f"error {status_code}"


class FakeSession:
    """Records requests and replays scripted outcomes."""

    def __init__(
        self,
        *,
        bucket_exists: bool = True,
        fail_keys: dict[str, int] | None = None,
        drop_headers: bool = False,
    ) -> None:
        self.headers: dict[str, str] = {}
        self.uploads: list[tuple[str, dict[str, str]]] = []
        self.stored: dict[str, dict[str, str]] = {}
        self._bucket_exists = bucket_exists
        self._fail_keys = fail_keys or {}
        self._drop_headers = drop_headers

    def get(self, url: str, **kwargs: object) -> FakeResponse:
        # Bucket existence check.
        if "/bucket/" in url or url.endswith("/bucket"):
            return FakeResponse(200 if self._bucket_exists else 404)

        # A ranged object GET — how verification reads headers, because
        # this backend serves different cache headers on HEAD than GET.
        key = url.split("/creator-map/", 1)[-1]
        if key not in self.stored:
            return FakeResponse(404)
        ranged = bool((kwargs.get("headers") or {}).get("Range"))  # type: ignore[union-attr]
        return FakeResponse(206 if ranged else 200, self.stored[key])

    def post(self, url: str, **kwargs: object) -> FakeResponse:
        if "/bucket" in url and "/object/" not in url:
            self._bucket_exists = True
            return FakeResponse(200)

        key = url.split("/creator-map/", 1)[-1]
        headers = dict(kwargs.get("headers") or {})  # type: ignore[arg-type]

        if key in self._fail_keys:
            return FakeResponse(self._fail_keys[key])

        self.uploads.append((key, headers))
        self.stored[key] = (
            {}
            if self._drop_headers
            else {
                "cache-control": headers.get("Cache-Control", ""),
                "content-type": headers.get("Content-Type", ""),
            }
        )
        return FakeResponse(200)


def build_dist(tmp_path: Path) -> Path:
    dist = tmp_path / "dist"
    release = dist / "releases" / "r1" / "countries"
    release.mkdir(parents=True)
    (dist / "releases" / "r1" / "manifest.json").write_text("{}", encoding="utf-8")
    (dist / "releases" / "r1" / "overview.json").write_text("{}", encoding="utf-8")
    (release / "ZA.json").write_text('{"country":"ZA"}', encoding="utf-8")
    (dist / "active-release.json").write_text(json.dumps({"releaseId": "r1"}), encoding="utf-8")
    return dist


def storage_for(session: FakeSession) -> SupabaseStorage:
    return SupabaseStorage(
        "https://example.supabase.co",
        "service-key-not-real",
        session=session,  # type: ignore[arg-type]
    )


# --- Requirement 8.7: publication order -----------------------------------


def test_the_pointer_is_published_last(tmp_path: Path) -> None:
    """A pointer naming a release whose shards are still uploading leaves
    visitors with no complete release to fall back on."""
    session = FakeSession()
    report = publish(plan_delivery(build_dist(tmp_path)), storage_for(session))

    assert report.pointer_moved
    assert session.uploads[-1][0] == "active-release.json"
    assert all(k != "active-release.json" for k, _ in session.uploads[:-1])


def test_a_failed_artifact_leaves_the_pointer_alone(tmp_path: Path) -> None:
    """The failure this whole ordering exists to prevent.

    An incomplete artifact set must never be pointed at, so a single
    failed shard has to stop the pointer from moving — leaving whatever
    was published before still serving.
    """
    session = FakeSession(fail_keys={"releases/r1/countries/ZA.json": 403})

    report = publish(plan_delivery(build_dist(tmp_path)), storage_for(session))

    assert not report.pointer_moved
    assert report.failed
    assert "active-release.json" not in [k for k, _ in session.uploads]


def test_stage_only_uploads_artifacts_without_activating(tmp_path: Path) -> None:
    """Requirement 8.4: stage the complete set, then activate."""
    session = FakeSession()

    report = publish(plan_delivery(build_dist(tmp_path)), storage_for(session), move_pointer=False)

    assert not report.pointer_moved
    assert report.uploaded > 0
    assert "active-release.json" not in [k for k, _ in session.uploads]


# --- Requirement 14.10: cache headers travel with the bytes ---------------


def test_artifacts_are_uploaded_with_immutable_caching(tmp_path: Path) -> None:
    session = FakeSession()
    publish(plan_delivery(build_dist(tmp_path)), storage_for(session))

    for key, headers in session.uploads:
        if key == "active-release.json":
            continue
        assert "immutable" in headers["Cache-Control"], key


def test_the_pointer_is_not_uploaded_as_immutable(tmp_path: Path) -> None:
    """Caching the pointer like an artifact would keep clients on the old
    release after a rollback, with nothing in the bytes looking wrong."""
    session = FakeSession()
    publish(plan_delivery(build_dist(tmp_path)), storage_for(session))

    pointer = next(h for k, h in session.uploads if k == "active-release.json")

    assert "immutable" not in pointer["Cache-Control"]
    assert "must-revalidate" in pointer["Cache-Control"]


def test_content_types_travel_with_the_upload(tmp_path: Path) -> None:
    session = FakeSession()
    publish(plan_delivery(build_dist(tmp_path)), storage_for(session))

    assert all(h["Content-Type"] == "application/json" for _, h in session.uploads)


# --- Requirement 8.5: verify what was actually published ------------------


def test_verification_passes_on_a_correct_publish(tmp_path: Path) -> None:
    session = FakeSession()
    plan = plan_delivery(build_dist(tmp_path))
    publish(plan, storage_for(session))

    assert verify_published(plan, storage_for(session)) == []


def test_verification_catches_a_missing_object(tmp_path: Path) -> None:
    """A 200 on upload means the request was accepted, not that the bytes
    are readable at the right URL."""
    session = FakeSession()
    plan = plan_delivery(build_dist(tmp_path))
    publish(plan, storage_for(session))
    session.stored.pop("releases/r1/countries/ZA.json")

    problems = verify_published(plan, storage_for(session))

    assert any("not readable" in p for p in problems)


def test_verification_catches_a_dropped_cache_header(tmp_path: Path) -> None:
    """A host that ignores Cache-Control breaks immutability silently:
    everything still works, just slower and wrong after a rollback."""
    session = FakeSession(drop_headers=True)
    plan = plan_delivery(build_dist(tmp_path))
    publish(plan, storage_for(session))

    problems = verify_published(plan, storage_for(session))

    assert any("immutable caching" in p for p in problems)


def test_verification_reads_headers_from_a_get_not_a_head(tmp_path: Path) -> None:
    """The bug that failed the first live publish.

    Supabase serves `no-cache` on HEAD but the stored `Cache-Control` on
    GET, and the GET is what a browser gets. A HEAD-based check reported
    620 false failures on objects that were in fact published correctly.
    This asserts a HEAD that lies is ignored in favour of the GET.
    """

    class HeadLies(FakeSession):
        def head(self, url: str, **_: object) -> FakeResponse:
            # What Supabase actually does: a misleading cache header.
            return FakeResponse(200, {"cache-control": "no-cache"})

    session = HeadLies()
    plan = plan_delivery(build_dist(tmp_path))
    publish(plan, storage_for(session))

    # No problems, because verification uses the GET, which carries the
    # real immutable header.
    assert verify_published(plan, storage_for(session)) == []


def test_verification_catches_an_immutably_cached_pointer(tmp_path: Path) -> None:
    session = FakeSession()
    plan = plan_delivery(build_dist(tmp_path))
    publish(plan, storage_for(session))
    session.stored["active-release.json"]["cache-control"] = "public, max-age=31536000, immutable"

    problems = verify_published(plan, storage_for(session))

    assert any("must not be immutably cached" in p for p in problems)


# --- Retries ---------------------------------------------------------------


def test_a_permission_error_is_not_retried(tmp_path: Path) -> None:
    """Uploading into a 403 a dozen times only delays the report."""
    calls: list[str] = []

    class Counting(FakeSession):
        def post(self, url: str, **kwargs: object) -> FakeResponse:
            if "/object/" in url:
                calls.append(url)
                return FakeResponse(403)
            return super().post(url, **kwargs)

    report = publish(plan_delivery(build_dist(tmp_path)), storage_for(Counting()))

    assert report.failed
    # One attempt per object, not one per object per retry.
    assert len(calls) == len(report.results)


def test_a_network_error_is_recorded_not_raised(tmp_path: Path) -> None:
    """One unreachable object must not abort the run and lose the report
    for everything else."""

    class Broken(FakeSession):
        def post(self, url: str, **kwargs: object) -> FakeResponse:
            if "ZA.json" in url:
                raise requests.ConnectionError("no route")
            return super().post(url, **kwargs)

    report = publish(plan_delivery(build_dist(tmp_path)), storage_for(Broken()))

    assert report.failed
    assert not report.pointer_moved


# --- Bucket ----------------------------------------------------------------


def test_a_missing_bucket_is_created(tmp_path: Path) -> None:
    session = FakeSession(bucket_exists=False)

    publish(plan_delivery(build_dist(tmp_path)), storage_for(session))

    assert session.uploads


def test_a_bucket_that_cannot_be_created_stops_the_publish(tmp_path: Path) -> None:
    class Denied(FakeSession):
        def post(self, url: str, **kwargs: object) -> FakeResponse:
            if "/bucket" in url and "/object/" not in url:
                return FakeResponse(403)
            return super().post(url, **kwargs)

    with pytest.raises(RuntimeError, match="could not create bucket"):
        publish(
            plan_delivery(build_dist(tmp_path)),
            storage_for(Denied(bucket_exists=False)),
        )


# --- Credentials -----------------------------------------------------------


def test_the_key_is_sent_as_a_header_not_a_query_parameter() -> None:
    """A credential in a URL lands in access logs and referrer headers."""
    session = FakeSession()
    storage_for(session)

    assert session.headers["apikey"] == "service-key-not-real"
    assert session.headers["Authorization"].startswith("Bearer ")


def test_public_urls_carry_no_credential() -> None:
    session = FakeSession()
    url = storage_for(session).public_url("releases/r1/overview.json")

    assert "service-key-not-real" not in url
    assert url.endswith("/creator-map/releases/r1/overview.json")


# --- Reporting -------------------------------------------------------------


def test_the_report_states_the_pointer_was_not_moved(tmp_path: Path) -> None:
    session = FakeSession(fail_keys={"releases/r1/overview.json": 500})

    described = publish(plan_delivery(build_dist(tmp_path)), storage_for(session)).describe()

    assert "was NOT moved" in described
    assert "previously published release is unchanged" in described


def test_the_report_counts_bytes_sent(tmp_path: Path) -> None:
    report = publish(plan_delivery(build_dist(tmp_path)), storage_for(FakeSession()))

    assert report.bytes_sent > 0
    assert report.uploaded == len(report.results)
