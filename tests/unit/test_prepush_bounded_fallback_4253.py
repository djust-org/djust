"""The pre-push hook's full-suite fallback stays inside the local load guard (#4253).

When ``scripts/select-tests.py`` answers FULL (transitive impact, a blast-radius path, a
harness file, an empty selection) or cannot run, the hook used to run the whole fast tier
over every test root with ``DJUST_FULL_LOCAL=1``. ``tests/local_load_guard.py`` refuses any
local run over 5,000 tests without the developer's time-limited approval, so such a push
could never pass the hook (PR #4252: ~85.8k tests for a change under ``admin_ext/``).

The fallback now runs a bounded tier: the early tier (``--early-only``, which the guard
exempts) plus the direct selection (``DJUST_PREPUSH_TARGETED=1``), and leaves the rest to CI.
These drive the REAL script in a throwaway git repo whose ``run-with-venv-python.sh`` is a
stub that records every pytest invocation instead of running it, so no suite runs here.

``.test_durations`` alone no longer forces FULL either: it only moves tests between tiers,
and the tests that read it name it (rule (c) selects them).
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

from tests.git_env import GIT_EXECUTION_VARS, isolated_git_env

ROOT = Path(__file__).resolve().parents[2]
HOOK = ROOT / "scripts" / "pre-push-pytest.sh"
TEST_ROOTS = {"tests/", "python/tests/", "python/djust/tests/", "tests", "python/tests"}

STUB = r"""#!/usr/bin/env bash
# Stand-in for scripts/run-with-venv-python.sh: answers the hook's probes and logs pytest runs.
case "$1" in
    --worktree-pythonpath) exit 0 ;;
    --print) echo /usr/bin/false; exit 0 ;;
    scripts/select-tests.py)
        [ "${STUB_SELECT_FAILS:-}" = "1" ] && exit 1
        if [ "${DJUST_PREPUSH_TARGETED:-}" = "1" ]; then
            printf '%s\n' "${STUB_TARGETED_ANSWER:-tests/test_direct.py}"
        else
            echo FULL
        fi
        exit 0 ;;
    scripts/promotion_ledger.py) echo tests/test_early.py; exit 0 ;;
    -c) exit 0 ;;
    -m)
        shift
        printf '%s\t%s\n' "${DJUST_FULL_LOCAL:-}" "$*" >> "$STUB_LOG"
        exit 0 ;;
esac
exit 1
"""


@pytest.fixture(autouse=True)
def _no_inherited_git_env(monkeypatch):
    for var in GIT_EXECUTION_VARS:
        monkeypatch.delenv(var, raising=False)


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        env=isolated_git_env(
            GIT_AUTHOR_NAME="t",
            GIT_AUTHOR_EMAIL="t@t",
            GIT_COMMITTER_NAME="t",
            GIT_COMMITTER_EMAIL="t@t",
        ),
    )


def _run_hook(
    tmp_path: Path, **env: str
) -> tuple[subprocess.CompletedProcess, list[tuple[str, list[str]]]]:
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "scripts" / "pre-push-pytest.sh").write_text(HOOK.read_text())
    (repo / "scripts" / "run-with-venv-python.sh").write_text(STUB)
    # The hook only checks that the selector exists; the stub answers for it.
    (repo / "scripts" / "select-tests.py").write_text("")
    (repo / "scripts" / "promotion_ledger.py").write_text("")
    _git(repo, "init", "-q", "-b", "main")
    log = tmp_path / "pytest-calls.log"
    log.write_text("")
    full_env = isolated_git_env(STUB_LOG=str(log), **env)
    for var in ("DJUST_PREPUSH_FULL", "DJUST_PREPUSH_TARGETED", "DJUST_FULL_LOCAL"):
        if var not in env:
            full_env.pop(var, None)
    proc = subprocess.run(
        ["bash", "scripts/pre-push-pytest.sh"],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=60,
        env=full_env,
    )
    calls = []
    for line in log.read_text().splitlines():
        full_local, _, argv = line.partition("\t")
        calls.append((full_local, argv.split()))
    return proc, calls


def _assert_within_guard(calls):
    """No invocation is a whole-root run the guard would refuse."""
    assert calls, "the hook ran no pytest at all"
    for full_local, argv in calls:
        assert full_local != "1", "the fallback must not claim a deliberate full run: %r" % argv
        if "--early-only" in argv:
            continue  # the bounded early tier: exempt from the guard (local_load_guard._early_only)
        roots = [a for a in argv if a.rstrip("/") + "/" in TEST_ROOTS]
        assert not roots, "a whole test root outside the early tier: %r" % argv


def test_full_from_transitive_impact_runs_early_tier_plus_direct_selection(tmp_path):
    proc, calls = _run_hook(tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    _assert_within_guard(calls)
    assert any("--early-only" in argv for _, argv in calls), calls
    assert any("tests/test_direct.py" in argv for _, argv in calls), calls
    assert "CI" in proc.stdout + proc.stderr


def test_full_with_no_direct_selection_runs_only_the_early_tier(tmp_path):
    # A harness change: still FULL under DJUST_PREPUSH_TARGETED=1.
    proc, calls = _run_hook(tmp_path, STUB_TARGETED_ANSWER="FULL")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    _assert_within_guard(calls)
    assert any("--early-only" in argv for _, argv in calls), calls


def test_selector_that_cannot_run_falls_back_to_the_bounded_tier(tmp_path):
    proc, calls = _run_hook(tmp_path, STUB_SELECT_FAILS="1")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    _assert_within_guard(calls)
    assert any("--early-only" in argv for _, argv in calls), calls


def test_explicit_full_request_still_runs_every_root(tmp_path):
    # DJUST_PREPUSH_FULL=1 is the deliberate full gate; the guard's approval governs it.
    proc, calls = _run_hook(tmp_path, DJUST_PREPUSH_FULL="1")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert any(full_local == "1" and "tests/" in argv for full_local, argv in calls), calls


def _load_selector():
    spec = importlib.util.spec_from_file_location(
        "select_tests_4253", ROOT / "scripts" / "select-tests.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["select_tests_4253"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_test_durations_alone_selects_its_readers_not_full():
    st = _load_selector()
    reader = "tests/test_slow_tier.py"
    other = "tests/unit/test_forms.py"
    texts = {reader: 'DURATIONS_FILE = ".test_durations"', other: "import djust"}
    sel = st.select_tests([".test_durations"], [reader, other], texts.__getitem__)
    assert not sel.full, sel.reason
    assert sel.tests == [reader]
