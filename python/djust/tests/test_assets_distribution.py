"""djust's own manifests are valid, match their files, and djust.cdx.json is current."""

from __future__ import annotations

import json
from pathlib import Path

from djust.assets.registry import get_registry
from djust.assets.sbom import digest_of
from djust.checks.assets import check_asset_files, check_asset_manifests

PKG = Path(__file__).resolve().parents[1]


def test_djust_assets_are_declared_and_clean():
    assert {"markdown-visual", "highlight.js", "xterm", "admin-css"} <= set(get_registry().assets)
    # The demo project declares no assets of its own, so every message would be djust's.
    assert check_asset_manifests(None) + check_asset_files(None) == []


def test_committed_distribution_sbom_matches_the_manifests():
    from djust.assets.registry import build_registry
    from djust.assets.sbom import DISTRIBUTION_MANIFESTS

    registry = build_registry(PKG.parent / rel for rel in DISTRIBUTION_MANIFESTS)
    document = json.loads((PKG / "djust.cdx.json").read_text())
    assert digest_of(document) == registry.digest()
