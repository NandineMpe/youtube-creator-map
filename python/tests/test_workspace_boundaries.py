import json
import tomllib
from pathlib import Path
from typing import Any

import creator_map_pipeline
import creator_map_restricted
import creator_map_schemas

ROOT = Path(__file__).parents[2]
PROHIBITED_MEDIA_DEPENDENCIES = {"pytube", "yt-dlp", "youtube-dl"}


def _python_dependencies(manifest: Path) -> set[str]:
    document = tomllib.loads(manifest.read_text(encoding="utf-8"))
    specifications = list(document.get("project", {}).get("dependencies", []))
    for group in document.get("dependency-groups", {}).values():
        specifications.extend(group)
    return {
        specification.split("[", maxsplit=1)[0]
        .split("=", maxsplit=1)[0]
        .split("<", maxsplit=1)[0]
        .split(">", maxsplit=1)[0]
        .strip()
        .lower()
        for specification in specifications
    }


def _npm_dependencies(manifest: Path) -> set[str]:
    document: dict[str, Any] = json.loads(manifest.read_text(encoding="utf-8"))
    dependency_fields = (
        "dependencies",
        "devDependencies",
        "optionalDependencies",
        "peerDependencies",
    )
    return {name.lower() for field in dependency_fields for name in document.get(field, {})}


def test_packages_expose_distinct_boundaries() -> None:
    assert creator_map_schemas.PACKAGE_BOUNDARY == "shared-schemas"
    assert creator_map_restricted.PACKAGE_BOUNDARY == "restricted-infrastructure"
    assert creator_map_pipeline.PACKAGE_BOUNDARY == "pipeline"


def test_workspace_dependency_graph_points_inward() -> None:
    schemas = _python_dependencies(ROOT / "python" / "packages" / "schemas" / "pyproject.toml")
    restricted = _python_dependencies(
        ROOT / "python" / "packages" / "restricted-infra" / "pyproject.toml"
    )
    pipeline = _python_dependencies(ROOT / "python" / "packages" / "pipeline" / "pyproject.toml")
    web = _npm_dependencies(ROOT / "apps" / "web" / "package.json")
    public_schemas = _npm_dependencies(ROOT / "packages" / "shared-schemas" / "package.json")

    assert not {"creator-map-restricted-infra", "creator-map-pipeline"} & schemas
    assert "creator-map-schemas" in restricted
    assert "creator-map-pipeline" not in restricted
    assert {"creator-map-schemas", "creator-map-restricted-infra"} <= pipeline
    assert "@creator-map/shared-schemas" in web
    assert "@creator-map/web" not in public_schemas


def test_manifests_exclude_media_download_dependencies() -> None:
    python_manifests = [ROOT / "pyproject.toml"]
    python_manifests.extend((ROOT / "python" / "packages").glob("*/pyproject.toml"))
    npm_manifests = [ROOT / "package.json"]
    npm_manifests.extend((ROOT / "packages").glob("*/package.json"))
    npm_manifests.extend((ROOT / "apps").glob("*/package.json"))

    dependencies = set().union(
        *(_python_dependencies(manifest) for manifest in python_manifests),
        *(_npm_dependencies(manifest) for manifest in npm_manifests),
    )
    assert PROHIBITED_MEDIA_DEPENDENCIES.isdisjoint(dependencies)
