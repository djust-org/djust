"""CycloneDX 1.6 SBOMs from the asset registry (ADR-040).

One generator for every output: the distribution file committed as
``python/djust/djust.cdx.json`` (``python -m djust.assets.sbom
--distribution``, no Django needed), the app file ``collectstatic`` writes,
and ``manage.py djust_sbom``. Output is byte-identical for identical inputs:
no serialNumber, no timestamp, sorted keys and arrays; ``make vendor-check``
depends on that.

Shape: flat. ``trivy sbom`` ignores packages nested inside a component, so
each asset's packages become top-level ``library`` components instead of
being nested under the asset's ``file`` component, and a top-level
``dependencies`` array records which packages belong to which asset. This
module must not import Django at module level: it runs in CI via
``PYTHONPATH=python python -m djust.assets.sbom --distribution`` with no
Django settings configured.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import subprocess
import sys
from collections import deque
from pathlib import Path
from typing import Iterable, Sequence

from .manifest import Asset, Package
from .registry import build_registry

DIGEST_PROPERTY = "djust:manifest-digest"
DISTRIBUTION_MANIFESTS = (
    "djust/components/djust_assets.json",
    "djust/admin_ext/djust_assets.json",
)
_CDX_ALG = {"sha256": "SHA-256", "sha384": "SHA-384", "sha512": "SHA-512"}


def _hash(integrity: str) -> dict:
    alg, b64 = integrity.split("-", 1)
    return {"alg": _CDX_ALG[alg], "content": base64.b64decode(b64).hex()}


def _package_component(ref: str, package: Package) -> dict:
    return {
        "type": "library",
        "name": package.name,
        "version": package.version,
        "purl": package.purl,
        "bom-ref": f"{ref}:{package.purl}",
        "licenses": [{"expression": package.license}],
    }


def _asset_component(asset: Asset) -> dict:
    ref = f"asset:{asset.name}"
    component: dict = {
        "type": "file",
        "name": asset.name,
        "bom-ref": ref,
        "evidence": {"occurrences": [{"location": f.location} for f in asset.files]},
    }
    if len(asset.files) == 1:
        component["hashes"] = [_hash(asset.files[0].integrity)]
    else:
        component["properties"] = [
            {"name": f"djust:integrity:{f.location}", "value": f.integrity} for f in asset.files
        ]
    if asset.external:
        component.setdefault("properties", []).append(
            {"name": "djust:delivery", "value": "external"}
        )
        component["externalReferences"] = [
            {"type": "distribution", "url": f.url} for f in asset.files if f.url
        ]
    return component


def to_cyclonedx(
    assets: Iterable[Asset],
    *,
    root_name: str,
    root_version: str | None,
    digest: str,
    extra_components: Sequence[dict] = (),
) -> dict:
    root: dict = {"type": "application", "name": root_name, "bom-ref": f"root:{root_name}"}
    if root_version:
        root["version"] = root_version

    sorted_assets = sorted(assets, key=lambda a: a.name)
    components: list[dict] = []
    dependencies: list[dict] = []
    for asset in sorted_assets:
        ref = f"asset:{asset.name}"
        components.append(_asset_component(asset))
        package_components = sorted(
            (_package_component(ref, package) for package in asset.packages),
            key=lambda c: c["purl"],
        )
        components.extend(package_components)
        dependencies.append(
            {
                "ref": ref,
                "dependsOn": sorted(c["bom-ref"] for c in package_components),
            }
        )
    components.extend(extra_components)
    components.sort(key=lambda c: c["bom-ref"])
    dependencies.sort(key=lambda d: d["ref"])

    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "version": 1,
        "metadata": {
            "component": root,
            "properties": [{"name": DIGEST_PROPERTY, "value": digest}],
            "tools": {"components": [{"type": "application", "name": "djust.assets.sbom"}]},
        },
        "components": components,
        "dependencies": dependencies,
    }


def dumps(document: dict) -> str:
    return json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def digest_of(document: dict) -> str | None:
    if not isinstance(document, dict):
        return None
    metadata = document.get("metadata", {})
    if not isinstance(metadata, dict):
        return None
    properties = metadata.get("properties", [])
    if not isinstance(properties, list):
        return None
    for prop in properties:
        if not isinstance(prop, dict):
            continue
        if prop.get("name") == DIGEST_PROPERTY:
            return prop.get("value")
    return None


def rust_components(manifest_path: Path) -> list[dict]:
    """Registry crates linked into the extension: normal dependencies
    reachable from the bindings crate. Workspace crates (no ``source``) are
    djust itself and are left out."""
    metadata = json.loads(
        subprocess.run(
            [
                "cargo",
                "metadata",
                "--format-version",
                "1",
                "--locked",
                "--manifest-path",
                str(manifest_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    )
    packages = {p["id"]: p for p in metadata["packages"]}
    nodes = {n["id"]: n for n in metadata["resolve"]["nodes"]}
    root = metadata["resolve"][
        "root"
    ]  # the --manifest-path package; avoids /tmp vs /private/tmp path matching
    seen: set[str] = set()
    queue = deque([root])
    while queue:
        node = nodes[queue.popleft()]
        for dep in node["deps"]:
            normal = any(kind.get("kind") is None for kind in dep["dep_kinds"])
            if normal and dep["pkg"] not in seen:
                seen.add(dep["pkg"])
                queue.append(dep["pkg"])
    components = []
    for pkg_id in seen:
        package = packages[pkg_id]
        if package.get("source") is None:
            continue
        purl = f"pkg:cargo/{package['name']}@{package['version']}"
        component = {
            "type": "library",
            "name": package["name"],
            "version": package["version"],
            "purl": purl,
            "bom-ref": f"rust:{purl}",
        }
        if package.get("license"):
            component["licenses"] = [{"expression": package["license"]}]
        components.append(component)
    return sorted(components, key=lambda c: c["purl"])


def _project_version(pyproject_text: str) -> str:
    """The ``[project]`` table's ``version``, read with a regex so this
    module never needs tomllib/tomli: it is imported at runtime inside
    users' apps (Python 3.10+, where tomli is dev-only)."""
    project_start = pyproject_text.find("[project]")
    if project_start == -1:
        raise SystemExit("pyproject.toml has no [project] section")
    section_start = project_start + len("[project]")
    next_table = re.search(r"^\[", pyproject_text[section_start:], re.M)
    section_end = section_start + next_table.start() if next_table else len(pyproject_text)
    section = pyproject_text[section_start:section_end]
    match = re.search(r'^version = "([^"]+)"', section, re.M)
    if match is None:
        raise SystemExit('pyproject.toml [project] section has no version = "..." line')
    return match[1]


def write_distribution_sbom(repo_root: Path) -> Path:
    python_dir = repo_root / "python"
    registry = build_registry(python_dir / rel for rel in DISTRIBUTION_MANIFESTS)
    if registry.problems:
        raise SystemExit(
            "\n".join(f"{p.check_id} {p.source}: {p.message}" for p in registry.problems)
        )
    version = _project_version((repo_root / "pyproject.toml").read_text(encoding="utf-8"))
    document = to_cyclonedx(
        registry.assets.values(),
        root_name="djust",
        root_version=version,
        digest=registry.digest(),
        extra_components=rust_components(repo_root / "crates" / "djust_live" / "Cargo.toml"),
    )
    out = python_dir / "djust" / "djust.cdx.json"
    out.write_text(dumps(document), encoding="utf-8")
    return out


def app_document() -> dict:
    from django.conf import settings

    from .registry import get_registry

    registry = get_registry()
    name = getattr(settings, "DJUST_SBOM_NAME", None) or str(settings.ROOT_URLCONF).split(".")[0]
    return to_cyclonedx(
        registry.assets.values(), root_name=name, root_version=None, digest=registry.digest()
    )


def served_directory_containing(path: Path) -> Path | None:
    from django.conf import settings

    candidates = [getattr(settings, "STATIC_ROOT", None), getattr(settings, "MEDIA_ROOT", None)]
    for entry in getattr(settings, "STATICFILES_DIRS", []):
        candidates.append(entry[1] if isinstance(entry, (tuple, list)) else entry)
    target = Path(path).resolve()
    for candidate in candidates:
        if not candidate:
            continue
        root = Path(candidate).resolve()
        if target == root or root in target.parents:
            return root
    return None


def write_app_sbom(path: Path) -> None:
    from django.core.management import CommandError

    served = served_directory_containing(path)
    if served is not None:
        raise CommandError(
            f"Refusing to write the SBOM to {path}: it is inside {served}, which is served "
            "to browsers. Set DJUST_SBOM_PATH outside every static and media directory (djust.B012)."
        )
    target = Path(path)
    if target.is_dir():
        raise CommandError(
            f"Refusing to write the SBOM to {path}: it is a directory. Set "
            "DJUST_SBOM_PATH to a file path such as /srv/app/sbom/djust-assets.cdx.json."
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(dumps(app_document()), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m djust.assets.sbom")
    parser.add_argument(
        "--distribution",
        action="store_true",
        required=True,
        help="write python/djust/djust.cdx.json for the djust wheel",
    )
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    print(write_distribution_sbom(args.repo_root.resolve()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
