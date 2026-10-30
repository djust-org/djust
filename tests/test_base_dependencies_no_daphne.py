"""A bare ``pip install djust`` must not install daphne or twisted.

djust used to depend on ``channels[daphne]``, so every djust app installed
daphne and, through ``daphne -> twisted[tls]``, Twisted, even apps served by
uvicorn that never import either. Their dependency audits then had to carry
every Twisted advisory. daphne now lives behind the ``djust[daphne]`` extra.

These tests fail if daphne or twisted comes back as a base dependency, either
directly in ``pyproject.toml`` or transitively through the locked graph.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = {"daphne", "twisted"}

_REQ = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[([^\]]*)\])?")


def _canon(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _parse(req: str) -> tuple[str, set[str]]:
    match = _REQ.match(req)
    assert match, f"unparseable requirement: {req!r}"
    extras = {_canon(e) for e in (match.group(2) or "").split(",") if e.strip()}
    return _canon(match.group(1)), extras


def _pyproject() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]


def test_base_dependencies_do_not_name_daphne_or_twisted():
    for req in _pyproject()["dependencies"]:
        name, extras = _parse(req)
        assert name not in FORBIDDEN, f"{req!r} is a base dependency; put it behind djust[daphne]"
        if name == "channels":
            assert "daphne" not in extras, (
                f"{req!r} pulls daphne and twisted into every djust install; "
                "depend on plain `channels` and keep `channels[daphne]` in the "
                "`daphne` extra"
            )


def test_daphne_extra_provides_channels_daphne():
    extras = _pyproject()["optional-dependencies"]
    parsed = [_parse(r) for r in extras["daphne"]]
    assert ("channels", {"daphne"}) in parsed
    assert any("daphne" in _parse(r)[1] for r in extras["all"] if _parse(r)[0] == "djust"), (
        "djust[all] should include the daphne extra"
    )


def _lock_packages() -> dict[str, dict]:
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    packages: dict[str, dict] = {}
    for pkg in lock["package"]:
        # A name can appear more than once (per-Python forks); merge them so
        # the walk covers every fork's edges.
        entry = packages.setdefault(_canon(pkg["name"]), {"dependencies": [], "optional": {}})
        entry["dependencies"].extend(pkg.get("dependencies", []))
        for extra, deps in pkg.get("optional-dependencies", {}).items():
            entry["optional"].setdefault(_canon(extra), []).extend(deps)
    return packages


def test_locked_base_install_reaches_no_daphne_or_twisted():
    """Walk djust's locked base graph (no djust extras) and check it never
    reaches daphne or twisted, on any Python/platform fork."""
    lock_path = ROOT / "uv.lock"
    if not lock_path.exists():
        pytest.skip("uv.lock not present")
    packages = _lock_packages()
    assert "djust" in packages

    seen: set[tuple[str, frozenset[str]]] = set()
    paths: dict[str, list[str]] = {}
    stack: list[tuple[str, frozenset[str], list[str]]] = []
    for dep in packages["djust"]["dependencies"]:
        stack.append(
            (_canon(dep["name"]), frozenset(_canon(e) for e in dep.get("extra", [])), ["djust"])
        )

    while stack:
        name, extras, path = stack.pop()
        if (name, extras) in seen:
            continue
        seen.add((name, extras))
        label = f"{name}[{','.join(sorted(extras))}]" if extras else name
        here = path + [label]
        if name in FORBIDDEN:
            paths.setdefault(name, here)
            continue
        pkg = packages.get(name)
        if pkg is None:
            continue
        edges = list(pkg["dependencies"])
        for extra in extras:
            edges.extend(pkg["optional"].get(extra, []))
        for dep in edges:
            stack.append(
                (_canon(dep["name"]), frozenset(_canon(e) for e in dep.get("extra", [])), here)
            )

    chains = "; ".join(" -> ".join(p) for p in paths.values())
    assert not paths, f"base djust install reaches {chains}"


def test_installed_metadata_has_no_unconditional_daphne_or_twisted():
    """The built package's metadata (what pip and uv actually read) agrees."""
    from importlib import metadata

    try:
        requires = metadata.requires("djust") or []
    except metadata.PackageNotFoundError:
        pytest.skip("djust is not installed as a distribution")
    for req in requires:
        requirement, _, marker = req.partition(";")
        if "extra ==" in marker:
            continue
        name, extras = _parse(requirement)
        assert name not in FORBIDDEN, req
        assert not (name == "channels" and "daphne" in extras), req
