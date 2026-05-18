#!/usr/bin/env python3
"""
Verify lockfile self-entry versions match their manifests — closes #1498.

Catches the drift class #1487 cited: `make version` bumps `pyproject.toml`
and `Cargo.toml` but historically never re-ran `uv lock` / `cargo update`,
so a lockfile's *self-entry* (the editable `djust` package in `uv.lock`,
the workspace-member crates in `Cargo.lock`) stayed pinned at the prior
version. A stale self-entry is a release-time footgun: a freshly tagged
build can ship a lockfile that disagrees with the manifest it was cut from.

This audit is mechanical and self-contained — it does NOT call git, gh,
uv, cargo, or the network (keeps it CI-fast and deterministic, matching
check-adr-status.py's no-network design). It only compares strings already
on disk.

Two checks, each hard (sets exit 1 on failure):

  uv.lock check:
    Parse the `[[package]]` block whose `name = "djust"` AND
    `source = { editable = "." }`; assert its `version` equals
    `pyproject.toml`'s `[project] version` verbatim. Both store the PEP 440
    form (e.g. `1.0.0rc1`), so a direct string equality.

  Cargo.lock check:
    Discover every workspace crate by scanning `Cargo.lock` for
    `[[package]]` blocks whose `name` starts with `djust` AND which carry
    no `source` key (workspace members have no registry source). For each,
    assert its `version` equals `Cargo.toml`'s `[workspace.package] version`
    (the Cargo form, e.g. `1.0.0-rc.1`). Crate names are discovered
    dynamically — a new workspace crate is caught automatically.

NOTE on version forms: `Cargo.toml`/`Cargo.lock` use the Cargo form
(`1.0.0-rc.1`); `pyproject.toml`/`uv.lock` use the PEP 440 form
(`1.0.0rc1`). This script compares each lockfile to its OWN manifest and
NEVER cross-compares — so it does not need to bridge the two forms.

Usage:
    python3 scripts/check-lockfile-versions.py
    python3 scripts/check-lockfile-versions.py --root /path/to/repo
    python3 scripts/check-lockfile-versions.py --verbose
    make check-lockfile-versions

Exit code:
    0 — all self-entries in sync
    1 — drift found (>=1 lockfile self-entry stale)
    2 — usage error (a lockfile or manifest is missing/unparseable)
"""

from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_toml(path: Path) -> dict:
    """Parse a TOML file. Raises FileNotFoundError if absent."""
    with path.open("rb") as fh:
        return tomllib.load(fh)


def _pyproject_version(root: Path) -> str:
    """Return `[project] version` from pyproject.toml (PEP 440 form)."""
    data = _load_toml(root / "pyproject.toml")
    return str(data["project"]["version"])


def _cargo_workspace_version(root: Path) -> str:
    """Return `[workspace.package] version` from Cargo.toml (Cargo form)."""
    data = _load_toml(root / "Cargo.toml")
    return str(data["workspace"]["package"]["version"])


def _uv_djust_self_entry(root: Path) -> str:
    """Return the version of the editable `djust` self-entry in uv.lock.

    Selects the `[[package]]` block whose name is `djust` AND whose
    `source` is `{ editable = "." }` — not a registry dep that happens
    to share the prefix.
    """
    data = _load_toml(root / "uv.lock")
    for pkg in data.get("package", []):
        if pkg.get("name") != "djust":
            continue
        source = pkg.get("source", {})
        if isinstance(source, dict) and "editable" in source:
            return str(pkg.get("version", ""))
    raise KeyError("no editable `djust` self-entry found in uv.lock")


def _cargo_lock_workspace_crates(root: Path) -> dict[str, str]:
    """Return {crate_name: version} for every djust* workspace member.

    Workspace members are `[[package]]` blocks whose name starts with
    `djust` and which carry no `source` key (registry deps always have
    one). Discovered dynamically so a new workspace crate is caught.
    """
    data = _load_toml(root / "Cargo.lock")
    crates: dict[str, str] = {}
    for pkg in data.get("package", []):
        name = pkg.get("name", "")
        if not name.startswith("djust"):
            continue
        if "source" in pkg:
            # A registry dependency, not a workspace member.
            continue
        crates[name] = str(pkg.get("version", ""))
    return crates


def run(root: Path, verbose: bool = False) -> tuple[int, str]:
    """Core logic exposed for testing.

    Returns (exit_code, message). Exit 2 for missing/unparseable inputs,
    1 for drift, 0 for in-sync.
    """
    lines: list[str] = []
    errors: list[str] = []

    # --- Load manifests + lockfiles, mapping missing files to exit 2 ---
    required = {
        "pyproject.toml": root / "pyproject.toml",
        "Cargo.toml": root / "Cargo.toml",
        "uv.lock": root / "uv.lock",
        "Cargo.lock": root / "Cargo.lock",
    }
    for label, path in required.items():
        if not path.is_file():
            return 2, f"ERROR: {label} not found: {path}"

    try:
        py_version = _pyproject_version(root)
        cargo_version = _cargo_workspace_version(root)
        uv_djust = _uv_djust_self_entry(root)
        cargo_crates = _cargo_lock_workspace_crates(root)
    except (tomllib.TOMLDecodeError, KeyError) as exc:
        return 2, f"ERROR: failed to parse a manifest/lockfile: {exc}"

    if verbose:
        lines.append(f"pyproject.toml [project] version:        {py_version}")
        lines.append(f"Cargo.toml [workspace.package] version:  {cargo_version}")
        lines.append(f"uv.lock djust self-entry:                {uv_djust}")
        for name in sorted(cargo_crates):
            lines.append(f"Cargo.lock {name}: {cargo_crates[name]}")

    # --- uv.lock check ---
    if uv_djust != py_version:
        errors.append(
            f"uv.lock djust self-entry is {uv_djust}, expected "
            f"{py_version} (matching pyproject.toml) — run `uv lock`."
        )

    # --- Cargo.lock check ---
    if not cargo_crates:
        errors.append(
            "Cargo.lock has no djust* workspace-member entries — "
            "expected the djust_* workspace crates."
        )
    for name in sorted(cargo_crates):
        actual = cargo_crates[name]
        if actual != cargo_version:
            errors.append(
                f"Cargo.lock {name} self-entry is {actual}, expected "
                f"{cargo_version} (matching Cargo.toml [workspace.package]) "
                f"— run `cargo update --workspace`."
            )

    if errors:
        lines.append(
            f"Found {len(errors)} stale lockfile self-entr"
            f"{'y' if len(errors) == 1 else 'ies'}:"
        )
        for e in errors:
            lines.append(f"  {e}")
        return 1, "\n".join(lines)

    lines.append(
        f"OK — uv.lock + Cargo.lock self-entries in sync "
        f"({len(cargo_crates)} djust crate(s) checked)"
    )
    return 0, "\n".join(lines)


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument(
        "--root",
        default=None,
        help="Repo root containing the manifests/lockfiles (default: "
        "the djust repo root)",
    )
    p.add_argument(
        "--verbose",
        action="store_true",
        help="Print each parsed version line before the verdict",
    )
    return p


def main(argv=None):
    args = build_arg_parser().parse_args(argv)

    root = Path(args.root) if args.root else ROOT
    if not root.is_dir():
        print(f"ERROR: root directory not found: {root}")
        sys.exit(2)

    exit_code, msg = run(root, verbose=args.verbose)
    print(msg)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
