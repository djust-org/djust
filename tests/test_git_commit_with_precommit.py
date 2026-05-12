"""Self-test for ``scripts/git-commit-with-precommit.sh`` (closes #1464).

Builds a throwaway git repo with a synthetic pre-commit hook that rewrites
staged files (simulating ruff-format) and asserts the wrapper auto-restages
the rewrite and produces a single successful commit.

The wrapper is shell, but the test is in Python so it integrates with
pytest discovery and the existing CI layout.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
WRAPPER = REPO_ROOT / "scripts" / "git-commit-with-precommit.sh"


def _git(
    cwd: Path, *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def _make_repo(tmp: Path) -> dict[str, str]:
    """Initialise a bare-bones git repo with an isolated environment."""
    _git(tmp, "init", "-q", "-b", "main").check_returncode()
    _git(tmp, "config", "user.email", "test@example.com").check_returncode()
    _git(tmp, "config", "user.name", "Test").check_returncode()
    _git(tmp, "config", "commit.gpgsign", "false").check_returncode()
    # Empty initial commit so HEAD exists and POST_HEAD comparison works.
    _git(tmp, "commit", "-q", "--allow-empty", "-m", "initial").check_returncode()
    env = os.environ.copy()
    # Don't let the host user's global pre-commit/hooks config leak in.
    env["GIT_CONFIG_GLOBAL"] = "/dev/null"
    return env


def _install_reformatter_hook(shim_dir: Path) -> None:
    """Install a synthetic `uvx` shim that simulates ruff-format.

    The wrapper invokes `uvx pre-commit run --files <list>`. Rather than
    spin up the real `pre-commit` framework (slow + needs network), we
    short-circuit `uvx` via a `PATH` shim that rewrites the staged file
    and exits 1 — exactly the behavior we want to recover from.

    The shim lives OUTSIDE the repo under test so it doesn't pollute the
    repo's working tree (which `git status --porcelain` checks).
    """
    shim_dir.mkdir()
    shim = shim_dir / "uvx"
    # When asked to run pre-commit, rewrite ANY .py file in the arg list
    # to a canonicalised form and exit 1 (mimics ruff-format on dirty file).
    shim.write_text(
        "#!/usr/bin/env bash\n"
        "# Synthetic uvx that simulates a ruff-format auto-reformat bounce.\n"
        "set -e\n"
        'if [ "$1" = "pre-commit" ] && [ "$2" = "run" ] && [ "$3" = "--files" ]; then\n'
        "    shift 3\n"
        "    rewrote=0\n"
        '    for f in "$@"; do\n'
        '        case "$f" in\n'
        "            *.py)\n"
        "                # Only rewrite if not already canonical, to test\n"
        '                # the "second pass is a no-op" property of the wrapper.\n'
        '                if ! grep -q "^x = 1$" "$f"; then\n'
        '                    printf "x = 1\\n" > "$f"\n'
        "                    rewrote=1\n"
        "                fi\n"
        "                ;;\n"
        "        esac\n"
        "    done\n"
        '    if [ "$rewrote" -eq 1 ]; then\n'
        '        echo "ruff-format..............................................................Failed"\n'
        '        echo "- files were modified by this hook"\n'
        "        exit 1\n"
        "    fi\n"
        "    exit 0\n"
        "fi\n"
        'echo "shim: unhandled args: $*" >&2\n'
        "exit 99\n"
    )
    shim.chmod(0o755)


def _run_wrapper(tmp: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(WRAPPER), *args],
        cwd=tmp,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


@pytest.fixture
def repo(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    env = _make_repo(repo_dir)
    shim_dir = tmp_path / "shim"
    _install_reformatter_hook(shim_dir)
    env["PATH"] = f"{shim_dir}{os.pathsep}{env.get('PATH', '')}"
    return repo_dir, env


def test_wrapper_recovers_from_ruff_bounce(repo: tuple[Path, dict[str, str]]) -> None:
    """Reformatter rewrites the staged file; wrapper restages and commits."""
    tmp, env = repo
    py = tmp / "foo.py"
    py.write_text("x=1\n")  # un-canonicalised — shim will rewrite to "x = 1\n"
    _git(tmp, "add", "foo.py", env=env).check_returncode()
    pre_head = _git(tmp, "rev-parse", "HEAD", env=env).stdout.strip()

    result = _run_wrapper(tmp, env, "-m", "feat: add foo")

    assert result.returncode == 0, (
        f"wrapper failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    # Exactly one new commit landed.
    post_head = _git(tmp, "rev-parse", "HEAD", env=env).stdout.strip()
    assert post_head != pre_head
    log = _git(tmp, "log", "--oneline", env=env).stdout
    assert log.count("\n") == 2  # initial + new commit
    # Commit subject is the one we passed.
    subj = _git(tmp, "log", "-1", "--format=%s", env=env).stdout.strip()
    assert subj == "feat: add foo"
    # Working tree is clean — no leftover unstaged reformat.
    status = _git(tmp, "status", "--porcelain", env=env).stdout
    assert status == "", f"working tree not clean: {status!r}"
    # File contents reflect the reformat.
    assert py.read_text() == "x = 1\n"


def test_wrapper_rejects_unstaged(repo: tuple[Path, dict[str, str]]) -> None:
    """No staged files → exit 1, no commit."""
    tmp, env = repo
    pre_head = _git(tmp, "rev-parse", "HEAD", env=env).stdout.strip()
    result = _run_wrapper(tmp, env, "-m", "noop")
    assert result.returncode == 1
    assert "no files staged" in result.stderr
    post_head = _git(tmp, "rev-parse", "HEAD", env=env).stdout.strip()
    assert post_head == pre_head


def test_wrapper_no_reformat_needed(repo: tuple[Path, dict[str, str]]) -> None:
    """Already-canonical file → pre-commit exits 0; wrapper commits directly."""
    tmp, env = repo
    py = tmp / "bar.py"
    py.write_text("x = 1\n")  # already canonical; shim won't rewrite
    _git(tmp, "add", "bar.py", env=env).check_returncode()
    pre_head = _git(tmp, "rev-parse", "HEAD", env=env).stdout.strip()

    result = _run_wrapper(tmp, env, "-m", "feat: add bar")

    assert result.returncode == 0, result.stderr
    post_head = _git(tmp, "rev-parse", "HEAD", env=env).stdout.strip()
    assert post_head != pre_head


def test_wrapper_skipped_when_wrapper_missing() -> None:
    """The wrapper itself must exist and be executable in the repo."""
    assert WRAPPER.exists()
    assert os.access(WRAPPER, os.X_OK), f"{WRAPPER} not executable"


def test_bash_available() -> None:
    """Sanity check that bash is on PATH (the wrapper's shebang relies on it)."""
    assert shutil.which("bash") is not None
