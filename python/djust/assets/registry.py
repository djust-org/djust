"""The resolved set of declared assets for this process.

``build_registry`` is pure; ``manifest_paths``/``get_registry`` add Django
discovery. Resolution follows Django's template and static-file order:
project manifests first, then apps in ``INSTALLED_APPS`` order; the first
declaration of a name wins and every later one is recorded as shadowed
(check B009 reports it).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable

from .manifest import Asset, Problem, load_manifest

MANIFEST_NAME = "djust_assets.json"


@dataclass(frozen=True)
class Shadow:
    name: str
    winner: Asset
    loser: Asset


@dataclass
class Registry:
    assets: dict[str, Asset] = field(default_factory=dict)
    shadows: list[Shadow] = field(default_factory=list)
    problems: list[Problem] = field(default_factory=list)

    def digest(self) -> str:
        """SHA-256 over the resolved assets, excluding where they were declared."""
        canonical = [
            {key: value for key, value in asdict(asset).items() if key != "source"}
            for _, asset in sorted(self.assets.items())
        ]
        payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_registry(manifest_paths: Iterable[Path]) -> Registry:
    registry = Registry()
    for path in manifest_paths:
        assets, problems = load_manifest(Path(path))
        registry.problems.extend(problems)
        for asset in assets:
            winner = registry.assets.get(asset.name)
            if winner is None:
                registry.assets[asset.name] = asset
            else:
                registry.shadows.append(Shadow(asset.name, winner, asset))
    return registry


def manifest_paths() -> list[Path]:
    from django.apps import apps
    from django.conf import settings

    paths = [Path(p) for p in getattr(settings, "DJUST_ASSET_MANIFESTS", [])]
    for app_config in apps.get_app_configs():
        candidate = Path(app_config.path) / MANIFEST_NAME
        if candidate.is_file():
            paths.append(candidate)
    return paths


_registry: Registry | None = None


def get_registry() -> Registry:
    global _registry
    if _registry is None:
        _registry = build_registry(manifest_paths())
    return _registry


def reset_registry() -> None:
    global _registry
    _registry = None
