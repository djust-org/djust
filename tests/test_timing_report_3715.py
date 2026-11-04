"""tests/timing_report.py — each pytest process reports its worst GC pause and stall (#3715).

The plugin exists so the next timing failure on CI is attributable: was the process
paused (a stop-the-world collection, a descheduled runner), or is the code slow?
Each check is exercised for real, through pytest's ``pytester``: a throwaway suite
run with the plugin registered via ``-p``, asserting on what the terminal summary
says. Nothing here asserts how LONG a pause or stall lasted (that is the machine);
it asserts that one is reported, attributed to the right test, and relayed from
every xdist worker.

Gate-off, per mechanism (#2135: name the test that goes red when ONLY that mechanism
is removed):

- stop appending ``Recorder.on_gc`` to ``gc.callbacks`` ->
  ``test_a_gen_2_collection_is_reported_and_attributed_to_its_test``;
- compare against the wrong generation in ``on_gc`` ->
  ``test_only_generation_2_is_a_pause`` and ``test_gen_2_pause_is_measured_from_its_start``;
- do not start the ticker -> ``test_a_stall_is_reported_and_attributed_to_its_test``;
- drop the ``workeroutput`` relay or the ``pytest_testnodedown`` read ->
  ``test_under_xdist_the_controller_prints_one_line_per_worker``;
- let the report change an exit code or an outcome ->
  ``test_the_report_changes_no_outcome``.
"""

from __future__ import annotations

import ctypes
import gc
import os
import threading
from pathlib import Path

import pytest

from tests.timing_report import (
    AFTER_LAST_TEST,
    BEFORE_FIRST_TEST,
    BETWEEN_TESTS,
    NOTABLE_S,
    SECTION,
    STALL_COUNTED_S,
    TICK_S,
    Recorder,
    TimingReport,
    Ticker,
    Worst,
    format_line,
)

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = "tests.timing_report"


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


# ---------------------------------------------------------------------------
# The recorder: pure bookkeeping, clock injected
# ---------------------------------------------------------------------------


def test_gen_2_pause_is_measured_from_its_start():
    clock = FakeClock()
    rec = Recorder(clock=clock, current="t::a")
    rec.on_gc("start", {"generation": 2})
    clock.now += 0.75
    rec.on_gc("stop", {"generation": 2})
    assert rec.gc_pause.seconds == pytest.approx(0.75)
    assert rec.gc_pause.when == "t::a"
    assert rec.gc_pause.seen == 1


def test_only_generation_2_is_a_pause():
    clock = FakeClock()
    rec = Recorder(clock=clock)
    for generation in (0, 1):
        rec.on_gc("start", {"generation": generation})
        clock.now += 5
        rec.on_gc("stop", {"generation": generation})
    assert rec.gc_pause.seen == 0 and rec.gc_pause.seconds == 0


def test_the_worst_pause_is_kept_with_the_test_that_had_it():
    clock = FakeClock()
    rec = Recorder(clock=clock)
    for test, took in (("t::slow", 1.5), ("t::fast", 0.1), ("t::mid", 0.9)):
        rec.current = test
        rec.on_gc("start", {"generation": 2})
        clock.now += took
        rec.on_gc("stop", {"generation": 2})
    assert (rec.gc_pause.seconds, rec.gc_pause.when) == (pytest.approx(1.5), "t::slow")
    assert rec.gc_pause.seen == 3
    assert rec.gc_pause.over_notable == 1  # only the 1.5 s one reaches NOTABLE_S


def test_a_stop_without_a_start_is_ignored():
    rec = Recorder(clock=FakeClock())
    rec.on_gc("stop", {"generation": 2})  # the plugin was installed mid-collection
    assert rec.gc_pause.seen == 0


def test_a_tick_on_time_is_not_a_stall_and_a_late_one_is_by_its_lateness():
    rec = Recorder(clock=FakeClock(), current="t::a")
    rec.on_tick(TICK_S)  # woke exactly when it should
    rec.on_tick(TICK_S - 0.01)  # (timer slop can be early)
    assert rec.stall.seen == 0
    rec.on_tick(TICK_S + 2.0)
    assert rec.stall.seconds == pytest.approx(2.0)
    assert rec.stall.when == "t::a"
    assert rec.stall.over_notable == 1


def test_a_notable_threshold_is_the_shortest_communicator_wait():
    # connect() waited 1 s by default: the wait that timed out in #3692.
    assert NOTABLE_S == 1.0
    worst = Worst()
    worst.note(NOTABLE_S - 0.001, "x")
    worst.note(NOTABLE_S, "y")
    assert worst.over_notable == 1


def test_the_line_names_the_worst_pause_and_stall_with_their_tests():
    rec = Recorder(clock=FakeClock(), current="t::a")
    rec.gc_pause.note(2.5, "t::gc")
    rec.stall.note(0.4, "t::stall")
    line = format_line("gw3", rec.snapshot())
    assert line.startswith("gw3: ")
    assert "gen-2 GC pause: worst 2.50 s in t::gc; 1 collections, 1 over 1 s" in line
    assert "stall: worst 0.40 s in t::stall; 1 ticks over 0.1 s late, 0 over 1 s" in line


def test_an_empty_recorder_reports_none_rather_than_a_zero():
    line = format_line("this process", Recorder(clock=FakeClock()).snapshot())
    assert "gen-2 GC pause: none" in line and "stall: none" in line


def test_most_late_ticks_are_seen_but_only_the_worth_counting_ones_are_counted():
    # Every 50 ms tick is a few ms late on a busy machine; "7149 late ticks" is no number.
    rec = Recorder(clock=FakeClock(), current="t::a")
    for _ in range(1000):
        rec.on_tick(TICK_S + 0.004)  # 4 ms of ordinary slop
    rec.on_tick(TICK_S + STALL_COUNTED_S)  # exactly at the floor
    rec.on_tick(TICK_S + 1.5)
    assert rec.stall.seen == 1002
    assert rec.stall.counted == 2
    assert rec.stall.over_notable == 1
    line = format_line("p", rec.snapshot())
    assert "2 ticks over 0.1 s late, 1 over 1 s" in line
    assert "1002" not in line, "the count of every late tick is not reported"


def test_a_gen_2_collection_counts_every_time_it_ran():
    clock = FakeClock()
    rec = Recorder(clock=clock)
    for _ in range(4):
        rec.on_gc("start", {"generation": 2})
        clock.now += 0.01
        rec.on_gc("stop", {"generation": 2})
    assert rec.gc_pause.seen == rec.gc_pause.counted == 4
    assert "4 collections" in format_line("p", rec.snapshot())


def test_a_pause_outside_a_test_is_not_blamed_on_the_test_before_it():
    plugin = TimingReport.__new__(TimingReport)
    plugin.recorder = Recorder(clock=FakeClock())
    assert plugin.recorder.current == BEFORE_FIRST_TEST
    plugin.pytest_runtest_logstart("t::a", None)
    assert plugin.recorder.current == "t::a"
    plugin.pytest_runtest_logfinish("t::a", None)
    assert plugin.recorder.current == BETWEEN_TESTS
    assert len({BEFORE_FIRST_TEST, BETWEEN_TESTS, AFTER_LAST_TEST}) == 3


def test_a_gen_2_collection_after_the_last_test_is_labelled_so(inner: pytest.Pytester):
    inner.makepyfile(test_one="def test_one():\n    pass\n")
    out = _run(inner).stdout.str()
    line = next(line for line in out.splitlines() if line.startswith("this process:"))
    assert "test_one.py::test_one" not in line.split("stall:")[0], line


# ---------------------------------------------------------------------------
# The ticker thread
# ---------------------------------------------------------------------------


def test_the_ticker_is_a_daemon_that_stops_on_request():
    ticker = Ticker(Recorder())
    assert ticker.daemon, "a daemon: it must never keep a pytest process alive"
    ticker.start()
    assert ticker.is_alive()
    ticker.stop()
    assert not ticker.is_alive()


def test_the_ticker_reports_each_gap_to_the_recorder():
    seen = []
    done = threading.Event()

    class Spy(Recorder):
        def on_tick(self, gap):
            seen.append(gap)
            if len(seen) == 3:
                done.set()

    ticker = Ticker(Spy())
    ticker.start()
    try:
        assert done.wait(30), "the ticker never ticked three times"
    finally:
        ticker.stop()
    assert all(gap > 0 for gap in seen)


# ---------------------------------------------------------------------------
# The plugin, for real
# ---------------------------------------------------------------------------


@pytest.fixture
def inner(pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch) -> pytest.Pytester:
    """A pytester whose runs load the plugin and nothing of this repo's settings."""
    monkeypatch.delenv("DJANGO_SETTINGS_MODULE", raising=False)
    monkeypatch.delenv("DJUST_COLLECTED_FLOOR", raising=False)
    monkeypatch.setenv("PYTHONPATH", str(ROOT))  # subprocess runs import the plugin by name
    pytester.syspathinsert(ROOT)
    return pytester


def _run(pytester: pytest.Pytester, *args: str, subprocess_: bool = False) -> pytest.RunResult:
    argv = ("-p", PLUGIN, "-p", "no:cacheprovider", "-p", "no:django", *args)
    return pytester.runpytest_subprocess(*argv) if subprocess_ else pytester.runpytest(*argv)


def test_a_serial_run_prints_one_line_for_its_own_process(inner: pytest.Pytester):
    inner.makepyfile("def test_a(): pass\ndef test_b(): pass\n")
    result = _run(inner)
    assert result.ret == pytest.ExitCode.OK
    out = result.stdout.str()
    assert SECTION in out
    assert "this process: gen-2 GC pause:" in out and "; stall:" in out


#: How long the inner suite's slow collection takes. It has to beat every OTHER generation-2
#: pause in the inner session (the ones that happen on their own while pytest imports and
#: collects, in a process that already holds this suite's heap) with room to spare.
SLOW_COLLECTION_S = 1.0


def test_a_gen_2_collection_is_reported_and_attributed_to_its_test(inner: pytest.Pytester):
    """The worst pause is named after the test it happened in.

    "Worst" is a comparison between this session's generation-2 pauses, and the inner
    session is in-process: its imports and collection run generation-2 collections of their
    own in a process holding the whole suite's heap, which on CI took 0.10 s (#3800). An
    explicit ``gc.collect()`` in ``test_collects`` takes about as long, so which of them was
    "worst" was a coin toss, and the report named ``(startup or collection, before the first
    test)`` instead of the test. The test makes its own pause unmistakably the worst: a
    callback registered after the plugin's (callbacks run in order) holds the collection for
    :data:`SLOW_COLLECTION_S`, inside the interval the plugin is timing.
    """
    inner.makepyfile(
        test_gc=f"""
        import gc
        import time

        def test_collects():
            def hold(phase, info):
                if phase == "start" and info["generation"] == 2:
                    time.sleep({SLOW_COLLECTION_S})

            gc.callbacks.append(hold)
            try:
                gc.collect()  # a generation-2 collection
            finally:
                gc.callbacks.remove(hold)

        def test_quiet():
            pass
        """
    )
    out = _run(inner).stdout.str()
    line = next(line for line in out.splitlines() if line.startswith("this process:"))
    gc_text = line.split("; stall:")[0]
    assert "gen-2 GC pause: worst" in gc_text
    assert "in test_gc.py::test_collects;" in gc_text, gc_text
    assert "gen-2 GC pause: none" not in line


@pytest.mark.skipif(
    os.name != "posix" or not hasattr(ctypes, "PyDLL"),
    reason="the GIL-holding stall is built from libc usleep through ctypes.PyDLL",
)
def test_a_stall_is_reported_and_attributed_to_its_test(inner: pytest.Pytester):
    """The ticker records a stall, and records it against the test that was running.

    The first version held the GIL with ``list.sort`` and asserted that the final report line
    named the test. That depended on three things nothing in the test controlled (main, run
    41774238410, py3.14 shard 1/4: ``stall: worst 0.32 s in (between tests); 1 ticks over 0.1 s
    late``):

    * how long the sort took (the comment said "~0.5 s"; measured here it holds the GIL for about
      0.1 s, a tick ~0.05 s late, under ``STALL_COUNTED_S``, so on a fast runner the sort alone
      is not even "counted": the run above counted exactly one tick, the 0.32 s one);
    * that no other stall in the process was longer (the report names the WORST stall, so a
      0.32 s pause anywhere else, which a shared runner produces between tests, won);
    * that the ticker ran before pytest moved on to ``logfinish``, after which a late tick is
      correctly attributed to "between tests".

    Now the test owns all three. The hold is exactly 0.3 s (``usleep`` called through
    ``ctypes.PyDLL``, which keeps the GIL, unlike a ``CDLL`` call or ``time.sleep``); the stall
    statistics are reset inside the test so nothing earlier can shadow it; and the test does not
    return until the ticker has recorded the stall, so ``Recorder.current`` is still this test
    when it does. The attribution is asserted there, in the test, before anything later can
    change the worst stall. What it asserts is a logical fact (the tick the ticker recorded was
    noted against the running test), never a duration.
    """
    inner.makepyfile(
        test_stall="""
        import ctypes
        import time

        from tests.timing_report import STALL_COUNTED_S, Worst

        def test_holds_the_gil(request):
            recorder = request.config.pluginmanager.get_plugin("djust_timing_report").recorder
            recorder.stall = Worst(floor=STALL_COUNTED_S)  # nothing earlier may shadow this one
            # A PyDLL call does not release the GIL: nothing else in the process, the ticker
            # thread included, runs until it returns.
            ctypes.PyDLL(None).usleep(300_000)
            # time.sleep releases the GIL, so the ticker can now report the late tick it owes.
            deadline = time.monotonic() + 10
            while not recorder.stall.counted and time.monotonic() < deadline:
                time.sleep(0.005)
            assert recorder.stall.counted, "the ticker never recorded the stall"
            assert recorder.stall.when == request.node.nodeid, recorder.stall.when
        """
    )
    result = _run(inner)
    assert result.ret == pytest.ExitCode.OK, result.stdout.str()
    out = result.stdout.str()
    line = next(line for line in out.splitlines() if line.startswith("this process:"))
    # The report is printed from the same recorder: a stall is in it, not "none".
    assert "stall: worst" in line and "stall: none" not in line


def test_under_xdist_the_controller_prints_one_line_per_worker(inner: pytest.Pytester):
    pytest.importorskip("xdist")
    inner.makepyfile(
        "def test_a(): pass\ndef test_b(): pass\ndef test_c(): pass\ndef test_d(): pass\n"
    )
    result = _run(inner, "-n", "2", subprocess_=True)
    assert result.ret == pytest.ExitCode.OK
    lines = [line for line in result.stdout.str().splitlines() if line.startswith("gw")]
    workers = sorted(line.split(":")[0] for line in lines)
    assert workers == ["gw0", "gw1"], result.stdout.str()
    assert all("gen-2 GC pause:" in line and "; stall:" in line for line in lines)
    assert "this process:" not in result.stdout.str()


def test_a_worker_that_reported_nothing_is_named(inner: pytest.Pytester):
    from tests.timing_report import TimingReport

    plugin = TimingReport.__new__(TimingReport)
    plugin.workers = {"gw0": None, "gw1": Recorder(clock=FakeClock()).snapshot()}
    lines = plugin.lines()
    assert any(line.startswith("gw1: ") for line in lines)
    assert "gw0: reported no timing data (died?)" in lines


def test_the_report_changes_no_outcome(inner: pytest.Pytester):
    inner.makepyfile("def test_ok(): pass\ndef test_bad(): assert False\n")
    with_plugin = _run(inner)
    without = inner.runpytest("-p", "no:cacheprovider", "-p", "no:django")
    assert with_plugin.ret == without.ret == pytest.ExitCode.TESTS_FAILED
    assert with_plugin.parseoutcomes() == without.parseoutcomes() == {"passed": 1, "failed": 1}


def test_collect_only_prints_no_report(inner: pytest.Pytester):
    inner.makepyfile("def test_a(): pass\n")
    assert SECTION not in _run(inner, "--collect-only").stdout.str()


def test_the_plugin_removes_what_it_installed(inner: pytest.Pytester):
    """No callback left in ``gc.callbacks`` and no ticker thread left alive after a run."""
    before_callbacks = list(gc.callbacks)
    before_threads = {t.ident for t in threading.enumerate()}
    inner.makepyfile("def test_a(): pass\n")
    _run(inner)
    assert gc.callbacks == before_callbacks
    leaked = [
        t
        for t in threading.enumerate()
        if t.ident not in before_threads and t.name == "djust-timing-ticker"
    ]
    assert leaked == []


def test_the_root_conftest_registers_the_plugin():
    text = (ROOT / "conftest.py").read_text()
    assert f'"{PLUGIN}"' in text
    assert os.path.isfile(ROOT / "tests" / "timing_report.py")
