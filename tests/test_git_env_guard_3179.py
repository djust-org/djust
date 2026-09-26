"""No test may run `git` with an environment that names the real repository (#3179).

The main checkout's ``.git/config`` gained ``user.name = Test``,
``user.email = test@example.com`` and ``commit.gpgsign = false``, and commits
made there were then authored "Test". The cause was a test fixture that built
its git environment from the whole ``os.environ``. Whenever pytest runs with
``GIT_DIR`` / ``GIT_INDEX_FILE`` exported, as it does under a git hook, the
fixture's ``git init`` re-initialises the repository ``GIT_DIR`` names instead
of creating one in ``tmp_path``. Its ``git config user.name Test`` then lands in
the real config. This is the #2608 class, the one that also wrote
``core.bare = true``.

This module guards that class in two ways:

* **Behavioural.** It runs the helpers of the two fixtures that did this, with
  ``GIT_DIR`` aimed at a throwaway "victim" repository, and asserts the victim's
  config is byte-for-byte unchanged. Both tests fail on the pre-#3179 helpers.
* **Static.** Every test module that spawns ``git``, directly or through a
  ``scripts/`` file that runs git, must protect each subprocess call. It does
  that either with an autouse fixture that deletes
  ``tests.git_env.GIT_EXECUTION_VARS``, or by passing
  ``env=isolated_git_env(...)`` to the call. A new unprotected module fails
  here, before it can reach a hook.
"""

from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

import pytest

import tests.test_check_shared_git_config as shared_config_test
import tests.test_git_commit_with_precommit as precommit_test
from tests.git_env import GIT_EXECUTION_VARS, isolated_git_env

REPO_ROOT = Path(__file__).resolve().parent.parent
TEST_ROOTS = ("tests", "python/tests", "python/djust/tests")


@pytest.fixture(autouse=True)
def _no_inherited_git_env(monkeypatch):
    """This module runs git itself. Under a git hook an inherited GIT_DIR would
    aim those commands at the real repository (#2608, #3179)."""
    for var in GIT_EXECUTION_VARS:
        monkeypatch.delenv(var, raising=False)


# ---------------------------------------------------------------------------
# Behavioural: the two fixtures that wrote into the real config
# ---------------------------------------------------------------------------


@pytest.fixture
def victim(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, str]:
    """A throwaway repository standing in for the real one, exported the way a
    git hook exports it. Returns ``(victim_git_dir, config_before)``."""
    repo = tmp_path / "victim"
    subprocess.run(
        ["git", "init", "-q", str(repo)],
        check=True,
        capture_output=True,
        env=isolated_git_env(GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null"),
    )
    git_dir = repo / ".git"
    before = (git_dir / "config").read_text()
    monkeypatch.setenv("GIT_DIR", str(git_dir))
    monkeypatch.setenv("GIT_INDEX_FILE", str(git_dir / "index"))
    return git_dir, before


def _local_config(repo: Path, key: str) -> str:
    return subprocess.run(
        ["git", "config", "--file", str(repo / ".git" / "config"), "--get", key],
        capture_output=True,
        text=True,
        env=isolated_git_env(),
    ).stdout.strip()


def test_shared_git_config_helpers_leave_an_inherited_GIT_DIR_alone(
    victim: tuple[Path, str], tmp_path: Path
) -> None:
    victim_dir, before = victim
    repo = tmp_path / "fixture-repo"
    repo.mkdir()

    shared_config_test._git(repo, "init", "-q", "-b", "main").check_returncode()
    shared_config_test._git(repo, "config", "user.email", "test@example.com").check_returncode()
    shared_config_test._git(repo, "config", "user.name", "Test").check_returncode()
    assert (victim_dir / "config").read_text() == before, (
        "_git wrote user.name=Test into the repository GIT_DIR named — under a "
        "hook that is the real checkout (#3179)"
    )
    _git_bare = ("config", "--file", str(repo / ".git" / "config"), "core.bare", "true")
    shared_config_test._git(repo, *_git_bare).check_returncode()
    # The checker runs git itself: it must inspect (and --fix) the fixture repo.
    res = shared_config_test._run_checker(repo, "--fix")
    assert res.returncode == 0, res.stderr

    assert (victim_dir / "config").read_text() == before, (
        "the fixture wrote into the repository GIT_DIR named — under a hook "
        "that is the real checkout (#3179)"
    )
    assert _local_config(repo, "user.name") == "Test", "the fixture repo must get the identity"
    assert _local_config(repo, "core.bare") == "false", "the checker must fix the fixture repo"


def test_precommit_wrapper_helpers_leave_an_inherited_GIT_DIR_alone(
    victim: tuple[Path, str], tmp_path: Path
) -> None:
    victim_dir, before = victim
    victim_head = (victim_dir / "HEAD").read_text()
    repo = tmp_path / "fixture-repo"
    repo.mkdir()

    env = precommit_test._make_repo(repo)

    assert (victim_dir / "config").read_text() == before, (
        "_make_repo wrote user.name=Test / commit.gpgsign=false into the "
        "repository GIT_DIR named — the exact #3179 trio"
    )
    assert (victim_dir / "HEAD").read_text() == victim_head
    assert not list((victim_dir / "refs" / "heads").iterdir()), (
        "_make_repo's initial commit landed in the GIT_DIR repository"
    )
    leaked = sorted(k for k in GIT_EXECUTION_VARS if k in env)
    assert not leaked, f"the env handed to the wrapper still names another repo: {leaked}"
    assert _local_config(repo, "user.name") == "Test"
    assert _local_config(repo, "commit.gpgsign") == "false"


# ---------------------------------------------------------------------------
# Static: every git-spawning test module is protected
# ---------------------------------------------------------------------------

_SUBPROCESS_FUNCS = {"run", "call", "check_call", "check_output", "Popen"}
_PY_GIT_CALL = re.compile(r"""[\[(]\s*["']git["']""")
_SH_GIT_CALL = re.compile(
    r"""(^|[;&|(`!]|\$\(|\bthen\b|\bdo\b|\bif\b|\bwhile\b)\s*git\s+-{0,2}[a-zA-Z]"""
)


def _script_runs_git(path: Path) -> bool:
    text = path.read_text(errors="replace")
    if path.suffix == ".py":
        return bool(_PY_GIT_CALL.search(text))
    for line in text.splitlines():
        code = line.split(" #", 1)[0]
        if code.lstrip().startswith("#"):
            continue
        if _SH_GIT_CALL.search(code):
            return True
    return False


def git_running_scripts() -> list[str]:
    """Basenames of the repo scripts that invoke git."""
    scripts = REPO_ROOT / "scripts"
    return sorted(
        p.name
        for p in scripts.rglob("*")
        if p.is_file() and p.suffix in {".py", ".sh"} and _script_runs_git(p)
    )


def _is_autouse_git_strip(node: ast.AST) -> bool:
    if not isinstance(node, ast.FunctionDef):
        return False
    autouse = any(
        isinstance(dec, ast.Call)
        and any(
            kw.arg == "autouse" and isinstance(kw.value, ast.Constant) and kw.value.value is True
            for kw in dec.keywords
        )
        for dec in node.decorator_list
    )
    body = ast.unparse(node)
    return autouse and "GIT_EXECUTION_VARS" in body and "delenv" in body


def _is_subprocess_call(node: ast.AST, from_imports: set[str]) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Attribute):
        return (
            isinstance(func.value, ast.Name)
            and func.value.id == "subprocess"
            and func.attr in _SUBPROCESS_FUNCS
        ) or (isinstance(func.value, ast.Name) and func.value.id == "os" and func.attr == "system")
    return isinstance(func, ast.Name) and func.id in from_imports


def _env_is_isolated(call: ast.Call) -> bool:
    for kw in call.keywords:
        if kw.arg == "env":
            value = kw.value
            return (
                isinstance(value, ast.Call)
                and isinstance(value.func, ast.Name)
                and value.func.id == "isolated_git_env"
            )
    return False


def unprotected_calls(source: str, scripts: list[str]) -> list[int]:
    """Line numbers of subprocess calls in a git-relevant module that are
    neither under an autouse git-strip fixture nor given an isolated env.

    A module is git-relevant when it spawns anything AND either names ``git``
    as an argv element or names a script that runs git. Every subprocess call
    in such a module is checked: an argv built elsewhere cannot be told apart
    from one that is not git.
    """
    tree = ast.parse(source)
    from_imports = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "subprocess"
        for alias in node.names
        if alias.name in _SUBPROCESS_FUNCS
    }
    if not any(_is_subprocess_call(n, from_imports) for n in ast.walk(tree)):
        return []
    if not (_PY_GIT_CALL.search(source) or any(name in source for name in scripts)):
        return []
    if any(_is_autouse_git_strip(n) for n in tree.body):
        return []

    bad: list[int] = []

    def visit(node: ast.AST, covered: bool) -> None:
        if isinstance(node, ast.ClassDef) and any(_is_autouse_git_strip(n) for n in node.body):
            covered = True
        if _is_subprocess_call(node, from_imports) and not covered:
            if not _env_is_isolated(node):  # type: ignore[arg-type]
                bad.append(node.lineno)  # type: ignore[attr-defined]
        for child in ast.iter_child_nodes(node):
            visit(child, covered)

    visit(tree, False)
    return sorted(bad)


def _test_modules() -> list[Path]:
    found: list[Path] = []
    for root in TEST_ROOTS:
        for path in (REPO_ROOT / root).rglob("*.py"):
            if "__pycache__" in path.parts or "node_modules" in path.parts:
                continue
            found.append(path)
    return sorted(found)


def test_every_git_spawning_test_module_is_protected() -> None:
    scripts = git_running_scripts()
    offenders = []
    for path in _test_modules():
        lines = unprotected_calls(path.read_text(errors="replace"), scripts)
        if lines:
            offenders.append(f"{path.relative_to(REPO_ROOT)}:{','.join(map(str, lines))}")
    assert not offenders, (
        "these test modules spawn git (or a script that runs git) without "
        "stripping git's execution variables. Under a hook GIT_DIR names the "
        "REAL repository and a fixture's `git init` / `git config` rewrites it "
        "(#2608, #3179). Add a module-level autouse fixture that deletes "
        "tests.git_env.GIT_EXECUTION_VARS, or pass env=isolated_git_env(...):\n  "
        + "\n  ".join(offenders)
    )


def test_the_scan_is_not_vacuous() -> None:
    """The static check must actually see the modules it is meant to police."""
    scripts = git_running_scripts()
    for expected in ("git-commit-with-precommit.sh", "check-shared-git-config.sh"):
        assert expected in scripts, f"script detector missed {expected}"
    relevant = []
    for path in _test_modules():
        src = path.read_text(errors="replace")
        if _PY_GIT_CALL.search(src) and "subprocess" in src:
            relevant.append(path.name)
    assert "test_git_commit_with_precommit.py" in relevant
    assert "test_check_shared_git_config.py" in relevant
    assert len(relevant) >= 10, relevant


_UNPROTECTED = """
import os, subprocess
def _git(cwd, *args):
    env = os.environ.copy()
    return subprocess.run(["git", *args], cwd=cwd, env=env)
"""

_MODULE_FIXTURE = """
import subprocess, pytest
from tests.git_env import GIT_EXECUTION_VARS
@pytest.fixture(autouse=True)
def _strip(monkeypatch):
    for var in GIT_EXECUTION_VARS:
        monkeypatch.delenv(var, raising=False)
def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd)
"""

_PER_CALL = """
import subprocess
from tests.git_env import isolated_git_env
def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, env=isolated_git_env())
"""

_VIA_SCRIPT = """
import subprocess
def test_it():
    subprocess.run(["bash", "scripts/some-git-script.sh"])
"""

_CLASS_FIXTURE = """
import subprocess, pytest
class TestX:
    @pytest.fixture(autouse=True)
    def _strip(self, monkeypatch):
        from tests.git_env import GIT_EXECUTION_VARS
        for var in GIT_EXECUTION_VARS:
            monkeypatch.delenv(var, raising=False)
    def test_in(self):
        subprocess.run(["git", "init"])
def test_out():
    subprocess.run(["git", "init"])
"""


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (_UNPROTECTED, [5]),
        (_MODULE_FIXTURE, []),
        (_PER_CALL, []),
        (_VIA_SCRIPT, [4]),
        (_CLASS_FIXTURE, [12]),
        ("import subprocess\nsubprocess.run(['ls'])\n", []),
    ],
    ids=[
        "copied-environ",
        "module-fixture",
        "per-call-env",
        "via-script",
        "class-fixture",
        "no-git",
    ],
)
def test_the_detector(source: str, expected: list[int]) -> None:
    assert unprotected_calls(source, ["some-git-script.sh"]) == expected
