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
        fail(
            f'{where}: "type" must be one of {sorted(FILE_TYPES)} (cannot infer from {location!r})'
        )
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
