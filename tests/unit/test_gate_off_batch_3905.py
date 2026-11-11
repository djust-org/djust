"""``scripts/gate_off_batch.py``: batch gate-off with failure-text attribution (#3905).

The tool is tooling, so by the repository's empirical-canary rule (#1459) it is proven on a
synthetic bug class: a tiny REAL fixture package (modules, a conftest fixture, one test file)
in ``tmp_path``, run through real ``python -m pytest`` subprocesses. Every outcome the tool
can report has a fixture that produces it, and the tests assert on the reported status and on
how many full runs it took, never on elapsed time.

``scripts/gate-off-batch-selfcheck.json`` turns ``scripts/gate_off.py`` on this tool (each
attribution, restore and validation rule dropped in turn must turn a test here red);
``test_every_selfcheck_mutation_text_still_applies`` pins that the list cannot rot.
"""

from __future__ import annotations

import importlib.util
import json
import os
import py_compile
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.git_env import GIT_EXECUTION_VARS

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "gate_off_batch.py"
SELFCHECK = ROOT / "scripts" / "gate-off-batch-selfcheck.json"


def _load():
    spec = importlib.util.spec_from_file_location("gate_off_batch_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["gate_off_batch_under_test"] = module
    spec.loader.exec_module(module)
    return module


gb = _load()
go = gb.go

CALC = '''\
"""Fixture module for the batch gate-off tests."""

import os
import subprocess
import sys
import time

# EVERY busy loop in this fixture ends by itself after 45 s, so a bug in the tool under test
# (a kill that does not reach the process) can cost a developer 45 s of CPU, never a runaway.
_BOUND = 45
_SPIN_SRC = "import time\\nt = time.monotonic() + 45\\nwhile time.monotonic() < t:\\n    pass\\n"


def _hold():
    stop = time.monotonic() + _BOUND
    while time.monotonic() < stop:
        pass


def _spawn_pair(pidfile):
    """Record this process and a busy GRANDCHILD (own pids) in ``pidfile``, one line per call."""
    child = subprocess.Popen([sys.executable, "-c", _SPIN_SRC])
    with open(pidfile, "a") as handle:
        handle.write("%d %d\\n" % (os.getpid(), child.pid))


def alpha():
    return 1


def beta():
    return 2


def gamma():
    return 3


def delta():
    return 4


def pair_a():
    return 1


def pair_b():
    return 2


def untested():
    return 5


def untested_too():
    return 6


def make():
    return {"v": 7}


def spin():
    i = 0
    stop = time.monotonic() + _BOUND
    while i < 3 and time.monotonic() < stop:
        i += 1
    return i


def grow():
    chunks = []
    for _ in range(3):
        chunks.append(b"x" * 1_000_000)
        time.sleep(0.002)
    return len(chunks)
'''

OTHER = """\
def omega():
    return 9
"""

CONFTEST = """\
import pytest


@pytest.fixture
def base():
    import calc

    return calc.make()
"""

TESTS = """\
import calc
import other


def test_alpha():
    assert calc.alpha() == 1


def test_beta():
    assert calc.beta() == 2


def test_gamma():
    assert calc.gamma() == 3


def test_delta():
    assert calc.delta() == 4


def test_pair():
    assert calc.pair_a() + calc.pair_b() == 3


def test_make(base):
    assert base == {"v": 7}


def test_spin():
    assert calc.spin() == 3


def test_grow():
    assert calc.grow() == 3


def test_omega():
    assert other.omega() == 9
"""

# ``-p no:django``: the parent's pytest-django exports DJANGO_SETTINGS_MODULE into the children
COMMAND = [
    sys.executable,
    "-m",
    "pytest",
    "-q",
    "-p",
    "no:cacheprovider",
    "-p",
    "no:django",
    "--tb=short",
    "tests/test_calc.py",
]


@pytest.fixture(autouse=True)
def _no_inherited_git_env(monkeypatch):
    """This module runs git (and a script that runs git); never let a hook's GIT_DIR in."""
    for var in GIT_EXECUTION_VARS:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(autouse=True)
def _gate_off_state_dir(tmp_path, monkeypatch):
    """Locks and journals live in a state dir; never let a test touch the real one."""
    state = tmp_path / "gate-off-state"
    state.mkdir()
    monkeypatch.setenv("GATE_OFF_STATE_DIR", str(state))
    return state


def _fixture(tmp_path: Path) -> Path:
    root = tmp_path / "fx"
    (root / "tests").mkdir(parents=True)
    (root / "calc.py").write_text(CALC)
    (root / "other.py").write_text(OTHER)
    (root / "tests" / "conftest.py").write_text(CONFTEST)
    (root / "tests" / "test_calc.py").write_text(TESTS)
    return root


def _m(label, old, new, *, file="calc.py", **kw):
    return gb.BatchMutation(label=label, file=file, old=old, new=new, **kw)


# one mutation per function; each names its site as the failure text spells it
ALPHA = lambda: _m(  # noqa: E731
    "alpha returns 99",
    "def alpha():\n    return 1",
    "def alpha():\n    return 99",
    component="alpha",
    names=["calc.alpha("],
)
BETA = lambda: _m(  # noqa: E731
    "beta returns 99",
    "def beta():\n    return 2",
    "def beta():\n    return 99",
    component="beta",
    names=["calc.beta("],
)
GAMMA = lambda: _m(  # noqa: E731
    "gamma returns 99",
    "def gamma():\n    return 3",
    "def gamma():\n    return 99",
    component="gamma",
    names=["calc.gamma("],
)
DELTA = lambda: _m(  # noqa: E731
    "delta returns 99",
    "def delta():\n    return 4",
    "def delta():\n    return 99",
    component="delta",
    names=["calc.delta("],
)
PAIR_A = lambda: _m(  # noqa: E731
    "pair_a returns 99",
    "def pair_a():\n    return 1",
    "def pair_a():\n    return 99",
    component="pair_a",
    names=["calc.pair_a("],
)
PAIR_B = lambda: _m(  # noqa: E731
    "pair_b returns 99",
    "def pair_b():\n    return 2",
    "def pair_b():\n    return 99",
    component="pair_b",
    names=["calc.pair_b("],
)
UNTESTED = lambda: _m(  # noqa: E731
    "untested returns 6",
    "def untested():\n    return 5",
    "def untested():\n    return 6",
    component="untested",
    names=["calc.untested("],
)
OMEGA = lambda: _m(  # noqa: E731
    "omega returns 0",
    "def omega():\n    return 9",
    "def omega():\n    return 0",
    file="other.py",
    component="omega",
    names=["other.omega("],
)
SPIN_HANG = lambda: _m(  # noqa: E731
    "spin never advances",
    "        i += 1\n",
    "        i += 0\n",
    component="spin",
    names=["calc.spin("],
)
GROW_RUNAWAY = lambda: _m(  # noqa: E731
    "grow never stops allocating",
    "for _ in range(3):",
    "for _ in range(400):",  # 400 MB: over the 300 MB ceiling, bounded if never killed
    component="grow",
    names=["calc.grow("],
)
IMPORT_BREAK = lambda: _m(  # noqa: E731
    "import raises",
    "def pair_a():",
    'raise RuntimeError("import-time")\n\n\ndef pair_a():',
    component="boom",
    names=["import-time"],
)


def _run(root, mutations, **kw):
    kw.setdefault("command", COMMAND)
    kw.setdefault("deadline", 120)
    kw.setdefault("control", False)
    kw.setdefault("git_guard", False)
    return gb.run_batches(mutations, root=root, **kw)


def _read_pids(pidfile: Path) -> list[int]:
    if not pidfile.exists():
        return []
    return [int(word) for word in pidfile.read_text().split()]


def _alive(pid: int) -> bool:
    """Running, and not merely a zombie nobody has reaped (a container's PID 1 may not)."""
    state = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, check=False
    ).stdout.strip()
    return bool(state) and not state.startswith("Z")


def _wait_for_pids(pidfile: Path, seconds: float = 60) -> list[int]:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        pids = _read_pids(pidfile)
        if len(pids) >= 2:
            return pids
        time.sleep(0.05)
    raise AssertionError(f"the fixture never recorded its pids in {pidfile}")


def _assert_all_dead(pids: list[int], why: str, seconds: float = 15) -> None:
    assert pids, "no pid was recorded, so nothing was proven"
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and any(_alive(pid) for pid in pids):
        time.sleep(0.1)
    alive = [pid for pid in pids if _alive(pid)]
    for pid in alive:  # never leave a runaway behind, whatever the assertion says
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            # Already gone between the check and the kill: exactly what was wanted.
            pass
    assert not alive, f"{why}: still alive after {seconds:.0f}s: {alive}"


def _by_label(report):
    return {row.label: row for row in report.rows}


def _snapshot_tree(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts
    }


# --------------------------------------------------------------------------- (a) one run, attributed


def test_three_mutations_that_each_break_a_distinct_test_are_attributed_in_one_run(tmp_path):
    root = _fixture(tmp_path)
    before = _snapshot_tree(root)
    report, baseline = _run(root, [ALPHA(), GAMMA(), DELTA()])
    assert baseline is None
    assert report.suite_runs == 1, "three non-overlapping mutations must share ONE full run"
    rows = _by_label(report)
    assert {r.status for r in rows.values()} == {go.CAUGHT}
    assert {r.how for r in rows.values()} == {"batch"}
    assert rows["alpha returns 99"].failing_tests == ["tests.test_calc::test_alpha"]
    assert rows["gamma returns 99"].failing_tests == ["tests.test_calc::test_gamma"]
    assert rows["delta returns 99"].failing_tests == ["tests.test_calc::test_delta"]
    assert _snapshot_tree(root) == before, "every file is restored byte for byte"


def test_a_mutation_in_another_file_joins_the_same_batch(tmp_path):
    root = _fixture(tmp_path)
    report, _ = _run(root, [ALPHA(), OMEGA()])
    assert report.suite_runs == 1
    rows = _by_label(report)
    assert rows["omega returns 0"].failing_tests == ["tests.test_calc::test_omega"]
    assert rows["alpha returns 99"].status == go.CAUGHT


def test_a_failing_test_that_no_mutation_names_is_never_guessed_onto_one(tmp_path):
    """delta is mis-declared (its name never appears), so its failure is unclaimed: re-run alone."""
    root = _fixture(tmp_path)
    wrong = DELTA()
    wrong.names = ["calc.nothing("]
    report, _ = _run(root, [ALPHA(), wrong])
    rows = _by_label(report)
    assert report.suite_runs == 2, "one batch run, then one run for the mutation nobody claimed"
    assert rows["alpha returns 99"].how == "batch"
    assert rows["delta returns 99"].how == "single"
    assert rows["delta returns 99"].status == go.CAUGHT
    assert "name no mutation" in rows["delta returns 99"].fallback


def test_a_test_id_pattern_attributes_without_a_name(tmp_path):
    root = _fixture(tmp_path)
    by_id = GAMMA()
    by_id.names, by_id.test_ids = [], [r"::test_gamma$"]
    report, _ = _run(root, [ALPHA(), by_id])
    assert report.suite_runs == 1
    assert _by_label(report)["gamma returns 99"].failing_tests == ["tests.test_calc::test_gamma"]


def test_a_setup_error_is_counted_as_caught_and_as_an_error_not_a_failure(tmp_path):
    root = _fixture(tmp_path)
    broken = _m(
        "make raises",
        'def make():\n    return {"v": 7}',
        'def make():\n    raise RuntimeError("fixture broke")',
        component="make",
        names=["calc.make("],
    )
    report, _ = _run(root, [broken, ALPHA()])
    row = _by_label(report)["make raises"]
    assert row.status == go.CAUGHT
    assert row.errors == 1 and row.failed == 0
    assert row.failing_tests == ["tests.test_calc::test_make"]
    assert report.suite_runs == 1


# --------------------------------------------------------------------------- (b) invalid texts


def test_a_text_that_matches_twice_zero_times_or_equals_its_replacement_is_invalid(tmp_path):
    root = _fixture(tmp_path)
    twice = _m("matches twice", "return 1\n", "return 2\n", names=["x"])
    never = _m("matches never", "this text is nowhere", "x", names=["x"])
    same = _m("replacement equals the text", "return 5", "return 5", names=["x"])
    expect2 = _m("expects two", "return 1", "return 2", names=["x"], expect=2)
    report, _ = _run(root, [twice, never, same, expect2, ALPHA()])
    rows = _by_label(report)
    assert (
        rows["matches twice"].status == go.INVALID
        and "found 2 time(s)" in rows["matches twice"].detail
    )
    assert (
        rows["matches never"].status == go.INVALID
        and "found 0 time(s)" in rows["matches never"].detail
    )
    assert rows["replacement equals the text"].kind == "no-op"
    assert rows["expects two"].status == go.INVALID and "exactly once" in rows["expects two"].detail
    assert rows["alpha returns 99"].status == go.CAUGHT, "a bad sibling does not stop the rest"
    assert report.suite_runs == 1, "invalid mutations never reach a run"
    assert (root / "calc.py").read_text() == CALC


def test_a_missing_file_and_a_mutation_that_does_not_compile_are_invalid(tmp_path):
    root = _fixture(tmp_path)
    missing = _m("missing file", "x", "y", file="nowhere.py", names=["x"])
    syntax = _m("does not compile", "return 5", "return 5 +", names=["x"])
    report, _ = _run(root, [missing, syntax])
    rows = _by_label(report)
    assert rows["missing file"].kind == "missing-file"
    assert rows["does not compile"].kind == "syntax"
    assert report.suite_runs == 0


# --------------------------------------------------------------------------- (c) import break


def test_a_mutation_that_breaks_the_import_is_invalid_not_a_number(tmp_path):
    root = _fixture(tmp_path)
    report, _ = _run(root, [IMPORT_BREAK()])
    (row,) = report.rows
    assert row.status == go.INVALID and row.kind == "collection"
    assert row.failed == 0 and row.errors == 0, (
        "no count is reported for a module that did not import"
    )
    assert row.failing_tests == []


def test_an_import_break_inside_a_batch_is_isolated_and_the_siblings_still_attributed(tmp_path):
    root = _fixture(tmp_path)
    report, _ = _run(root, [ALPHA(), IMPORT_BREAK(), GAMMA()])
    rows = _by_label(report)
    assert rows["import raises"].status == go.INVALID
    assert rows["alpha returns 99"].status == go.CAUGHT and rows["alpha returns 99"].how == "single"
    assert rows["gamma returns 99"].status == go.CAUGHT
    assert "collection" in rows["alpha returns 99"].fallback
    assert report.suite_runs == 4, "the failed batch, then each member alone"


def test_an_import_time_hang_is_invalid_like_an_import_time_raise(tmp_path):
    root = _fixture(tmp_path)
    hang = _m(
        "import spins forever",
        "def make():",
        "_hold()\n\n\ndef make():",
        names=["calc.make("],
        deadline=3,
    )
    report, _ = _run(root, [hang])
    (row,) = report.rows
    assert row.status == go.INVALID and row.kind == "hung-at-import"


# --------------------------------------------------------------------------- (d) ambiguity


def test_two_mutations_that_break_the_same_test_fall_back_to_single_runs_and_still_attribute(
    tmp_path,
):
    root = _fixture(tmp_path)
    report, _ = _run(root, [PAIR_A(), PAIR_B()])
    rows = _by_label(report)
    assert report.suite_runs == 3, "one batch that cannot tell them apart, then one run each"
    for label in ("pair_a returns 99", "pair_b returns 99"):
        assert rows[label].status == go.CAUGHT
        assert rows[label].how == "single"
        assert rows[label].failing_tests == ["tests.test_calc::test_pair"]
        assert "also claimed by another mutation" in rows[label].fallback


def test_an_ambiguous_pair_does_not_drag_a_clean_third_member_into_a_re_run(tmp_path):
    root = _fixture(tmp_path)
    report, _ = _run(root, [PAIR_A(), PAIR_B(), GAMMA()])
    rows = _by_label(report)
    assert rows["gamma returns 99"].how == "batch"
    assert report.suite_runs == 3  # batch + the two ambiguous ones, not gamma


# --------------------------------------------------------------------------- (e) hang


def test_a_hang_is_reported_hung_within_the_deadline_and_counts_as_caught(tmp_path):
    root = _fixture(tmp_path)
    report, _ = _run(root, [SPIN_HANG()], deadline=3)
    (row,) = report.rows
    assert row.status == go.CAUGHT and row.kind == "hung"
    assert "HUNG" in row.detail and "peak RSS" in row.detail
    assert row.peak_rss_mb is not None
    assert (root / "calc.py").read_text() == CALC


def test_a_batch_that_hangs_is_re_run_member_by_member(tmp_path):
    root = _fixture(tmp_path)
    report, _ = _run(root, [ALPHA(), SPIN_HANG(), GAMMA()], deadline=3)
    rows = _by_label(report)
    assert (
        rows["spin never advances"].kind == "hung"
        and rows["spin never advances"].status == go.CAUGHT
    )
    assert rows["alpha returns 99"].status == go.CAUGHT and rows["alpha returns 99"].how == "single"
    assert rows["gamma returns 99"].status == go.CAUGHT
    assert "HUNG" in rows["alpha returns 99"].fallback


# --------------------------------------------------------------------------- (f) runaway memory


def test_unbounded_allocation_is_runaway_memory_and_counts_as_caught_with_the_measurement(tmp_path):
    root = _fixture(tmp_path)
    report, _ = _run(root, [GROW_RUNAWAY()], deadline=120, rss_mb=300)
    (row,) = report.rows
    assert row.status == go.CAUGHT and row.kind == "runaway-memory"
    assert "RUNAWAY-MEMORY" in row.detail
    assert row.peak_rss_mb is not None and row.peak_rss_mb > 300
    assert (root / "calc.py").read_text() == CALC


# --------------------------------------------------------------------------- no orphaned descendants


def test_no_descendant_of_a_hung_run_is_alive_afterwards(tmp_path):
    """The deadline kills the whole process GROUP: the command, and the grandchild it spawned."""
    root = _fixture(tmp_path)
    pidfile = tmp_path / "pids.txt"
    hang = _m(
        "spin hangs with a grandchild",
        "    i = 0\n",
        f"    i = 0\n    _spawn_pair({str(pidfile)!r})\n    _hold()\n",
        component="spin",
        names=["calc.spin("],
    )
    report, _ = _run(root, [hang], deadline=4)
    assert report.rows[0].kind == "hung"
    pids = _read_pids(pidfile)
    assert len(pids) == 2, "the command and its grandchild were both started"
    _assert_all_dead(pids, "a HUNG outcome left a descendant running")


def test_no_descendant_of_an_import_time_hang_is_alive_afterwards(tmp_path):
    root = _fixture(tmp_path)
    pidfile = tmp_path / "pids.txt"
    hang = _m(
        "import hangs with a grandchild",
        "def make():",
        f"_spawn_pair({str(pidfile)!r})\n_hold()\n\n\ndef make():",
        names=["calc.make("],
    )
    report, _ = _run(root, [hang], deadline=4)
    assert report.rows[0].kind == "hung-at-import"
    pids = _read_pids(pidfile)
    assert len(pids) >= 2, "the run and the --collect-only probe both started a pair"
    _assert_all_dead(pids, "an import-time hang left a descendant running")


def test_a_runaway_allocation_leaves_no_descendant_either(tmp_path):
    root = _fixture(tmp_path)
    pidfile = tmp_path / "pids.txt"
    grow = GROW_RUNAWAY()
    grow.old = "    chunks = []\n"
    grow.new = f"    chunks = []\n    _spawn_pair({str(pidfile)!r})\n    chunks.extend(b'x' * 1_000_000 for _ in range(400))\n    _hold()\n"
    report, _ = _run(root, [grow], rss_mb=300)
    assert report.rows[0].kind == "runaway-memory"
    _assert_all_dead(_read_pids(pidfile), "a RUNAWAY-MEMORY outcome left a descendant running")


def test_a_tool_that_is_sigkilled_does_not_leave_its_test_command_spinning(
    tmp_path, _gate_off_state_dir
):
    """Nothing can run in a SIGKILLed tool; the command's watchdog notices the missing parent."""
    root = _fixture(tmp_path)
    pidfile = tmp_path / "pids.txt"
    spec = _hang_spec(root, tmp_path, pidfile)
    proc = subprocess.Popen(
        [sys.executable, str(SCRIPT), str(spec), "--no-control", "--allow-dirty"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        pids = _wait_for_pids(pidfile)
        proc.kill()  # SIGKILL: no handler, no finally
        proc.wait(timeout=30)
        _assert_all_dead(pids, "SIGKILLing the tool orphaned its test command")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        # a SIGKILLed tool leaves its mutants and journals: that is what its next run recovers
        (root / "calc.py").write_text(CALC)
        (root / "other.py").write_text(OTHER)
        for journal in _gate_off_state_dir.glob("*.journal"):
            journal.unlink()


def test_the_watchdog_passes_the_exit_status_through_and_reports_a_missing_binary():
    ok = subprocess.run(
        gb._guarded([sys.executable, "-c", "raise SystemExit(3)"]), capture_output=True
    )
    assert ok.returncode == 3
    with pytest.raises(go.CommandNotRunnable):
        gb._guarded(["/nonexistent/interpreter"])


# --------------------------------------------------------------------------- (h) survivors


def test_a_mutation_no_test_notices_is_reported_survived_without_an_extra_run(tmp_path):
    root = _fixture(tmp_path)
    report, _ = _run(root, [ALPHA(), UNTESTED()])
    rows = _by_label(report)
    assert rows["untested returns 6"].status == go.SURVIVED
    assert rows["untested returns 6"].how == "batch"
    assert rows["alpha returns 99"].status == go.CAUGHT
    assert report.suite_runs == 1


def test_verify_survivors_re_runs_a_batch_survivor_alone(tmp_path):
    root = _fixture(tmp_path)
    report, _ = _run(root, [ALPHA(), UNTESTED()], verify_survivors=True)
    rows = _by_label(report)
    assert rows["untested returns 6"].status == go.SURVIVED
    assert rows["untested returns 6"].how == "single"
    assert report.suite_runs == 2


def test_a_batch_that_stays_green_survives_every_member_in_one_run(tmp_path):
    root = _fixture(tmp_path)
    second = _m(
        "untested_too returns 7",
        "def untested_too():\n    return 6",
        "def untested_too():\n    return 7",
        component="untested_too",
        names=["calc.untested_too("],
    )
    report, _ = _run(root, [UNTESTED(), second])
    assert {r.status for r in report.rows} == {go.SURVIVED}
    assert {r.how for r in report.rows} == {"batch"}
    assert report.suite_runs == 1


# --------------------------------------------------------------------------- (g) signals / restore


def _hang_spec(root: Path, tmp_path: Path, pidfile: Path) -> Path:
    spec = tmp_path / "spec.json"
    spec.write_text(
        json.dumps(
            {
                "root": str(root),
                "command": COMMAND,
                "deadline": 600,
                "mutations": [
                    {
                        "label": "hang",
                        "file": "calc.py",
                        "old": "    i = 0\n",
                        "new": f"    i = 0\n    _spawn_pair({str(pidfile)!r})\n    _hold()\n",
                        "names": ["calc.spin("],
                    },
                    {
                        "label": "omega",
                        "file": "other.py",
                        "old": "return 9",
                        "new": "return 8",
                        "names": ["other.omega("],
                    },
                ],
            }
        )
    )
    return spec


@pytest.mark.parametrize("sig", [signal.SIGINT, signal.SIGTERM], ids=["SIGINT", "SIGTERM"])
def test_every_file_is_restored_byte_for_byte_when_the_tool_is_signalled_mid_run(
    tmp_path, sig, _gate_off_state_dir
):
    """A real tool process, a batch that hangs the tests, a real signal."""
    root = _fixture(tmp_path)
    pidfile = tmp_path / "pids.txt"
    spec = _hang_spec(root, tmp_path, pidfile)
    proc = subprocess.Popen(
        [sys.executable, str(SCRIPT), str(spec), "--no-control", "--allow-dirty"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        # wait on observable state (BOTH mutants are on disk), not on a fixed delay
        for _ in range(1200):
            both = (
                b"_hold()" in (root / "calc.py").read_bytes()
                and b"return 8" in (root / "other.py").read_bytes()
            )
            if both or proc.poll() is not None:
                break
            time.sleep(0.05)
        assert both, "the mutations never reached the disk"
        _wait_for_pids(pidfile)  # the test command is really running (and has a grandchild)
        proc.send_signal(sig)
        proc.communicate(timeout=60)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    assert proc.returncode != 0
    assert (root / "calc.py").read_text() == CALC, "the signal left a mutant behind"
    assert (root / "other.py").read_text() == OTHER, "the signal left a mutant behind"
    assert sorted(p.name for p in root.iterdir() if p.is_file()) == ["calc.py", "other.py"]
    assert list(_gate_off_state_dir.glob("*.journal")) == [], "a journal outlived the restore"
    _assert_all_dead(_read_pids(pidfile), f"{sig.name} left the test command running")


def test_a_keyboard_interrupt_mid_run_restores_every_file_and_clears_the_bookkeeping(
    tmp_path, monkeypatch, _gate_off_state_dir
):
    root = _fixture(tmp_path)
    seen = {}

    def interrupted(command, cwd, deadline, rss_mb):
        seen["calc"] = (root / "calc.py").read_text()
        seen["other"] = (root / "other.py").read_text()
        raise KeyboardInterrupt

    monkeypatch.setattr(go, "_run_bounded", interrupted)
    with pytest.raises(KeyboardInterrupt):
        _run(root, [ALPHA(), OMEGA()])
    assert "return 99" in seen["calc"] and "return 0" in seen["other"], "both mutants were live"
    assert (root / "calc.py").read_text() == CALC
    assert (root / "other.py").read_text() == OTHER
    assert go._ACTIVE == {}
    assert list(_gate_off_state_dir.glob("*.journal")) == []


def test_a_restore_that_is_not_byte_identical_is_a_restore_error(tmp_path, monkeypatch):
    root = _fixture(tmp_path)
    real = go._write_atomic
    calls = []

    def corrupting(path, data, mode):
        calls.append(path)
        # the second write to calc.py is the restore: put back the WRONG bytes
        real(path, data if len(calls) == 1 else data + b"# corrupted\n", mode)

    monkeypatch.setattr(go, "_write_atomic", corrupting)
    monkeypatch.setattr(go, "_journal_write", lambda *a, **k: None)
    with pytest.raises(go.RestoreError):
        _run(root, [GAMMA()])
    monkeypatch.undo()
    (root / "calc.py").write_text(CALC)
    go._ACTIVE.clear()


def test_a_failure_to_restore_one_file_does_not_stop_the_others_being_restored(
    tmp_path, monkeypatch
):
    root = _fixture(tmp_path)
    real = go._restore

    def flaky(path, saved):
        if path.name == "calc.py":
            raise go.RestoreError("simulated")
        real(path, saved)

    monkeypatch.setattr(go, "_restore", flaky)
    with pytest.raises(go.RestoreError):
        _run(root, [ALPHA(), OMEGA()])
    assert (root / "other.py").read_text() == OTHER, "the file that could be restored was"
    monkeypatch.undo()
    (root / "calc.py").write_text(CALC)
    go._ACTIVE.clear()


def test_stale_bytecode_trusted_without_checking_the_source_cannot_certify_a_mutation(tmp_path):
    """The PRE-run clear: an unchecked-hash .pyc of the ORIGINAL that Python would trust."""
    root = _fixture(tmp_path)
    pyc = importlib.util.cache_from_source(str(root / "calc.py"))
    Path(pyc).parent.mkdir(exist_ok=True)
    py_compile.compile(
        str(root / "calc.py"),
        cfile=pyc,
        doraise=True,
        invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH,
    )
    report, _ = _run(root, [ALPHA()])
    assert report.rows[0].status == go.CAUGHT, "a stale .pyc made the mutation look equivalent"
    assert not Path(pyc).exists(), "the touched file's bytecode is cleared after the run too"


def test_other_files_bytecode_is_left_alone(tmp_path):
    root = _fixture(tmp_path)
    cache = root / "__pycache__"
    cache.mkdir()
    keep = cache / "unrelated.cpython-0.pyc"
    keep.write_bytes(b"keep")
    _run(root, [ALPHA()])
    assert keep.read_bytes() == b"keep"


# --------------------------------------------------------------------------- (i) the tree-dirty guard


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def _git_fixture(tmp_path: Path) -> Path:
    root = _fixture(tmp_path)
    _git(root, "init", "-q")
    _git(root, "add", "calc.py", "other.py", "tests")
    _git(root, "commit", "-q", "-m", "fixture")
    return root


def test_a_dirty_mutated_file_refuses_to_start_and_is_left_exactly_as_it_was(tmp_path, capsys):
    root = _git_fixture(tmp_path)
    (root / "calc.py").write_text(CALC + "\n# uncommitted work\n")
    before = (root / "calc.py").read_bytes()
    with pytest.raises(gb.DirtyTree) as info:
        _run(root, [ALPHA()], git_guard=True)
    assert "calc.py" in str(info.value)
    assert (root / "calc.py").read_bytes() == before, "the tool must not touch a dirty file"
    assert "after the run" not in capsys.readouterr().err, (
        "nothing ran, so nothing differs afterwards"
    )


def test_an_untracked_target_counts_as_dirty(tmp_path):
    root = _git_fixture(tmp_path)
    (root / "extra.py").write_text("def f():\n    return 1\n")
    extra = _m("extra", "return 1", "return 2", file="extra.py", names=["f("])
    with pytest.raises(gb.DirtyTree):
        _run(root, [extra], git_guard=True)


def test_the_cli_exits_five_on_a_dirty_tree_and_allow_dirty_runs_anyway(tmp_path):
    root = _git_fixture(tmp_path)
    (root / "calc.py").write_text(CALC + "\n# uncommitted work\n")
    spec = tmp_path / "spec.json"
    spec.write_text(
        json.dumps(
            {
                "root": str(root),
                "command": COMMAND,
                "mutations": [
                    {
                        "label": "gamma",
                        "file": "calc.py",
                        "old": "def gamma():\n    return 3",
                        "new": "def gamma():\n    return 99",
                        "names": ["calc.gamma("],
                    }
                ],
            }
        )
    )
    refused = subprocess.run(
        [sys.executable, str(SCRIPT), str(spec), "--no-control"], capture_output=True, text=True
    )
    assert refused.returncode == gb.EXIT_DIRTY
    assert "uncommitted" in refused.stderr
    allowed = subprocess.run(
        [sys.executable, str(SCRIPT), str(spec), "--no-control", "--allow-dirty"],
        capture_output=True,
        text=True,
    )
    assert allowed.returncode == 0, allowed.stdout + allowed.stderr


def test_a_clean_tree_runs_and_git_agrees_the_files_are_unchanged_afterwards(tmp_path):
    root = _git_fixture(tmp_path)
    report, _ = _run(root, [ALPHA(), GAMMA()], git_guard=True)
    assert {r.status for r in report.rows} == {go.CAUGHT}
    assert _git(root, "status", "--porcelain", "--", "calc.py", "other.py") == ""


def test_git_reporting_a_difference_after_the_run_is_a_restore_failure(tmp_path, monkeypatch):
    """If the restore hook is neutered, git is the second witness (the hash check lives in it)."""
    root = _git_fixture(tmp_path)
    monkeypatch.setattr(go, "_restore", lambda path, saved: None)
    with pytest.raises(go.RestoreError, match="differs from git"):
        _run(root, [ALPHA()], git_guard=True)
    monkeypatch.undo()
    (root / "calc.py").write_text(CALC)
    go._ACTIVE.clear()


def test_outside_a_git_work_tree_the_guard_says_so_and_runs(tmp_path, capsys):
    root = _fixture(tmp_path)
    report, _ = _run(root, [ALPHA()], git_guard=True)
    assert report.rows[0].status == go.CAUGHT
    assert "SHA-256 only" in capsys.readouterr().err


def test_git_is_asked_without_a_hooks_repository_variables(tmp_path, monkeypatch):
    root = _git_fixture(tmp_path)
    seen = {}
    real = subprocess.run

    def spy(args, **kwargs):
        seen["env"] = kwargs.get("env")
        return real(args, **kwargs)

    monkeypatch.setattr(gb.subprocess, "run", spy)
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "elsewhere"))
    dirty, verified = gb.git_dirty([root / "calc.py"])
    assert (dirty, verified) == ([], True)
    assert seen["env"] is not None and "GIT_DIR" not in seen["env"]


# --------------------------------------------------------------------------- the baseline


def test_a_red_baseline_refuses_to_judge_anything(tmp_path):
    root = _fixture(tmp_path)
    (root / "tests" / "test_calc.py").write_text(TESTS + "\n\ndef test_red():\n    assert False\n")
    report, baseline = _run(root, [ALPHA()], control=True)
    assert baseline is not None and "not green" in baseline.detail
    assert report.rows == []
    assert (root / "calc.py").read_text() == CALC


# --------------------------------------------------------------------------- pure pieces


def _f(test_id, text):
    return gb.Failure(test_id, text, ["failure"])


def test_attribution_is_exclusive_contested_or_unclaimed():
    a = _m("a", "x", "y", names=["site_a("])
    b = _m("b", "x", "y", names=["site_b("])
    attribution = gb.attribute(
        [_f("t::one", "site_a( broke"), _f("t::both", "site_a( and site_b("), _f("t::none", "?")],
        [a, b],
    )
    assert attribution.exclusive == {0: ["t::one"]}
    assert attribution.contested == {0: ["t::both"], 1: ["t::both"]}
    assert attribution.unclaimed == ["t::none"]
    assert gb.judge(attribution, 0) == ("caught", "")
    decision, reason = gb.judge(attribution, 1)
    assert decision == "single" and "also claimed" in reason


def test_a_mutation_is_survived_only_when_every_failure_belongs_to_someone_else():
    a = _m("a", "x", "y", names=["site_a("])
    b = _m("b", "x", "y", names=["site_b("])
    clean = gb.attribute([_f("t::one", "site_a(")], [a, b])
    assert gb.judge(clean, 1)[0] == "survived"
    with_stray = gb.attribute([_f("t::one", "site_a("), _f("t::x", "nobody")], [a, b])
    assert gb.judge(with_stray, 1)[0] == "single"


def test_an_empty_name_never_claims_everything():
    a = _m("a", "x", "y", names=[""])
    assert gb.attribute([_f("t::one", "any text")], [a]).unclaimed == ["t::one"]


def test_claims_matches_names_as_substrings_and_ids_as_regexes():
    m = _m("m", "x", "y", names=["calc.alpha("], test_ids=[r"::test_a\w+$"])
    assert gb.claims(m, _f("t::z", "in calc.alpha( here"))
    assert gb.claims(m, _f("t::test_alpha", "no name"))
    assert not gb.claims(m, _f("t::test_beta", "no name"))


def test_a_component_alone_names_the_site(tmp_path):
    muts = gb.mutations_from_spec(
        {
            "mutations": [
                {"label": "l", "file": "f.py", "old": "a", "new": "b", "component": "calc.alpha("}
            ]
        }
    )
    assert muts[0].names == ["calc.alpha("] and muts[0].attributable


def test_plan_never_puts_two_mutations_of_one_component_in_a_batch():
    a, b, c = (_m(n, "x", "y", names=["n"], component="same") for n in "abc")
    spans = {0: (Path("f"), 0, 1), 1: (Path("f"), 10, 11), 2: (Path("f"), 20, 21)}
    assert gb.plan_batches([a, b, c], spans) == [[0], [1], [2]]


def test_plan_separates_overlapping_texts_but_not_adjacent_ones():
    muts = [_m(n, "x", "y", names=["n"]) for n in "abc"]
    spans = {0: (Path("f"), 0, 10), 1: (Path("f"), 5, 15), 2: (Path("f"), 10, 20)}
    # a overlaps b; c touches a's end (adjacent, not overlapping) and overlaps b
    assert gb.plan_batches(muts, spans) == [[0, 2], [1]]


def test_plan_lets_the_same_span_in_different_files_share_a_batch():
    muts = [_m(n, "x", "y", names=["n"]) for n in "ab"]
    spans = {0: (Path("f"), 0, 10), 1: (Path("g"), 0, 10)}
    assert gb.plan_batches(muts, spans) == [[0, 1]]


def test_plan_keeps_different_run_configurations_apart():
    muts = [
        _m("a", "x", "y", names=["n"]),
        _m("b", "x", "y", names=["n"], deadline=3),
        _m("c", "x", "y", names=["n"], rss_mb=300),
        _m("d", "x", "y", names=["n"], smoke=["true"]),
        _m("e", "x", "y", names=["n"]),
    ]
    spans = {i: (Path(f"f{i}"), 0, 1) for i in range(5)}
    assert gb.plan_batches(muts, spans) == [[0, 4], [1], [2], [3]]


def test_plan_runs_an_unattributable_mutation_alone():
    muts = [_m("a", "x", "y", names=["n"]), _m("b", "x", "y"), _m("c", "x", "y", names=["n"])]
    spans = {i: (Path(f"f{i}"), 0, 1) for i in range(3)}
    assert gb.plan_batches(muts, spans) == [[0, 2], [1]]


def test_plan_honours_max_batch():
    muts = [_m(str(i), "x", "y", names=["n"]) for i in range(5)]
    spans = {i: (Path(f"f{i}"), 0, 1) for i in range(5)}
    assert gb.plan_batches(muts, spans, max_batch=2) == [[0, 1], [2, 3], [4]]
    assert gb.plan_batches(muts, spans, max_batch=1) == [[i] for i in range(5)]


def test_split_plan_deals_the_largest_batches_to_the_least_loaded_worker():
    shares = gb._split_plan([[0], [1, 2, 3], [4, 5], [6]], 2)
    assert sorted(len(sum(share, [])) for share in shares) == [3, 4]
    assert sorted(sum(sum(shares, []), [])) == list(range(7))


def test_parse_junit_reads_failures_and_errors_and_merges_a_test_with_both(tmp_path):
    xml = tmp_path / "r.xml"
    xml.write_text(
        "<testsuites><testsuite>"
        '<testcase classname="a.b" name="t1"><failure message="boom">trace one</failure></testcase>'
        '<testcase classname="a.b" name="t2"><error message="setup">trace two</error></testcase>'
        '<testcase classname="a.b" name="t3"><failure message="f">x</failure>'
        '<error message="teardown">y</error></testcase>'
        '<testcase classname="a.b" name="ok"/>'
        '<testcase classname="" name="collect"><error message="import">z</error></testcase>'
        "</testsuite></testsuites>"
    )
    failures = {f.id: f for f in gb.parse_junit(xml)}
    assert set(failures) == {"a.b::t1", "a.b::t2", "a.b::t3", "collect"}
    assert failures["a.b::t1"].kinds == ["failure"] and "trace one" in failures["a.b::t1"].text
    assert failures["a.b::t2"].kinds == ["error"]
    assert failures["a.b::t3"].kinds == ["failure", "error"]


def test_parse_junit_returns_none_not_an_empty_list_when_there_is_no_report(tmp_path):
    assert gb.parse_junit(tmp_path / "missing.xml") is None
    garbage = tmp_path / "bad.xml"
    garbage.write_text("<not xml")
    assert gb.parse_junit(garbage) is None


# --------------------------------------------------------------------------- the CLI


def _cli_spec(root: Path, tmp_path: Path, mutations: list[dict], **extra) -> Path:
    spec = tmp_path / "spec.json"
    spec.write_text(
        json.dumps(
            {"root": str(root), "command": COMMAND, "deadline": 60, **extra, "mutations": mutations}
        )
    )
    return spec


def _entry(label, old, new, names, file="calc.py", **kw):
    return {"label": label, "file": file, "old": old, "new": new, "names": names, **kw}


GAMMA_E = _entry(
    "gamma", "def gamma():\n    return 3", "def gamma():\n    return 99", ["calc.gamma("]
)
ALPHA_E = _entry(
    "alpha", "def alpha():\n    return 1", "def alpha():\n    return 99", ["calc.alpha("]
)
UNTESTED_E = _entry(
    "untested", "def untested():\n    return 5", "def untested():\n    return 6", ["calc.untested("]
)


def _cli(spec: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(spec), "--no-control", "--allow-dirty", *args],
        capture_output=True,
        text=True,
    )


def test_cli_writes_a_json_report_and_a_human_summary(tmp_path):
    root = _fixture(tmp_path)
    spec = _cli_spec(root, tmp_path, [ALPHA_E, GAMMA_E, UNTESTED_E])
    proc = _cli(spec)
    assert proc.returncode == 1, "a survivor is a non-zero exit"
    assert "SURVIVED  untested" in proc.stdout
    assert "caught by: tests.test_calc::test_alpha" in proc.stdout
    assert (
        "summary: 2 CAUGHT, 1 SURVIVED, 0 INVALID; 1 full test run(s) for 3 runnable" in proc.stdout
    )
    assert "a question, not a pass" in proc.stdout
    report = json.loads(Path(str(spec) + ".gateoff-report.json").read_text())
    by = {r["label"]: r for r in report["rows"]}
    assert by["alpha"]["status"] == "CAUGHT" and by["alpha"]["failing_tests"] == [
        "tests.test_calc::test_alpha"
    ]
    assert by["untested"]["status"] == "SURVIVED" and by["untested"]["how"] == "batch"
    assert report["summary"]["suite_runs"] == 1 and report["summary"]["naive_runs"] == 3
    assert (root / "calc.py").read_text() == CALC


def test_cli_exits_zero_when_everything_is_caught(tmp_path):
    root = _fixture(tmp_path)
    proc = _cli(_cli_spec(root, tmp_path, [ALPHA_E, GAMMA_E]))
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_cli_exits_one_on_an_invalid_mutation(tmp_path):
    root = _fixture(tmp_path)
    bad = _entry("never", "no such text", "x", ["calc.alpha("])
    proc = _cli(_cli_spec(root, tmp_path, [ALPHA_E, bad]))
    assert proc.returncode == 1 and "INVALID   never" in proc.stdout


def test_cli_exits_two_on_a_red_baseline(tmp_path):
    root = _fixture(tmp_path)
    (root / "tests" / "test_calc.py").write_text(TESTS + "\n\ndef test_red():\n    assert False\n")
    spec = _cli_spec(root, tmp_path, [ALPHA_E])
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), str(spec), "--allow-dirty"], capture_output=True, text=True
    )
    assert proc.returncode == 2 and "BASELINE" in proc.stderr
    assert (root / "calc.py").read_text() == CALC


@pytest.mark.parametrize(
    "command_tail",
    [["-x"], ["--exitfirst"], ["--maxfail=1"]],
    ids=["-x", "--exitfirst", "--maxfail"],
)
def test_cli_refuses_a_command_that_stops_at_the_first_failure(tmp_path, command_tail):
    root = _fixture(tmp_path)
    spec = _cli_spec(root, tmp_path, [ALPHA_E], command=COMMAND + command_tail)
    proc = _cli(spec)
    assert proc.returncode == 2 and "stops at the first failure" in proc.stderr
    assert (root / "calc.py").read_text() == CALC


def test_cli_exits_two_on_duplicate_labels_a_bad_spec_and_too_many_jobs(tmp_path):
    root = _fixture(tmp_path)
    dup = _cli(_cli_spec(root, tmp_path, [ALPHA_E, ALPHA_E]))
    assert dup.returncode == 2 and "unique" in dup.stderr
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert _cli(bad).returncode == 2
    many = _cli(_cli_spec(root, tmp_path, [ALPHA_E]), "--jobs", "3")
    assert many.returncode == 2 and "--jobs" in many.stderr


def test_cli_test_files_are_appended_to_the_command(tmp_path):
    root = _fixture(tmp_path)
    spec = _cli_spec(
        root, tmp_path, [ALPHA_E], command=COMMAND[:-1], test_files=["tests/test_calc.py"]
    )
    proc = _cli(spec)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_cli_only_filters_by_label(tmp_path):
    root = _fixture(tmp_path)
    proc = _cli(_cli_spec(root, tmp_path, [ALPHA_E, GAMMA_E]), "--only", "gam")
    assert "gamma" in proc.stdout and "alpha" not in proc.stdout.split("summary")[0]


def test_cli_survivors_only_re_runs_just_the_survivors_alone_and_merges_the_report(tmp_path):
    root = _fixture(tmp_path)
    spec = _cli_spec(root, tmp_path, [ALPHA_E, GAMMA_E, UNTESTED_E])
    first = _cli(spec)
    assert first.returncode == 1
    report_path = Path(str(spec) + ".gateoff-report.json")
    again = _cli(spec, "--survivors-only")
    assert again.returncode == 1
    assert "untested" in again.stdout.split("summary")[0]
    assert "alpha" not in again.stdout.split("summary")[0], "caught mutations are not re-run"
    report = json.loads(report_path.read_text())
    by = {r["label"]: r for r in report["rows"]}
    assert set(by) == {"alpha", "gamma", "untested"}, "the merged report keeps every row"
    assert by["untested"]["how"] == "single", "a survivor is confirmed ALONE"
    assert by["alpha"]["how"] == "batch"


def test_cli_survivors_only_without_an_earlier_report_is_a_usage_error(tmp_path):
    root = _fixture(tmp_path)
    proc = _cli(_cli_spec(root, tmp_path, [ALPHA_E]), "--survivors-only")
    assert proc.returncode == 2 and "earlier report" in proc.stderr


def test_cli_survivors_only_with_no_survivor_is_a_clean_no_op(tmp_path):
    root = _fixture(tmp_path)
    spec = _cli_spec(root, tmp_path, [ALPHA_E, GAMMA_E])
    assert _cli(spec).returncode == 0
    again = _cli(spec, "--survivors-only")
    assert again.returncode == 0 and "no SURVIVED" in again.stdout


def test_cli_reads_a_toml_spec(tmp_path):
    root = _fixture(tmp_path)
    spec = tmp_path / "spec.toml"
    command = ", ".join(json.dumps(part) for part in COMMAND)
    spec.write_text(
        f'root = "{root}"\ncommand = [{command}]\n[[mutations]]\nlabel = "alpha"\nfile = "calc.py"\n'
        'old = "def alpha():\\n    return 1"\nnew = "def alpha():\\n    return 99"\n'
        'names = ["calc.alpha("]\n'
    )
    proc = _cli(spec)
    assert proc.returncode == 0, proc.stdout + proc.stderr


# --------------------------------------------------------------------------- --jobs


def test_two_jobs_give_the_same_verdicts_and_leave_the_real_tree_untouched(tmp_path):
    root = _fixture(tmp_path)
    # same component twice: two batches, so there is something to deal to the second worker
    other_alpha = _entry(
        "alpha again",
        "def alpha():\n    return 1",
        "def alpha():\n    return 98",
        ["calc.alpha("],
        component="alpha",
    )
    first_alpha = dict(ALPHA_E, component="alpha")
    mutations = [first_alpha, other_alpha, GAMMA_E, UNTESTED_E]
    one = _cli(_cli_spec(root, tmp_path, mutations), "--jobs", "1")
    report_one = json.loads(Path(str(tmp_path / "spec.json") + ".gateoff-report.json").read_text())
    two = _cli(_cli_spec(root, tmp_path, mutations), "--jobs", "2")
    report_two = json.loads(Path(str(tmp_path / "spec.json") + ".gateoff-report.json").read_text())
    assert one.returncode == two.returncode == 1
    status = lambda report: {r["label"]: r["status"] for r in report["rows"]}  # noqa: E731
    assert status(report_one) == status(report_two)
    assert set(status(report_two).values()) == {"CAUGHT", "SURVIVED"}
    assert (root / "calc.py").read_text() == CALC


def test_jobs_beyond_two_are_refused(tmp_path):
    root = _fixture(tmp_path)
    with pytest.raises(ValueError, match="--jobs"):
        _run(root, [ALPHA()], jobs=3)


# --------------------------------------------------------------------------- locking


def test_a_second_run_on_a_directory_another_run_holds_refuses_and_touches_nothing(tmp_path):
    root = _fixture(tmp_path)
    guard = go._lock_dir(root.resolve())
    try:
        with pytest.raises(go.TargetLocked):
            _run(root, [ALPHA()])
    finally:
        go._release(guard)
    assert (root / "calc.py").read_text() == CALC


# --------------------------------------------------------------------------- formatting


def test_format_row_shows_the_evidence_the_fallback_and_the_batch():
    row = gb.Row(
        "m",
        "f.py",
        go.CAUGHT,
        "failed",
        "2 failing",
        how="single",
        batch=3,
        fallback="shared test",
        failing_tests=["a", "b", "c", "d"],
    )
    text = gb.format_row(row)
    assert "[batch 3, single]" in text and "re-run alone: shared test" in text
    assert "caught by: a, b, c (+1 more)" in text


def test_summary_counts_bounded_catches_and_the_saving():
    rows = [
        gb.Row("a", "f", go.CAUGHT, "hung", "d"),
        gb.Row("b", "f", go.CAUGHT, "runaway-memory", "d"),
        gb.Row("c", "f", go.CAUGHT, "failed", "d"),
        gb.Row("d", "f", go.INVALID, "collection", "d"),
    ]
    text = gb.format_summary(gb.Report(rows, suite_runs=2, control_runs=1, naive_runs=3))
    assert "3 CAUGHT (1 HUNG, 1 RUNAWAY-MEMORY), 0 SURVIVED, 1 INVALID" in text
    assert "2 full test run(s) for 3 runnable mutation(s)" in text


# --------------------------------------------------------------------------- bad reports, apply-time checks


@pytest.mark.parametrize("unreadable", [None, []], ids=["no-report", "empty-report"])
def test_a_missing_or_empty_junit_report_is_never_read_as_no_failures(
    tmp_path, monkeypatch, unreadable
):
    root = _fixture(tmp_path)
    monkeypatch.setattr(gb, "parse_junit", lambda path: unreadable)
    report, _ = _run(root, [ALPHA(), GAMMA()])
    assert report.suite_runs == 3, "a batch that cannot be attributed is re-run member by member"
    for row in report.rows:
        assert row.status == go.CAUGHT and row.how == "single"
        assert "junit" in row.fallback


def _ctx(root, tmp_path):
    scratch = tmp_path / "scratch"
    scratch.mkdir(exist_ok=True)
    return gb.Context(root, list(COMMAND), 60, 4096, scratch)


def test_combine_applies_non_overlapping_edits_to_one_file_in_one_pass(tmp_path):
    root = _fixture(tmp_path)
    combined = gb._combine(root, [GAMMA(), ALPHA()])
    expected = CALC.replace("def alpha():\n    return 1", "def alpha():\n    return 99").replace(
        "def gamma():\n    return 3", "def gamma():\n    return 99"
    )
    assert combined == {(root / "calc.py").resolve(): expected.encode()}


def test_combine_refuses_overlapping_edits(tmp_path):
    root = _fixture(tmp_path)
    left = _m("left", "def alpha():\n    return 1", "X", names=["a"])
    right = _m("right", "return 1\n\n\ndef beta", "Y", names=["b"])
    # the whole sentence: tmp_path is named after this test, so a bare "overlap" matches any path
    result = gb._combine(root, [left, right])
    assert isinstance(result, str) and result.startswith("two mutations overlap in ")


def test_combine_refuses_text_that_no_longer_matches_exactly_once(tmp_path):
    root = _fixture(tmp_path)
    twice = _m("twice", "return 1\n", "return 2\n", names=["a"])
    assert "no longer matches exactly once" in gb._combine(root, [twice])
    run = gb.run_group(_ctx(root, tmp_path), [twice])
    assert run.error_kind == "not-applicable" and run.outcome is None
    assert (root / "calc.py").read_text() == CALC


def test_combine_refuses_a_source_that_does_not_compile(tmp_path):
    root = _fixture(tmp_path)
    broken = _m("broken", "return 5", "return 5 +", names=["a"])
    assert "does not compile" in gb._combine(root, [broken])


def test_a_failing_smoke_command_makes_the_mutation_invalid_and_the_file_is_restored(tmp_path):
    root = _fixture(tmp_path)
    smoke = [sys.executable, "-c", "import calc; assert calc.alpha() == 1"]
    guarded = ALPHA()
    guarded.smoke = smoke
    report, _ = _run(root, [guarded])
    (row,) = report.rows
    assert row.status == go.INVALID and row.kind == "smoke"
    assert (root / "calc.py").read_text() == CALC


def test_a_command_that_cannot_start_is_invalid_and_the_file_is_restored(tmp_path):
    root = _fixture(tmp_path)
    report, _ = _run(root, [ALPHA(), GAMMA()], command=["/nonexistent/interpreter", "-m", "pytest"])
    assert {r.status for r in report.rows} == {go.INVALID}
    assert {r.kind for r in report.rows} == {"command-not-found"}
    assert (root / "calc.py").read_text() == CALC


def test_the_example_mutation_file_in_the_docs_loads_and_is_attributable():
    """Doc-claim-verbatim (#1046): the spec in docs/testing-strategy.md is read by the tool."""
    text = (ROOT / "docs" / "testing-strategy.md").read_text()
    section = text.split("## Gate-off over many sites", 1)[1].split("\n## ", 1)[0]
    block = section.split("```json\n", 1)[1].split("\n```", 1)[0]
    spec = json.loads(block)
    mutations = gb.mutations_from_spec(spec)
    assert len(mutations) == 2
    assert all(m.attributable for m in mutations)
    assert mutations[0].names == ["calc.alpha("] and mutations[1].test_ids == ["::test_gamma"]
    assert spec["test_files"] == ["tests/test_calc.py"]
    for entry in spec["mutations"]:
        assert entry["old"] != entry["new"]


# --------------------------------------------------------------------------- the self-check list


def test_every_selfcheck_mutation_text_still_applies():
    """The tool is gate-off'd with gate_off.py; this keeps that list from rotting."""
    spec = json.loads(SELFCHECK.read_text())
    sources = {
        "scripts/gate_off_batch.py": SCRIPT.read_text(),
        "scripts/gate_off.py": (ROOT / "scripts" / "gate_off.py").read_text(),
    }
    assert spec["mutations"], "the self-check list is empty"
    labels = [e["label"] for e in spec["mutations"]]
    assert len(set(labels)) == len(labels)
    for entry in spec["mutations"]:
        assert entry["file"] in sources, f"{entry['label']!r} names a file this test cannot read"
        found = sources[entry["file"]].count(entry["old"])
        assert found == entry.get("expect", 1), f"{entry['label']!r}: found {found} time(s)"
        assert entry["old"] != entry["new"]


def test_the_tool_loads_the_single_mutation_harness_it_reuses():
    assert os.path.basename(go.__file__) == "gate_off.py"
    for name in ("_run_bounded", "_restore", "_lock_dir", "_journal_write", "classify"):
        assert hasattr(go, name)
