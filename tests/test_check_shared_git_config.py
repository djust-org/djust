"""Self-test for ``scripts/check-shared-git-config.sh`` (closes #1938).

#1938: a worktree's shared ``.git/config`` can get ``core.bare = true`` written
into it (a build/PyO3-repoint step, an IDE integration, a stray manual
command), which corrupts git in BOTH the worktree AND the main checkout (every
tracked file shows as deleted). The detector script reads ``core.bare`` from the
SHARED config — resolved via ``--git-common-dir`` so it works from any linked
worktree — and reports / optionally auto-recovers the leak.

These tests build a throwaway main-repo + linked-worktree (the bug's exact
topology) IN AN ISOLATED tmp dir, simulate the leak in the throwaway repo's
shared config, and assert the script detects it and ``--fix`` recovers it.
Every git operation runs against ``tmp_path`` with ``GIT_CONFIG_GLOBAL``/
``GIT_CONFIG_SYSTEM`` pointed at ``/dev/null`` — the real repo's config is
NEVER touched. The script itself NEVER writes ``core.bare true``; its only
write is the recovery ``core.bare false`` behind ``--fix``.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CHECKER = REPO_ROOT / "scripts" / "check-shared-git-config.sh"


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    # Isolate from the host user's global/system git config so a real-repo
    # setting can never influence (or be influenced by) these throwaway repos.
    env["GIT_CONFIG_GLOBAL"] = "/dev/null"
    env["GIT_CONFIG_SYSTEM"] = "/dev/null"
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def _run_checker(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["GIT_CONFIG_GLOBAL"] = "/dev/null"
    env["GIT_CONFIG_SYSTEM"] = "/dev/null"
    return subprocess.run(
        ["bash", str(CHECKER), *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


@pytest.fixture
def main_and_worktree(tmp_path: Path) -> tuple[Path, Path]:
    """Build a throwaway main repo + a linked worktree (the #1938 topology).

    Returns ``(main_root, worktree_root)``. The worktree's ``.git`` is a FILE
    pointing at the shared ``.git`` dir, exactly like a real linked worktree.
    """
    main = tmp_path / "main"
    main.mkdir()
    _git(main, "init", "-q", "-b", "main").check_returncode()
    _git(main, "config", "user.email", "test@example.com").check_returncode()
    _git(main, "config", "user.name", "Test").check_returncode()
    (main / "f.txt").write_text("hi\n")
    _git(main, "add", "-A").check_returncode()
    _git(main, "commit", "-q", "-m", "init").check_returncode()

    worktree = tmp_path / "wt"
    _git(main, "worktree", "add", "-q", "-b", "feature", str(worktree)).check_returncode()
    # Precondition: the worktree's .git is a FILE (linked worktree), and the
    # shared config lives under the MAIN tree — the #1938 topology.
    assert (worktree / ".git").is_file(), "precondition: linked worktree .git is a file"
    return main, worktree


def _shared_config_path(main: Path) -> Path:
    return main / ".git" / "config"


def test_checker_passes_when_bare_false(main_and_worktree: tuple[Path, Path]) -> None:
    """Healthy repo (bare=false): exit 0 from BOTH the worktree and the main."""
    main, worktree = main_and_worktree
    for cwd in (main, worktree):
        res = _run_checker(cwd)
        assert res.returncode == 0, f"from {cwd}: {res.stderr}"
        assert "OK" in res.stdout, res.stdout


def test_checker_detects_leak_from_worktree(main_and_worktree: tuple[Path, Path]) -> None:
    """The load-bearing test: with core.bare=true in the SHARED config, the
    checker — run from the WORKTREE — detects it and exits non-zero.

    Gate-off (#1468): if the script read the wrong config (e.g. a per-worktree
    file instead of the shared one) it would NOT see the leak and this would
    pass with exit 0 — making the assertion meaningful, not tautological.
    """
    main, worktree = main_and_worktree
    # Simulate the leak directly in the throwaway shared config file.
    _git(
        main, "config", "--file", str(_shared_config_path(main)), "core.bare", "true"
    ).check_returncode()

    res = _run_checker(worktree)
    assert res.returncode == 1, (
        f"checker should fail on a core.bare=true leak. stdout={res.stdout!r} stderr={res.stderr!r}"
    )
    assert "LEAK DETECTED" in res.stderr, res.stderr
    # Without --fix the checker must NOT mutate the config.
    still = _git(main, "config", "--file", str(_shared_config_path(main)), "--get", "core.bare")
    assert still.stdout.strip() == "true", "non-fix run must not change core.bare"


def test_checker_fix_recovers_leak(main_and_worktree: tuple[Path, Path]) -> None:
    """``--fix`` resets a leaked core.bare to false (the documented recovery)."""
    main, worktree = main_and_worktree
    _git(
        main, "config", "--file", str(_shared_config_path(main)), "core.bare", "true"
    ).check_returncode()

    res = _run_checker(worktree, "--fix")
    assert res.returncode == 0, res.stderr
    assert "RECOVERED" in res.stderr, res.stderr
    recovered = _git(main, "config", "--file", str(_shared_config_path(main)), "--get", "core.bare")
    assert recovered.stdout.strip() == "false", (
        f"--fix should reset core.bare to false, got {recovered.stdout!r}"
    )


def test_checker_never_writes_bare_true(main_and_worktree: tuple[Path, Path]) -> None:
    """Hard invariant: the checker NEVER sets core.bare=true.

    Starting from a healthy repo, neither the default check nor ``--fix`` may
    flip bare to true — the script's only legal write is the recovery to false.
    """
    main, worktree = main_and_worktree
    for args in ((), ("--fix",)):
        _run_checker(worktree, *args)
        bare = _git(main, "config", "--file", str(_shared_config_path(main)), "--get", "core.bare")
        # `false` or unset are both fine; `true` is the forbidden state.
        assert bare.stdout.strip() != "true", (
            f"checker {args} flipped core.bare to true — forbidden"
        )


def test_checker_script_exists_and_executable() -> None:
    """Source-pin: the detector script must exist and be executable."""
    assert CHECKER.exists(), f"{CHECKER} missing"
    assert os.access(CHECKER, os.X_OK), f"{CHECKER} not executable"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
