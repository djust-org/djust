"""Every Python test root stays in the gating CI invocation (#2032/#2034).

`python/djust/tests/` was once absent from CI's Python step. The cause is a
sharp edge worth restating: passing explicit paths to pytest **overrides**
`pyproject.toml`'s `testpaths`, so listing two of the three roots silently
drops the third — no error, no warning, and the suite still reports green.

A large suite ran in neither CI nor the pre-push hook that way, and a RED
`TestSetattrChokepoint` CWE-915 guard sat undetected on `main` (#2032). It was
restored as a separate soak step, then promoted to blocking (#2034).

The two steps were later merged into one invocation for wall-clock (split
93s + 43s = 136s at `-n 4`; merged 109s). That merge is exactly the kind of
edit that could re-drop a root — the paths become one list that someone might
"tidy" — so the roots are pinned here rather than trusted to a comment (#1859).

This asserts the CI invocation, not pytest's own discovery: a test that only
checked `testpaths` would pass while CI ran a subset, which is the original
bug.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
import yaml

from tests.git_env import isolated_git_env

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/test.yml"

REQUIRED_ROOTS = ("tests/", "python/tests/", "python/djust/tests/")


def _gating_pytest_commands() -> list[str]:
    """Every `pytest` line in the python-tests job's run steps."""
    jobs = yaml.safe_load(WORKFLOW.read_text())["jobs"]
    assert "python-tests" in jobs, (
        "the python-tests job was renamed or removed — this guard now pins "
        "nothing and must be updated rather than left silently passing."
    )
    cmds: list[str] = []
    for step in jobs["python-tests"].get("steps", []):
        run = step.get("run") or ""
        for line in run.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue  # a comment mentioning pytest is not an invocation
            if re.search(r"\bpytest\b", stripped) and "-m pytest" in stripped:
                cmds.append(stripped)
    assert cmds, "no pytest invocation found in the python-tests job"
    return cmds


@pytest.mark.parametrize("root", REQUIRED_ROOTS)
def test_every_test_root_is_in_the_gating_ci_invocation(root: str) -> None:
    combined = " ".join(_gating_pytest_commands())
    assert root in combined, (
        f"{root} is not passed to pytest in the python-tests job.\n"
        f"Explicit paths OVERRIDE pyproject's `testpaths`, so this root is not "
        f"merely deprioritised — it does not run at all, and CI still reports "
        f"green. That is exactly #2032: a RED security-structural guard sat "
        f"undetected on main because this root was missing.\n"
        f"Invocation(s) found: {_gating_pytest_commands()}"
    )


def test_the_roots_run_under_one_invocation() -> None:
    """Merged, not split — the shape the wall-clock measurement chose.

    Not a style preference: two invocations each drain their own xdist worker
    pool, so end-of-run stragglers hold one worker while the rest idle. 136s
    split against 109s merged at `-n 4`.
    """
    cmds = _gating_pytest_commands()
    assert len(cmds) == 1, (
        f"expected ONE pytest invocation covering all roots, found {len(cmds)}. "
        f"Splitting them back out costs ~27s of wall-clock at 4 workers. If the "
        f"split is deliberate (a root that genuinely cannot share a session), "
        f"say so here and update this test.\n{cmds}"
    )


def test_the_gating_invocation_runs_in_parallel() -> None:
    """A serial run of 10,489 tests would dwarf everything else in the job."""
    cmd = _gating_pytest_commands()[0]
    assert re.search(r"-n\s+(auto|\d+)", cmd), (
        f"the gating pytest invocation lost its `-n auto`: {cmd}"
    )


def test_make_test_runs_every_python_root() -> None:
    result = subprocess.run(
        ["make", "--dry-run", "test"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
        env=isolated_git_env(),
    )
    commands = [line for line in result.stdout.splitlines() if "-m pytest" in line]
    assert len(commands) == 1, commands
    for root in REQUIRED_ROOTS:
        assert root in commands[0].split(), (root, commands[0])


# ---------------------------------------------------------------------------
# #4217 — no test file outside the collected roots
# ---------------------------------------------------------------------------
#
# The pins above check that CI passes every root. They cannot see a test file
# that sits in NO root: `python/djust/components/tests/` (9 files, 153 tests)
# ran in no job and no default `pytest` until #4217 moved it under
# `python/djust/tests/components/`. A fixture failure in it was invisible to
# every targeted run and the early tier (#4186).
#
# So: every TRACKED test-shaped file is under a root, and not under one of
# pyproject's `--ignore`s, or it is listed here with the reason it is not a
# suite test. Tracked files only (`git ls-files`): an untracked scratch file in
# a working checkout is not the repository's, and CI checks out tracked files.
# A new test file anywhere else, including next to a listed one, fails this pin.

TEST_FILE_RE = re.compile(r"^(test_.*|.*_test)\.py$")

_DEMO_VIEW = "they are views (demo pages whose names contain 'test'), not tests"
_RENTALS = (
    "the rentals demo app's own tests: no job runs them today, and "
    "test_status_badge_escapes_icon among them fails. The demo-project "
    "migration (#4187) fixes that test and moves them into the demo-checks job"
)

# path (a file, or a directory ending in "/") -> why it is not collected.
OUTSIDE_ROOTS = {
    "tests/playwright/": (
        "browser scripts outside pytest (pyproject's addopts --ignore it): the ones a "
        ".github/workflows job names run there directly; the rest are run by hand, "
        "as tests/playwright/README.md says"
    ),
    "examples/demo_project/demo_app/views/cache_test.py": _DEMO_VIEW,
    "examples/demo_project/demo_app/views/draft_mode_test.py": _DEMO_VIEW,
    "examples/demo_project/demo_app/views/loading_test.py": _DEMO_VIEW,
    "examples/demo_project/demo_app/views/test_index.py": _DEMO_VIEW,
    "examples/demo_project/djust_tests/views/cache_test.py": _DEMO_VIEW,
    "examples/demo_project/djust_tests/views/draft_mode_test.py": _DEMO_VIEW,
    "examples/demo_project/djust_tests/views/loading_test.py": _DEMO_VIEW,
    "examples/demo_project/djust_tests/views/test_index.py": _DEMO_VIEW,
    "examples/demo_project/djust_rentals/components/tests/test_component_security.py": _RENTALS,
    "examples/demo_project/djust_rentals/tests/test_jit_integration.py": _RENTALS,
    "examples/demo_project/djust_rentals/tests/test_lease_forms.py": _RENTALS,
    "examples/test_textarea_component.py": (
        "a hand-run example script that configures its own settings; no job runs it, "
        "and 2 of its tests fail today (an ImportError on the Rust TextArea)"
    ),
    "python/djust/test_isolation.py": (
        "a library module (public test-isolation helpers), not a test file"
    ),
}


def _pytest_ini() -> dict:
    import tomllib

    return tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["pytest"]["ini_options"]


def _ignored_paths() -> list[str]:
    out = []
    for opt in _pytest_ini().get("addopts", []):
        if opt.startswith("--ignore="):
            out.append(opt.split("=", 1)[1].rstrip("/") + "/")
    return out


def _test_shaped_files(root: Path = ROOT) -> list[str]:
    """Every tracked file under ``root`` whose name pytest would collect."""
    listed = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        capture_output=True,
        check=True,
        env=isolated_git_env(),
    ).stdout.decode()
    return sorted(
        path for path in listed.split("\0") if path and TEST_FILE_RE.match(path.rsplit("/", 1)[-1])
    )


def _matches(path: str, key: str) -> bool:
    return path == key or (key.endswith("/") and path.startswith(key))


def _ledgered(path: str) -> bool:
    return any(_matches(path, key) for key in OUTSIDE_ROOTS)


def _collected(path: str) -> bool:
    in_root = any(path.startswith(root) for root in REQUIRED_ROOTS)
    ignored = any(path.startswith(ign) for ign in _ignored_paths())
    return in_root and not ignored


def test_pyproject_testpaths_are_the_ci_roots() -> None:
    """A bare `pytest` collects what CI collects: the same three roots."""
    testpaths = {p.rstrip("/") + "/" for p in _pytest_ini()["testpaths"]}
    assert testpaths == set(REQUIRED_ROOTS), (
        f"pyproject testpaths {sorted(testpaths)} differ from the CI roots "
        f"{sorted(REQUIRED_ROOTS)}: a bare `pytest` and CI would run different suites."
    )


def test_every_test_file_is_collected_or_listed() -> None:
    files = _test_shaped_files()
    assert len(files) > 500, f"git ls-files found only {len(files)} test files; it is broken"
    stray = [f for f in files if not _collected(f) and not _ledgered(f)]
    assert not stray, (
        "tracked test files that NO pytest run collects (not under "
        f"{list(REQUIRED_ROOTS)}, or under an addopts --ignore):\n  "
        + "\n  ".join(stray)
        + "\nMove them under a root, or list them in OUTSIDE_ROOTS with the reason "
        "they are not suite tests (#4217)."
    )


def test_every_ledger_entry_still_matches_a_file() -> None:
    files = _test_shaped_files()
    stale = [key for key in OUTSIDE_ROOTS if not any(_matches(f, key) for f in files)]
    assert not stale, f"OUTSIDE_ROOTS entries that match no tracked test file: {stale}"


def test_an_untracked_test_file_is_not_the_repositorys(tmp_path: Path) -> None:
    """A scratch file in a working checkout is ignored; the same file tracked is not."""
    env = isolated_git_env(GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, capture_output=True, env=env)
    (tmp_path / "scratch").mkdir()
    (tmp_path / "scratch" / "test_untracked.py").write_text("def test_x():\n    pass\n")
    (tmp_path / "tracked_test.py").write_text("def test_y():\n    pass\n")
    subprocess.run(
        ["git", "add", "tracked_test.py"], cwd=tmp_path, check=True, capture_output=True, env=env
    )
    assert _test_shaped_files(tmp_path) == ["tracked_test.py"]
