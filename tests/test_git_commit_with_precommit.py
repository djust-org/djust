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


def test_wrapper_multi_file_partial_rewrite(repo: tuple[Path, dict[str, str]]) -> None:
    """Two staged files, only one rewritten — wrapper re-stages only the rewritten one."""
    tmp, env = repo
    dirty = tmp / "dirty.py"
    clean = tmp / "clean.py"
    dirty.write_text("x=1\n")  # will be rewritten by shim
    clean.write_text("x = 1\n")  # already canonical
    _git(tmp, "add", "dirty.py", "clean.py", env=env).check_returncode()

    result = _run_wrapper(tmp, env, "-m", "feat: multi")

    assert result.returncode == 0, result.stderr
    # Only ONE file should appear in the "rewrote N file(s)" message —
    # locks in the per-file diff check rather than blanket re-stage.
    assert "rewrote 1 file" in result.stdout, result.stdout
    status = _git(tmp, "status", "--porcelain", env=env).stdout
    assert status == "", f"working tree not clean: {status!r}"


def test_wrapper_filename_with_space(repo: tuple[Path, dict[str, str]]) -> None:
    """Filename with embedded space must not corrupt the re-stage path (Stage 11 #1)."""
    tmp, env = repo
    weird = tmp / "my file.py"
    weird.write_text("x=1\n")
    _git(tmp, "add", "my file.py", env=env).check_returncode()
    pre_head = _git(tmp, "rev-parse", "HEAD", env=env).stdout.strip()

    result = _run_wrapper(tmp, env, "-m", "feat: weirdname")

    assert result.returncode == 0, (
        f"wrapper failed on space-in-name:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    post_head = _git(tmp, "rev-parse", "HEAD", env=env).stdout.strip()
    assert post_head != pre_head
    status = _git(tmp, "status", "--porcelain", env=env).stdout
    assert status == "", f"working tree not clean for space-in-name: {status!r}"
    assert weird.read_text() == "x = 1\n"


def test_wrapper_preserves_unstaged_hunks(repo: tuple[Path, dict[str, str]]) -> None:
    """`git add -p`-style partial stage: unstaged hunks must NOT be promoted.

    Exercises the REWRITE path: staged.py needs reformatting (forcing the
    auto-restage branch); other.txt has unstaged local changes; the
    auto-restage must scope to staged.py ONLY and leave other.txt's
    unstaged hunks where they were. A regression that swapped
    `git add -- "${REWRITTEN[@]}"` for `git add -A` would promote
    other.txt's unstaged changes into the commit.
    """
    tmp, env = repo
    # File 1 (other.txt): commit a baseline, then modify locally WITHOUT
    # staging. The wrapper must not promote this change to the commit.
    other = tmp / "other.txt"
    other.write_text("v1\n")
    _git(tmp, "add", "other.txt", env=env).check_returncode()
    _git(tmp, "commit", "-q", "-m", "prep", env=env).check_returncode()
    other.write_text("v2-unstaged\n")  # local change, not in index

    # File 2 (staged.py): create + stage in un-canonical form so the shim
    # WILL rewrite it, forcing the wrapper into the recovery branch.
    staged = tmp / "staged.py"
    staged.write_text("x=1\n")
    _git(tmp, "add", "staged.py", env=env).check_returncode()

    result = _run_wrapper(tmp, env, "-m", "feat: partial")

    assert result.returncode == 0, result.stderr
    # Recovery branch was taken — locks in that the unstaged-hunks
    # protection applies to the path where auto-restage actually runs.
    assert "rewrote 1 file" in result.stdout, result.stdout
    # The new commit must contain ONLY staged.py (NOT other.txt).
    files = _git(tmp, "show", "--name-only", "--format=", "HEAD", env=env).stdout.strip()
    assert files == "staged.py", f"unexpected files in commit: {files!r}"
    # `other.txt` should still show as unstaged (M) — the load-bearing assertion.
    status = _git(tmp, "status", "--porcelain", env=env).stdout
    assert " M other.txt" in status, f"expected unstaged other.txt, got: {status!r}"
    assert other.read_text() == "v2-unstaged\n"


def test_wrapper_outside_git_repo(tmp_path: Path) -> None:
    """Invocation outside a git repo gives a clean exit-1 instead of git's raw usage."""
    env = os.environ.copy()
    env["GIT_CONFIG_GLOBAL"] = "/dev/null"
    bare = tmp_path / "not-a-repo"
    bare.mkdir()
    result = subprocess.run(
        [str(WRAPPER), "-m", "x"],
        cwd=bare,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert result.returncode == 1
    assert "not in a git repository" in result.stderr


def test_wrapper_skipped_when_wrapper_missing() -> None:
    """The wrapper itself must exist and be executable in the repo."""
    assert WRAPPER.exists()
    assert os.access(WRAPPER, os.X_OK), f"{WRAPPER} not executable"


def test_bash_available() -> None:
    """Sanity check that bash is on PATH (the wrapper's shebang relies on it)."""
    assert shutil.which("bash") is not None
