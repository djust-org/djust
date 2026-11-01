"""Each pytest process reports its worst generation-2 GC pause and longest stall (#3715).

The failure shape this attributes: a timing assertion fails on CI (#3692, #3710) and
nothing in the log says whether the process was paused. A stop-the-world collection
or a descheduled runner looks, from inside one test, exactly like slow code. #3708
attributed four flakes to GC pauses on a measured basis (up to 3.38 s on 3.12,
locally) but could not witness the cause in CI, because CI logs carried no pause data
and nothing was measured on 3.13 / 3.14 or on a runner.

Two measurements per process, both cheap and neither able to change a test:

* **Worst gen-2 pause**, from :data:`gc.callbacks`. The callback does nothing for
  generations 0 and 1 (the frequent ones) beyond one comparison.
* **Longest stall**, from a daemon "ticker" thread that wakes every
  :data:`TICK_S` and records how late it ran. A thread needs the GIL to run, so a
  stop-the-world collection, a native call that holds the GIL, a VM pause and CPU
  starvation all show up as one late tick. That is the interval an event loop in the
  same process cannot have run in either, which is what a communicator wait feels.
  It is measured as lateness (``gap - TICK_S``), so an idle process reports ~0.

Each measurement carries the test that was running when it happened.

Reporting: serial, the one process prints its own line in the terminal summary.
Under xdist every worker ships its numbers to the controller through
``config.workeroutput`` (the channel ``tests/lost_items_guard.py`` uses) and the
controller prints one line per worker, worst first. Workers' own stdout is not in the
CI log, which is why a plain ``print`` at session end would have reported nothing.

Registered in the root ``conftest.py``; ``tests/test_timing_report_3715.py`` pins it.
"""

from __future__ import annotations

import gc
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

import pytest

SECTION = "djust timing report (#3715)"
WORKEROUTPUT_KEY = "djust_timing_report"
#: How often the ticker wakes. A stall shorter than this is not seen; a longer one is
#: seen as (stall - TICK_S) late. 20 Hz costs nothing measurable.
TICK_S = 0.05
#: A pause or stall over this many seconds is counted in the "over 1 s" tally: the
#: shortest default wait a Channels communicator ever had (``connect()``, #3692).
NOTABLE_S = 1.0


@dataclass
class Worst:
    seconds: float = 0.0
    when: str = ""
    over_notable: int = 0
    count: int = 0

    def note(self, seconds: float, when: str) -> None:
        self.count += 1
        if seconds >= NOTABLE_S:
            self.over_notable += 1
        if seconds > self.seconds:
            self.seconds = seconds
            self.when = when

    def as_dict(self) -> Dict[str, object]:
        return {
            "seconds": self.seconds,
            "when": self.when,
            "over_notable": self.over_notable,
            "count": self.count,
        }


@dataclass
class Recorder:
    """The measurements of one process. Pure bookkeeping: clock and thread are injected."""

    clock: Callable[[], float] = time.perf_counter
    current: str = "(collection, or between tests)"
    gc_pause: Worst = field(default_factory=Worst)
    stall: Worst = field(default_factory=Worst)
    _gc_start: Optional[float] = None

    # -- gc.callbacks ------------------------------------------------------------

    def on_gc(self, phase: str, info: Dict[str, int]) -> None:
        if info.get("generation") != 2:
            return
        if phase == "start":
            self._gc_start = self.clock()
        elif self._gc_start is not None:
            self.gc_pause.note(self.clock() - self._gc_start, self.current)
            self._gc_start = None

    # -- ticker -------------------------------------------------------------------

    def on_tick(self, gap: float) -> None:
        """``gap`` is the time since the previous tick; the lateness is what is over TICK_S."""
        late = gap - TICK_S
        if late > 0:
            self.stall.note(late, self.current)

    def snapshot(self) -> Dict[str, Dict[str, object]]:
        return {"gc": self.gc_pause.as_dict(), "stall": self.stall.as_dict()}


class Ticker(threading.Thread):
    """Wakes every ``TICK_S`` and reports how long it had to wait for the GIL."""

    def __init__(self, recorder: Recorder) -> None:
        super().__init__(name="djust-timing-ticker", daemon=True)
        self._recorder = recorder
        self._halt = threading.Event()  # not `_stop`: Thread owns that name

    def run(self) -> None:
        clock = self._recorder.clock
        last = clock()
        while not self._halt.wait(TICK_S):
            now = clock()
            self._recorder.on_tick(now - last)
            last = now

    def stop(self) -> None:
        self._halt.set()
        self.join(TICK_S * 20)


def format_line(who: str, data: Dict[str, Dict[str, object]]) -> str:
    gc_part = data["gc"]
    stall = data["stall"]

    def part(label: str, worst: Dict[str, object]) -> str:
        if not worst["count"]:
            return f"{label}: none"
        text = f"{label}: worst {worst['seconds']:.2f} s ({worst['over_notable']} over 1 s of {worst['count']})"
        return f"{text} in {worst['when']}" if worst["seconds"] else text

    return f"{who}: {part('gen-2 GC pause', gc_part)}; {part('stall', stall)}"


class TimingReport:
    def __init__(self, config: pytest.Config) -> None:
        self.config = config
        self.is_worker = hasattr(config, "workerinput")  # stamped on xdist workers only
        self.recorder = Recorder()
        self.ticker: Optional[Ticker] = None
        #: Controller only: worker id -> its snapshot (or None when it sent none).
        self.workers: Dict[str, Optional[Dict[str, Dict[str, object]]]] = {}
        self._installed = False

    def start(self) -> None:
        gc.callbacks.append(self.recorder.on_gc)
        self.ticker = Ticker(self.recorder)
        self.ticker.start()
        self._installed = True

    def stop(self) -> None:
        if not self._installed:
            return
        self._installed = False
        try:
            gc.callbacks.remove(self.recorder.on_gc)
        except ValueError:  # already removed
            pass
        if self.ticker is not None:
            self.ticker.stop()

    # -- hooks -------------------------------------------------------------------

    def pytest_runtest_logstart(self, nodeid: str, location: object) -> None:
        self.recorder.current = nodeid

    def pytest_sessionfinish(self, session: pytest.Session) -> None:
        self.stop()
        if self.is_worker:
            self.config.workeroutput[WORKEROUTPUT_KEY] = self.recorder.snapshot()  # type: ignore[attr-defined]

    @pytest.hookimpl(optionalhook=True)
    def pytest_testnodedown(self, node: object, error: object) -> None:
        output = getattr(node, "workeroutput", None) or {}
        worker_id = getattr(getattr(node, "gateway", None), "id", repr(node))
        self.workers[worker_id] = output.get(WORKEROUTPUT_KEY)

    def lines(self) -> List[str]:
        if self.workers:
            reported = {w: d for w, d in self.workers.items() if d}
            lost = sorted(w for w, d in self.workers.items() if not d)
            ordered = sorted(
                reported.items(),
                key=lambda item: -max(item[1]["gc"]["seconds"], item[1]["stall"]["seconds"]),  # type: ignore[type-var]
            )
            out = [format_line(worker, data) for worker, data in ordered]
            out.extend(f"{worker}: reported no timing data (died?)" for worker in lost)
            return out
        return [format_line("this process", self.recorder.snapshot())]

    def pytest_terminal_summary(self, terminalreporter, exitstatus: int) -> None:
        if self.is_worker or self.config.option.collectonly:
            return
        terminalreporter.write_sep("-", SECTION)
        for line in self.lines():
            terminalreporter.write_line(line)


def pytest_configure(config: pytest.Config) -> None:
    plugin = TimingReport(config)
    plugin.start()
    config.pluginmanager.register(plugin, "djust_timing_report")


def pytest_unconfigure(config: pytest.Config) -> None:
    plugin = config.pluginmanager.get_plugin("djust_timing_report")
    if plugin is not None:
        plugin.stop()
