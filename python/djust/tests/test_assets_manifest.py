"""djust_assets.json parsing (schema 1). Pure: no Django needed."""

from __future__ import annotations

import base64
import hashlib
import json

import pytest

from djust.assets.manifest import (
    Asset,
    AssetFile,
    Package,
    load_manifest,
    parse_manifest,
    sri,
)

GOOD_SRI = sri(b"x")


def _doc(**asset):
    base = {
        "files": [{"path": "lib/lib.js", "integrity": GOOD_SRI}],
        "packages": [{"purl": "pkg:npm/lib@1.2.3", "license": "MIT"}],
    }
    base.update(asset)
    return {"schema": 1, "assets": {"lib": base}}


def ids(problems):
    return [p.check_id for p in problems]


def test_sri_matches_browser_format():
    digest = base64.b64encode(hashlib.sha384(b"x").digest()).decode()
    assert sri(b"x") == f"sha384-{digest}"
    assert sri(b"x", "sha256").startswith("sha256-")


def test_valid_manifest_parses():
    assets, problems = parse_manifest(_doc(license_file="lib/LICENSE.txt"), "m.json")
    assert problems == []
    (asset,) = assets
    assert asset == Asset(
        name="lib",
        source="m.json",
        files=(AssetFile(integrity=GOOD_SRI, type="script", path="lib/lib.js"),),
        packages=(Package("pkg:npm/lib@1.2.3", "MIT"),),
        license_file="lib/LICENSE.txt",
    )
    assert asset.external is False


def test_scoped_purl_name_and_version():
    pkg = Package("pkg:npm/%40tiptap/core@3.31.3", "MIT")
    assert (pkg.name, pkg.version) == ("@tiptap/core", "3.31.3")


@pytest.mark.parametrize("ext,expected", [(".js", "script"), (".mjs", "module"), (".css", "style")])
def test_type_inferred_from_extension(ext, expected):
    doc = _doc(files=[{"path": f"lib/x{ext}", "integrity": GOOD_SRI}])
    (asset,), _ = parse_manifest(doc, "m")
    assert asset.files[0].type == expected


def test_variants_and_files_for():
    doc = _doc(
        files=[
            {"path": "hl/hl.js", "integrity": GOOD_SRI},
            {"path": "hl/dark.css", "integrity": GOOD_SRI, "variant": "dark"},
            {"path": "hl/light.css", "integrity": GOOD_SRI, "variant": "light"},
        ]
    )
    (asset,), _ = parse_manifest(doc, "m")
    assert asset.variants == ("dark", "light")
    assert [f.path for f in asset.files_for("dark")] == ["hl/hl.js", "hl/dark.css"]
    assert [f.path for f in asset.files_for()] == ["hl/hl.js"]


@pytest.mark.parametrize(
    "data",
    [
        [],
        {"schema": 2, "assets": {}},
        {"schema": 1},
        {"schema": 1, "assets": {"lib": "nope"}},
    ],
)
def test_structural_errors_are_b001(data):
    assets, problems = parse_manifest(data, "m")
    assert assets == [] and ids(problems) == ["djust.B001"]


@pytest.mark.parametrize(
    "files",
    [
        [],
        [{"integrity": GOOD_SRI}],
        [{"path": "a.js", "url": "https://x/a.js", "integrity": GOOD_SRI}],
        [{"path": "a.js", "integrity": "md5-abc"}],
        [{"path": "a.txt", "integrity": GOOD_SRI}],
        [{"url": "http://cdn.example/a.js", "integrity": GOOD_SRI}],
        [{"path": "a.js"}],
    ],
)
def test_bad_files_are_b001(files):
    assets, problems = parse_manifest(_doc(files=files), "m")
    assert assets == [] and ids(problems) == ["djust.B001"]


def test_external_without_integrity_is_b006():
    doc = _doc(files=[{"url": "https://cdn.example/a@1.0.0/a.js"}])
    assets, problems = parse_manifest(doc, "m")
    assert assets == [] and ids(problems) == ["djust.B006"]


@pytest.mark.parametrize(
    "package",
    [
        {"purl": "pkg:npm/lib", "license": "MIT"},
        {"purl": "lib@1.0.0", "license": "MIT"},
        {"purl": "pkg:npm/lib@1.0.0"},
        {"purl": "pkg:npm/lib@1.0.0", "license": "  "},
    ],
)
def test_floating_or_unlicensed_packages_are_b002(package):
    assets, problems = parse_manifest(_doc(packages=[package]), "m")
    assert assets == [] and ids(problems) == ["djust.B002"]


def test_one_bad_asset_does_not_drop_the_others():
    doc = _doc()
    doc["assets"]["broken"] = {"files": [], "packages": []}
    assets, problems = parse_manifest(doc, "m")
    assert [a.name for a in assets] == ["lib"]
    assert problems and all("broken" in p.message for p in problems)


def test_load_manifest_reports_missing_and_invalid_json(tmp_path):
    _, missing = load_manifest(tmp_path / "nope.json")
    assert ids(missing) == ["djust.B001"]
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    _, invalid = load_manifest(bad)
    assert ids(invalid) == ["djust.B001"]
    good = tmp_path / "good.json"
    good.write_text(json.dumps(_doc()))
    assets, problems = load_manifest(good)
    assert problems == [] and assets[0].source == str(good)


@pytest.mark.parametrize(
    "path",
    ["../../x.js", "/etc/hosts.js", "lib/../../x.js", "lib\\x.js", "C:\\x.js", "lib/..", ".."],
)
def test_paths_escaping_static_are_b001_naming_the_path(path):
    """A path finders.find() would reject with SuspiciousFileOperation (which
    escaped the check framework and aborted check/runserver/migrate) is a
    manifest error instead."""
    assets, problems = parse_manifest(
        _doc(files=[{"path": path, "integrity": GOOD_SRI, "type": "script"}]), "m"
    )
    assert assets == [] and ids(problems) == ["djust.B001"]
    assert repr(path) in problems[0].message


def test_dotted_file_names_are_still_valid_paths():
    assets, problems = parse_manifest(
        _doc(files=[{"path": "lib/..hidden/a.min.js", "integrity": GOOD_SRI}]), "m"
    )
    assert problems == [] and assets[0].files[0].path == "lib/..hidden/a.min.js"
