"""Drift test for scripts/generate-interactive-reference.py (ADR-034 C4-Q2).

The interactive-components tables in docs/website/api-reference/components.md
are generated from the running contracts. The check must pass on the
committed page, fail with a named message on a hand-edited copy, and
``--write`` must restore the block and be idempotent.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

_REPO = pathlib.Path(__file__).resolve().parents[1]
_SCRIPT = _REPO / "scripts" / "generate-interactive-reference.py"
_DOC = _REPO / "docs" / "website" / "api-reference" / "components.md"
_ENV = {**os.environ, "PYTHONPATH": os.pathsep.join([str(_REPO / "python"), str(_REPO)])}


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_SCRIPT), *args],
        capture_output=True,
        text=True,
        env=_ENV,
        check=False,
        timeout=120,
    )


def test_committed_block_matches_the_contracts():
    result = _run()
    assert result.returncode == 0, result.stdout + result.stderr
    assert "unchanged" in result.stdout


def test_generated_block_names_every_output_and_action():
    block = _run("--print").stdout
    for name in ("@menu.on.selected", "@menu.on.toggled", "`select`", "`toggle`", "`close`"):
        assert name in block, name
    assert "`value: str`" in block and "rows.get()" in block


def test_a_hand_edited_block_fails_and_write_restores_it(tmp_path):
    copy = tmp_path / "components.md"
    original = _DOC.read_text(encoding="utf-8")
    copy.write_text(original.replace("@menu.on.selected", "@menu.on.chosen"), encoding="utf-8")
    stale = _run("--doc", str(copy))
    assert stale.returncode == 1
    assert "interactive reference is stale" in stale.stderr
    assert _run("--doc", str(copy), "--write").returncode == 0
    assert copy.read_text(encoding="utf-8") == original
    again = _run("--doc", str(copy), "--write")
    assert again.returncode == 0 and "unchanged" in again.stdout


def test_missing_markers_are_an_error(tmp_path):
    copy = tmp_path / "components.md"
    copy.write_text("# Components\n", encoding="utf-8")
    result = _run("--doc", str(copy))
    assert result.returncode == 2
    assert "markers not found" in result.stderr
