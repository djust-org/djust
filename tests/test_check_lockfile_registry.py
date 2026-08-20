"""The lockfile-registry guard must catch a developer-local index leak.

`uv lock` writes whichever index it resolved against into every entry's
``source = { registry = ... }``. A global ``~/.config/uv/uv.toml`` mirror
therefore leaks machine-local URLs into a shared file — silently, because uv
installs from the per-wheel ``url`` fields, so nothing fails and CI stays
green. It has leaked twice already (``e39a9242``, then ``5fe74931``).

The canary that matters is the last test: the guard is run against the REAL
leaked lockfile from git history, not a hand-written imitation (#1459).
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "check-lockfile-registry.py"

PUBLIC = "https://pypi.org/simple"
LEAK = "http://127.0.0.1:8418/pypi/simple/"


def run(path: pathlib.Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), str(path)], capture_output=True, text=True)


def write_lock(tmp_path: pathlib.Path, *registries: str) -> pathlib.Path:
    body = "\n".join(
        f'[[package]]\nname = "pkg{i}"\nversion = "1.0"\nsource = {{ registry = "{r}" }}\n'
        for i, r in enumerate(registries)
    )
    p = tmp_path / "uv.lock"
    p.write_text(body)
    return p


def test_script_exists() -> None:
    assert SCRIPT.is_file(), "the guard is what keeps the leak from recurring a third time"


def test_all_public_passes(tmp_path: pathlib.Path) -> None:
    r = run(write_lock(tmp_path, PUBLIC, PUBLIC, PUBLIC))
    assert r.returncode == 0, r.stderr
    assert "3 registry entries" in r.stdout


def test_localhost_leak_fails(tmp_path: pathlib.Path) -> None:
    r = run(write_lock(tmp_path, LEAK, LEAK))
    assert r.returncode == 1
    assert LEAK in r.stderr


def test_a_single_leaked_entry_among_many_fails(tmp_path: pathlib.Path) -> None:
    """The realistic partial case — one entry re-resolved against the mirror."""
    r = run(write_lock(tmp_path, *([PUBLIC] * 40), LEAK, *([PUBLIC] * 40)))
    assert r.returncode == 1, "one leaked entry among 80 clean ones must still fail"
    assert "1x" in r.stderr.replace("   1x", "1x")


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8418/pypi/simple/",
        "http://localhost:8080/simple",
        "https://pypi.example.internal/simple",
        "file:///Users/someone/wheels",
    ],
)
def test_non_public_indexes_all_fail(tmp_path: pathlib.Path, url: str) -> None:
    assert run(write_lock(tmp_path, url)).returncode == 1


def test_missing_file_is_not_an_error(tmp_path: pathlib.Path) -> None:
    """Absent lockfile is not this guard's business — the hook is path-scoped."""
    assert run(tmp_path / "nope.lock").returncode == 0


def test_the_repos_own_lockfile_is_clean() -> None:
    r = run(REPO / "uv.lock")
    assert r.returncode == 0, f"the committed uv.lock leaked an index:\n{r.stderr}"


def test_catches_the_real_historical_leak() -> None:
    """Empirical canary (#1459): the guard vs. the ACTUAL leaked lockfile.

    `5fe74931` is the 1.1.0 release commit, which shipped 125 localhost
    entries. A guard that only catches a synthetic fixture proves nothing about
    the shape that really occurred.
    """
    real = subprocess.run(
        ["git", "show", "5fe74931:uv.lock"],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    if real.returncode != 0:
        pytest.skip("commit 5fe74931 unavailable (shallow clone?)")

    tmp = REPO / "uv.lock.canary.tmp"
    try:
        tmp.write_text(real.stdout)
        r = run(tmp)
        assert r.returncode == 1, (
            "the guard did NOT flag the real 1.1.0 release lockfile, which "
            "contained 125 localhost registry entries"
        )
        assert "127.0.0.1:8418" in r.stderr
    finally:
        tmp.unlink(missing_ok=True)
