"""Generated UI discovery blocks must match their shared renderer."""

import os
from pathlib import Path
import subprocess
import sys

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts/generate-ai-ui-section.py"
DOCS = ("llms.txt", "llms-full.txt", "BEST_PRACTICES_AI.md")
BEGIN = "<!-- BEGIN GENERATED: UI building blocks (scripts/generate-ai-ui-section.py) -->"
ENV = {**os.environ, "PYTHONPATH": os.pathsep.join([str(REPO / "python"), str(REPO)])}


def _run(*args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        env=ENV,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )


def test_committed_docs_match_the_rendering():
    result = _run()
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count("unchanged") == 3


def test_block_sits_near_the_top_of_each_doc():
    for doc, heading in zip(DOCS, ("## Docs", "## Setup", "## 1. ")):
        text = (REPO / "docs" / doc).read_text()
        assert text.index(BEGIN) < text.index(heading)


def test_a_stale_block_fails_and_write_restores_it(tmp_path):
    original = (REPO / "docs/llms.txt").read_text()
    copy = tmp_path / "llms.txt"
    copy.write_text(original.replace("{% data_table %}", "{% data_tablex %}"))
    stale = _run("--doc", str(copy))
    assert stale.returncode == 1
    assert "UI building blocks section is stale" in stale.stderr
    assert _run("--doc", str(copy), "--write").returncode == 0
    assert copy.read_text() == original
    again = _run("--doc", str(copy), "--write")
    assert again.returncode == 0 and "unchanged" in again.stdout


def test_missing_markers_are_an_error(tmp_path):
    copy = tmp_path / "llms.txt"
    copy.write_text("# UI\n")
    result = _run("--doc", str(copy))
    assert result.returncode == 2
    assert "markers not found" in result.stderr
