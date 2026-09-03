"""A fixture that shells out to `git` must not inherit git's execution variables.

Under a git hook, `GIT_DIR` and `GIT_INDEX_FILE` point at the REAL repository.
A temp-repo fixture that inherits them re-initialises that repository (which is
where a stray ``core.bare = true`` came from) and writes into its index. See
#2608; the helper is `tests/git_env.isolated_git_env`.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from tests.git_env import GIT_EXECUTION_VARS, isolated_git_env


@pytest.fixture
def ambient_git_hook_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Simulate the environment git gives a hook: GIT_DIR and friends set."""
    real = tmp_path / "real.git"
    real.mkdir()
    for name in GIT_EXECUTION_VARS:
        monkeypatch.setenv(name, str(real / name.lower()))
    monkeypatch.setenv("GIT_DIR", str(real))
    return real


def test_the_helper_strips_every_execution_variable(ambient_git_hook_env: Path) -> None:
    env = isolated_git_env()
    leaked = sorted(k for k in GIT_EXECUTION_VARS if k in env)
    assert not leaked, f"these would redirect git at the real repo: {leaked}"


def test_the_helper_keeps_overrides_and_the_rest_of_the_environment(
    ambient_git_hook_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SOME_UNRELATED_VAR", "kept")
    env = isolated_git_env(GIT_AUTHOR_NAME="t", GIT_CONFIG_GLOBAL="/dev/null")
    assert env["GIT_AUTHOR_NAME"] == "t"
    assert env["GIT_CONFIG_GLOBAL"] == "/dev/null"
    assert env["SOME_UNRELATED_VAR"] == "kept"


def test_git_init_under_an_inherited_GIT_DIR_hits_the_other_repository(
    tmp_path: Path,
) -> None:
    """The failure this guards against, demonstrated.

    With ``GIT_DIR`` inherited, ``git init`` in a fresh directory re-initialises
    the repository ``GIT_DIR`` names instead — it does not create one where it
    was run. That is the whole bug: the fixture believed it had its own repo.
    """
    victim = tmp_path / "victim"
    subprocess.run(["git", "init", "-q", str(victim)], check=True, capture_output=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    leaked = dict(os.environ, GIT_DIR=str(victim / ".git"))
    subprocess.run(
        ["git", "init", "-q"], cwd=elsewhere, env=leaked, check=True, capture_output=True
    )
    assert not (elsewhere / ".git").exists(), (
        "git init wrote to GIT_DIR, not to the directory it ran in — "
        "which is why an inherited GIT_DIR corrupts the real repository"
    )

    isolated = isolated_git_env()
    subprocess.run(
        ["git", "init", "-q"], cwd=elsewhere, env=isolated, check=True, capture_output=True
    )
    assert (elsewhere / ".git").exists(), "with the helper, git init creates a local repo"


def test_the_hook_wrapper_unsets_them_too() -> None:
    """Defence in depth: the pre-push wrapper strips them before pytest runs."""
    script = Path(__file__).resolve().parents[1] / "scripts" / "pre-push-pytest.sh"
    body = script.read_text()
    assert "GIT_DIR" in body and "GIT_INDEX_FILE" in body, (
        "scripts/pre-push-pytest.sh must unset git's execution variables before "
        "running pytest (#2608)"
    )
