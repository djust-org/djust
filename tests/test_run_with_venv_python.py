"""Self-test for ``scripts/run-with-venv-python.sh`` (closes #1796).

The native pre-push hook (and several ``make`` targets) used to hardcode
``.venv/bin/python`` relative to the current working directory. That works in
the main checkout but FAILS inside a ``git worktree`` (which has no ``.venv``),
forcing ``git push --no-verify`` and skipping the real gates. The resolver
script fixes this by locating the interpreter relative to the MAIN working tree
root, with ``uv run python`` / ``python3`` fallbacks.

These tests build throwaway git repos + worktrees (the bug's exact scenario)
and assert the resolver resolves an interpreter and runs there instead of
erroring on a missing ``.venv/bin/python``. The resolver is shell, but the
tests are Python so they integrate with pytest discovery / the CI layout
(mirrors ``tests/test_git_commit_with_precommit.py``).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
RESOLVER = REPO_ROOT / "scripts" / "run-with-venv-python.sh"


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    # Don't let the host user's global git config leak in.
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


def _run_resolver(
    cwd: Path, *args: str, extra_path: str | None = None
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    if extra_path is not None:
        env["PATH"] = extra_path
    return subprocess.run(
        ["bash", str(RESOLVER), *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def _make_repo_with_venv(tmp: Path) -> tuple[Path, Path]:
    """Create a git repo whose main checkout has a fake ``.venv/bin/python``.

    Returns ``(main_root, fake_python)``.
    """
    main = tmp / "main"
    main.mkdir()
    _git(main, "init", "-q", "-b", "main").check_returncode()
    _git(main, "config", "user.email", "test@example.com").check_returncode()
    _git(main, "config", "user.name", "Test").check_returncode()
    # Copy the resolver into the repo so it's callable by its in-repo path.
    (main / "scripts").mkdir()
    shutil.copy(RESOLVER, main / "scripts" / RESOLVER.name)
    _git(main, "add", "-A").check_returncode()
    _git(main, "commit", "-q", "-m", "init").check_returncode()

    # Build a fake venv interpreter that just prints a sentinel so we can
    # prove the resolver execs THIS interpreter (not some PATH python).
    venv_bin = main / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    fake_python = venv_bin / "python"
    fake_python.write_text(
        "#!/usr/bin/env bash\n"
        "# Fake venv interpreter for the resolver self-test.\n"
        'if [ "$1" = "--marker" ]; then echo "MAIN_VENV_PYTHON"; exit 0; fi\n'
        "exit 0\n"
    )
    fake_python.chmod(0o755)
    return main, fake_python


def test_resolver_finds_main_venv_from_worktree(tmp_path: Path) -> None:
    """The bug: a worktree has no ``.venv`` — resolver must find the main one.

    This is the exact #1796 scenario. Without the fix, ``.venv/bin/python``
    relative to the worktree cwd does not exist (exit 127).
    """
    main, fake_python = _make_repo_with_venv(tmp_path)
    worktree = tmp_path / "wt"
    _git(main, "worktree", "add", "-q", "-b", "feature", str(worktree)).check_returncode()
    assert not (worktree / ".venv").exists(), "precondition: worktree has no .venv"

    # --print mode: must emit the MAIN checkout's interpreter path.
    printed = _run_resolver(worktree, "--print")
    assert printed.returncode == 0, printed.stderr
    assert printed.stdout.strip() == str(fake_python), printed.stdout

    # exec mode: must actually run the main venv's interpreter.
    ran = _run_resolver(worktree, "--marker")
    assert ran.returncode == 0, ran.stderr
    assert ran.stdout.strip() == "MAIN_VENV_PYTHON", ran.stdout


def test_resolver_finds_main_venv_from_main_checkout(tmp_path: Path) -> None:
    """The resolver must work identically from the main checkout (no regression)."""
    main, fake_python = _make_repo_with_venv(tmp_path)
    printed = _run_resolver(main, "--print")
    assert printed.returncode == 0, printed.stderr
    assert printed.stdout.strip() == str(fake_python), printed.stdout


def test_resolver_falls_back_to_python3_when_no_venv(tmp_path: Path) -> None:
    """No ``.venv`` anywhere + no ``uv`` on PATH -> fall back to ``python3``.

    We point PATH at a directory containing only a sentinel ``python3`` (and
    the real ``git``/``bash``/``dirname`` from /usr/bin) so the resolver can
    still run but ``uv`` is absent.
    """
    main = tmp_path / "main"
    main.mkdir()
    _git(main, "init", "-q", "-b", "main").check_returncode()
    _git(main, "config", "user.email", "test@example.com").check_returncode()
    _git(main, "config", "user.name", "Test").check_returncode()
    # No .venv created.

    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    fake_py3 = fakebin / "python3"
    fake_py3.write_text(
        "#!/usr/bin/env bash\n"
        'if [ "$1" = "--marker" ]; then echo "FALLBACK_PYTHON3"; exit 0; fi\n'
        "exit 0\n"
    )
    fake_py3.chmod(0o755)
    # Symlink the real tools the resolver needs (git, bash, dirname, command).
    real_dirs = ["/usr/bin", "/bin"]
    for tool in ("git", "bash", "dirname"):
        src = shutil.which(tool)
        if src:
            (fakebin / tool).symlink_to(src)

    path = os.pathsep.join([str(fakebin), *real_dirs])

    printed = _run_resolver(main, "--print", extra_path=path)
    assert printed.returncode == 0, printed.stderr
    assert printed.stdout.strip() == str(fake_py3), printed.stdout

    ran = _run_resolver(main, "--marker", extra_path=path)
    assert ran.returncode == 0, ran.stderr
    assert ran.stdout.strip() == "FALLBACK_PYTHON3", ran.stdout


def test_resolver_errors_when_no_interpreter_available(tmp_path: Path) -> None:
    """No ``.venv``, no ``uv``, no ``python3`` -> exit 1 with a helpful message."""
    main = tmp_path / "main"
    main.mkdir()
    _git(main, "init", "-q", "-b", "main").check_returncode()

    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    # Provide only the tools the resolver itself needs — NO python3, NO uv.
    for tool in ("git", "bash", "dirname"):
        src = shutil.which(tool)
        if src:
            (fakebin / tool).symlink_to(src)

    path = str(fakebin)  # deliberately minimal; no /usr/bin python3/uv
    res = _run_resolver(main, "--print", extra_path=path)
    assert res.returncode == 1, (res.returncode, res.stdout, res.stderr)
    assert "no Python interpreter found" in res.stderr, res.stderr


def test_repo_pre_push_entries_route_through_resolver() -> None:
    """Source-pin: no pre-push entry in ``.pre-commit-config.yaml`` may hardcode
    ``.venv/bin/python`` — they must route through the resolver (#1796).

    Gate-off self-test (#1468): if a future edit reintroduces a raw
    ``.venv/bin/python`` entry, this assertion fails.
    """
    config = (REPO_ROOT / ".pre-commit-config.yaml").read_text()
    assert ".venv/bin/python" not in config, (
        "A hook entry hardcodes '.venv/bin/python' — it will fail inside a "
        "git worktree (#1796). Route it through scripts/run-with-venv-python.sh."
    )
    assert "run-with-venv-python.sh" in config, (
        "Expected pre-push hooks to route through the venv resolver."
    )


def test_makefile_routes_through_resolver() -> None:
    """Source-pin: the Makefile must not hardcode ``.venv/bin/python`` (#1796)."""
    makefile = (REPO_ROOT / "Makefile").read_text()
    assert ".venv/bin/python" not in makefile, (
        "The Makefile hardcodes '.venv/bin/python' — `make test` fails inside "
        "a git worktree (#1796). Use the $(PYTHON) variable instead."
    )
    assert "run-with-venv-python.sh" in makefile, (
        "Expected the Makefile PYTHON variable to use the venv resolver."
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
