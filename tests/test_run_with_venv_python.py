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
    # Like the real repo: a venv is never committed, so a linked worktree does
    # not inherit one (#2526 — a worktree that OWNS a `.venv` is preferred).
    (main / ".gitignore").write_text(".venv/\n")
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


def _give_worktree_own_venv(worktree: Path) -> Path:
    """A `make worktree-env` result: the worktree's own fake interpreter."""
    venv_bin = worktree / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    own = venv_bin / "python"
    own.write_text(
        "#!/usr/bin/env bash\n"
        'if [ "$1" = "--marker" ]; then echo "WORKTREE_OWN_VENV_PYTHON"; exit 0; fi\n'
        "exit 0\n"
    )
    own.chmod(0o755)
    return own


def test_worktree_own_venv_wins_over_main(tmp_path: Path) -> None:
    """`make worktree-env` (#2526): a checkout that owns a `.venv` is resolved
    to THAT interpreter, not the main checkout's — its own `djust.pth` points
    at itself, so the build it made is the one its tests must run against."""
    main, _ = _make_repo_with_venv(tmp_path)
    worktree = tmp_path / "wt"
    _git(main, "worktree", "add", "-q", "-b", "feature", str(worktree)).check_returncode()
    own = _give_worktree_own_venv(worktree)

    res = _run_resolver(worktree, "--marker")
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == "WORKTREE_OWN_VENV_PYTHON", res.stdout
    printed = _run_resolver(worktree, "--print")
    assert printed.stdout.strip() == str(own), printed.stdout
    # The main checkout is unaffected.
    assert _run_resolver(main, "--marker").stdout.strip() == "MAIN_VENV_PYTHON"


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

    def test_emits_nothing_when_worktree_owns_a_venv(self, tmp_path: Path) -> None:
        """A `make worktree-env` checkout (#2526) needs no shadow: its own
        `djust.pth` already points at its `python/`, and symlinking the MAIN
        tree's `.so` in would clobber the extension it built for itself."""
        main, _ = _make_repo_with_venv(tmp_path)
        self._seed_python_pkg(main)
        _git(main, "add", "-A").check_returncode()
        _git(main, "commit", "-q", "-m", "add pkg").check_returncode()
        worktree = tmp_path / "wt"
        _git(main, "worktree", "add", "-q", "-b", "feature", str(worktree)).check_returncode()
        _give_worktree_own_venv(worktree)
        own_so = worktree / "python" / "djust" / "_rust.cpython-000-x.so"
        own_so.write_text("built here")

        res = _run_resolver(worktree, "--worktree-pythonpath")
        assert res.returncode == 0, res.stderr
        assert res.stdout.strip() == "", res.stdout
        assert not own_so.is_symlink() and own_so.read_text() == "built here"

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

        # The venv is never committed (as in the real repo), so the worktree
        # does not inherit it and reads as one WITHOUT its own venv (#2526).
        (main / ".gitignore").write_text(".venv/\n")
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
        """Source-pin: the pre-push pytest path must wire in the worktree
        shadow (#1810). Gate-off self-test (#1468): a future edit that drops
        the ``--worktree-pythonpath`` plumbing fails this assertion.

        The wiring MOVED in #2139. The hook's entry used to inline the whole
        command; it now calls ``scripts/pre-push-pytest.sh``, which runs the
        suite and, on failure, re-runs only the failing tests against the
        merge-base so a blocked push can tell whose failures blocked it. The
        worktree plumbing went with it.

        So this follows the wiring rather than the line it used to sit on:
        find whatever the hook actually invokes, and assert the flag is there.
        Pinning the old entry shape would fail on a refactor that KEPT the
        behaviour, and asserting against the whole config would pass on a
        comment alone.
        """
        config = (REPO_ROOT / ".pre-commit-config.yaml").read_text()
        entry_lines = [
            ln
            for ln in config.splitlines()
            if ln.lstrip().startswith("entry:")
            and ("pytest tests/" in ln or "pre-push-pytest.sh" in ln)
        ]
        assert entry_lines, "Could not find the pytest pre-push hook entry line."

        def _carries_flag(line: str) -> bool:
            # Either the entry carries the flag itself, or it delegates to a
            # script that does.
            if "--worktree-pythonpath" in line:
                return True
            for token in line.split():
                candidate = token.strip("'\"")
                if candidate.endswith(".sh"):
                    script = REPO_ROOT / candidate
                    if not script.is_file():
                        continue
                    # NON-COMMENT lines only. Grepping the whole file would
                    # pass on a comment mentioning the flag — the exact hole
                    # this docstring disclaims one level up, reintroduced one
                    # level down. Gate-off proved it: flag moved into a
                    # comment with the plumbing gone, and the test stayed green.
                    for sline in script.read_text().splitlines():
                        if sline.lstrip().startswith("#"):
                            continue
                        if "--worktree-pythonpath" in sline:
                            return True
            return False

        assert any(_carries_flag(ln) for ln in entry_lines), (
            "The pre-push pytest hook must prepend the worktree's python/ to "
            "PYTHONPATH via `run-with-venv-python.sh --worktree-pythonpath` so "
            "a worktree push tests the worktree's source (#1810) — either in "
            f"its entry line or in the script it delegates to. Found: {entry_lines!r}"
        )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
