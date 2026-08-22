"""Documented flag defaults must match the code (#2017 sweep debt).

The `virtual_keyed_ops` flip needed FIVE manual documentation sweeps, each of
which under-counted — and the misses were not obscure files but sentences a few
lines from ones that had just been edited.

The test that matters most here is `test_a_silently_unmatched_pattern_fails`.
A first version of the checker tried to detect prose self-contradiction and did
not fire on an empirical replay of the real bug, because the claim it needed to
see never named the flag. A checker that quietly finds nothing looks identical
to a checker that found nothing wrong — so "no match" must be a hard error, and
that property needs its own test.
"""

from __future__ import annotations

import pathlib
import re
import shutil
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "check-flag-default-consistency.py"


def run(root: pathlib.Path | None = None) -> subprocess.CompletedProcess:
    """Run the checker rooted at `root`.

    The script resolves its repo root from its OWN location, so a sandbox run
    must invoke the sandbox's COPY of the script — passing `cwd` alone would
    silently read the real repo and make every mutation test vacuous.
    """
    root = root or REPO
    script = root / "scripts" / "check-flag-default-consistency.py"
    return subprocess.run([sys.executable, str(script)], cwd=root, capture_output=True, text=True)


@pytest.fixture
def sandbox(tmp_path: pathlib.Path):
    """A copy of the files the checker reads, so mutations never touch the repo."""
    for rel in (
        "scripts/check-flag-default-consistency.py",
        "python/djust/config.py",
        "crates/djust_vdom/src/diff.rs",
        "docs/adr/026-dj-virtual-differ-awareness.md",
    ):
        dst = tmp_path / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO / rel, dst)
    return tmp_path


def sub(root: pathlib.Path, rel: str, old: str, new: str) -> None:
    p = root / rel
    s = p.read_text()
    assert old in s, f"fixture text not found in {rel}: {old[:60]!r}"
    p.write_text(s.replace(old, new, 1))


def test_script_exists() -> None:
    assert SCRIPT.is_file()


def test_the_repo_is_currently_consistent() -> None:
    r = run()
    assert r.returncode == 0, f"docs disagree with config.py:\n{r.stderr}"


def test_flipping_the_code_default_fails(sandbox: pathlib.Path) -> None:
    """The core invariant: change the code, docs must follow."""
    sub(
        sandbox,
        "python/djust/config.py",
        '"virtual_keyed_ops": True,',
        '"virtual_keyed_ops": False,',
    )
    r = run(sandbox)
    assert r.returncode == 1
    # Every registered site should now disagree, not just one.
    assert r.stderr.count("defaults ON, but config.py says OFF") >= 3


def test_reverting_one_doc_site_fails(sandbox: pathlib.Path) -> None:
    """The real #2017 drift shape: code moved on, one doc did not."""
    # Version-agnostic anchors: pinning the release number here broke these
    # fixtures on the 1.1.1 -> 1.1.0 rename, and would break again every bump.
    sub(
        sandbox,
        "docs/adr/026-dj-virtual-differ-awareness.md",
        "| 3. flag flips ON after a soak | **shipped",
        "| 3. flag flips ON after a soak | **not shipped",
    )
    r = run(sandbox)
    assert r.returncode == 1
    assert "026-dj-virtual-differ-awareness.md" in r.stderr


def test_a_silently_unmatched_pattern_fails(sandbox: pathlib.Path) -> None:
    """Deleting the statement must FAIL, not silently pass.

    This is the property whose absence made the checker's first version
    worthless: it found nothing and reported success.
    """
    sub(
        sandbox,
        "crates/djust_vdom/src/diff.rs",
        "/// Default ON since",
        "/// (statement removed)",
    )
    r = run(sandbox)
    assert r.returncode == 1, "a deleted default-statement must fail, not pass"
    assert "no statement of" in r.stderr


def test_every_registered_site_actually_matches_today() -> None:
    """No registered pattern may be dead weight.

    A pattern that matches nothing can never fail, so it would sit in the
    registry looking like coverage while providing none.
    """
    src = SCRIPT.read_text()
    sites = re.findall(r'\(\s*\n\s*"([^"]+)",\s*\n\s*r"([^"]+)",\s*\n\s*\)', src)
    assert sites, "could not parse the SITES registry — has its shape changed?"
    for rel, pattern in sites:
        text = (REPO / rel).read_text()
        assert re.search(pattern, text), f"registered pattern never matches in {rel}"


def test_the_hook_is_registered() -> None:
    cfg = (REPO / ".pre-commit-config.yaml").read_text()
    assert "check-flag-default-consistency" in cfg, (
        "the checker only helps if it runs; register it in pre-commit"
    )
