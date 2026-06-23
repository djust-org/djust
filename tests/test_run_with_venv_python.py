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


class TestWorktreePythonpath:
    """`--worktree-pythonpath` mode (closes #1810).

    #1796 fixed interpreter *resolution* from a worktree, but the editable
    install (`maturin develop` -> a plain ``djust.pth`` appending the MAIN
    checkout's ``python/`` to ``sys.path``) meant a worktree push ran the
    pre-push pytest suite against the MAIN tree's source, not the worktree's
    changes. ``--worktree-pythonpath`` emits the path to PREPEND to
    ``PYTHONPATH`` so the worktree's ``python/`` wins (PYTHONPATH is inserted
    before ``.pth`` processing), and symlinks the matching compiled ``.so`` so
    ``import djust._rust`` keeps resolving.
    """

    @staticmethod
    def _seed_python_pkg(root: Path) -> None:
        """Create a minimal ``python/djust/__init__.py`` under ``root``."""
        pkg = root / "python" / "djust"
        pkg.mkdir(parents=True, exist_ok=True)
        (pkg / "__init__.py").write_text("# djust package stub\n")

    def test_emits_worktree_python_from_worktree(self, tmp_path: Path) -> None:
        """From a linked worktree (with a ``python/djust`` pkg) it emits the
        worktree's ``python/`` dir — the path the pre-push hook prepends."""
        main, _ = _make_repo_with_venv(tmp_path)
        self._seed_python_pkg(main)
        _git(main, "add", "-A").check_returncode()
        _git(main, "commit", "-q", "-m", "add pkg").check_returncode()

        worktree = tmp_path / "wt"
        _git(main, "worktree", "add", "-q", "-b", "feature", str(worktree)).check_returncode()

        res = _run_resolver(worktree, "--worktree-pythonpath")
        assert res.returncode == 0, res.stderr
        assert res.stdout.strip() == str(worktree / "python"), res.stdout

    def test_emits_nothing_from_main_checkout(self, tmp_path: Path) -> None:
        """From the editable-install target (main checkout) it is a no-op: the
        ``.pth`` already points at the right source, so no shadow is needed."""
        main, _ = _make_repo_with_venv(tmp_path)
        self._seed_python_pkg(main)
        res = _run_resolver(main, "--worktree-pythonpath")
        assert res.returncode == 0, res.stderr
        assert res.stdout.strip() == "", (
            "Expected empty output in the main checkout — emitting a path here "
            "would needlessly double-add python/ to PYTHONPATH."
        )

    def test_emits_nothing_when_worktree_has_no_python_pkg(self, tmp_path: Path) -> None:
        """A worktree without a ``python/djust`` package has nothing to shadow
        -> no-op (defends a non-djust-layout repo from a spurious prepend)."""
        main, _ = _make_repo_with_venv(tmp_path)  # no python/ pkg seeded
        worktree = tmp_path / "wt"
        _git(main, "worktree", "add", "-q", "-b", "feature", str(worktree)).check_returncode()
        res = _run_resolver(worktree, "--worktree-pythonpath")
        assert res.returncode == 0, res.stderr
        assert res.stdout.strip() == "", res.stdout

    def test_prepended_pythonpath_shadows_worktree_source(self, tmp_path: Path) -> None:
        """The behavior-meaningful test (gate-off self-test, #1468): with the
        emitted path PREPENDED to PYTHONPATH, importing ``djust`` must resolve
        the WORKTREE's source, not the main checkout's.

        This needs a REAL python interpreter (the fake bash venv can't import).
        We simulate the editable ``.pth`` by appending the MAIN tree's
        ``python/`` to PYTHONPATH (lower precedence than the prepended worktree
        path), then assert the worktree's distinct sentinel wins.

        Gate-off: WITHOUT the prepend (PYTHONPATH = main python only), the main
        sentinel wins — proving the prepend is load-bearing.
        """
        import sys

        main, _ = _make_repo_with_venv(tmp_path)
        # Give the two trees DISTINCT djust packages so we can tell which wins.
        self._seed_python_pkg(main)
        (main / "python" / "djust" / "__init__.py").write_text("WHICH = 'main'\n")
        _git(main, "add", "-A").check_returncode()
        _git(main, "commit", "-q", "-m", "add pkg").check_returncode()

        worktree = tmp_path / "wt"
        _git(main, "worktree", "add", "-q", "-b", "feature", str(worktree)).check_returncode()
        (worktree / "python" / "djust" / "__init__.py").write_text("WHICH = 'worktree'\n")

        emitted = _run_resolver(worktree, "--worktree-pythonpath").stdout.strip()
        assert emitted == str(worktree / "python"), emitted

        main_python = str(main / "python")  # stands in for the editable .pth
        probe = "import djust; print(djust.WHICH)"
        real_py = sys.executable

        # WITH the prepend: worktree source wins (the fix).
        env_fixed = os.environ.copy()
        env_fixed["PYTHONPATH"] = os.pathsep.join([emitted, main_python])
        with_fix = subprocess.run(
            [real_py, "-c", probe],
            capture_output=True,
            text=True,
            env=env_fixed,
            cwd=str(tmp_path),
        )
        assert with_fix.returncode == 0, with_fix.stderr
        assert with_fix.stdout.strip() == "worktree", (
            f"Expected worktree source to win, got {with_fix.stdout!r}"
        )

        # GATE-OFF: WITHOUT the prepend (only the .pth-equivalent main path),
        # the MAIN source wins — this is exactly the #1810 bug.
        env_broken = os.environ.copy()
        env_broken["PYTHONPATH"] = main_python
        without_fix = subprocess.run(
            [real_py, "-c", probe],
            capture_output=True,
            text=True,
            env=env_broken,
            cwd=str(tmp_path),
        )
        assert without_fix.returncode == 0, without_fix.stderr
        assert without_fix.stdout.strip() == "main", (
            "Gate-off failed: main source did NOT win without the prepend — "
            "the test no longer proves the prepend is load-bearing."
        )

    def test_symlinks_rust_so_into_worktree(self, tmp_path: Path) -> None:
        """When a matching compiled ``.so`` exists in the main tree, the mode
        symlinks it into the worktree's ``python/djust/`` so ``import
        djust._rust`` keeps working after the Python source is shadowed.

        Uses a REAL interpreter so ``sys.implementation.cache_tag`` resolves
        (the fake bash venv returns no cache tag and the symlink step no-ops).
        """
        import sys

        main = tmp_path / "main"
        main.mkdir()
        _git(main, "init", "-q", "-b", "main").check_returncode()
        _git(main, "config", "user.email", "test@example.com").check_returncode()
        _git(main, "config", "user.name", "Test").check_returncode()
        (main / "scripts").mkdir()
        shutil.copy(RESOLVER, main / "scripts" / RESOLVER.name)
        self._seed_python_pkg(main)

        # Real venv interpreter so the resolver finds it AND cache_tag works.
        venv_bin = main / ".venv" / "bin"
        venv_bin.mkdir(parents=True)
        (venv_bin / "python").symlink_to(sys.executable)

        # A fake compiled extension named with THIS interpreter's cache tag.
        cache_tag = sys.implementation.cache_tag
        so_name = f"_rust.{cache_tag}-fake.so"
        (main / "python" / "djust" / so_name).write_bytes(b"\x00fake-so\x00")

        _git(main, "add", "-A").check_returncode()
        _git(main, "commit", "-q", "-m", "init").check_returncode()

        worktree = tmp_path / "wt"
        _git(main, "worktree", "add", "-q", "-b", "feature", str(worktree)).check_returncode()

        res = _run_resolver(worktree, "--worktree-pythonpath")
        assert res.returncode == 0, res.stderr

        linked = worktree / "python" / "djust" / so_name
        assert linked.is_symlink(), f"{so_name} was not symlinked into worktree"
        assert linked.resolve() == (main / "python" / "djust" / so_name).resolve()

    def test_config_pytest_hook_uses_worktree_pythonpath(self) -> None:
        """Source-pin: the pre-push pytest hook must wire in the worktree
        shadow (#1810). Gate-off self-test (#1468): a future edit that drops
        the ``--worktree-pythonpath`` plumbing fails this assertion.

        We match on the hook's ``entry:`` LINE specifically (not the whole
        file) so an explanatory comment mentioning the flag can't make this
        pass on its own — the load-bearing wiring is the entry command.
        """
        config = (REPO_ROOT / ".pre-commit-config.yaml").read_text()
        entry_lines = [
            ln
            for ln in config.splitlines()
            if ln.lstrip().startswith("entry:") and "pytest tests/" in ln
        ]
        assert entry_lines, "Could not find the pytest pre-push hook entry line."
        assert any("--worktree-pythonpath" in ln for ln in entry_lines), (
            "The pre-push pytest hook ENTRY must prepend the worktree's "
            "python/ to PYTHONPATH via "
            "`run-with-venv-python.sh --worktree-pythonpath` so a worktree "
            "push tests the worktree's source (#1810). Found entry line(s): "
            f"{entry_lines!r}"
        )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
