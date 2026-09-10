"""tests/lost_items_guard.py — a run that loses collected items goes red (#2746).

Each check is exercised through pytest's own ``pytester`` fixture: a throwaway
test directory, run with the guard registered via ``-p``, asserting on the
subprocess/inline result. The "lost item" is manufactured the way a real loss
manifests — the item is selected, then never produces a report — by a
``tryfirst`` ``pytest_runtest_protocol`` that claims to have run it. Under
xdist that short-circuits the worker's ``run_one_test`` the same way, so no
``testreport`` event is ever sent to the controller.

Gate-off, per mechanism (#2135 — name the test that goes red when ONLY that
mechanism is removed):

- remove the ``selected - reported`` check in ``_evaluate`` →
  ``test_lost_item_goes_red_and_is_named`` (serial) and
  ``test_lost_item_under_xdist_goes_red_on_the_controller``;
- remove ``session.exitstatus = TESTS_FAILED`` →
  ``test_exit_code_is_nonzero_in_a_subprocess`` (the empirical proof that a
  mutation in ``pytest_sessionfinish`` IS the process exit code);
- remove the floor comparison → ``test_floor_above_collected_goes_red``;
- remove the ``workeroutput`` relay → ``test_floor_under_xdist_reads_the_workers``;
- remove the missing-file branch → ``test_floor_enabled_without_a_file_goes_red``.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = "tests.lost_items_guard"

FLOOR_ENV = "DJUST_COLLECTED_FLOOR"
FLOOR_WRITE_ENV = "DJUST_COLLECTED_FLOOR_WRITE"

THREE_TESTS = """
def test_a(): pass
def test_lost(): pass
def test_c(): pass
"""

# A loss, manufactured: the item is collected and selected, then never runs
# and never reports. `tryfirst` beats pytest's own runner impl; returning True
# tells the caller the protocol was handled.
LOSE_ONE = """
import pytest

@pytest.hookimpl(tryfirst=True)
def pytest_runtest_protocol(item, nextitem):
    if item.name == "test_lost":
        return True
"""


@pytest.fixture
def guarded(pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch) -> pytest.Pytester:
    """A pytester whose runs load the guard and inherit NO floor settings.

    The outer CI run sets DJUST_COLLECTED_FLOOR=1; without the delenv every
    inner run here would demand a floor file in the temp dir.
    """
    monkeypatch.delenv(FLOOR_ENV, raising=False)
    monkeypatch.delenv(FLOOR_WRITE_ENV, raising=False)
    # The outer session exports Django settings; pytest-django would then try
    # to stand Django up inside the temp dir. The inner suites are plain.
    monkeypatch.delenv("DJANGO_SETTINGS_MODULE", raising=False)
    monkeypatch.setenv("PYTHONPATH", str(ROOT))  # subprocess runs import the plugin by name
    pytester.syspathinsert(ROOT)  # inline runs too
    return pytester


def _run(pytester: pytest.Pytester, *args: str, subprocess_: bool = False) -> pytest.RunResult:
    argv = ("-p", PLUGIN, "-p", "no:cacheprovider", "-p", "no:django", *args)
    return pytester.runpytest_subprocess(*argv) if subprocess_ else pytester.runpytest(*argv)


# ---------------------------------------------------------------------------
# Check 1: every selected item produced a report
# ---------------------------------------------------------------------------


def test_a_normal_run_stays_green(guarded: pytest.Pytester) -> None:
    guarded.makepyfile(THREE_TESTS)
    result = _run(guarded)
    assert result.ret == pytest.ExitCode.OK
    assert "lost-items guard" not in result.stdout.str()


def test_lost_item_goes_red_and_is_named(guarded: pytest.Pytester) -> None:
    guarded.makepyfile(test_mod=THREE_TESTS)
    guarded.makeconftest(LOSE_ONE)
    result = _run(guarded)
    assert result.ret == pytest.ExitCode.TESTS_FAILED
    out = result.stdout.str()
    assert "1 of 3 selected item(s) produced NO report" in out
    assert "LOST test_mod.py::test_lost" in out
    # The other two are not named — the report is the delta, not the suite.
    assert "LOST test_mod.py::test_a" not in out
    # And pytest's own summary still looks clean — which is the whole point.
    result.stdout.re_match_lines([r".*2 passed.*"])


def test_deselected_items_are_not_counted_as_lost(guarded: pytest.Pytester) -> None:
    """The comparison is against the POST-deselection set (`-k`, `--group`)."""
    guarded.makepyfile(THREE_TESTS)
    result = _run(guarded, "-k", "test_a or test_c")
    assert result.ret == pytest.ExitCode.OK
    assert "lost-items guard" not in result.stdout.str()


def test_skipped_at_setup_counts_as_reported(guarded: pytest.Pytester) -> None:
    guarded.makepyfile(
        """
        import pytest
        @pytest.fixture
        def bail(): pytest.skip("at setup")
        def test_skipped(bail): pass
        def test_ok(): pass
        """
    )
    result = _run(guarded)
    assert result.ret == pytest.ExitCode.OK
    assert "lost-items guard" not in result.stdout.str()


def test_fail_fast_stop_is_not_reported_as_a_loss(guarded: pytest.Pytester) -> None:
    """`-x` leaves items unrun by design and is already red; stay quiet."""
    guarded.makepyfile(
        """
        def test_a(): assert False
        def test_b(): pass
        """
    )
    result = _run(guarded, "-x")
    assert result.ret == pytest.ExitCode.TESTS_FAILED  # red for the right reason
    assert "stopping after 1 failures" in result.stdout.str()
    assert "lost-items guard" not in result.stdout.str()  # ...and not blamed on a loss


def test_exit_code_is_nonzero_in_a_subprocess(guarded: pytest.Pytester) -> None:
    """Empirical proof: setting `session.exitstatus` in `pytest_sessionfinish`
    changes the exit code of the `python -m pytest` PROCESS, not just an
    in-memory attribute. A real child process, its real return code."""
    guarded.makepyfile(test_mod=THREE_TESTS)
    guarded.makeconftest(LOSE_ONE)
    result = _run(guarded, subprocess_=True)
    assert result.ret == 1, result.stdout.str()
    assert "LOST test_mod.py::test_lost" in result.stdout.str()


def test_exit_code_via_a_bare_shell_invocation(guarded: pytest.Pytester) -> None:
    """Belt to the braces above: no pytester runner at all, just `$?`."""
    guarded.makepyfile(test_mod=THREE_TESTS)
    guarded.makeconftest(LOSE_ONE)
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    env.pop(FLOOR_ENV, None)
    env.pop("DJANGO_SETTINGS_MODULE", None)
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            PLUGIN,
            "-p",
            "no:cacheprovider",
            "-p",
            "no:django",
            "-q",
        ],
        cwd=str(guarded.path),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "2 passed" in proc.stdout  # pytest's own summary was green
    assert "LOST test_mod.py::test_lost" in proc.stdout


def test_lost_item_under_xdist_goes_red_on_the_controller(guarded: pytest.Pytester) -> None:
    """The failure shape from the issue: workers, one item never reports."""
    pytest.importorskip("xdist")
    guarded.makepyfile(test_mod=THREE_TESTS)
    guarded.makeconftest(LOSE_ONE)
    result = _run(guarded, "-n", "2", subprocess_=True)
    assert result.ret == pytest.ExitCode.TESTS_FAILED
    out = result.stdout.str()
    assert "1 of 3 selected item(s) produced NO report" in out
    assert "LOST test_mod.py::test_lost" in out


def test_normal_xdist_run_stays_green(guarded: pytest.Pytester) -> None:
    pytest.importorskip("xdist")
    guarded.makepyfile(THREE_TESTS)
    result = _run(guarded, "-n", "2", subprocess_=True)
    assert result.ret == pytest.ExitCode.OK
    assert "lost-items guard" not in result.stdout.str()


# ---------------------------------------------------------------------------
# Check 2: collected-count floor (CI-only, DJUST_COLLECTED_FLOOR=1)
# ---------------------------------------------------------------------------


def _write_floor(pytester: pytest.Pytester, floor: int) -> None:
    (pytester.path / ".test_collected_floor").write_text(f"# comment\n{floor}\n")


def test_floor_is_off_unless_the_env_var_is_set(guarded: pytest.Pytester) -> None:
    guarded.makepyfile(THREE_TESTS)
    _write_floor(guarded, 1000)  # would fail if checked
    result = _run(guarded)
    assert result.ret == pytest.ExitCode.OK


def test_floor_at_or_below_collected_stays_green(
    guarded: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(FLOOR_ENV, "1")
    guarded.makepyfile(THREE_TESTS)
    _write_floor(guarded, 3)
    result = _run(guarded)
    assert result.ret == pytest.ExitCode.OK
    assert "collected 3 items >= floor 3" in result.stdout.str()


def test_floor_above_collected_goes_red(
    guarded: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(FLOOR_ENV, "1")
    guarded.makepyfile(THREE_TESTS)
    _write_floor(guarded, 8)
    result = _run(guarded, subprocess_=True)
    assert result.ret == pytest.ExitCode.TESTS_FAILED
    assert "collected 3 items, floor is 8 (5 short)" in result.stdout.str()


def test_floor_compares_the_pre_deselection_count(
    guarded: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """pytest-split's `--group` deselects; the floor must see what was collected."""
    monkeypatch.setenv(FLOOR_ENV, "1")
    guarded.makepyfile(THREE_TESTS)
    _write_floor(guarded, 3)
    result = _run(guarded, "-k", "test_a")
    assert result.ret == pytest.ExitCode.OK
    assert "collected 3 items >= floor 3" in result.stdout.str()


def test_floor_enabled_without_a_file_goes_red(
    guarded: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cannot measure is not a pass (#2730)."""
    monkeypatch.setenv(FLOOR_ENV, "1")
    guarded.makepyfile(THREE_TESTS)
    result = _run(guarded)
    assert result.ret == pytest.ExitCode.TESTS_FAILED
    assert ".test_collected_floor is missing" in result.stdout.str()


def test_floor_under_xdist_reads_the_workers(
    guarded: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The controller never collects; the count has to come from workeroutput."""
    pytest.importorskip("xdist")
    monkeypatch.setenv(FLOOR_ENV, "1")
    guarded.makepyfile(THREE_TESTS)
    _write_floor(guarded, 3)
    ok = _run(guarded, "-n", "2", subprocess_=True)
    assert ok.ret == pytest.ExitCode.OK, ok.stdout.str()
    assert "collected 3 items >= floor 3" in ok.stdout.str()

    _write_floor(guarded, 4)
    red = _run(guarded, "-n", "2", subprocess_=True)
    assert red.ret == pytest.ExitCode.TESTS_FAILED
    assert "collected 3 items, floor is 4 (1 short)" in red.stdout.str()


def test_write_env_derives_the_floor_from_a_real_collection(
    guarded: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`make test-collected-floor` — the guard writes what it counted."""
    from tests.lost_items_guard import read_floor

    guarded.makepyfile(THREE_TESTS)
    out = guarded.path / "floor.txt"
    result = _run(guarded, "--collect-only", "-q", subprocess_=True)
    assert result.ret == pytest.ExitCode.OK
    assert not out.exists()  # only written when asked

    monkeypatch.setenv(FLOOR_WRITE_ENV, str(out))
    result = _run(guarded, "--collect-only", "-q", subprocess_=True)
    assert result.ret == pytest.ExitCode.OK, result.stdout.str()
    assert read_floor(out) == 3
    assert "DERIVED, never hand-typed" in out.read_text()


# ---------------------------------------------------------------------------
# Wiring: the guard is armed where it matters, and the floor is derived
# ---------------------------------------------------------------------------


def test_root_conftest_registers_the_guard_for_this_very_run(
    request: pytest.FixtureRequest,
) -> None:
    """No `-p` here — if the root conftest stops loading it, this fails."""
    assert request.config.pluginmanager.get_plugin("djust_lost_items_guard") is not None


def test_committed_floor_is_a_derived_integer() -> None:
    from tests.lost_items_guard import read_floor

    floor = read_floor(ROOT / ".test_collected_floor")
    assert floor > 20_000, floor  # the suite is ~27.6k items; a placeholder 0 would pin nothing


@pytest.mark.parametrize(
    ("workflow", "job", "step_name"),
    [
        (".github/workflows/test.yml", "python-tests", "Run Python tests"),
        (".github/workflows/main-health.yml", None, "Run the suite"),
    ],
)
def test_ci_pytest_steps_arm_the_floor(workflow: str, job: str | None, step_name: str) -> None:
    yaml = pytest.importorskip("yaml")
    jobs = yaml.safe_load((ROOT / workflow).read_text())["jobs"]
    candidates = [jobs[job]] if job else list(jobs.values())
    steps = [
        s
        for j in candidates
        for s in j.get("steps", [])
        if str(s.get("name", "")).startswith(step_name)
    ]
    assert len(steps) == 1, f"{workflow}: expected one '{step_name}' step, found {len(steps)}"
    (step,) = steps
    assert str(step.get("env", {}).get(FLOOR_ENV)) == "1", (
        f"{workflow} '{step_name}' does not set {FLOOR_ENV}=1 — the floor is unarmed there"
    )
    # #1713: a red guard must actually fail the job — no continue-on-error on
    # the step. (test-summary's AND-condition over python-tests is pinned by
    # tests/test_ci_python_test_shards.py.)
    assert not step.get("continue-on-error"), f"{workflow} '{step_name}' has continue-on-error"


def test_make_target_derives_over_the_ci_roots() -> None:
    text = (ROOT / "Makefile").read_text()
    start = text.index("test-collected-floor:")
    body = text[start : text.index("\n\n", start)]
    assert "DJUST_COLLECTED_FLOOR_WRITE=.test_collected_floor" in body
    for root in ("tests/", "python/tests/", "python/djust/tests/"):
        assert root in body, f"make test-collected-floor omits {root}; the floor would under-count"
