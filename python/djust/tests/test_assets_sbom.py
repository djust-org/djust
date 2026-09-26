from __future__ import annotations

import json
from pathlib import Path

import pytest

from djust.assets.manifest import AssetFile, Asset, Package, sri
from djust.assets.sbom import _project_version, digest_of, dumps, rust_components, to_cyclonedx

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


def test_digest_of_tolerates_malformed_documents():
    assert digest_of([]) is None
    assert digest_of(None) is None
    assert digest_of("x") is None
    assert digest_of({"metadata": []}) is None
    assert digest_of({"metadata": {"properties": "x"}}) is None


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


def test_multi_file_asset_has_no_hashes_and_per_file_integrity_properties():
    asset = Asset(
        name="two-file",
        source="z",
        files=(
            AssetFile(integrity=sri(b"a"), type="script", path="djc/a.js"),
            AssetFile(integrity=sri(b"b"), type="style", path="djc/a.css", variant="dark"),
        ),
        packages=(Package("pkg:npm/two-file@1.0.0", "MIT"),),
    )
    document = to_cyclonedx([asset], root_name="demo", root_version="1.0", digest="e" * 64)
    component = next(c for c in document["components"] if c["name"] == "two-file")
    assert "hashes" not in component
    assert component["evidence"]["occurrences"] == [
        {"location": "djc/a.js"},
        {"location": "djc/a.css"},
    ]
    assert component["properties"] == [
        {"name": "djust:integrity:djc/a.js", "value": asset.files[0].integrity},
        {"name": "djust:integrity:djc/a.css", "value": asset.files[1].integrity},
    ]
    assert "externalReferences" not in component


def test_multi_file_external_asset_has_integrity_properties_and_delivery_marker():
    asset = Asset(
        name="two-file-external",
        source="z",
        files=(
            AssetFile(integrity=sri(b"c"), type="script", url="https://cdn.example.com/a.js"),
            AssetFile(integrity=sri(b"d"), type="style", url="https://cdn.example.com/a.css"),
        ),
        packages=(Package("pkg:npm/two-file-external@1.0.0", "MIT"),),
    )
    document = to_cyclonedx([asset], root_name="demo", root_version="1.0", digest="f" * 64)
    component = next(c for c in document["components"] if c["name"] == "two-file-external")
    assert "hashes" not in component
    assert component["properties"] == [
        {
            "name": "djust:integrity:https://cdn.example.com/a.js",
            "value": asset.files[0].integrity,
        },
        {
            "name": "djust:integrity:https://cdn.example.com/a.css",
            "value": asset.files[1].integrity,
        },
        {"name": "djust:delivery", "value": "external"},
    ]
    assert component["externalReferences"] == [
        {"type": "distribution", "url": "https://cdn.example.com/a.js"},
        {"type": "distribution", "url": "https://cdn.example.com/a.css"},
    ]


def test_project_version_reads_the_project_table():
    text = (
        '[build-system]\nrequires = ["x"]\n\n'
        '[project]\nname = "demo"\nversion = "1.2.3"\n\n'
        '[tool.other]\nversion = "9.9.9"\n'
    )
    assert _project_version(text) == "1.2.3"


def test_project_version_raises_when_no_project_section():
    text = '[tool.other]\nversion = "9.9.9"\n'
    with pytest.raises(SystemExit):
        _project_version(text)


def test_project_version_does_not_leak_into_a_later_table():
    text = '[project]\nname = "demo"\n\n[tool.other]\nversion = "9.9.9"\n'
    with pytest.raises(SystemExit):
        _project_version(text)


def test_rust_components_normalise_legacy_slash_licenses(monkeypatch):
    """Cargo accepts the legacy ``MIT/Apache-2.0`` form, which is not an SPDX
    expression; scanners reject it, so it becomes ``MIT OR Apache-2.0``."""
    import subprocess
    import types

    def pkg(name, license):
        return {
            "id": name,
            "name": name,
            "version": "1.0.0",
            "source": "registry+https://github.com/rust-lang/crates.io-index",
            "license": license,
        }

    metadata = {
        "packages": [
            {"id": "root", "name": "djust_live", "version": "0", "source": None},
            pkg("a", "MIT/Apache-2.0"),
            pkg("b", "Apache-2.0 / MIT"),
            pkg("c", "MIT OR Apache-2.0"),
            pkg("d", "Unlicense/MIT/Apache-2.0"),
        ],
        "resolve": {
            "root": "root",
            "nodes": [
                {
                    "id": "root",
                    "deps": [{"pkg": p, "dep_kinds": [{"kind": None}]} for p in "abcd"],
                },
                *({"id": p, "deps": []} for p in "abcd"),
            ],
        },
    }
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: types.SimpleNamespace(stdout=json.dumps(metadata)),
    )
    licenses = {
        c["name"]: c["licenses"][0]["expression"] for c in rust_components(Path("Cargo.toml"))
    }
    assert licenses == {
        "a": "MIT OR Apache-2.0",
        "b": "Apache-2.0 OR MIT",
        "c": "MIT OR Apache-2.0",
        "d": "Unlicense OR MIT OR Apache-2.0",
    }
