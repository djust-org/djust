# Vendored Assets and SBOMs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every third-party file djust, a component package, or an app serves to browsers is declared in a `djust_assets.json` manifest, loaded from the app's own origin by default, verified at startup, and published as a CycloneDX SBOM that Trivy/Syft/OSV-Scanner can read.

**Architecture:** A stdlib-only `djust.assets` package parses manifests into a process-wide registry (Django discovery layered on top), renders SRI-bearing tags, and emits deterministic CycloneDX. One `js/vendor/` npm project builds every djust-shipped bundle and regenerates djust's own manifests from esbuild metafiles. System checks `djust.B001`–`B014` turn every drift, leak and misconfiguration into an error or warning; CI gates advisories with OSV-Scanner.

**Tech Stack:** Python 3.10+, Django system checks and template tags, esbuild 0.28, Tailwind CLI 3.4, Node 22, CycloneDX 1.6 JSON, OSV-Scanner v2, Trivy, Syft, maturin ≥ 1.12.1, `cargo metadata`.

**Spec:** `docs/superpowers/specs/2026-09-25-vendored-assets-sbom-design.md`

## Global Constraints

- Target: the next 1.3 pre-release after `v1.3.0rc2` (already tagged). No 1.2.x backport.
- Manifest file name: `djust_assets.json`; `"schema": 1`.
- Settings (all top-level): `DJUST_ASSET_MANIFESTS` (default `[]`), `DJUST_ALLOW_EXTERNAL_ASSETS` (default `False`), `DJUST_SBOM_PATH` (default unset/`None`), `DJUST_SBOM_NAME` (default: first component of `ROOT_URLCONF`).
- Check IDs `djust.B001`–`djust.B014`, meanings exactly as the spec's table, with the B013 amendment below. Each ID is emitted from exactly one function (`test_check_id_uniqueness_2070.py`).
- `djust.assets.manifest` and `djust.assets.sbom` import nothing from Django at module level (`PYTHONPATH=python python -m djust.assets.sbom --distribution` must run with no Django settings and no Rust extension).
- CycloneDX output: `specVersion` `"1.6"`, no `serialNumber`, no `metadata.timestamp`, `json.dumps(..., indent=2, sort_keys=True) + "\n"`; byte-identical for identical inputs.
- purls must carry `@version`; scoped npm purls percent-encode `@` (`pkg:npm/%40tiptap/core@3.31.3`).
- No SBOM, and no served file, ever contains a version list: `*.LICENSE.txt` carries package names and license texts only.
- The app SBOM is never written inside `STATIC_ROOT`, `MEDIA_ROOT` or any `STATICFILES_DIRS` entry.
- Python tests: `.venv/bin/python -m pytest <path> -v` from the worktree root (never `uv run`; it re-locks against the local mirror).
- Commits: conventional prefixes; end every message with `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`. Each user-visible change adds a `changelog.d/<slug>.<section>.md` fragment (Task 13), never edits `CHANGELOG.md`.

### Amendments to the spec made while planning (Task 13 writes them into the spec)

1. **B013 is conditional.** It fires only when `DJUST_SBOM_PATH` is set. The demo project (and most apps) list `"djust"` after `"django.contrib.staticfiles"`; without an SBOM path the `collectstatic` override has nothing to do.
2. **Schema validation is replaced by scanner canaries.** The spec's "validate against the CycloneDX 1.6 JSON schema" needs the schema plus its SPDX/JSF sub-schemas vendored into tests. Parsing by the real consumers (Task 0 probe, Task 12 CI canary) is the stronger test.
3. **Nested vs flat is decided by Task 0.** If any of Trivy, Syft, OSV-Scanner ignores nested `components`, the generator emits packages top-level and records asset→package in `dependencies`.
4. **`vendor-check` uses `git diff` on a rebuilt tree**, like today's `markdown-editor.yml`, rather than a temp directory.
5. **highlight.js themes are a curated set** (Task 7 list), not all ~250; an unknown theme is a loud `ImproperlyConfigured` naming the available ones. (Wheel size: each release publishes ~20 platform wheels against the PyPI 10 GB project cap.)
6. **Modules render as `<link rel="modulepreload" integrity>`** plus `{% djust_asset_url %}` for the importing code, because `import()` cannot carry SRI.

## Review Focus

1. **Static files on another origin (S3/CloudFront `STATIC_URL`).** SRI on a cross-origin script requires `crossorigin="anonymous"` and a CORS header; without the attribute the browser blocks the file. Expect: vendored tags carry `crossorigin="anonymous"` whenever `STATIC_URL` is absolute, and the guide names the CORS header. Test: Task 4 `test_absolute_static_url_adds_crossorigin`.
2. **`{% code_block theme="dracula" %}`**, which worked when themes came from the CDN. Expect: `ImproperlyConfigured` listing the vendored themes, never an unhighlighted block with no explanation. Test: Task 8 `test_unknown_theme_fails_loudly`.
3. **An existing app upgrading with `"djust"` after `staticfiles` and no `DJUST_SBOM_PATH`.** Expect: `check` output unchanged, `check --deploy` adds only B011. Test: Task 6 `test_existing_app_without_sbom_path_gets_only_b011`.
4. **A Rust dependency bump (Cargo.lock) without regenerating `djust.cdx.json`.** Expect: the `vendor` workflow runs on `Cargo.lock` changes and fails naming `make vendor`. Test: Task 12 `test_vendor_workflow_triggers_on_every_input`.
5. **A scanner that reads our SBOM but finds zero packages** (nested components ignored, wrong flag). Expect: CI fails. Test: Task 12 canary step, which scans an SBOM containing `pkg:npm/lodash@4.17.20` (CVE-2021-23337) through the same generator and requires a non-zero exit.

---

### Task 0: Worktree environment and scanner compatibility probe

Decides amendment 3 before any generator code exists. Output is a recorded answer, not code to keep.

**Files:**
- Create (scratch, not committed): `$SCRATCH/probe/nested.cdx.json`, `$SCRATCH/probe/flat.cdx.json`
- Modify: `docs/superpowers/specs/2026-09-25-vendored-assets-sbom-design.md` (append a "Scanner probe results" section)

**Interfaces:**
- Produces: `SBOM_SHAPE` decision, `nested` or `flat`, recorded in the spec; Task 3 reads it.

- [ ] **Step 1: Give the worktree its own venv and Rust extension**

Run: `make worktree-env`
Expected: ends with a success line; `.venv/bin/python -c "import djust._rust"` exits 0.

- [ ] **Step 2: Install the three scanners**

Run: `brew install trivy syft osv-scanner && trivy --version && syft version && osv-scanner --version`
Expected: three version lines. Record them in the spec section.

- [ ] **Step 3: Write the two probe SBOMs**

`$SCRATCH` is the session scratchpad. `nested.cdx.json`:

```json
{
  "bomFormat": "CycloneDX",
  "specVersion": "1.6",
  "version": 1,
  "metadata": {"component": {"type": "library", "name": "probe", "bom-ref": "probe"}},
  "components": [
    {
      "type": "file",
      "name": "probe-bundle",
      "bom-ref": "asset:probe-bundle",
      "components": [
        {"type": "library", "name": "lodash", "version": "4.17.20",
         "purl": "pkg:npm/lodash@4.17.20", "bom-ref": "probe-bundle:pkg:npm/lodash@4.17.20",
         "licenses": [{"expression": "MIT"}]}
      ]
    }
  ]
}
```

`flat.cdx.json`: the same lodash object moved into top-level `components` beside `probe-bundle` (which then has no `components`), plus:

```json
"dependencies": [{"ref": "asset:probe-bundle", "dependsOn": ["probe-bundle:pkg:npm/lodash@4.17.20"]}]
```

- [ ] **Step 4: Scan both with every scanner, the way users will**

```bash
cd "$SCRATCH/probe"
for f in nested flat; do
  echo "== $f / trivy";  trivy sbom --quiet $f.cdx.json | grep -c CVE-2021-23337
  echo "== $f / trivy fs"; mkdir -p fs-$f && cp $f.cdx.json fs-$f/ && trivy fs --quiet fs-$f | grep -c CVE-2021-23337
  echo "== $f / syft+grype"; syft dir:fs-$f --select-catalogers +sbom-cataloger -o json | grep -c '"pkg:npm/lodash@4.17.20"'
  echo "== $f / osv"; osv-scanner scan source -L $f.cdx.json; echo "exit=$?"
done
```

Expected: each line prints a count ≥ 1 (osv: non-zero exit and CVE listed). If `osv-scanner scan source -L` rejects the file, run `osv-scanner scan source --help`, find the flag that accepts a CycloneDX file in the installed version, and record the exact working command.

- [ ] **Step 5: Record the decision**

Append to the spec:

```markdown
## Scanner probe results (Task 0)

| Scanner (version) | nested | flat |
|---|---|---|
| trivy sbom | … | … |
| trivy fs | … | … |
| syft +sbom-cataloger | … | … |
| osv-scanner (`<exact command>`) | … | … |

Decision: SBOM_SHAPE = nested   # or flat: any "no" in the nested column forces flat
```

- [ ] **Step 6: Commit**

```bash
git add docs/superpowers/specs/2026-09-25-vendored-assets-sbom-design.md
git commit -m "docs: record scanner probe results for the vendored-assets SBOM shape"
```

---

### Task 1: Manifest parsing (`djust.assets.manifest`)

**Files:**
- Create: `python/djust/assets/__init__.py`
- Create: `python/djust/assets/manifest.py`
- Test: `python/djust/tests/test_assets_manifest.py`

**Interfaces:**
- Produces:
  - `sri(data: bytes, alg: str = "sha384") -> str`
  - `Problem(check_id: str, source: str, message: str)` (frozen dataclass; `check_id` is `"djust.B001"`, `"djust.B002"` or `"djust.B006"`)
  - `AssetFile(integrity: str, type: str, path: str | None = None, url: str | None = None, variant: str | None = None)` with `.external -> bool`, `.location -> str`
  - `Package(purl: str, license: str)` with `.name -> str`, `.version -> str`
  - `Asset(name: str, source: str, files: tuple[AssetFile, ...], packages: tuple[Package, ...], license_file: str | None = None)` with `.external -> bool`, `.variants -> tuple[str, ...]`, `.files_for(variant: str | None = None) -> tuple[AssetFile, ...]`
  - `parse_manifest(data: object, source: str) -> tuple[list[Asset], list[Problem]]`
  - `load_manifest(path: Path) -> tuple[list[Asset], list[Problem]]`

- [ ] **Step 1: Write the failing tests**

```python
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


@pytest.mark.parametrize(
    "ext,expected", [(".js", "script"), (".mjs", "module"), (".css", "style")]
)
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest python/djust/tests/test_assets_manifest.py -v`
Expected: collection error, `ModuleNotFoundError: No module named 'djust.assets'`.

- [ ] **Step 3: Implement**

`python/djust/assets/__init__.py` (Task 2 and Task 4 extend it):

```python
"""Third-party browser assets: manifests, registry, tags and SBOMs (ADR-040)."""
```

`python/djust/assets/manifest.py`:

```python
"""Parse and validate ``djust_assets.json`` manifests (schema 1).

Stdlib only, with no Django import: ``python -m djust.assets.sbom
--distribution`` runs this in CI without settings or the Rust extension.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote

SCHEMA_VERSION = 1

_SRI = re.compile(r"^sha(256|384|512)-[A-Za-z0-9+/]+={0,2}$")
_PURL = re.compile(
    r"^pkg:(?P<type>[a-z][a-z0-9.+-]*)/(?P<name>[^@?#]+)@(?P<version>[^@?#/]+)(?:\?[^#]*)?$"
)
_TYPES_BY_EXT = {".js": "script", ".mjs": "module", ".css": "style"}
FILE_TYPES = frozenset(_TYPES_BY_EXT.values())


def sri(data: bytes, alg: str = "sha384") -> str:
    """The Subresource Integrity string for ``data``."""
    digest = hashlib.new(alg, data).digest()
    return f"{alg}-{base64.b64encode(digest).decode('ascii')}"


@dataclass(frozen=True)
class Problem:
    check_id: str
    source: str
    message: str


@dataclass(frozen=True)
class AssetFile:
    integrity: str
    type: str
    path: str | None = None
    url: str | None = None
    variant: str | None = None

    @property
    def external(self) -> bool:
        return self.url is not None

    @property
    def location(self) -> str:
        return self.url if self.url is not None else str(self.path)


@dataclass(frozen=True)
class Package:
    purl: str
    license: str

    def _part(self, group: str) -> str:
        match = _PURL.match(self.purl)
        assert match is not None, "Package is only built from a validated purl"
        return match[group]

    @property
    def name(self) -> str:
        return unquote(self._part("name"))

    @property
    def version(self) -> str:
        return self._part("version")


@dataclass(frozen=True)
class Asset:
    name: str
    source: str
    files: tuple[AssetFile, ...]
    packages: tuple[Package, ...]
    license_file: str | None = None

    @property
    def external(self) -> bool:
        return any(f.external for f in self.files)

    @property
    def variants(self) -> tuple[str, ...]:
        return tuple(sorted({f.variant for f in self.files if f.variant}))

    def files_for(self, variant: str | None = None) -> tuple[AssetFile, ...]:
        """The files one page needs: every file without a variant, plus the named variant's."""
        return tuple(f for f in self.files if f.variant is None or f.variant == variant)


Fail = Callable[..., None]


def parse_manifest(data: Any, source: str) -> tuple[list[Asset], list[Problem]]:
    """Every valid asset, and a problem for each invalid one. An invalid
    asset is dropped whole; its neighbours still load."""
    if not isinstance(data, dict) or data.get("schema") != SCHEMA_VERSION:
        return [], [Problem("djust.B001", source, f'"schema" must be {SCHEMA_VERSION}')]
    raw_assets = data.get("assets")
    if not isinstance(raw_assets, dict):
        return [], [Problem("djust.B001", source, '"assets" must be an object')]
    assets: list[Asset] = []
    problems: list[Problem] = []
    for name, raw in sorted(raw_assets.items()):
        asset, found = _parse_asset(name, raw, source)
        problems.extend(found)
        if asset is not None:
            assets.append(asset)
    return assets, problems


def load_manifest(path: Path) -> tuple[list[Asset], list[Problem]]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return [], [Problem("djust.B001", str(path), "manifest file not found")]
    except (OSError, ValueError) as exc:
        return [], [Problem("djust.B001", str(path), f"cannot be read as JSON: {exc}")]
    return parse_manifest(data, str(path))


def _parse_asset(name: str, raw: Any, source: str) -> tuple[Asset | None, list[Problem]]:
    problems: list[Problem] = []

    def fail(message: str, check_id: str = "djust.B001") -> None:
        problems.append(Problem(check_id, source, f"asset {name!r}: {message}"))

    if not isinstance(raw, dict):
        fail("must be an object")
        return None, problems

    files: list[AssetFile] = []
    raw_files = raw.get("files")
    if not isinstance(raw_files, list) or not raw_files:
        fail('"files" must be a non-empty list')
    else:
        for index, raw_file in enumerate(raw_files):
            parsed = _parse_file(raw_file, f"files[{index}]", fail)
            if parsed is not None:
                files.append(parsed)

    packages: list[Package] = []
    raw_packages = raw.get("packages")
    if not isinstance(raw_packages, list) or not raw_packages:
        fail('"packages" must be a non-empty list')
    else:
        for index, raw_package in enumerate(raw_packages):
            parsed_package = _parse_package(raw_package, f"packages[{index}]", fail)
            if parsed_package is not None:
                packages.append(parsed_package)

    license_file = raw.get("license_file")
    if license_file is not None and not (isinstance(license_file, str) and license_file):
        fail('"license_file" must be a non-empty string')

    if problems:
        return None, problems
    return Asset(name, source, tuple(files), tuple(packages), license_file), []


def _parse_file(raw: Any, where: str, fail: Fail) -> AssetFile | None:
    if not isinstance(raw, dict):
        fail(f"{where} must be an object")
        return None
    path, url = raw.get("path"), raw.get("url")
    if (path is None) == (url is None):
        fail(f'{where} needs exactly one of "path" or "url"')
        return None
    location = path if path is not None else url
    if not isinstance(location, str) or not location:
        fail(f"{where}: path/url must be a non-empty string")
        return None
    if url is not None and not url.startswith("https://"):
        fail(f"{where}: external url {url!r} must use https://")
        return None
    integrity = raw.get("integrity")
    if integrity is None:
        fail(f"{where} ({location}) has no integrity", "djust.B006" if url else "djust.B001")
        return None
    if not isinstance(integrity, str) or not _SRI.match(integrity):
        fail(f"{where}: integrity {integrity!r} is not sha256-/sha384-/sha512- SRI")
        return None
    file_type = raw.get("type") or _TYPES_BY_EXT.get(Path(location.split("?")[0]).suffix)
    if file_type not in FILE_TYPES:
        fail(f'{where}: "type" must be one of {sorted(FILE_TYPES)} (cannot infer from {location!r})')
        return None
    variant = raw.get("variant")
    if variant is not None and not (isinstance(variant, str) and variant):
        fail(f'{where}: "variant" must be a non-empty string')
        return None
    return AssetFile(integrity=integrity, type=file_type, path=path, url=url, variant=variant)


def _parse_package(raw: Any, where: str, fail: Fail) -> Package | None:
    if not isinstance(raw, dict):
        fail(f"{where} must be an object")
        return None
    purl, license_ = raw.get("purl"), raw.get("license")
    if not isinstance(purl, str) or not _PURL.match(purl):
        fail(f"{where}: {purl!r} is not a package URL with an exact @version", "djust.B002")
        return None
    if not isinstance(license_, str) or not license_.strip():
        fail(f"{where} ({purl}): license must be an SPDX expression", "djust.B002")
        return None
    return Package(purl, license_)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest python/djust/tests/test_assets_manifest.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add python/djust/assets/__init__.py python/djust/assets/manifest.py python/djust/tests/test_assets_manifest.py
git commit -m "feat(assets): parse and validate djust_assets.json manifests"
```

---

### Task 2: Registry and discovery (`djust.assets.registry`)

**Files:**
- Create: `python/djust/assets/registry.py`
- Modify: `python/djust/assets/__init__.py`
- Modify: `python/djust/apps.py` (in `DjustConfig.ready()`, after `import djust.checks`)
- Create: `python/djust/tests/_asset_fixtures.py`
- Test: `python/djust/tests/test_assets_registry.py`

**Interfaces:**
- Consumes: `load_manifest`, `Asset`, `Problem` (Task 1).
- Produces:
  - `MANIFEST_NAME = "djust_assets.json"`
  - `Shadow(name: str, winner: Asset, loser: Asset)`
  - `Registry(assets: dict[str, Asset], shadows: list[Shadow], problems: list[Problem])` with `.digest() -> str` (64-char hex)
  - `build_registry(manifest_paths: Iterable[Path]) -> Registry` (pure; first declaration wins)
  - `manifest_paths() -> list[Path]` (Django: `DJUST_ASSET_MANIFESTS` in order, then each installed app's `djust_assets.json` in `INSTALLED_APPS` order)
  - `get_registry() -> Registry` (cached), `reset_registry() -> None`
  - `djust.assets.on_setting_changed(*, setting: str, **kwargs) -> None` (resets the registry and, from Task 4, the integrity cache)
  - Test helper `write_asset(tmp_path, name="test-lib", rel="testlib/lib.js", content=b"console.log(1);\n", version="1.0.0", **file_extra) -> tuple[Path, Path]` returning `(static_dir, manifest_path)`

- [ ] **Step 1: Write the fixture helper**

`python/djust/tests/_asset_fixtures.py`:

```python
"""Build a throwaway vendored asset: a static file plus a manifest declaring it."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from djust.assets.manifest import sri


def write_asset(
    tmp_path: Path,
    name: str = "test-lib",
    rel: str = "testlib/lib.js",
    content: bytes = b"console.log(1);\n",
    version: str = "1.0.0",
    manifest_name: str = "djust_assets.json",
    **file_extra: Any,
) -> tuple[Path, Path]:
    static_dir = tmp_path / "static"
    target = static_dir / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    entry = {"path": rel, "integrity": sri(content), **file_extra}
    manifest = tmp_path / manifest_name
    manifest.write_text(
        json.dumps(
            {
                "schema": 1,
                "assets": {
                    name: {
                        "files": [entry],
                        "packages": [{"purl": f"pkg:npm/{name}@{version}", "license": "MIT"}],
                    }
                },
            }
        )
    )
    return static_dir, manifest
```

- [ ] **Step 2: Write the failing tests**

`python/djust/tests/test_assets_registry.py`:

```python
from __future__ import annotations

from django.test import override_settings

from djust.assets.registry import build_registry, get_registry
from djust.tests._asset_fixtures import write_asset


def test_first_declaration_wins_and_the_other_is_recorded(tmp_path):
    _, first = write_asset(tmp_path / "a", version="2.0.0")
    _, second = write_asset(tmp_path / "b", version="1.0.0")
    reg = build_registry([first, second])
    assert reg.assets["test-lib"].packages[0].version == "2.0.0"
    (shadow,) = reg.shadows
    assert shadow.winner.source == str(first)
    assert shadow.loser.packages[0].version == "1.0.0"


def test_problems_are_collected_not_raised(tmp_path):
    reg = build_registry([tmp_path / "missing.json"])
    assert reg.assets == {}
    assert [p.check_id for p in reg.problems] == ["djust.B001"]


def test_digest_is_stable_and_ignores_source_path(tmp_path):
    _, a = write_asset(tmp_path / "a")
    _, b = write_asset(tmp_path / "b")
    assert build_registry([a]).digest() == build_registry([b]).digest()
    _, c = write_asset(tmp_path / "c", version="9.9.9")
    assert build_registry([a]).digest() != build_registry([c]).digest()
    assert len(build_registry([a]).digest()) == 64


def test_project_manifests_come_first_and_reset_on_setting_change(tmp_path):
    _, manifest = write_asset(tmp_path)
    with override_settings(DJUST_ASSET_MANIFESTS=[str(manifest)]):
        reg = get_registry()
        assert reg.assets["test-lib"].source == str(manifest)
    assert "test-lib" not in get_registry().assets
```

- [ ] **Step 3: Run them to verify they fail**

Run: `.venv/bin/python -m pytest python/djust/tests/test_assets_registry.py -v`
Expected: `ModuleNotFoundError: No module named 'djust.assets.registry'`.

- [ ] **Step 4: Implement**

`python/djust/assets/registry.py`:

```python
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
```

Append to `python/djust/assets/__init__.py`:

```python

RESET_ON = frozenset(
    {
        "DJUST_ASSET_MANIFESTS",
        "INSTALLED_APPS",
        "STATICFILES_DIRS",
        "STATIC_ROOT",
        "STATIC_URL",
        "STORAGES",
        "STATICFILES_STORAGE",
    }
)


def on_setting_changed(*, setting: str, **kwargs: object) -> None:
    """``setting_changed`` receiver: drop cached state derived from settings."""
    if setting not in RESET_ON:
        return
    from .registry import reset_registry

    reset_registry()
```

In `python/djust/apps.py`, directly after `import djust.checks  # noqa: F401`:

```python
        # Asset registry and integrity caches follow override_settings (ADR-040).
        from django.core.signals import setting_changed

        from djust.assets import on_setting_changed

        setting_changed.connect(on_setting_changed, dispatch_uid="djust.assets")
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest python/djust/tests/test_assets_registry.py python/djust/tests/test_assets_manifest.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add python/djust/assets python/djust/apps.py python/djust/tests/_asset_fixtures.py python/djust/tests/test_assets_registry.py
git commit -m "feat(assets): registry with Django-order resolution and shadow records"
```

---

### Task 3: CycloneDX generator and distribution SBOM (`djust.assets.sbom`)

**Files:**
- Create: `python/djust/assets/sbom.py`
- Test: `python/djust/tests/test_assets_sbom.py`
- Create: `python/djust/tests/fixtures/assets_sbom_golden.cdx.json` (generated in Step 4, then reviewed)

**Interfaces:**
- Consumes: `Asset`, `Registry`, `build_registry` (Tasks 1–2); `SBOM_SHAPE` (Task 0).
- Produces:
  - `to_cyclonedx(assets: Iterable[Asset], *, root_name: str, root_version: str | None, digest: str, extra_components: Sequence[dict] = ()) -> dict`
  - `dumps(document: dict) -> str`
  - `digest_of(document: dict) -> str | None` (reads the `djust:manifest-digest` property)
  - `rust_components(manifest_path: Path) -> list[dict]`
  - `write_distribution_sbom(repo_root: Path) -> Path` and `main(argv: list[str] | None = None) -> int` (`python -m djust.assets.sbom --distribution`)
  - `DISTRIBUTION_MANIFESTS = ("djust/components/djust_assets.json", "djust/admin_ext/djust_assets.json")` relative to `python/`

- [ ] **Step 1: Write the failing tests**

```python
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
    assert not any(p.startswith("pkg:cargo/djust") for p in purls)  # workspace crates are djust itself
    assert any(p.startswith("pkg:cargo/pyo3@") for p in purls)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest python/djust/tests/test_assets_sbom.py -v`
Expected: `ModuleNotFoundError: No module named 'djust.assets.sbom'`.

- [ ] **Step 3: Implement**

The nested form is shown. If Task 0 recorded `SBOM_SHAPE = flat`, `_asset_component` returns the asset without `components`, `to_cyclonedx` appends the package components to the top-level list, and adds `{"ref": asset_ref, "dependsOn": [package refs]}` to a top-level `dependencies` array. Everything else is unchanged.

```python
"""CycloneDX 1.6 SBOMs from the asset registry (ADR-040).

One generator for every output: the distribution file committed as
``python/djust/djust.cdx.json`` (``python -m djust.assets.sbom
--distribution``, no Django needed), the app file ``collectstatic`` writes,
and ``manage.py djust_sbom``. Output is byte-identical for identical inputs:
no serialNumber, no timestamp, sorted keys and arrays; ``make vendor-check``
depends on that.
"""

from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
import tomllib
from collections import deque
from pathlib import Path
from typing import Iterable, Sequence

from .manifest import Asset
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


def _asset_component(asset: Asset) -> dict:
    ref = f"asset:{asset.name}"
    component: dict = {
        "type": "file",
        "name": asset.name,
        "bom-ref": ref,
        "evidence": {"occurrences": [{"location": f.location} for f in asset.files]},
        "components": sorted(
            (
                {
                    "type": "library",
                    "name": package.name,
                    "version": package.version,
                    "purl": package.purl,
                    "bom-ref": f"{ref}:{package.purl}",
                    "licenses": [{"expression": package.license}],
                }
                for package in asset.packages
            ),
            key=lambda c: c["purl"],
        ),
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
    components = [_asset_component(a) for a in sorted(assets, key=lambda a: a.name)]
    components.extend(sorted(extra_components, key=lambda c: c["purl"]))
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
    }


def dumps(document: dict) -> str:
    return json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def digest_of(document: dict) -> str | None:
    for prop in document.get("metadata", {}).get("properties", []):
        if prop.get("name") == DIGEST_PROPERTY:
            return prop.get("value")
    return None


def rust_components(manifest_path: Path) -> list[dict]:
    """Registry crates linked into the extension: normal dependencies
    reachable from the bindings crate. Workspace crates (no ``source``) are
    djust itself and are left out."""
    metadata = json.loads(
        subprocess.run(
            ["cargo", "metadata", "--format-version", "1", "--locked",
             "--manifest-path", str(manifest_path)],
            check=True, capture_output=True, text=True,
        ).stdout
    )
    packages = {p["id"]: p for p in metadata["packages"]}
    nodes = {n["id"]: n for n in metadata["resolve"]["nodes"]}
    root = metadata["resolve"]["root"]  # the --manifest-path package; avoids /tmp vs /private/tmp path matching
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


def write_distribution_sbom(repo_root: Path) -> Path:
    python_dir = repo_root / "python"
    registry = build_registry(python_dir / rel for rel in DISTRIBUTION_MANIFESTS)
    if registry.problems:
        raise SystemExit("\n".join(f"{p.check_id} {p.source}: {p.message}" for p in registry.problems))
    version = tomllib.loads((repo_root / "pyproject.toml").read_text())["project"]["version"]
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m djust.assets.sbom")
    parser.add_argument("--distribution", action="store_true", required=True,
                        help="write python/djust/djust.cdx.json for the djust wheel")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    print(write_distribution_sbom(args.repo_root.resolve()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

`tomllib` is 3.11+; djust supports 3.10. Replace the import with:

```python
try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]
```

and confirm `tomli` is already in `uv.lock` (`grep -n '^name = "tomli"' uv.lock`). If it is absent, read the version with `re.search(r'^version = "([^"]+)"', text, re.M)` instead and drop the import.

- [ ] **Step 4: Generate the golden file, then review it by eye**

```bash
.venv/bin/python -c "
from pathlib import Path
import sys; sys.path.insert(0, 'python/djust/tests')
from test_assets_sbom import _doc
from djust.assets.sbom import dumps
Path('python/djust/tests/fixtures/assets_sbom_golden.cdx.json').write_text(dumps(_doc()))
"
```

Open the file. Check that it has two asset components, the nested (or flat) packages, `hashes` with `alg: SHA-384`, the external marker on `chart.js`, and no timestamp. The golden file freezes the format, so review it before committing.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest python/djust/tests/test_assets_sbom.py -v`
Expected: all PASS (`test_rust_components_are_registry_crates_only` needs `cargo` on PATH; see memory "Rust toolchain PATH for builds").

- [ ] **Step 6: Commit**

```bash
git add python/djust/assets/sbom.py python/djust/tests/test_assets_sbom.py python/djust/tests/fixtures/assets_sbom_golden.cdx.json
git commit -m "feat(assets): deterministic CycloneDX generator with Rust crates"
```

---

### Task 4: Tags, template library, and `Component.requires_assets`

**Files:**
- Create: `python/djust/assets/tags.py`
- Create: `python/djust/templatetags/djust_assets.py`
- Modify: `python/djust/assets/__init__.py` (`on_setting_changed` also clears the integrity cache)
- Modify: `python/djust/components/base.py:215` (class attribute on `Component`)
- Test: `python/djust/tests/test_assets_tags.py`
- Test: `python/tests/test_djust_asset_tag_rust_engine.py`

**Interfaces:**
- Consumes: `get_registry` (Task 2), `sri`, `Asset`, `AssetFile` (Task 1).
- Produces:
  - `get_asset(name: str) -> Asset` (raises `ImproperlyConfigured` listing declared names)
  - `asset_tags(name: str, variant: str | None = None) -> SafeString`
  - `asset_url(name: str, variant: str | None = None, file_type: str = "module") -> str`
  - `stored_integrity(path: str, alg: str) -> str`, `clear_integrity_cache() -> None`
  - Template tags `{% load djust_assets %}`, `{% djust_asset "name" [variant] %}`, `{% djust_asset_url "name" [variant] [file_type] %}`
  - `Component.requires_assets: ClassVar[tuple[str, ...]] = ()` and `Component.asset_tags(self, variant: str | None = None) -> SafeString`

- [ ] **Step 1: Write the failing tests**

`python/djust/tests/test_assets_tags.py`:

```python
from __future__ import annotations

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.template import Context, Template
from django.test import override_settings

from djust.assets.manifest import sri
from djust.assets.tags import asset_tags, asset_url
from djust.tests._asset_fixtures import write_asset

CONTENT = b"console.log(1);\n"


def _settings(tmp_path, **extra):
    static_dir, manifest = write_asset(tmp_path, content=CONTENT, **extra)
    return override_settings(
        DJUST_ASSET_MANIFESTS=[str(manifest)],
        STATICFILES_DIRS=[str(static_dir)],
        STATIC_URL="/static/",
    )


def test_vendored_script_gets_integrity_from_the_file(tmp_path):
    with _settings(tmp_path):
        html = asset_tags("test-lib")
    assert html == (
        f'<script src="/static/testlib/lib.js" integrity="{sri(CONTENT)}"></script>'
    )


def test_absolute_static_url_adds_crossorigin(tmp_path):
    with _settings(tmp_path), override_settings(STATIC_URL="https://static.example.com/"):
        html = asset_tags("test-lib")
    assert 'crossorigin="anonymous"' in html
    assert 'src="https://static.example.com/testlib/lib.js"' in html


def test_module_renders_modulepreload_and_url(tmp_path):
    with _settings(tmp_path, rel="testlib/lib.mjs"):
        assert asset_tags("test-lib").startswith('<link rel="modulepreload" href="/static/testlib/lib.mjs"')
        assert asset_url("test-lib") == "/static/testlib/lib.mjs"


def test_style_renders_stylesheet_link(tmp_path):
    with _settings(tmp_path, rel="testlib/lib.css"):
        assert asset_tags("test-lib").startswith('<link rel="stylesheet" href="/static/testlib/lib.css"')


def test_unknown_asset_and_variant_fail_loudly(tmp_path):
    with _settings(tmp_path):
        with pytest.raises(ImproperlyConfigured, match="test-lib"):
            asset_tags("nope")
        with pytest.raises(ImproperlyConfigured, match="no variant 'dark'"):
            asset_tags("test-lib", "dark")


def test_integrity_follows_the_stored_file_not_the_source(tmp_path):
    """ManifestStaticFilesStorage rewrites url() in CSS; SRI must hash what is served."""
    css = b'.a{background:url("img.png")}\n'
    static_dir, manifest = write_asset(tmp_path, rel="testlib/lib.css", content=css)
    (static_dir / "testlib" / "img.png").write_bytes(b"png")
    root = tmp_path / "collected"
    storages = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.ManifestStaticFilesStorage"},
    }
    with override_settings(
        DJUST_ASSET_MANIFESTS=[str(manifest)],
        STATICFILES_DIRS=[str(static_dir)],
        STATIC_ROOT=str(root),
        STORAGES=storages,
    ):
        from django.core.management import call_command

        call_command("collectstatic", interactive=False, verbosity=0)
        served = next(root.glob("testlib/lib.*.css")).read_bytes()
        assert served != css
        html = asset_tags("test-lib")
    assert f'integrity="{sri(served)}"' in html


def test_template_tag(tmp_path):
    with _settings(tmp_path):
        out = Template('{% load djust_assets %}{% djust_asset "test-lib" %}').render(Context())
    assert out.startswith('<script src="/static/testlib/lib.js" integrity="sha384-')


def test_component_requires_assets_renders_its_tags(tmp_path):
    from djust.components.base import Component

    class Uses(Component):
        requires_assets = ("test-lib",)
        template = "<div></div>"

    with _settings(tmp_path):
        assert "testlib/lib.js" in Uses().asset_tags()
```

(If `Component` needs a different minimal definition to instantiate, copy the smallest one from `python/djust/tests/test_component_*` and keep the `requires_assets` line.)

`python/tests/test_djust_asset_tag_rust_engine.py`:

```python
"""{% djust_asset %} renders through the Rust engine's library bridge (#2547)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip("django")

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from django.test import override_settings  # noqa: E402

from djust.tests._asset_fixtures import write_asset  # noqa: E402
from test_load_imports_django_libraries_2547 import liveview_render  # noqa: E402


def test_djust_asset_in_a_liveview_template(tmp_path):
    static_dir, manifest = write_asset(tmp_path)
    with override_settings(DJUST_ASSET_MANIFESTS=[str(manifest)], STATICFILES_DIRS=[str(static_dir)]):
        out = liveview_render('<div>{% load djust_assets %}{% djust_asset "test-lib" %}</div>', {})
    assert '<script src="/static/testlib/lib.js" integrity="sha384-' in out
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest python/djust/tests/test_assets_tags.py python/tests/test_djust_asset_tag_rust_engine.py -v`
Expected: `ModuleNotFoundError: No module named 'djust.assets.tags'`.

- [ ] **Step 3: Implement `python/djust/assets/tags.py`**

```python
"""Render declared assets as tags carrying Subresource Integrity (ADR-040)."""

from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured
from django.utils.html import format_html
from django.utils.safestring import SafeString, mark_safe

from .manifest import Asset, AssetFile, sri
from .registry import get_registry

_integrity_cache: dict[tuple[str, str], str] = {}


def clear_integrity_cache() -> None:
    _integrity_cache.clear()


def get_asset(name: str) -> Asset:
    assets = get_registry().assets
    try:
        return assets[name]
    except KeyError:
        declared = ", ".join(sorted(assets)) or "none"
        raise ImproperlyConfigured(
            f"No djust asset named {name!r} is declared (declared: {declared}). "
            "Declare it in a djust_assets.json manifest."
        ) from None


def _read_static(path: str) -> bytes:
    """The bytes the app serves for ``path``: the stored (post-processed)
    file when static storage has it, else the source a finder locates
    (runserver before collectstatic)."""
    from django.contrib.staticfiles import finders
    from django.contrib.staticfiles.storage import staticfiles_storage

    stored = path
    stored_name = getattr(staticfiles_storage, "stored_name", None)
    if stored_name is not None:
        try:
            stored = stored_name(path)
        except ValueError:  # not in the manifest yet (not collected)
            stored = path
    try:
        with staticfiles_storage.open(stored) as handle:
            return handle.read()
    except Exception:  # noqa: BLE001 - any backend's "not stored here"; fall back to the source
        found = finders.find(path)
        if not found:
            raise ImproperlyConfigured(
                f"djust asset file {path!r} is in neither static storage nor any "
                "staticfiles finder (see check djust.B003)."
            ) from None
        with open(found, "rb") as handle:
            return handle.read()


def stored_integrity(path: str, alg: str) -> str:
    key = (path, alg)
    if key not in _integrity_cache:
        _integrity_cache[key] = sri(_read_static(path), alg)
    return _integrity_cache[key]


def _static_is_cross_origin() -> bool:
    from django.conf import settings

    return str(settings.STATIC_URL or "").startswith(("http://", "https://", "//"))


def _source(file: AssetFile) -> tuple[str, str, bool]:
    if file.url is not None:
        return file.url, file.integrity, True
    from django.templatetags.static import static

    alg = file.integrity.split("-", 1)[0]
    return static(file.path), stored_integrity(str(file.path), alg), _static_is_cross_origin()


def _tag(file: AssetFile) -> SafeString:
    src, integrity, cross_origin = _source(file)
    cors = mark_safe(' crossorigin="anonymous"') if cross_origin else ""  # constant markup
    if file.type == "style":
        return format_html('<link rel="stylesheet" href="{}" integrity="{}"{}>', src, integrity, cors)
    if file.type == "module":
        return format_html('<link rel="modulepreload" href="{}" integrity="{}"{}>', src, integrity, cors)
    return format_html('<script src="{}" integrity="{}"{}></script>', src, integrity, cors)


def _files(name: str, variant: str | None) -> tuple[AssetFile, ...]:
    asset = get_asset(name)
    if variant is not None and variant not in asset.variants:
        available = ", ".join(asset.variants) or "none"
        raise ImproperlyConfigured(
            f"djust asset {name!r} has no variant {variant!r} (available: {available})."
        )
    return asset.files_for(variant)


def asset_tags(name: str, variant: str | None = None) -> SafeString:
    # Every element is format_html output, so joining them is safe.
    return mark_safe("\n".join(_tag(f) for f in _files(name, variant)))


def asset_url(name: str, variant: str | None = None, file_type: str = "module") -> str:
    for file in _files(name, variant):
        if file.type == file_type:
            return _source(file)[0]
    raise ImproperlyConfigured(f"djust asset {name!r} has no {file_type} file.")
```

Extend `on_setting_changed` in `python/djust/assets/__init__.py`:

```python
    from .registry import reset_registry
    from .tags import clear_integrity_cache

    reset_registry()
    clear_integrity_cache()
```

`python/djust/templatetags/djust_assets.py`:

```python
"""``{% load djust_assets %}``: tags for declared third-party assets (ADR-040)."""

from __future__ import annotations

from django import template
from django.utils.safestring import SafeString

from djust.assets.tags import asset_tags, asset_url

register = template.Library()


@register.simple_tag
def djust_asset(name: str, variant: str | None = None) -> SafeString:
    """Script/stylesheet/modulepreload tags, with integrity, for asset ``name``."""
    return asset_tags(name, variant)


@register.simple_tag
def djust_asset_url(name: str, variant: str | None = None, file_type: str = "module") -> str:
    """The URL of the asset's first ``file_type`` file, for code that imports it."""
    return asset_url(name, variant, file_type)
```

In `python/djust/components/base.py`, inside `class Component` (next to its other class attributes), add:

```python
    #: Names of declared third-party assets this component needs (ADR-040).
    #: Check djust.B007 fails when a name is not declared in any manifest.
    requires_assets: ClassVar[tuple[str, ...]] = ()

    def asset_tags(self, variant: str | None = None) -> SafeString:
        """Tags for every asset in ``requires_assets``, for the component's template."""
        from djust.assets.tags import asset_tags

        return mark_safe("\n".join(asset_tags(name, variant) for name in self.requires_assets))
```

Import `ClassVar` from `typing`, and `SafeString`/`mark_safe` from `django.utils.safestring`, if the module does not already.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest python/djust/tests/test_assets_tags.py python/tests/test_djust_asset_tag_rust_engine.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add python/djust/assets python/djust/templatetags/djust_assets.py python/djust/components/base.py python/djust/tests/test_assets_tags.py python/tests/test_djust_asset_tag_rust_engine.py
git commit -m "feat(assets): SRI tags, djust_assets template library, Component.requires_assets"
```

---

### Task 5: Manifest and file checks B001–B010

**Files:**
- Create: `python/djust/checks/assets.py`
- Modify: `python/djust/checks/__init__.py` (import `assets` in both lists; add `- Assets (B0xx) -- vendored third-party files and SBOMs` to the docstring)
- Test: `python/djust/tests/test_checks_assets.py`

**Interfaces:**
- Consumes: `get_registry`, `Registry`, `sri`, `Component`, `_walk_subclasses`, `_get_template_dirs`, `_iter_template_files`, `_is_check_suppressed` (from `.utils`).
- Produces: `@register("djust")` functions `check_asset_manifests` (B001, B002, B006, B009), `check_asset_files` (B003, B004, B005), `check_required_assets` (B007), `check_sbom_not_served` (B008), `check_undeclared_origins` (B010).

- [ ] **Step 1: Write the failing tests**

```python
from __future__ import annotations

import json

from django.test import override_settings

from djust.checks.assets import (
    check_asset_files,
    check_asset_manifests,
    check_required_assets,
    check_sbom_not_served,
    check_undeclared_origins,
)
from djust.tests._asset_fixtures import write_asset


def ids(messages):
    return sorted(m.id for m in messages)


def _with(tmp_path, *manifests, static_dirs=(), **extra):
    return override_settings(
        DJUST_ASSET_MANIFESTS=[str(m) for m in manifests],
        STATICFILES_DIRS=[str(d) for d in static_dirs],
        **extra,
    )


def test_clean_project_has_no_asset_messages(tmp_path):
    static_dir, manifest = write_asset(tmp_path)
    with _with(tmp_path, manifest, static_dirs=[static_dir]):
        found = check_asset_manifests(None) + check_asset_files(None)
    assert [m for m in found if "test-lib" in m.msg] == []


def test_b001_b002_b006_come_from_parse_problems(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"schema": 1, "assets": {
        "a": {"files": [], "packages": []},
        "b": {"files": [{"path": "b.js", "integrity": "sha384-AAAA"}], "packages": [{"purl": "pkg:npm/b", "license": "MIT"}]},
        "c": {"files": [{"url": "https://x.example/c@1.0.0/c.js"}], "packages": [{"purl": "pkg:npm/c@1.0.0", "license": "MIT"}]},
    }}))
    with _with(tmp_path, bad):
        found = [m for m in check_asset_manifests(None) if str(bad) in m.msg]
    assert set(ids(found)) == {"djust.B001", "djust.B002", "djust.B006"}


def test_b003_missing_file_and_b004_hash_mismatch(tmp_path):
    static_dir, manifest = write_asset(tmp_path)
    with _with(tmp_path, manifest):  # no STATICFILES_DIRS: the file can't be found
        assert "djust.B003" in ids(check_asset_files(None))
    (static_dir / "testlib" / "lib.js").write_bytes(b"tampered\n")
    with _with(tmp_path, manifest, static_dirs=[static_dir]):
        (b004,) = [m for m in check_asset_files(None) if m.id == "djust.B004"]
    assert "testlib/lib.js" in b004.msg and "make vendor" in b004.hint


def test_b005_external_needs_opt_in(tmp_path):
    manifest = tmp_path / "ext.json"
    manifest.write_text(json.dumps({"schema": 1, "assets": {"cdn-lib": {
        "files": [{"url": "https://cdn.example/lib@1.0.0/lib.js", "integrity": "sha384-" + "A" * 64}],
        "packages": [{"purl": "pkg:npm/lib@1.0.0", "license": "MIT"}]}}}))
    with _with(tmp_path, manifest):
        assert "djust.B005" in ids(check_asset_files(None))
    with _with(tmp_path, manifest, DJUST_ALLOW_EXTERNAL_ASSETS=True):
        assert "djust.B005" not in ids(check_asset_files(None))


def test_b007_undeclared_required_asset(tmp_path):
    from djust.components.base import Component

    class NeedsGhost(Component):
        requires_assets = ("ghost-lib",)
        template = "<div></div>"

    (b007,) = [m for m in check_required_assets(None) if "ghost-lib" in m.msg]
    assert b007.id == "djust.B007" and "NeedsGhost" in b007.msg


def test_b008_sbom_in_static_dirs(tmp_path):
    static_dir = tmp_path / "static"
    (static_dir / "pkg").mkdir(parents=True)
    (static_dir / "pkg" / "vendor.cdx.json").write_text("{}")
    with override_settings(STATICFILES_DIRS=[str(static_dir)]):
        (b008,) = [m for m in check_sbom_not_served(None) if "vendor.cdx.json" in m.msg]
    assert b008.id == "djust.B008"


def test_b009_shadowing_names_both_versions(tmp_path):
    s1, first = write_asset(tmp_path / "a", version="2.0.0")
    _, second = write_asset(tmp_path / "b", version="1.0.0")
    with _with(tmp_path, first, second, static_dirs=[s1]):
        (b009,) = [m for m in check_asset_manifests(None) if m.id == "djust.B009"]
    assert "2.0.0" in b009.msg and "1.0.0" in b009.msg


def test_b010_undeclared_cdn_in_project_template(tmp_path):
    tpl = tmp_path / "templates"
    tpl.mkdir()
    (tpl / "page.html").write_text(
        '<script src="https://cdn.other.example/x.js"></script>\n'
        '<script src="https://cdn.ok.example/y.js"></script> {# noqa: B010 #}\n'
    )
    templates = [{"BACKEND": "django.template.backends.django.DjangoTemplates", "DIRS": [str(tpl)], "APP_DIRS": False}]
    with override_settings(TEMPLATES=templates):
        found = check_undeclared_origins(None)
    assert [m.id for m in found] == ["djust.B010"]
    assert "cdn.other.example" in found[0].msg and "page.html:1" in found[0].msg
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest python/djust/tests/test_checks_assets.py -v`
Expected: `ModuleNotFoundError: No module named 'djust.checks.assets'`.

- [ ] **Step 3: Implement `python/djust/checks/assets.py`**

```python
"""Vendored third-party asset checks, ``djust.B001``-``B010`` (ADR-040).

B011-B014 (the app SBOM) live in ``checks/sbom.py``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from django.conf import settings
from django.core.checks import CheckMessage, Error, Warning, register

from .utils import _get_template_dirs, _is_check_suppressed, _iter_template_files, _walk_subclasses

_SBOM_SUFFIXES = (".cdx.json", ".spdx.json", ".bom.json")
_EXTERNAL_REF = re.compile(
    r"<(?:script|link)\b[^>]*?\b(?:src|href)\s*=\s*[\"']((?:https?:)?//[^\"'/]+)", re.I
)
_REBUILD = "Rebuild with `make vendor` (djust) or regenerate your manifest's integrity."


@register("djust")
def check_asset_manifests(app_configs: Any, **kwargs: Any) -> list[CheckMessage]:
    from djust.assets.registry import get_registry

    registry = get_registry()
    messages: list[CheckMessage] = []
    for problem in registry.problems:
        text = f"{problem.source}: {problem.message}"
        if problem.check_id == "djust.B002":
            messages.append(Error(text, hint="Pin an exact version and an SPDX license.", id="djust.B002"))
        elif problem.check_id == "djust.B006":
            messages.append(Error(text, hint="Add the file's SRI hash as \"integrity\".", id="djust.B006"))
        else:
            messages.append(Error(text, hint="See the Vendoring third-party JS guide.", id="djust.B001"))
    if not _is_check_suppressed("B009"):
        for shadow in registry.shadows:
            won = ", ".join(p.purl for p in shadow.winner.packages)
            lost = ", ".join(p.purl for p in shadow.loser.packages)
            messages.append(
                Warning(
                    f"Asset {shadow.name!r} from {shadow.winner.source} ({won}) overrides "
                    f"{shadow.loser.source} ({lost}).",
                    hint="Intended overrides can be silenced with suppress_checks: ['B009'].",
                    id="djust.B009",
                )
            )
    return messages


@register("djust")
def check_asset_files(app_configs: Any, **kwargs: Any) -> list[CheckMessage]:
    from django.contrib.staticfiles import finders

    from djust.assets.manifest import sri
    from djust.assets.registry import get_registry

    allow_external = getattr(settings, "DJUST_ALLOW_EXTERNAL_ASSETS", False)
    messages: list[CheckMessage] = []
    for asset in get_registry().assets.values():
        if asset.external and not allow_external:
            messages.append(
                Error(
                    f"Asset {asset.name!r} ({asset.source}) loads from an external origin.",
                    hint="Vendor the file, or set DJUST_ALLOW_EXTERNAL_ASSETS = True.",
                    id="djust.B005",
                )
            )
        for file in asset.files:
            if file.path is None:
                continue
            found = finders.find(file.path)
            if not found:
                messages.append(
                    Error(
                        f"Asset {asset.name!r}: {file.path!r} is not found by any staticfiles finder.",
                        hint="Check the path and that the owning app or STATICFILES_DIRS is configured.",
                        id="djust.B003",
                    )
                )
                continue
            alg = file.integrity.split("-", 1)[0]
            with open(found, "rb") as handle:
                actual = sri(handle.read(), alg)
            if actual != file.integrity:
                messages.append(
                    Error(
                        f"Asset {asset.name!r}: {file.path!r} does not match its manifest "
                        f"integrity ({asset.source}). The file was edited or replaced "
                        "without updating the declared versions.",
                        hint=_REBUILD,
                        id="djust.B004",
                    )
                )
    return messages


@register("djust")
def check_required_assets(app_configs: Any, **kwargs: Any) -> list[CheckMessage]:
    from djust.assets.registry import get_registry
    from djust.components.base import Component

    declared = get_registry().assets
    messages: list[CheckMessage] = []
    for cls in _walk_subclasses(Component):
        for name in getattr(cls, "requires_assets", ()):
            if name not in declared:
                messages.append(
                    Error(
                        f"{cls.__module__}.{cls.__qualname__}.requires_assets names {name!r}, "
                        "which no djust_assets.json declares.",
                        hint="Declare it, or install the package that ships it.",
                        id="djust.B007",
                    )
                )
    return messages


@register("djust")
def check_sbom_not_served(app_configs: Any, **kwargs: Any) -> list[CheckMessage]:
    from django.contrib.staticfiles import finders

    messages: list[CheckMessage] = []
    for finder in finders.get_finders():
        for path, _storage in finder.list([]):
            if path.endswith(_SBOM_SUFFIXES):
                messages.append(
                    Error(
                        f"{path!r} is a static file: collectstatic would publish this SBOM.",
                        hint="Move it outside every static directory; ship it in the package or dist-info.",
                        id="djust.B008",
                    )
                )
    return messages


@register("djust")
def check_undeclared_origins(app_configs: Any, **kwargs: Any) -> list[CheckMessage]:
    if _is_check_suppressed("B010"):
        return []
    from djust.assets.registry import get_registry

    declared = {
        urlsplit(f.url).netloc
        for asset in get_registry().assets.values()
        for f in asset.files
        if f.url
    }
    messages: list[CheckMessage] = []
    for template_path in _iter_template_files(_get_template_dirs()):
        lines = Path(template_path).read_text(encoding="utf-8", errors="replace").splitlines()
        for lineno, line in enumerate(lines, start=1):
            if "noqa: B010" in line:
                continue
            for ref in _EXTERNAL_REF.findall(line):
                origin = urlsplit(ref if ref.startswith("http") else "https:" + ref).netloc
                if origin not in declared:
                    messages.append(
                        Warning(
                            f"{template_path}:{lineno} loads from {origin}, which no manifest declares; "
                            "scanners will not see what it serves.",
                            hint="Vendor it and declare it, declare it as external with integrity, "
                            "or add {# noqa: B010 #} to the line.",
                            id="djust.B010",
                        )
                    )
    return messages
```

In `python/djust/checks/__init__.py`, add `assets,` to the `from . import (...)` list and to the `for _mod in (...)` tuple, after `accounts`.

- [ ] **Step 4: Run the tests, then the check-ID guards**

Run: `.venv/bin/python -m pytest python/djust/tests/test_checks_assets.py python/djust/tests/test_check_id_uniqueness_2070.py python/tests/test_checks.py -v`
Expected: all PASS. If `test_checks.py` asserts an exact message count for the demo project, the new checks must add nothing there, because the demo has no manifests yet.

- [ ] **Step 5: Commit**

```bash
git add python/djust/checks/assets.py python/djust/checks/__init__.py python/djust/tests/test_checks_assets.py
git commit -m "feat(checks): djust.B001-B010 for asset manifests, files and undeclared origins"
```

---

### Task 6: App SBOM, `collectstatic` override, `djust_sbom`, checks B011–B014

**Files:**
- Create: `python/djust/checks/sbom.py`
- Modify: `python/djust/checks/__init__.py` (import `sbom` in both lists)
- Modify: `python/djust/assets/sbom.py` (add `app_document()`, `served_directory_containing()`, `write_app_sbom()`)
- Create: `python/djust/management/commands/collectstatic.py`
- Create: `python/djust/management/commands/djust_sbom.py`
- Test: `python/djust/tests/test_assets_app_sbom.py`

**Interfaces:**
- Consumes: `to_cyclonedx`, `dumps`, `digest_of` (Task 3); `get_registry` (Task 2).
- Produces:
  - `app_document() -> dict` (root name `DJUST_SBOM_NAME` or the first component of `ROOT_URLCONF`)
  - `served_directory_containing(path: Path) -> Path | None` (the `STATIC_ROOT`/`MEDIA_ROOT`/`STATICFILES_DIRS` entry that contains `path`)
  - `write_app_sbom(path: Path) -> None` (raises `CommandError` when the path is inside a served directory)
  - Checks `check_sbom_configured` (B011, `deploy=True`), `check_sbom_path` (B012), `check_sbom_collectstatic_order` (B013, `deploy=True`), `check_sbom_current` (B014, `deploy=True`). Django never passes `include_deployment_checks` to check functions; deploy-only means registered with `deploy=True`.

- [ ] **Step 1: Write the failing tests**

```python
from __future__ import annotations

import json

import pytest
from django.core.management import CommandError, call_command
from django.test import override_settings

from djust.assets.registry import get_registry
from djust.assets.sbom import served_directory_containing, write_app_sbom
from djust.checks.sbom import (
    check_sbom_collectstatic_order,
    check_sbom_configured,
    check_sbom_current,
    check_sbom_path,
)
from djust.tests._asset_fixtures import write_asset

APPS_DJUST_FIRST = ["djust", "django.contrib.contenttypes", "django.contrib.auth", "django.contrib.staticfiles"]


def ids(messages):
    return sorted(m.id for m in messages)


def test_writes_to_the_configured_path(tmp_path):
    static_dir, manifest = write_asset(tmp_path)
    out = tmp_path / "sbom" / "app.cdx.json"
    with override_settings(DJUST_ASSET_MANIFESTS=[str(manifest)], STATICFILES_DIRS=[str(static_dir)]):
        write_app_sbom(out)
        doc = json.loads(out.read_text())
        assert any(c["name"] == "test-lib" for c in doc["components"])
        assert doc["metadata"]["properties"][0]["value"] == get_registry().digest()


@pytest.mark.parametrize("setting", ["STATIC_ROOT", "MEDIA_ROOT"])
def test_refuses_served_directories(tmp_path, setting):
    served = tmp_path / "served"
    with override_settings(**{setting: str(served)}):
        assert served_directory_containing(served / "x" / "app.cdx.json") == served
        with pytest.raises(CommandError, match="served"):
            write_app_sbom(served / "app.cdx.json")


def test_collectstatic_writes_nothing_without_the_setting(tmp_path):
    root = tmp_path / "collected"
    with override_settings(STATIC_ROOT=str(root), DJUST_SBOM_PATH=None):
        call_command("collectstatic", interactive=False, verbosity=0)
    assert not list(tmp_path.rglob("*.cdx.json"))


def test_collectstatic_writes_the_sbom_when_configured(tmp_path):
    out = tmp_path / "private" / "djust-assets.cdx.json"
    with override_settings(
        STATIC_ROOT=str(tmp_path / "collected"), DJUST_SBOM_PATH=str(out), INSTALLED_APPS=APPS_DJUST_FIRST
    ):
        call_command("collectstatic", interactive=False, verbosity=0)
    assert out.is_file()


def test_existing_app_without_sbom_path_gets_only_b011(settings):
    # Demo settings list "djust" after staticfiles, like most apps.
    settings.DJUST_SBOM_PATH = None
    found = (
        check_sbom_configured(None)
        + check_sbom_path(None)
        + check_sbom_collectstatic_order(None)
        + check_sbom_current(None)
    )
    assert ids(found) == ["djust.B011"]


def test_b012_path_inside_static_root(tmp_path):
    with override_settings(STATIC_ROOT=str(tmp_path), DJUST_SBOM_PATH=str(tmp_path / "a.cdx.json")):
        assert "djust.B012" in ids(check_sbom_path(None))


def test_b013_only_when_sbom_path_is_set(tmp_path):
    apps_wrong = ["django.contrib.contenttypes", "django.contrib.auth", "django.contrib.staticfiles", "djust"]
    with override_settings(INSTALLED_APPS=apps_wrong, DJUST_SBOM_PATH=None):
        assert check_sbom_collectstatic_order(None) == []
    with override_settings(INSTALLED_APPS=apps_wrong, DJUST_SBOM_PATH=str(tmp_path / "a.cdx.json")):
        assert ids(check_sbom_collectstatic_order(None)) == ["djust.B013"]


def test_b014_missing_or_stale(tmp_path):
    out = tmp_path / "a.cdx.json"
    with override_settings(DJUST_SBOM_PATH=str(out)):
        assert ids(check_sbom_current(None)) == ["djust.B014"]
        write_app_sbom(out)
        assert check_sbom_current(None) == []
        out.write_text(out.read_text().replace(get_registry().digest(), "0" * 64))
        assert ids(check_sbom_current(None)) == ["djust.B014"]


def test_djust_sbom_command_prints_the_document(capsys):
    call_command("djust_sbom")
    assert json.loads(capsys.readouterr().out)["bomFormat"] == "CycloneDX"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest python/djust/tests/test_assets_app_sbom.py -v`
Expected: `ImportError: cannot import name 'served_directory_containing'`.

- [ ] **Step 3: Implement**

Append to `python/djust/assets/sbom.py`:

```python
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
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(dumps(app_document()), encoding="utf-8")
```

`python/djust/management/commands/collectstatic.py`:

```python
"""``collectstatic``, then the app SBOM when DJUST_SBOM_PATH is set (ADR-040).

Takes effect only when "djust" precedes "django.contrib.staticfiles" in
INSTALLED_APPS; check djust.B013 errors when it does not and an SBOM path is set.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.contrib.staticfiles.management.commands.collectstatic import Command as BaseCommand


class Command(BaseCommand):
    def handle(self, **options: Any) -> str | None:
        result = super().handle(**options)
        path = getattr(settings, "DJUST_SBOM_PATH", None)
        if path and not options.get("dry_run"):
            from djust.assets.sbom import write_app_sbom

            write_app_sbom(path)
            if options.get("verbosity", 1) >= 1:
                self.stdout.write(f"Wrote djust asset SBOM to {path}")
        return result
```

`python/djust/management/commands/djust_sbom.py`:

```python
"""Print or write the app's CycloneDX SBOM of declared browser assets (ADR-040)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandParser


class Command(BaseCommand):
    help = "Print the CycloneDX SBOM of every declared third-party browser asset."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("-o", "--output", type=Path, help="write here instead of stdout")

    def handle(self, *args: Any, **options: Any) -> None:
        from djust.assets.sbom import app_document, dumps, write_app_sbom

        if options["output"]:
            write_app_sbom(options["output"])
        else:
            self.stdout.write(dumps(app_document()), ending="")
```

`python/djust/checks/sbom.py`:

```python
"""App SBOM checks, ``djust.B011``-``B014`` (ADR-040)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.checks import CheckMessage, Error, Tags, Warning, register


def _sbom_path() -> Path | None:
    value = getattr(settings, "DJUST_SBOM_PATH", None)
    return Path(value) if value else None


@register("djust", deploy=True)
def check_sbom_configured(app_configs: Any, **kwargs: Any) -> list[CheckMessage]:
    if _sbom_path() is not None:
        return []
    return [
        Warning(
            "DJUST_SBOM_PATH is not set, so collectstatic writes no SBOM of the "
            "third-party browser code this app serves.",
            hint="Set it to a path outside every directory your web server serves.",
            id="djust.B011",
        )
    ]


@register("djust")
def check_sbom_path(app_configs: Any, **kwargs: Any) -> list[CheckMessage]:
    from djust.assets.sbom import served_directory_containing

    path = _sbom_path()
    if path is None:
        return []
    served = served_directory_containing(path)
    if served is not None:
        return [
            Error(
                f"DJUST_SBOM_PATH ({path}) is inside {served}, which is served to browsers.",
                hint="Move it outside STATIC_ROOT, MEDIA_ROOT and STATICFILES_DIRS.",
                id="djust.B012",
            )
        ]
    return []


@register("djust", Tags.staticfiles, deploy=True)
def check_sbom_collectstatic_order(app_configs: Any, **kwargs: Any) -> list[CheckMessage]:
    if _sbom_path() is None:
        return []
    apps = list(settings.INSTALLED_APPS)
    if "djust" in apps and "django.contrib.staticfiles" in apps:
        if apps.index("djust") > apps.index("django.contrib.staticfiles"):
            return [
                Error(
                    "DJUST_SBOM_PATH is set, but 'djust' comes after 'django.contrib.staticfiles' "
                    "in INSTALLED_APPS, so collectstatic will not write the SBOM.",
                    hint="Move 'djust' above 'django.contrib.staticfiles'.",
                    id="djust.B013",
                )
            ]
    return []


@register("djust", deploy=True)
def check_sbom_current(app_configs: Any, **kwargs: Any) -> list[CheckMessage]:
    from djust.assets.registry import get_registry
    from djust.assets.sbom import digest_of

    path = _sbom_path()
    if path is None:
        return []
    try:
        recorded = digest_of(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        recorded = None
    if recorded != get_registry().digest():
        return [
            Error(
                f"The SBOM at {path} is missing or does not match the declared assets.",
                hint="Run collectstatic (or manage.py djust_sbom -o PATH) as part of the build.",
                id="djust.B014",
            )
        ]
    return []
```

The tests call the functions directly, so `deploy=True` registration is not in play there; `manage.py check --deploy` is what runs B011, B013 and B014 in a real project.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest python/djust/tests/test_assets_app_sbom.py python/djust/tests/test_check_id_uniqueness_2070.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add python/djust/assets/sbom.py python/djust/checks/sbom.py python/djust/checks/__init__.py python/djust/management/commands/collectstatic.py python/djust/management/commands/djust_sbom.py python/djust/tests/test_assets_app_sbom.py
git commit -m "feat(assets): app SBOM via collectstatic and djust_sbom, checks B011-B014"
```

---

### Task 7: `js/vendor/` build and djust's own manifests

**Files:**
- Move: `js/markdown-editor/` → `js/vendor/` (`git mv`; `visual.js`, `test/visual.check.mjs`, lockfile and all)
- Create: `js/vendor/bundles.mjs`, `js/vendor/highlight-entry.js`, `js/vendor/xterm-entry.js`, `js/vendor/admin.tailwind.css`, `js/vendor/tailwind.config.cjs`
- Rewrite: `js/vendor/build.mjs`
- Modify: `js/vendor/package.json` (name `djust-vendor`; add dependencies)
- Generated and committed: `python/djust/components/djust_assets.json`, `python/djust/admin_ext/djust_assets.json`, `python/djust/djust.cdx.json`, `python/djust/components/static/djust_components/vendor/**`, `python/djust/admin_ext/static/djust_admin/admin.css`, updated `markdown-visual.LICENSE.txt`
- Modify: `Makefile` (replace `markdown-editor-build`/`test-markdown-editor` with `vendor`, `vendor-check`, `test-vendor`)
- Test: `js/vendor/test/build.check.mjs`; `python/djust/tests/test_assets_distribution.py`

**Interfaces:**
- Consumes: `python -m djust.assets.sbom --distribution` (Task 3); manifest schema (Task 1).
- Produces: declared assets `markdown-visual`, `highlight.js` (variants below), `xterm` (module `xterm.mjs` plus style `xterm.css`), `admin-css`. Tasks 8–9 reference these exact names.
- Curated highlight.js themes (amendment 5): `github`, `github-dark`, `atom-one-dark`, `atom-one-light`, `monokai`, `vs2015`, `nord`, `default`, `dark`, `a11y-dark`, `a11y-light`, `stackoverflow-light`, `stackoverflow-dark`.

- [ ] **Step 1: Move the project and add the dependencies**

```bash
git mv js/markdown-editor js/vendor
cd js/vendor
npm pkg set name=djust-vendor
npm install --save-exact highlight.js@11.11.1 @xterm/xterm@5.5.0 @xterm/addon-fit@0.10.0
npm install --save-exact --save-dev tailwindcss@3.4.17
cd -
```

Check each version against the npm registry before pinning (`npm view highlight.js version`, and so on) and use the latest stable release of the pinned major. The versions above are the expected ones and may be stale.

- [ ] **Step 2: Write the failing build checks**

`js/vendor/test/build.check.mjs`:

```js
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";

const root = new URL("../../../python/djust/", import.meta.url);
const read = (p) => readFileSync(new URL(p, root));
const manifest = (app) => JSON.parse(read(`${app}/djust_assets.json`));
const sri = (buf) => "sha384-" + createHash("sha384").update(buf).digest("base64");

test("every declared vendored file matches its integrity", () => {
  for (const app of ["components", "admin_ext"]) {
    const { schema, assets } = manifest(app);
    assert.equal(schema, 1);
    for (const [name, asset] of Object.entries(assets)) {
      for (const file of asset.files) {
        const staticDir = app === "components" ? "components/static/" : "admin_ext/static/";
        assert.equal(sri(read(staticDir + file.path)), file.integrity, `${name} ${file.path}`);
      }
      for (const pkg of asset.packages) assert.match(pkg.purl, /^pkg:npm\/.+@[^@]+$/);
    }
  }
});

test("djust's assets are all declared", () => {
  assert.deepEqual(Object.keys(manifest("components").assets).sort(), ["highlight.js", "markdown-visual", "xterm"]);
  assert.deepEqual(Object.keys(manifest("admin_ext").assets), ["admin-css"]);
});

test("served license files carry no version numbers", () => {
  const text = read("components/static/djust_components/markdown-visual.LICENSE.txt").toString();
  assert.doesNotMatch(text, /^@?[\w./-]+ \d+\.\d+\.\d+$/m);
  assert.match(text, /^@tiptap\/core$/m);
});

test("scoped purls are percent-encoded", () => {
  const purls = manifest("components").assets["markdown-visual"].packages.map((p) => p.purl);
  assert.ok(purls.some((p) => p.startsWith("pkg:npm/%40tiptap/core@")), purls.join("\n"));
});
```

Update `package.json` `"test"` to `"npm run build && node --test test/*.check.mjs"` (unchanged glob, so `visual.check.mjs` still runs).

- [ ] **Step 3: Run the checks to verify they fail**

Run: `cd js/vendor && npm ci && node --test test/build.check.mjs; cd -`
Expected: FAIL, `ENOENT ... components/djust_assets.json`.

- [ ] **Step 4: Write the bundle list and entry points**

`js/vendor/bundles.mjs`:

```js
// Every third-party browser asset djust ships (ADR-040). build.mjs builds each
// one and regenerates the owning app's djust_assets.json from what esbuild
// actually bundled; never edit those manifests by hand.
export const APPS = {
  components: "../../python/djust/components",
  admin_ext: "../../python/djust/admin_ext",
};

export const THEMES = [
  "github", "github-dark", "atom-one-dark", "atom-one-light", "monokai", "vs2015", "nord",
  "default", "dark", "a11y-dark", "a11y-light", "stackoverflow-light", "stackoverflow-dark",
];

export const BUNDLES = [
  {
    asset: "markdown-visual",
    app: "components",
    kind: "esbuild",
    entry: "visual.js",
    format: "iife",
    out: "djust_components/markdown-visual.js",
    license: true,
  },
  {
    asset: "highlight.js",
    app: "components",
    kind: "esbuild",
    entry: "highlight-entry.js",
    format: "iife",
    out: "djust_components/vendor/highlight/highlight.js",
    license: true,
    variants: THEMES.map((theme) => ({
      variant: theme,
      from: `node_modules/highlight.js/styles/${theme}.min.css`,
      out: `djust_components/vendor/highlight/styles/${theme}.css`,
    })),
  },
  {
    asset: "xterm",
    app: "components",
    kind: "esbuild",
    entry: "xterm-entry.js",
    format: "esm",
    out: "djust_components/vendor/xterm/xterm.mjs",
    license: true,
    extra: [{ from: "node_modules/@xterm/xterm/css/xterm.css", out: "djust_components/vendor/xterm/xterm.css" }],
  },
  {
    asset: "admin-css",
    app: "admin_ext",
    kind: "tailwind",
    input: "admin.tailwind.css",
    config: "tailwind.config.cjs",
    out: "djust_admin/admin.css",
    packages: ["tailwindcss"],
  },
];
```

`js/vendor/highlight-entry.js`:

```js
import hljs from "highlight.js/lib/common";
window.hljs = hljs;
```

`js/vendor/xterm-entry.js`:

```js
export { Terminal } from "@xterm/xterm";
export { FitAddon } from "@xterm/addon-fit";
```

`js/vendor/admin.tailwind.css`:

```css
@tailwind base;
@tailwind components;
@tailwind utilities;
```

`js/vendor/tailwind.config.cjs`:

```js
module.exports = {
  content: [
    "../../python/djust/admin_ext/templates/**/*.html",
    "../../python/djust/admin_ext/**/*.py",
  ],
};
```

- [ ] **Step 5: Rewrite `js/vendor/build.mjs`**

```js
import { build } from "esbuild";
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { copyFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { gzipSync } from "node:zlib";
import { APPS, BUNDLES } from "./bundles.mjs";

const LICENSE_FILES = ["LICENSE", "LICENSE.md", "LICENSE.txt", "license", "license.md"];
const sri = (buf) => "sha384-" + createHash("sha384").update(buf).digest("base64");
const staticPath = (app, rel) => join(APPS[app], "static", rel);
const purl = (name, version) => `pkg:npm/${name.replace(/^@/, "%40")}@${version}`;

function ensureDir(file) {
  mkdirSync(dirname(file), { recursive: true });
}

function packageDir(input) {
  let dir = dirname(resolve(input));
  while (!existsSync(join(dir, "package.json")) && dir !== dirname(dir)) dir = dirname(dir);
  return dir;
}

function describe(dir) {
  const pkg = JSON.parse(readFileSync(join(dir, "package.json"), "utf8"));
  if (typeof pkg.license !== "string" || !pkg.license.trim())
    throw new Error(`${pkg.name}@${pkg.version} has no SPDX "license" field`);
  const file = LICENSE_FILES.find((name) => existsSync(join(dir, name)));
  if (!file) throw new Error(`Missing license text for ${pkg.name}`);
  return { name: pkg.name, version: pkg.version, license: pkg.license, text: readFileSync(join(dir, file), "utf8") };
}

function fileEntry(app, rel, extra = {}) {
  return { path: rel, integrity: sri(readFileSync(staticPath(app, rel))), ...extra };
}

async function buildEsbuild(bundle) {
  const outfile = staticPath(bundle.app, bundle.out);
  const result = await build({
    entryPoints: [bundle.entry],
    bundle: true,
    minify: true,
    format: bundle.format,
    target: ["es2020"],
    outfile,
    metafile: true,
    supported: { "template-literal": false },
    legalComments: "none",
    banner: { js: `/* djust vendored asset "${bundle.asset}". Third-party licenses: see the adjacent .LICENSE.txt */` },
  });
  const packages = new Map();
  for (const input of Object.keys(result.metafile.inputs)) {
    if (!input.includes("node_modules/")) continue;
    const info = describe(packageDir(input));
    packages.set(info.name, info);
  }
  const sorted = [...packages.values()].sort((a, b) => a.name.localeCompare(b.name));
  const licenseRel = bundle.out.replace(/\.m?js$/, ".LICENSE.txt");
  // Names and license texts only: a served file must not publish the version list.
  writeFileSync(
    staticPath(bundle.app, licenseRel),
    sorted.map((p) => `${p.name}\n${p.text}`).join("\n\n---\n\n"),
  );
  const files = [fileEntry(bundle.app, bundle.out)];
  for (const item of [...(bundle.extra ?? []), ...(bundle.variants ?? [])]) {
    const target = staticPath(bundle.app, item.out);
    ensureDir(target);
    copyFileSync(item.from, target);
    files.push(fileEntry(bundle.app, item.out, item.variant ? { variant: item.variant } : {}));
  }
  process.stdout.write(`${bundle.asset}: ${gzipSync(readFileSync(outfile)).length} bytes gzip\n`);
  return {
    files,
    license_file: licenseRel,
    packages: sorted.map((p) => ({ purl: purl(p.name, p.version), license: p.license })),
  };
}

function buildTailwind(bundle) {
  const outfile = staticPath(bundle.app, bundle.out);
  ensureDir(outfile);
  execFileSync("npx", ["tailwindcss", "-c", bundle.config, "-i", bundle.input, "-o", outfile, "--minify"], {
    stdio: "inherit",
  });
  const packages = bundle.packages.map((name) => describe(join("node_modules", name)));
  return {
    files: [fileEntry(bundle.app, bundle.out)],
    packages: packages.map((p) => ({ purl: purl(p.name, p.version), license: p.license })),
  };
}

const manifests = Object.fromEntries(Object.keys(APPS).map((app) => [app, {}]));
for (const bundle of BUNDLES) {
  manifests[bundle.app][bundle.asset] =
    bundle.kind === "tailwind" ? buildTailwind(bundle) : await buildEsbuild(bundle);
}
for (const [app, assets] of Object.entries(manifests)) {
  const sortedAssets = Object.fromEntries(Object.entries(assets).sort(([a], [b]) => a.localeCompare(b)));
  writeFileSync(join(APPS[app], "djust_assets.json"), JSON.stringify({ schema: 1, assets: sortedAssets }, null, 2) + "\n");
}
```

Before relying on `tailwind.config.cjs`, read `python/djust/admin_ext/templates/djust_admin/base.html` for an inline `tailwind.config = {...}` script. If there is one, copy its `theme`/`plugins` into `tailwind.config.cjs` and delete the inline script in Task 9.

- [ ] **Step 6: Add the Make targets**

Replace the `markdown-editor-build` and `test-markdown-editor` targets (and their `.PHONY` line) in `Makefile` with:

```make
.PHONY: vendor vendor-check test-vendor
VENDOR_OUTPUTS := python/djust/components/djust_assets.json python/djust/admin_ext/djust_assets.json \
	python/djust/djust.cdx.json python/djust/components/static/djust_components \
	python/djust/admin_ext/static

vendor: ## Build vendored third-party browser assets, their manifests and python/djust/djust.cdx.json (ADR-040)
	cd js/vendor && npm ci && npm run build
	PYTHONPATH=python $(PYTHON) -m djust.assets.sbom --distribution

vendor-check: vendor ## Fail when committed vendored assets, manifests or djust.cdx.json differ from a rebuild
	@git diff --exit-code -- $(VENDOR_OUTPUTS) || { echo "Vendored outputs are stale: run 'make vendor' and commit."; exit 1; }
	@test -z "$$(git status --porcelain --untracked-files=all -- $(VENDOR_OUTPUTS))" || { git status --short -- $(VENDOR_OUTPUTS); echo "Untracked vendored outputs: run 'make vendor' and commit."; exit 1; }

test-vendor: ## Test the vendored bundles (Markdown/Visual conversion, manifests, licenses)
	cd js/vendor && npm ci && npm test
```

Then run `grep -rn "markdown-editor-build\|test-markdown-editor\|js/markdown-editor" --exclude-dir=node_modules .` and update every hit (docs, `CONTRIBUTING.md`, `docs/website/guides/markdown-editor.md:159`) to the new names.

- [ ] **Step 7: Build, then run the checks**

Run: `make vendor && cd js/vendor && node --test test/*.check.mjs; cd -`
Expected: all `node:test` cases pass, and `markdown-visual.js` still builds. The Visual editor behaviour tests in `visual.check.mjs` are unchanged.

- [ ] **Step 8: Add the Python distribution test**

`python/djust/tests/test_assets_distribution.py`:

```python
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
```

Run: `.venv/bin/python -m pytest python/djust/tests/test_assets_distribution.py python/djust/tests/test_checks_assets.py -v`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add -A js/vendor python/djust/components/djust_assets.json python/djust/admin_ext/djust_assets.json python/djust/djust.cdx.json python/djust/components/static/djust_components python/djust/admin_ext/static Makefile python/djust/tests/test_assets_distribution.py docs CONTRIBUTING.md
git commit -m "feat(vendor): one js/vendor build for every shipped asset, generating djust's manifests and SBOM"
```

(`git add -A js/vendor` also records the removal of `js/markdown-editor`. Check `git status` before committing, so nothing outside these paths is staged.)

---

### Task 8: Move the loaders onto declared assets; delete `components/dependencies.py`

**Files:**
- Modify: `python/djust/components/templatetags/djust_components.py:1869-1960` (`code_block`)
- Modify: `python/djust/components/static/djust_components/ttyd/ttyd_terminal.js:1-27`
- Modify: `python/djust/components/templates/djust_components/ttyd_terminal.html`
- Delete: `python/djust/components/dependencies.py`
- Modify: `docs/website/guides/markdown-editor.md:52-62`
- Test: `python/djust/tests/test_code_block_vendored_highlight.py`; update any existing test asserting the CDN URLs (`git grep -n "cdn-release@11\|esm.sh" -- '*test*'`)

**Interfaces:**
- Consumes: `asset_tags`, `asset_url`, `get_asset` (Task 4); assets `highlight.js` and `xterm` (Task 7).

- [ ] **Step 1: Write the failing tests**

```python
from __future__ import annotations

import pytest
from django.core.exceptions import ImproperlyConfigured

from djust.components.templatetags.djust_components import code_block


def test_code_block_loads_vendored_highlight_with_integrity():
    html = str(code_block(code="x = 1", language="python", theme="github-dark"))
    assert "cdn.jsdelivr.net" not in html and "cdnjs" not in html
    assert 'src="/static/djust_components/vendor/highlight/highlight.js" integrity="sha384-' in html
    assert 'href="/static/djust_components/vendor/highlight/styles/github-dark.css" integrity="sha384-' in html


def test_unknown_theme_fails_loudly():
    with pytest.raises(ImproperlyConfigured, match="github-dark"):
        code_block(code="x", language="python", theme="dracula")


def test_highlight_false_loads_nothing():
    assert "<script" not in str(code_block(code="x", highlight=False))


def test_ttyd_template_imports_vendored_xterm():
    from django.template.loader import render_to_string

    html = render_to_string("djust_components/ttyd_terminal.html", {"ttyd_url": "ws://x", "rows": 24, "cols": 80, "theme_json": "{}"})
    assert "esm.sh" not in html
    assert 'data-xterm-src="/static/djust_components/vendor/xterm/xterm.mjs"' in html
    assert 'rel="modulepreload"' in html and 'rel="stylesheet"' in html


def test_dependencies_module_is_gone():
    with pytest.raises(ModuleNotFoundError):
        __import__("djust.components.dependencies")
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest python/djust/tests/test_code_block_vendored_highlight.py -v`
Expected: FAIL (the CDN URL is present; `dracula` does not raise).

- [ ] **Step 3: Change `code_block`**

In `djust_components.py`, inside `code_block`:

1. At the top of `if highlight:`, add:

```python
        from djust.assets.tags import asset_tags

        # Raises ImproperlyConfigured naming the vendored themes for an unknown one.
        loader = str(asset_tags("highlight.js", str(theme)))
```

   and initialise `loader = ""` just before the `if highlight:` line.
2. In the inline script, replace the whole CDN injector (from the `if(!window.__djcHljsLoading){{` line through its `}}else{{var iv=setInterval(...)}}` line) with the existing wait loop alone:

```python
            f"var iv=setInterval(function(){{if(window.hljs){{clearInterval(iv);doHL();installObserver();}}}},50);"
```

   Keep the preceding `if(window.hljs){{doHL();installObserver();return;}}` line unchanged. On an initial page load the vendored `<script src>` sits before this inline script and is parser-blocking, so `window.hljs` already exists and the loop never runs; the loop only covers a script served `async` by a proxy.
3. Emit `loader` BEFORE the `<pre>` in the returned markup, so the inline script's `document.currentScript.previousElementSibling` is still the `<pre>`:

```python
        f'{loader}<pre class="code-block-pre"><code class="language-{e_language}">{e_code}</code></pre>'
```

4. Update the docstring: `highlight: When True (default), loads djust's vendored highlight.js (declared in djust_assets.json).`

Keep `safe_theme`/`e_theme`: they still feed the `data-highlight` attribute. Several blocks on one page emit the same `<script src>`/`<link>`; the browser fetches each once, and the `window.hljs` guard is unchanged. The #1625 MutationObserver stays as is.

- [ ] **Step 4: Change the ttyd hook and template**

`ttyd_terminal.js` lines 1–27:

```js
// ttyd_terminal.js — djust hook for xterm.js + ttyd WebSocket
// xterm is djust's vendored build (ADR-040): the template renders a
// <link rel="modulepreload" integrity> for it and passes its URL here.
//
// ttyd binary protocol:
//   Client→server: [0x00, ...stdin_bytes] | [0x01, ...resize_json_bytes]
//   Server→client: [0x00, ...stdout_bytes] | [0x01, title] | [0x02, prefs]
//
// ttyd must be run with --check-origin=false (or same origin) to allow
// WebSocket connections.

export const TtydTerminalHook = {
  async mounted() {
    const el = this.el;
    const url  = el.dataset.ttydUrl || "ws://localhost:7681";
    const rows = parseInt(el.dataset.rows || "24", 10);
    const cols = parseInt(el.dataset.cols || "80", 10);
    let theme  = {};
    try { theme = JSON.parse(el.dataset.theme || "{}"); } catch (_) {}

    const { Terminal, FitAddon } = await import(el.dataset.xtermSrc);
```

(Delete the two `await import(..._CDN)` lines this replaces.)

`ttyd_terminal.html`: change the first line to `{% load static djust_assets %}`, add `{% djust_asset "xterm" %}` before the `<div>`, and add `data-xterm-src="{% djust_asset_url "xterm" %}"` to the `<div>`'s attributes.

- [ ] **Step 5: Delete the dead registry and update the docs snippet**

```bash
git rm python/djust/components/dependencies.py
git grep -n "components.dependencies\|DEPENDENCY_REGISTRY\|DependencyManager\|requires_dependencies" -- python docs
```

Expected: no hits in `python/` or `docs/`. Fix any that appear.

In `docs/website/guides/markdown-editor.md`, replace the three-line asset snippet with:

```django
{% load static djust_assets %}
<link rel="stylesheet" href="{% static 'djust_components/markdown-editor.css' %}">
{% djust_asset "markdown-visual" %}
<script src="{% static 'djust_components/markdown-editor.js' %}"></script>
```

Replace its next paragraph's first sentence with: "Omit the `markdown-visual` asset for a lightweight Markdown-only editor; `{% djust_asset %}` adds its integrity hash, and its bundled package versions are listed in djust's SBOM (see [Scanning a djust app](scanning.md))."

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest python/djust/tests/test_code_block_vendored_highlight.py python/djust/tests/test_assets_distribution.py -v && git grep -ln "cdn-release@11\|esm.sh/xterm" -- '*test*' | xargs -r .venv/bin/python -m pytest -q`
Expected: PASS. Update any old test that pinned the CDN URL so it asserts the vendored path instead.

- [ ] **Step 7: Commit**

```bash
git add -A python/djust/components python/djust/tests/test_code_block_vendored_highlight.py docs/website/guides/markdown-editor.md
git commit -m "feat(components): load highlight.js and xterm from vendored assets; remove the CDN registry"
```

---

### Task 9: Static admin CSS instead of the Tailwind CDN

**Files:**
- Modify: `python/djust/admin_ext/templates/djust_admin/base.html:8-9`, `login.html`, `logout.html` (each `cdn.tailwindcss.com` line)
- Test: `python/djust/tests/test_admin_ext_no_cdn.py`

**Interfaces:**
- Consumes: asset `admin-css` (Task 7).

- [ ] **Step 1: Take "before" screenshots while the CDN is still in place**

Start the demo server (`make start-bg`), log in, and use the djust-browser MCP (`screenshot`, full page) on the admin index, a model list, a change form, a detail page, the delete confirmation, bulk progress, login and logout. Save them to `$SCRATCH/admin-before/`. Navigate by `http://localhost:…` (the extension ignores 127.0.0.1).

- [ ] **Step 2: Write the failing test**

```python
from pathlib import Path

ADMIN_TEMPLATES = Path(__file__).resolve().parents[1] / "admin_ext" / "templates" / "djust_admin"


def test_admin_templates_load_no_cdn():
    offenders = [p.name for p in ADMIN_TEMPLATES.glob("*.html") if "cdn.tailwindcss.com" in p.read_text()]
    assert offenders == []


def test_admin_base_uses_the_declared_stylesheet():
    text = (ADMIN_TEMPLATES / "base.html").read_text()
    assert '{% djust_asset "admin-css" %}' in text and "djust_assets" in text
```

Run: `.venv/bin/python -m pytest python/djust/tests/test_admin_ext_no_cdn.py -v`
Expected: FAIL, listing `base.html`, `login.html`, `logout.html`.

- [ ] **Step 3: Swap the tags**

In each of the three templates, replace `<script src="https://cdn.tailwindcss.com"></script>` with `{% djust_asset "admin-css" %}`, and add `djust_assets` to the template's existing `{% load %}` line (or add `{% load djust_assets %}` after `{% extends %}` when there is none).

- [ ] **Step 4: Take "after" screenshots and compare**

Restart the server (`make restart`), capture the same pages to `$SCRATCH/admin-after/`, and compare each pair (djust-browser `visual_diff`, or by eye). For every class that renders differently, grep for where it is built (often in `admin_ext/adapters.py` from a Python string). Then add that file's pattern to `content` in `tailwind.config.cjs`, or the literal class to a `safelist` there. Run `make vendor`, and repeat until the pairs match. Record the final comparison in the PR description.

- [ ] **Step 5: Run the tests and commit**

Run: `.venv/bin/python -m pytest python/djust/tests/test_admin_ext_no_cdn.py python/djust/tests/test_assets_distribution.py -v`
Expected: PASS.

```bash
git add python/djust/admin_ext js/vendor/tailwind.config.cjs python/djust/tests/test_admin_ext_no_cdn.py python/djust/djust.cdx.json
git commit -m "feat(admin): serve a static Tailwind build instead of the runtime CDN compiler"
```

---

### Task 10: The scaffold and the C011 hint stop recommending the CDN

**Files:**
- Modify: `python/djust/theming/management/commands/djust_theme.py:492-494`
- Modify: `python/djust/checks_css_proposal.py:86` and the live C011 hint it proposes for (find with `git grep -n "cdn.tailwindcss.com" -- python/djust`)
- Test: `python/djust/tests/test_no_cdn_recommendations.py`

- [ ] **Step 1: Write the failing test**

```python
import subprocess


def test_djust_never_emits_or_recommends_the_tailwind_cdn():
    hits = subprocess.run(
        ["git", "grep", "-n", "cdn.tailwindcss.com", "--", "python/djust", ":!python/djust/tests"],
        capture_output=True, text=True,
    ).stdout
    assert hits == ""
```

Run: `.venv/bin/python -m pytest python/djust/tests/test_no_cdn_recommendations.py -v`
Expected: FAIL, listing `djust_theme.py`, `checks_css_proposal.py` and the C011 source.

- [ ] **Step 2: Change them**

- In `djust_theme.py`, replace the `{% if tailwind %}<link href="https://cdn.tailwindcss.com" ...>{% endif %}` block in the generated example with `{% if tailwind %}<link rel="stylesheet" href="{% static 'css/output.css' %}">{% endif %}`. Add `static` to that template's `{% load %}` line. The command already tells users to run the Tailwind CLI to produce `output.css`; if it does not, add that line to its closing message.
- In the C011 hint (both the live check and `checks_css_proposal.py`), delete the `Or use Tailwind CDN for development only: …` sentence and leave the `tailwindcss -i … -o …` instruction.

- [ ] **Step 3: Run the tests, including the scaffold's own**

Run: `.venv/bin/python -m pytest python/djust/tests/test_no_cdn_recommendations.py python/djust/tests/test_scaffold_setup_environment.py -v`
Expected: PASS. Update any scaffold test that expected the CDN link.

- [ ] **Step 4: Commit**

```bash
git add python/djust/theming/management/commands/djust_theme.py python/djust/checks_css_proposal.py python/djust/checks python/djust/tests/test_no_cdn_recommendations.py python/djust/tests/test_scaffold_setup_environment.py
git commit -m "fix(theming): scaffold and C011 hint no longer point at the Tailwind CDN"
```

---

### Task 11: Ship the SBOM in the wheel (PEP 770)

**Files:**
- Modify: `pyproject.toml:2` and `:105` (maturin floor), and `[tool.maturin]` (add `[tool.maturin.sbom]`)
- Test: `python/djust/tests/test_wheel_sbom.py` (marked `slow`)

- [ ] **Step 1: Write the failing test**

```python
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]


@pytest.mark.slow
def test_wheel_carries_the_sbom_in_both_places(tmp_path):
    subprocess.run(
        [sys.executable, "-m", "maturin", "build", "--release", "-i", sys.executable, "-o", str(tmp_path)],
        cwd=REPO, check=True,
    )
    (wheel,) = tmp_path.glob("djust-*.whl")
    names = zipfile.ZipFile(wheel).namelist()
    assert "djust/djust.cdx.json" in names
    assert any(n.endswith(".dist-info/sboms/djust.cdx.json") for n in names)
    assert not any("/static/" in n and n.endswith(".cdx.json") for n in names)
```

Run: `.venv/bin/python -m pytest python/djust/tests/test_wheel_sbom.py -v -m slow`
Expected: FAIL on the `.dist-info/sboms/` assertion. (`djust/djust.cdx.json` is already in the package tree from Task 7.)

- [ ] **Step 2: Configure maturin**

- `pyproject.toml` line 2: `requires = ["maturin>=1.12.1,<2.0"]`. Make the same change on line 105 (dev dependency).
- After the `include = [...]` of `[tool.maturin]`, add:

```toml
[tool.maturin.sbom]
rust = true
include = ["python/djust/djust.cdx.json"]
```

Then upgrade the worktree venv's maturin (`.venv/bin/python -m pip install 'maturin>=1.12.1,<2.0'`), and update `uv.lock` (`UV_INDEX_URL=https://pypi.org/simple uv lock --upgrade-package maturin`). Check `git diff uv.lock | grep -c 127.0.0.1` prints `0` (memory: "uv re-locks against the local mirror").

- [ ] **Step 3: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest python/djust/tests/test_wheel_sbom.py -v -m slow`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock python/djust/tests/test_wheel_sbom.py
git commit -m "build: ship djust.cdx.json in .dist-info/sboms (PEP 770); maturin >= 1.12.1"
```

---

### Task 12: CI: the `vendor` gate, the scanner canary, and the weekly scan

**Files:**
- Rename and rewrite: `.github/workflows/markdown-editor.yml` → `.github/workflows/vendor.yml`
- Create: `.github/workflows/vendor-advisories-weekly.yml`
- Create: `osv-scanner.toml`
- Create: `scripts/vendor_canary_sbom.py`
- Test: `python/djust/tests/test_vendor_workflows.py`

**Interfaces:**
- Consumes: `make vendor-check`, `make test-vendor` (Task 7); `to_cyclonedx`, `dumps` (Task 3); the osv-scanner command recorded in Task 0.

- [ ] **Step 1: Write the failing test**

```python
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

REPO = Path(__file__).resolve().parents[3]


def _workflow(name):
    return yaml.safe_load((REPO / ".github" / "workflows" / name).read_text())


def test_vendor_workflow_triggers_on_every_input():
    on = _workflow("vendor.yml")[True]  # PyYAML parses the key "on" as True
    paths = set(on["pull_request"]["paths"])
    assert {
        "js/vendor/**",
        "Cargo.lock",
        "crates/*/Cargo.toml",
        "python/djust/djust.cdx.json",
        "python/djust/**/djust_assets.json",
        "python/djust/assets/**",
        "osv-scanner.toml",
    } <= paths


def test_vendor_workflow_runs_check_canary_and_scan():
    steps = " ".join(str(s.get("run", "")) for s in _workflow("vendor.yml")["jobs"]["vendor"]["steps"])
    assert "make vendor-check" in steps
    assert "vendor_canary_sbom.py" in steps
    assert "osv-scanner" in steps


def test_weekly_scan_is_scheduled():
    assert "schedule" in _workflow("vendor-advisories-weekly.yml")[True]
```

Run: `.venv/bin/python -m pytest python/djust/tests/test_vendor_workflows.py -v`
Expected: FAIL (`vendor.yml` does not exist).

- [ ] **Step 2: Write the canary generator**

`scripts/vendor_canary_sbom.py`:

```python
"""Write an SBOM with a known-vulnerable package through djust's own generator.

CI scans it and requires the scanner to FAIL: that proves the scanner reads
our SBOM shape (nested components, purls) instead of silently seeing nothing.
lodash 4.17.20 has CVE-2021-23337 (fixed in 4.17.21).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

from djust.assets.manifest import Asset, AssetFile, Package, sri  # noqa: E402
from djust.assets.sbom import dumps, to_cyclonedx  # noqa: E402

asset = Asset(
    name="canary",
    source="canary",
    files=(AssetFile(integrity=sri(b"canary"), type="script", path="canary.js"),),
    packages=(Package("pkg:npm/lodash@4.17.20", "MIT"),),
)
out = Path(sys.argv[1])
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(dumps(to_cyclonedx([asset], root_name="canary", root_version=None, digest="0" * 64)))
```

- [ ] **Step 3: Write `osv-scanner.toml`**

```toml
# Known advisories in vendored browser packages that we accept for now.
# Every entry needs a reason and an ignoreUntil date; after that date the
# advisory fails CI again. Add entries only with a linked issue.
#
# [[IgnoredVulns]]
# id = "GHSA-xxxx-xxxx-xxxx"
# ignoreUntil = 2026-12-31
# reason = "Not reachable: … (#NNNN)"
```

- [ ] **Step 4: Write `.github/workflows/vendor.yml`**

Use the exact osv-scanner invocation recorded in Task 0 wherever `osv-scanner scan source -L` appears below.

```yaml
name: Vendored assets
on:
  pull_request:
    paths:
      - 'js/vendor/**'
      - 'Cargo.lock'
      - 'crates/*/Cargo.toml'
      - 'python/djust/djust.cdx.json'
      - 'python/djust/**/djust_assets.json'
      - 'python/djust/assets/**'
      - 'python/djust/components/static/djust_components/**'
      - 'python/djust/admin_ext/static/**'
      - 'python/djust/admin_ext/templates/**'
      - 'osv-scanner.toml'
      - 'scripts/vendor_canary_sbom.py'
      - '.github/workflows/vendor.yml'
  push:
    branches: [main]
permissions:
  contents: read
jobs:
  vendor:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: actions/setup-node@v7
        with:
          node-version: '22'
          cache: npm
          cache-dependency-path: js/vendor/package-lock.json
      - uses: actions/setup-python@v6
        with:
          python-version: '3.12'
      - uses: dtolnay/rust-toolchain@stable
      - name: Rebuild and compare every vendored output
        run: make vendor-check PYTHON=python
      - name: Bundle behaviour, manifests and license files
        run: make test-vendor
      - name: Install osv-scanner
        run: go install github.com/google/osv-scanner/v2/cmd/osv-scanner@latest && echo "$(go env GOPATH)/bin" >> "$GITHUB_PATH"
      - name: Canary — the scanner must see a package inside our SBOM
        run: |
          python scripts/vendor_canary_sbom.py "$RUNNER_TEMP/canary/canary.cdx.json"
          if osv-scanner scan source -L "$RUNNER_TEMP/canary/canary.cdx.json"; then
            echo "osv-scanner found no advisory in the canary SBOM: it is not reading our SBOM shape."
            exit 1
          fi
      - name: Scan djust's vendored packages for known advisories
        run: osv-scanner scan source --config osv-scanner.toml -L python/djust/djust.cdx.json
```

Check each `uses:` major version against the ones `test.yml` already uses (`grep -h "uses:" .github/workflows/test.yml | sort -u`) and match them. If `setup-go` is not preinstalled, add `actions/setup-go` with the major version `test.yml` or `codeql.yml` uses. Then `git rm .github/workflows/markdown-editor.yml`.

- [ ] **Step 5: Write `.github/workflows/vendor-advisories-weekly.yml`**

```yaml
name: Vendored asset advisories (weekly)
on:
  schedule:
    - cron: '17 6 * * 1'
  workflow_dispatch:
permissions:
  contents: read
  issues: write
jobs:
  scan:
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        line: ['1.3']
    steps:
      - uses: actions/checkout@v7
        with:
          fetch-depth: 0
      - name: Latest tag on the line
        id: tag
        run: echo "tag=$(git tag -l 'v${{ matrix.line }}.*' --sort=-v:refname | head -1)" >> "$GITHUB_OUTPUT"
      - name: Check out that release's SBOM
        run: git show "${{ steps.tag.outputs.tag }}:python/djust/djust.cdx.json" > release.cdx.json
      - name: Install osv-scanner
        run: go install github.com/google/osv-scanner/v2/cmd/osv-scanner@latest && echo "$(go env GOPATH)/bin" >> "$GITHUB_PATH"
      - name: Scan
        id: scan
        run: |
          set +e
          osv-scanner scan source --format json -L release.cdx.json > result.json
          echo "code=$?" >> "$GITHUB_OUTPUT"
      - name: One issue per advisory and line
        if: steps.scan.outputs.code != '0'
        env:
          GH_TOKEN: ${{ github.token }}
          TAG: ${{ steps.tag.outputs.tag }}
        run: |
          jq -r '.results[].packages[] | .package as $p | .vulnerabilities[] | "\(.id)\t\($p.name)@\($p.version)"' result.json | sort -u |
          while IFS=$'\t' read -r id pkg; do
            title="Vendored ${pkg} in ${TAG}: ${id}"
            if [ -z "$(gh issue list --state all --search "\"${title}\" in:title" --json number -q '.[].number')" ]; then
              gh issue create --title "$title" --label security \
                --body "The weekly scan of \`python/djust/djust.cdx.json\` at ${TAG} matched ${id} for ${pkg}. Bump it in js/vendor, run \`make vendor\`, and release a patch. Run: ${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}"
            fi
          done
```

The matrix starts at `1.3`, the first line with a committed SBOM. Add each later line when its branch exists.

- [ ] **Step 6: Run the tests and lint the workflows**

Run: `.venv/bin/python -m pytest python/djust/tests/test_vendor_workflows.py -v && pre-commit run check-yaml --files .github/workflows/vendor.yml .github/workflows/vendor-advisories-weekly.yml`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add -A .github/workflows scripts/vendor_canary_sbom.py osv-scanner.toml python/djust/tests/test_vendor_workflows.py
git commit -m "ci: vendor gate with scanner canary and OSV scan; weekly advisory scan of releases"
```

---

### Task 13: Docs, ADR-040, changelog fragments, spec amendments

**Files:**
- Create: `docs/adr/040-vendored-assets-and-sboms.md`
- Create: `docs/website/guides/vendored-assets.md`
- Create: `docs/website/guides/scanning.md`
- Modify: `docs/website/guides/error-codes.md` (category table at line 16; new `## Vendored Assets (B0xx)` section after the A1xx entries)
- Modify: `docs/system-checks.md` (B-range rows, same format as its A rows)
- Modify: the guides index/nav that lists `markdown-editor.md` (`git grep -n "markdown-editor.md" -- docs/website`) to add the two new guides
- Modify: `docs/superpowers/specs/2026-09-25-vendored-assets-sbom-design.md` (target release; the six planning amendments)
- Create: `changelog.d/vendored-assets.security.md`, `changelog.d/vendored-assets.added.md`, `changelog.d/vendored-assets.removed.md`, `changelog.d/vendored-assets.changed.md`

- [ ] **Step 1: Write ADR-040**

Use ADR-039's header format: Status `Accepted`, Date `2026-09-25`, Spec link, Related (`components/dependencies.py` (removed), `js/markdown-editor` (moved), GHSA context: the downstream govcon report). Sections:
- **Context**: the five external loads, and the invisible versions.
- **Decision**: vendored by default; manifests; SBOM outputs; the B checks; the CI gates.
- **Consequences**: the override rule, the curated themes, maturin ≥ 1.12.1, and that Dependabot/pip-audit/Snyk still don't see vendored JS.
- **Alternatives rejected**: CDN with SRI only; Python declarations; CycloneDX as the hand-written format; per-bundle lockfiles.

Run `make check-adr-status`. Expected: pass.

- [ ] **Step 2: Write the two guides**

`vendored-assets.md` covers:
- **Who it's for:** component packages and apps.
- **The schema:** copy the spec's manifest example and rules.
- **Where manifests live:** `<app>/djust_assets.json`, or `DJUST_ASSET_MANIFESTS` for project files.
- **How to compute an integrity:** `openssl dgst -sha384 -binary FILE | openssl base64 -A`, prefixed `sha384-`.
- **Using assets:** `{% djust_asset %}`, `{% djust_asset_url %}` and `requires_assets`.
- **Overriding a djust-bundled library** for a security fix, and what B009 then says.
- **External assets:** `DJUST_ALLOW_EXTERNAL_ASSETS`, integrity, and the CORS/`crossorigin` note for an absolute `STATIC_URL`.
- **Supported highlight.js themes:** the Task 7 list.

`scanning.md` covers:
- **Setting up the app SBOM:** set `DJUST_SBOM_PATH` outside every served directory (and check that against your web server config), then put `"djust"` above `"django.contrib.staticfiles"`.
- **Exact commands:** `trivy fs /app`, `syft dir:/app --select-catalogers +sbom-cataloger`, `grype sbom:/path/djust-assets.cdx.json`, and the osv-scanner command from Task 0.
- **Coverage limits:** pip-audit, Dependabot and Snyk will not see vendored JS. Dependabot can be fed the SBOM through GitHub's dependency submission API.
- **Python dependencies:** `cyclonedx-py` covers the environment.

- [ ] **Step 3: Document B001–B014**

In `error-codes.md`, add `| B0xx | Vendored assets and SBOMs |` to the category table. Add one `### B0NN: <title>` entry per ID, using the A100 format (`**Severity**`, `**What causes it**`, `**Fix**`), with wording from the spec table plus the B013 amendment. Add matching rows to `docs/system-checks.md`.

Run: `make docs-lint check-doc-snippets`
Expected: pass.

- [ ] **Step 4: Amend the spec**

In the spec:
- change `Target: 1.3.0rc2` to `Target: the next 1.3 pre-release after v1.3.0rc2`;
- change the rollout heading the same way;
- add a `## Planning amendments` section copying the six amendments from this plan's Global Constraints.

- [ ] **Step 5: Write the changelog fragments**

`changelog.d/vendored-assets.security.md`:

```markdown
- **Third-party browser code is vendored, verified and listed in an SBOM** (ADR-040). highlight.js, xterm and the admin stylesheet no longer load from cdnjs, jsdelivr, esm.sh or cdn.tailwindcss.com; every bundled package is listed with its exact version in `djust/djust.cdx.json` (also in `.dist-info/sboms/`), so Trivy and Syft can match advisories against it. Served license files no longer list package versions.
```

`changelog.d/vendored-assets.added.md`:

```markdown
- **`djust_assets.json` manifests, `{% djust_asset %}`, and app SBOMs.** Component packages and apps declare the third-party JS/CSS they serve; djust renders it with Subresource Integrity, checks it at startup (`djust.B001`–`B014`), and `collectstatic` writes a CycloneDX SBOM of all of it to `DJUST_SBOM_PATH`. See the Vendoring third-party JS and Scanning a djust app guides.
```

`changelog.d/vendored-assets.removed.md`:

```markdown
- **`djust.components.dependencies`** (`DEPENDENCY_REGISTRY`, `DependencyManager`). It was undocumented and unused; declare assets in `djust_assets.json` and set `requires_assets` instead.
```

`changelog.d/vendored-assets.changed.md`:

```markdown
- **`{% code_block %}` themes are the vendored set** (github, github-dark, atom-one-dark, atom-one-light, monokai, vs2015, nord, default, dark, a11y-dark, a11y-light, stackoverflow-light, stackoverflow-dark). Any other `theme=` raises `ImproperlyConfigured` naming them. `js/markdown-editor/` moved to `js/vendor/`; `make markdown-editor-build` is now `make vendor`.
```

Run: `make changelog-preview | grep -c vendored`
Expected: ≥ 4.

- [ ] **Step 6: Commit**

```bash
git add docs changelog.d
git commit -m "docs: ADR-040, vendoring and scanning guides, B0xx error codes"
```

---

### Task 14: End-to-end verification

Evidence for the PR description. Nothing here is committed unless a fix is needed; any fix goes back to its owning task's files, with a test.

- [ ] **Step 1: Full suite in a frozen worktree** (memory: "Run long suites in a frozen worktree")

Run, in the background: `make test 2>&1 | tee context/terminal/vendored-assets-test-$(date +%Y%m%d-%H%M%S).log`
Expected: no failures. Report the counts from the log.

- [ ] **Step 2: The scanners find a bundled package in a real image**

```bash
.venv/bin/python -m maturin build --release -i .venv/bin/python -o "$SCRATCH/wheel"
cat > "$SCRATCH/wheel/Dockerfile" <<'EOF'
FROM python:3.12-slim
COPY djust-*.whl /tmp/
RUN pip install /tmp/djust-*.whl
EOF
docker build -t djust-sbom-e2e "$SCRATCH/wheel"
trivy image --quiet --list-all-pkgs --format json djust-sbom-e2e | grep -c '"@tiptap/core"'
syft djust-sbom-e2e --select-catalogers +sbom-cataloger -o json | grep -c 'pkg:npm/%40tiptap/core@'
```

Expected: both counts ≥ 1, at the version in `js/vendor/package-lock.json`.

- [ ] **Step 3: Browser behaviour** (memory: "Browser ref-clicks miss djust events": click by coordinate)

On the demo server:
- a `{% code_block %}` highlights on the initial load, and again after a WebSocket patch adds a block (#1625);
- the Markdown editor's Visual mode loads, with no integrity errors in the console;
- the ttyd terminal page imports `xterm.mjs` (network panel), with no `esm.sh` request;
- the admin pages match the Task 9 "after" screenshots.

Check the console with `assert_no_console_errors` on each page.

- [ ] **Step 4: Downstream projects**

For each of `djust.org`, `djustlive` and `djust-docs`, install this branch editable into its venv (see memory "djust.org catalogue harness" for the PYTHONPATH form), then run `python manage.py check --deploy`. Also run `make docs-verify` in `djust-docs`.
Expected: no B001–B008 errors. Every B010 warning is a real CDN tag in that project: record it in the PR as a follow-up for that repo, and do not silence it here.

- [ ] **Step 5: Report**

Write the evidence into the PR description:
- the test counts;
- the two scanner counts;
- the browser checklist;
- the before/after admin screenshots;
- each downstream project's check output.
