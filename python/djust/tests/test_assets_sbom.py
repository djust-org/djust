from __future__ import annotations

import json
from pathlib import Path

from djust.assets.manifest import AssetFile, Asset, Package, sri
from djust.assets.sbom import digest_of, dumps, rust_components, to_cyclonedx

GOLDEN = Path(__file__).parent / "fixtures" / "assets_sbom_golden.cdx.json"


def _assets():
    return [
        Asset(
            name="markdown-visual",
            source="x",
            files=(AssetFile(integrity=sri(b"bundle"), type="script", path="djc/mv.js"),),
            packages=(
                Package("pkg:npm/%40tiptap/core@3.31.3", "MIT"),
                Package("pkg:npm/prosemirror-model@1.25.1", "MIT"),
            ),
        ),
        Asset(
            name="chart.js",
            source="y",
            files=(
                AssetFile(
                    integrity=sri(b"chart"),
                    type="script",
                    url="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js",
                ),
            ),
            packages=(Package("pkg:npm/chart.js@4.4.0", "MIT"),),
        ),
    ]


def _doc():
    return to_cyclonedx(_assets(), root_name="demo", root_version="1.0", digest="d" * 64)


def _all_purls(document):
    found = []

    def walk(components):
        for c in components:
            if "purl" in c:
                found.append(c["purl"])
            walk(c.get("components", []))

    walk(document["components"])
    return sorted(found)


def test_every_package_is_present_with_encoded_scoped_purl():
    assert _all_purls(_doc()) == [
        "pkg:npm/%40tiptap/core@3.31.3",
        "pkg:npm/chart.js@4.4.0",
        "pkg:npm/prosemirror-model@1.25.1",
    ]


def test_output_is_deterministic_and_has_no_volatile_fields():
    first, second = dumps(_doc()), dumps(_doc())
    assert first == second and first.endswith("\n")
    parsed = json.loads(first)
    assert "serialNumber" not in parsed
    assert "timestamp" not in parsed["metadata"]
    assert parsed["specVersion"] == "1.6"


def test_digest_round_trips():
    assert digest_of(_doc()) == "d" * 64
    assert digest_of({"metadata": {}}) is None


def test_external_asset_is_marked():
    chart = next(c for c in _doc()["components"] if c["name"] == "chart.js")
    assert {"name": "djust:delivery", "value": "external"} in chart["properties"]
    assert chart["externalReferences"][0]["url"].startswith("https://cdn.jsdelivr.net/")


def test_matches_golden_file():
    assert dumps(_doc()) == GOLDEN.read_text()


def test_rust_components_are_registry_crates_only():
    repo = Path(__file__).resolve().parents[3]
    components = rust_components(repo / "crates" / "djust_live" / "Cargo.toml")
    purls = [c["purl"] for c in components]
    assert purls == sorted(purls) and purls
    assert all(p.startswith("pkg:cargo/") and "@" in p for p in purls)
    assert not any(
        p.startswith("pkg:cargo/djust") for p in purls
    )  # workspace crates are djust itself
    assert any(p.startswith("pkg:cargo/pyo3@") for p in purls)
