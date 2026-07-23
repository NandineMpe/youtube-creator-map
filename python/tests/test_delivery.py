"""Tests for the delivery plan.

The properties that matter here are the ones whose violation is silent:
a pointer cached as long as an artifact still *works*, right up until a
rollback fails to reach anyone. So these assert the header split, the
publication order, and the refusal to guess a Content-Type.

Requirement refs: 8.4, 8.7, 14.10
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from creator_map_pipeline.release.delivery import (
    IMMUTABLE_MAX_AGE,
    POINTER_MAX_AGE,
    DeliveryPlan,
    content_type_for,
    plan_delivery,
    plan_web_bundle,
    render_plan,
)


def build_dist(tmp_path: Path, *, release_id: str = "r1", pointer: bool = True) -> Path:
    dist = tmp_path / "dist"
    release = dist / "releases" / release_id
    (release / "countries").mkdir(parents=True)
    (release / "manifest.json").write_text(json.dumps({"releaseId": release_id}), encoding="utf-8")
    (release / "overview.json").write_text(json.dumps({"countries": []}), encoding="utf-8")
    (release / "countries" / "ZA.json").write_text(json.dumps({"country": "ZA"}), encoding="utf-8")
    if pointer:
        (dist / "active-release.json").write_text(
            json.dumps({"releaseId": release_id}), encoding="utf-8"
        )
    return dist


# --- Requirement 14.10: immutable artifacts, refreshable pointer ----------


def test_release_artifacts_are_cached_immutably(tmp_path: Path) -> None:
    plan = plan_delivery(build_dist(tmp_path))

    assert plan.artifacts
    for obj in plan.artifacts:
        assert obj.is_immutable
        assert f"max-age={IMMUTABLE_MAX_AGE}" in obj.cache_control


def test_pointer_is_not_cached_immutably(tmp_path: Path) -> None:
    """The one that would break rollback.

    A pointer served with a year-long max-age keeps clients on the
    previous release indefinitely, and nothing about the published bytes
    would look wrong while it happened.
    """
    plan = plan_delivery(build_dist(tmp_path))

    (pointer,) = plan.pointers
    assert not pointer.is_immutable
    assert f"max-age={POINTER_MAX_AGE}" in pointer.cache_control
    assert POINTER_MAX_AGE < IMMUTABLE_MAX_AGE


def test_release_id_appears_in_every_artifact_key(tmp_path: Path) -> None:
    """Immutable caching is only safe because the URL is release-scoped."""
    plan = plan_delivery(build_dist(tmp_path, release_id="2026-01-01T00-00-00Z"))

    for obj in plan.artifacts:
        assert obj.key.startswith("releases/2026-01-01T00-00-00Z/")


def test_pointer_key_is_stable_across_releases(tmp_path: Path) -> None:
    """Clients find new releases by re-reading one unchanging URL."""
    first = plan_delivery(build_dist(tmp_path / "a", release_id="r1"))
    second = plan_delivery(build_dist(tmp_path / "b", release_id="r2"))

    assert first.pointers[0].key == second.pointers[0].key == "active-release.json"
    assert first.artifacts[0].key != second.artifacts[0].key


# --- Requirement 8.7: one complete release at every instant ---------------


def test_pointer_is_published_after_every_artifact(tmp_path: Path) -> None:
    plan = plan_delivery(build_dist(tmp_path))
    keys = [o.key for o in plan.ordered()]

    assert keys[-1] == "active-release.json"
    assert len(keys) == len(plan.artifacts) + 1


def test_plan_can_stage_artifacts_without_a_pointer(tmp_path: Path) -> None:
    """Requirement 8.4: stage the whole set, then activate."""
    plan = plan_delivery(build_dist(tmp_path, pointer=False), release_id="r1")

    assert plan.artifacts
    assert plan.pointers == ()


def test_unactivated_release_can_be_planned_by_id(tmp_path: Path) -> None:
    """A release built but not activated is still publishable."""
    dist = build_dist(tmp_path, release_id="r1")
    (dist / "releases" / "r2" / "countries").mkdir(parents=True)
    (dist / "releases" / "r2" / "manifest.json").write_text("{}", encoding="utf-8")

    plan = plan_delivery(dist, release_id="r2")

    assert all(o.key.startswith("releases/r2/") for o in plan.artifacts)
    # The pointer still names r1: staging must not imply activation.
    assert json.loads(plan.pointers[0].source.read_text(encoding="utf-8"))["releaseId"] == "r1"


# --- Content types ---------------------------------------------------------


def test_unknown_extension_is_refused_not_guessed(tmp_path: Path) -> None:
    """A wrong Content-Type surfaces as data loss in the browser, far
    from the build that caused it."""
    with pytest.raises(ValueError, match="refusing to guess"):
        content_type_for(Path("artifact.bin"))


def test_json_artifacts_get_a_json_content_type(tmp_path: Path) -> None:
    plan = plan_delivery(build_dist(tmp_path))
    assert {o.content_type for o in plan.artifacts} == {"application/json"}


# --- Keys are URL paths ----------------------------------------------------


def test_nested_keys_use_forward_slashes(tmp_path: Path) -> None:
    """Computed on Windows, served as URLs. A backslash here is a broken
    link, not a separator."""
    plan = plan_delivery(build_dist(tmp_path))

    shard = next(o for o in plan.artifacts if o.key.endswith("ZA.json"))
    assert shard.key == "releases/r1/countries/ZA.json"
    assert "\\" not in shard.key


# --- Determinism -----------------------------------------------------------


def test_plan_is_deterministic(tmp_path: Path) -> None:
    dist = build_dist(tmp_path)
    assert render_plan(plan_delivery(dist)) == render_plan(plan_delivery(dist))


def test_digests_match_file_contents(tmp_path: Path) -> None:
    import hashlib

    plan = plan_delivery(build_dist(tmp_path))
    for obj in plan.artifacts:
        assert obj.digest == hashlib.sha256(obj.source.read_bytes()).hexdigest()


# --- Failure modes ---------------------------------------------------------


def test_missing_release_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        plan_delivery(build_dist(tmp_path), release_id="nope")


def test_unactivated_build_plans_without_a_pointer(tmp_path: Path) -> None:
    """Requirement 8.4 stages the artifact set before the pointer moves,
    so a release with no pointer is the normal input here. Refusing would
    make staging impossible without first activating, which is backwards.
    """
    plan = plan_delivery(build_dist(tmp_path, release_id="r1", pointer=False))

    assert plan.release_id == "r1"
    assert plan.pointers == ()


def test_newest_release_is_chosen_when_there_is_no_pointer(tmp_path: Path) -> None:
    dist = build_dist(tmp_path, release_id="2026-01-01T00-00-00Z", pointer=False)
    newer = dist / "releases" / "2026-06-01T00-00-00Z"
    newer.mkdir(parents=True)
    (newer / "manifest.json").write_text("{}", encoding="utf-8")

    assert plan_delivery(dist).release_id == "2026-06-01T00-00-00Z"


def test_no_pointer_and_no_releases_is_an_error(tmp_path: Path) -> None:
    """Planning nothing is not a successful plan."""
    empty = tmp_path / "dist"
    empty.mkdir()

    with pytest.raises(FileNotFoundError):
        plan_delivery(empty)


def test_empty_release_is_an_error(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    (dist / "releases" / "r1").mkdir(parents=True)
    with pytest.raises(ValueError, match="no files"):
        plan_delivery(dist, release_id="r1")


# --- Web bundle ------------------------------------------------------------


def test_hashed_chunks_are_immutable_and_html_is_not(tmp_path: Path) -> None:
    """A deploy that caches HTML forever strands clients on old HTML
    referencing chunks that no longer exist."""
    out = tmp_path / "out"
    (out / "_next" / "static" / "chunks").mkdir(parents=True)
    (out / "_next" / "static" / "chunks" / "a-abc123.js").write_text("x", encoding="utf-8")
    (out / "index.html").write_text("<html></html>", encoding="utf-8")

    objects = {o.key: o for o in plan_web_bundle(out)}

    assert objects["_next/static/chunks/a-abc123.js"].is_immutable
    assert not objects["index.html"].is_immutable
    assert "must-revalidate" in objects["index.html"].cache_control


def test_web_bundle_skips_files_the_cdn_never_serves(tmp_path: Path) -> None:
    """Unlike a release, build output contains internal files. Skipping
    beats failing, since they are not meant to be public."""
    out = tmp_path / "out"
    out.mkdir()
    (out / "index.html").write_text("<html></html>", encoding="utf-8")
    (out / "trace.nft").write_text("internal", encoding="utf-8")

    assert [o.key for o in plan_web_bundle(out)] == ["index.html"]


def test_web_bundle_prefix_is_applied(tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    (out / "index.html").write_text("<html></html>", encoding="utf-8")

    assert plan_web_bundle(out, prefix="app/")[0].key == "app/index.html"


def test_html_can_be_excluded_for_stores_that_mangle_it(tmp_path: Path) -> None:
    """Supabase Storage serves uploaded HTML as text/plain, so a browser
    downloads the page instead of rendering it. On such a store the HTML
    is hosted elsewhere and only the data and hashed assets are uploaded;
    including the HTML anyway leaves dead files served with the wrong
    type."""
    out = tmp_path / "out"
    (out / "_next" / "static").mkdir(parents=True)
    (out / "_next" / "static" / "a-abc.js").write_text("x", encoding="utf-8")
    (out / "index.html").write_text("<html></html>", encoding="utf-8")
    (out / "methodology" / "index.html").parent.mkdir()
    (out / "methodology" / "index.html").write_text("<html></html>", encoding="utf-8")

    keys = {o.key for o in plan_web_bundle(out, include_html=False)}

    assert "_next/static/a-abc.js" in keys
    assert not any(k.endswith(".html") for k in keys)


# --- Reporting -------------------------------------------------------------


def test_rendered_plan_records_the_publish_order(tmp_path: Path) -> None:
    report = json.loads(render_plan(plan_delivery(build_dist(tmp_path))))

    assert report["publishOrder"] == ["artifacts", "pointers"]
    assert report["releaseId"] == "r1"
    assert all("sha256" in entry for entry in report["artifacts"])


def test_total_bytes_counts_both_classes(tmp_path: Path) -> None:
    plan = plan_delivery(build_dist(tmp_path))
    expected = sum(o.size for o in plan.ordered())

    assert plan.total_bytes == expected
    assert isinstance(plan, DeliveryPlan)
