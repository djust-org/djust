"""A silent CI hang must name the test it is stuck in (#3800).

Python shard 3/4 once stopped logging on two interpreters and ended with no pytest summary
and no worker-crash line, so nothing could say which test was running. The shard step now
passes ``-o faulthandler_timeout=N``: pytest's own faulthandler plugin dumps every thread's
stack once a single test exceeds N seconds, then lets it carry on.

Three properties are pinned, because each can be wrong while the workflow looks fine:

* the flag is on the step that runs the suite, and N is DERIVED from ``.test_durations`` (more
  than 3x the slowest recorded test), not restated: a test that outgrows it fails here and
  says to raise it, rather than printing a hang dump on every run;
* under ``-n 2 --dist loadfile`` (the CI shape) the dump reaches the output and names the test;
* it changes nothing else: same exit status, and nothing is printed when no test is slow.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "test.yml"
DURATIONS = ROOT / ".test_durations"
HEADROOM = 3  # "comfortably above the slowest legitimate test"


def _shard_command() -> str:
    jobs = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]
    (step,) = [s for s in jobs["python-tests"]["steps"] if "-m pytest" in (s.get("run") or "")]
    return step["run"]


def _timeout() -> int:
    match = re.search(r"-o\s+faulthandler_timeout=(\d+)", _shard_command())
    assert match, "the python-tests shard step has no `-o faulthandler_timeout=N`"
    return int(match.group(1))


def test_the_shard_step_arms_the_dump():
    assert _timeout() > 0


def test_the_threshold_is_more_than_3x_the_slowest_recorded_test():
    recorded = json.loads(DURATIONS.read_text(encoding="utf-8"))
    slowest_id, slowest = max(recorded.items(), key=lambda kv: kv[1])
    assert _timeout() > HEADROOM * slowest, (
        f"faulthandler_timeout={_timeout()}s is within {HEADROOM}x of the slowest recorded test "
        f"({slowest_id}: {slowest:.0f}s). Raise it in .github/workflows/test.yml, or the dump "
        "fires on a legitimately slow test and stops meaning 'hung'."
    )


def _pytest(tmp_path: Path, timeout: int | None, *, test_body: str) -> subprocess.CompletedProcess:
    (tmp_path / "test_sample.py").write_text(test_body)
    env = {k: v for k, v in os.environ.items() if k != "PYTEST_ADDOPTS"}
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    env["PYTEST_PLUGINS"] = "xdist.plugin"
    args = [
        sys.executable,
        "-m",
        "pytest",
        "test_sample.py",
        "-n",
        "2",
        "--dist",
        "loadfile",
        "-p",
        "no:cacheprovider",
        "--rootdir",
        str(tmp_path),
        "-c",
        os.devnull,
        "-q",
    ]
    if timeout is not None:
        args += ["-o", f"faulthandler_timeout={timeout}"]
    return subprocess.run(args, cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120)


SLOW_TEST = """
import time

def test_quick():
    pass

def test_the_slow_one_that_must_be_named():
    time.sleep(2.5)
"""


def test_a_slow_test_is_named_in_the_dump_under_xdist(tmp_path):
    done = _pytest(tmp_path, 1, test_body=SLOW_TEST)
    out = done.stdout + done.stderr
    assert done.returncode == 0, out  # the dump never fails the run
    assert "Timeout (0:00:01)!" in out, out
    # the stack of the stuck test: file, line and function
    assert re.search(r'test_sample\.py", line \d+ in test_the_slow_one_that_must_be_named', out), (
        out
    )


def test_the_dump_is_silent_and_inert_when_no_test_is_slow(tmp_path):
    with_flag = _pytest(tmp_path, 600, test_body=SLOW_TEST)
    without = _pytest(tmp_path, None, test_body=SLOW_TEST)
    assert with_flag.returncode == without.returncode == 0
    assert "Timeout (" not in with_flag.stdout + with_flag.stderr


def test_a_failing_test_still_fails_with_the_flag(tmp_path):
    done = _pytest(tmp_path, 600, test_body="def test_bad():\n    assert False\n")
    assert done.returncode == 1, done.stdout + done.stderr


def _python_tests_job() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["python-tests"]


def test_a_hung_shard_ends_in_bounded_time():
    """The faulthandler dump names the test; a job timeout is what ENDS the hang (#3800)."""
    minutes = _python_tests_job().get("timeout-minutes")
    assert isinstance(minutes, int), "python-tests has no job-level timeout-minutes"
    assert minutes < 360, "that is GitHub's default; it bounds nothing"
    # headroom over the slowest legitimate shard: 15.4 min over the last five green main runs
    assert minutes >= 3 * 15, f"{minutes} min risks timing out a healthy shard"
    # the dump must be able to fire BEFORE the job is killed, or it is never seen
    assert minutes * 60 > _timeout() + 300, (minutes, _timeout())
