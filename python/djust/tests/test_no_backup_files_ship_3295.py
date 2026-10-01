"""#3295 — a stale ``client.js.backup`` (215 KB, unreferenced) was tracked in
git and shipped in every wheel. Keep ``*.backup`` files out of the repo and out
of the sdist/wheel.
"""

from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]


def test_no_tracked_backup_files():
    if not (REPO / ".git").exists():
        pytest.skip("not a git checkout")
    out = subprocess.run(
        ["git", "ls-files", "*.backup"], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout.split()
    assert out == [], f"tracked *.backup files would ship in the package: {out}"


def test_maturin_excludes_backup_files():
    cfg = tomllib.loads((REPO / "pyproject.toml").read_text())
    assert "**/*.backup" in cfg["tool"]["maturin"].get("exclude", [])


def test_backup_files_are_gitignored():
    assert "*.backup" in (REPO / ".gitignore").read_text().splitlines()
