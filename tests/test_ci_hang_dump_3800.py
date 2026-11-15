"""A silent CI hang must name the test it is stuck in (#3800).

Python shard 3/4 once stopped logging on two interpreters and ended with no pytest summary
and no worker-crash line, so nothing could say which test was running. The shard step now
passes ``-o faulthandler_timeout=N``: pytest's own faulthandler plugin dumps every thread's
stack once a single test exceeds N seconds, then lets it carry on.

Three properties are pinned, because each can be wrong while the workflow looks fine:

* the flag is on the step that runs the suite, and N is DERIVED from ``.test_durations`` (more
  than 3x every recorded test, 2x for a ``@pytest.mark.sweep`` unit, which runs in full only on
  pushes to ``main`` and the nightly), not restated: a test that outgrows it fails here, names
  the rule it broke and says to raise it, rather than printing a hang dump on every run;
* under ``-n 2 --dist loadfile`` (the CI shape) the dump reaches the output and names the test;
* it changes nothing else: same exit status, and nothing is printed when no test is slow.
"""

from __future__ import annotations

import ast
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
#: Read once at import: each CI xdist worker rewrites the live file with only its own tests
#: (tests/test_ci_python_test_shards.py), so a read inside a test can see a subset.
DURATIONS_TEXT = DURATIONS.read_text(encoding="utf-8")
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


#: A ``@pytest.mark.sweep`` unit runs in full only on pushes to ``main`` and the nightly (a PR
#: runs a seeded sample, #4074), so its recorded time is a full-mode figure; it still has to fit
#: twice over in the same timeout, which then still catches a hung sweep. Today's margin is thin:
#: the safe-markup guard fuzz (``test_safe_markup_guard_3820.py::TestTheDifferentialFuzz::
#: test_other_seeds_agree[3]``) records 846 s, 2 x 846 = 1692 < 1800, because the fuzz became
#: about 15x slower in #4135 (#4145). Fixing #4145 restores the room.
SWEEP_HEADROOM = 2


def _is_sweep_marker(node: ast.expr) -> bool:
    """``pytest.mark.sweep(...)``, the #4074 marker, as written in the source."""
    func = node.func if isinstance(node, ast.Call) else None
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "sweep"
        and isinstance(func.value, ast.Attribute)
        and func.value.attr == "mark"
        and isinstance(func.value.value, ast.Name)
        and func.value.value.id == "pytest"
    )


def _is_sweep(nodeid: str) -> bool:
    """Whether the test, an enclosing class or its module's ``pytestmark`` carries ``sweep``."""
    path, _, rest = nodeid.partition("::")
    file = ROOT / path
    if not file.is_file():
        return False
    tree = ast.parse(file.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "pytestmark" for t in node.targets
        ):
            items = (
                node.value.elts if isinstance(node.value, (ast.List, ast.Tuple)) else [node.value]
            )
            if any(_is_sweep_marker(item) for item in items):
                return True
    scope: list[ast.stmt] = tree.body
    for name in rest.split("[", 1)[0].split("::"):
        found = next(
            (
                n
                for n in scope
                if isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                and n.name == name
            ),
            None,
        )
        if found is None:
            return False
        if any(_is_sweep_marker(d) for d in found.decorator_list):
            return True
        scope = found.body if isinstance(found, ast.ClassDef) else []
    return False


def _violations(recorded: dict[str, float], timeout: int, is_sweep) -> list[str]:
    """Every recorded test the timeout does not clear by its rule's headroom, with the rule.

    Only a test within ``timeout / HEADROOM`` (the larger headroom) can break either rule, so
    only those are looked up for the marker.
    """
    out = []
    for nodeid, seconds in sorted(recorded.items(), key=lambda kv: -kv[1]):
        if timeout > HEADROOM * seconds:
            break  # sorted slowest first: this and every faster test clear both rules
        sweep = is_sweep(nodeid)
        headroom, kind = (SWEEP_HEADROOM, "sweep") if sweep else (HEADROOM, "ordinary")
        rule = f"{kind} ({headroom}x)"
        if not timeout > headroom * seconds:
            out.append(
                f"{rule} rule: faulthandler_timeout={timeout}s is within {headroom}x of "
                f"{nodeid} ({seconds:.0f}s)"
            )
    return out


def test_the_threshold_clears_every_recorded_test_by_its_rule():
    found = _violations(json.loads(DURATIONS_TEXT), _timeout(), _is_sweep)
    assert not found, (
        "\n".join(found) + "\nRaise faulthandler_timeout in .github/workflows/test.yml, or make "
        "the test faster: within its headroom the dump fires on a legitimately slow test and "
        "stops meaning 'hung'."
    )


def test_the_rule_is_3x_for_an_ordinary_test_and_2x_for_a_sweep():
    """The pin's own logic at both boundaries (#3800 rule, sweep rule for #4074 units)."""
    sweeps = {"s.py::test_sweep"}

    def check(recorded: dict[str, float]) -> list[str]:
        return _violations(recorded, 1800, lambda nodeid: nodeid in sweeps)

    # Ordinary: passes just under timeout/3, fails at it (strictly more than 3x is required).
    assert check({"o.py::test_plain": 599.9}) == []
    assert check({"o.py::test_plain": 600.0}) == [
        "ordinary (3x) rule: faulthandler_timeout=1800s is within 3x of o.py::test_plain (600s)"
    ]
    # A sweep at 846 s passes (today's fuzz), one at timeout/2 fails, and names the sweep rule.
    assert check({"s.py::test_sweep": 846.0}) == []
    assert check({"s.py::test_sweep": 899.9}) == []
    assert check({"s.py::test_sweep": 900.0}) == [
        "sweep (2x) rule: faulthandler_timeout=1800s is within 2x of s.py::test_sweep (900s)"
    ]
    # The same 846 s is a failure for an ordinary test: the sweep rule is not an exemption.
    assert check({"o.py::test_plain": 846.0})[0].startswith("ordinary (3x) rule")


def test_the_sweep_marker_is_read_from_the_source():
    fuzz = "python/tests/test_safe_markup_guard_3820.py::TestTheDifferentialFuzz::"
    assert _is_sweep(fuzz + "test_other_seeds_agree[3]")
    assert not _is_sweep(fuzz + "test_the_fuzz_is_not_vacuous")  # pr_tier, not a sweep
    assert not _is_sweep("tests/test_ci_hang_dump_3800.py::test_the_shard_step_arms_the_dump")


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
