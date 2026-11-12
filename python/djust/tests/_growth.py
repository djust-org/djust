"""Judge how a cost grows, never what it costs (#3711).

A test that asks "is this scan linear?" used to assert ``elapsed < 2``. That is
a wall-clock assertion about the runner, not about the code: main's CI failed
on a ``< 1.0`` bound over a corpus that costs 0.01-0.03 s, because the machine
stalled (#3710; a ~100 s gap was measured inside one unrelated test, #3715).
CLAUDE.md: a concurrency test asserts a logical ordering invariant, never a
duration, and a latency/complexity gate never asserts an outlier-sensitive
statistic when a robust one measures the same property.

The robust property of "linear" is the GROWTH: four times the input must cost
under ten times as much (linear is ~4x, quadratic ~16x). Each size is measured
several times, interleaved, and the cheapest run counts, so a stall that lands
on one run inflates neither size's best. The default clock is the CPU time of
this thread: a descheduled process is not charged for the time it sat idle.
Pass ``clock=time.perf_counter`` for work that leaves the thread (a Rust worker,
a subprocess).

COUNT FIRST (#3835). A growth ratio is still a ratio of two measured durations.
Where there is an operation to count, count it instead and the test is exact and
independent of the machine: the strings a function is handed (a "cut to 512
characters first" bound: ``test_ai_catalog_3448``, ``test_mcp_ui_components_3448``),
the records and heartbeats a listing touches (``test_realtime_multiuser``,
``test_presence_list_scales_linearly``), the characters a scanner's ``find`` and
``search`` calls examine (``test_template_checks_skip_comments_3283``,
``test_accessibility_checks::test_unclosed_comment_openers_are_linear``; the
:class:`Examined` / :class:`CountingPattern` / :func:`counting_str` spies below do the
counting, and a counting test keeps a sibling that runs the same measurement on a
rescanning pass and asserts it reads ~16x, so the assertion can fail), the members
a weigher pulls (``test_form_data_accounting_3841``). A latency claim about code
with no hook goes to ``tests/benchmarks/`` as a median against a target
(``test_lazy_provenance_cost_3835``).

This helper is for what is left: work whose steps happen inside an engine that
offers nothing to count from Python, namely the CPython ``re`` matcher and
``html.parser`` (a quadratic rescan is one call that runs long, not more calls) and
the Rust engine (markdown, template variable extraction). Use it
there.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Awaitable, Callable


# -- counting how much of the input a scanner looked at (#3835) ---------------------------
#
# A scanner that is a Python loop of ``str.find`` / ``pattern.search`` calls goes
# quadratic by rescanning to the end of the text once per opener. Each such call
# examines the characters between where it started and where it stopped, so the sum over
# every call is the work, and it is a number: 4.0x for 4x the input when linear, 16x when
# a rescan per opener. Nothing here reads a clock.


class Examined:
    """A running total of characters examined by the searches a scan made."""

    total = 0


class CountingPattern:
    """Stands in for a compiled pattern: ``search`` adds the characters between where it
    started and where it stopped (a hit's start, or the end of the text for a miss)."""

    def __init__(self, pattern, examined: Examined):
        self._pattern = pattern
        self._examined = examined

    def search(self, string, pos=0, endpos=None):
        if endpos is None:
            match = self._pattern.search(string, pos)
        else:
            match = self._pattern.search(string, pos, endpos)
        stop = match.start() if match else (len(string) if endpos is None else endpos)
        self._examined.total += max(stop - pos, 0) + 1
        return match


def counting_str(examined: Examined) -> type:
    """A ``str`` subclass whose ``find`` adds the characters it examined to ``examined``.

    Only ``find`` is counted: slices and ``in`` return plain ``str`` / ``bool`` and are not
    searches the scanner repeats per opener."""

    class Spy(str):
        def find(self, sub, start=0, end=None):
            found = str.find(self, sub, start, end)
            stop = found if found != -1 else (len(self) if end is None else min(end, len(self)))
            examined.total += max(stop - start, 0) + 1
            return found

    return Spy


#: Input is scaled by this much between the two measured sizes.
FACTOR = 4
#: ``FACTOR`` times the input may cost this many times as much. Linear is ~4x
#: and quadratic ~16x; the limit sits between them with room for cache effects.
LIMIT = 10.0
#: Below this many seconds at the large size the ratio measures clock
#: granularity and scheduler noise, not the work, and there is nothing to scale.
FLOOR = 0.0002


@dataclass(frozen=True)
class Growth:
    small: float
    big: float

    @property
    def ratio(self) -> float:
        return self.big / max(self.small, 1e-9)

    def __str__(self) -> str:
        return f"{self.ratio:.1f}x ({self.small:.5f}s -> {self.big:.5f}s)"


def best_of(
    work: Callable[[], object],
    *,
    runs: int = 3,
    clock: Callable[[], float] = time.thread_time,
) -> float:
    """The cheapest of ``runs`` runs of ``work``: a stall inflates one run, never all."""
    best = float("inf")
    for _ in range(runs):
        start = clock()
        work()
        best = min(best, clock() - start)
    return best


def measure(
    run_at: Callable[[int], object],
    *,
    factor: int = FACTOR,
    runs: int = 3,
    clock: Callable[[], float] = time.thread_time,
) -> Growth:
    """The cheapest of ``runs`` runs at scale 1 and at scale ``factor``.

    The sizes are interleaved (1, factor, 1, factor, ...), so a slow stretch of
    the machine touches both rather than only one.
    """
    small = big = float("inf")
    for _ in range(runs):
        for scale in (1, factor):
            start = clock()
            run_at(scale)
            took = clock() - start
            if scale == 1:
                small = min(small, took)
            else:
                big = min(big, took)
    return Growth(small, big)


async def ameasure(
    run_at: Callable[[int], Awaitable[object]],
    *,
    factor: int = FACTOR,
    runs: int = 3,
    clock: Callable[[], float] = time.perf_counter,
) -> Growth:
    """:func:`measure` for awaitable work. Wall clock by default: awaiting leaves the
    thread, so the thread's CPU time is not the cost."""
    small = big = float("inf")
    for _ in range(runs):
        for scale in (1, factor):
            start = clock()
            await run_at(scale)
            took = clock() - start
            if scale == 1:
                small = min(small, took)
            else:
                big = min(big, took)
    return Growth(small, big)


async def assert_scales_linearly_async(
    run_at: Callable[[int], Awaitable[object]],
    *,
    factor: int = FACTOR,
    limit: float = LIMIT,
    floor: float = FLOOR,
    runs: int = 3,
    clock: Callable[[], float] = time.perf_counter,
    what: str = "",
) -> Growth:
    """:func:`assert_scales_linearly` for awaitable work."""
    growth = await ameasure(run_at, factor=factor, runs=runs, clock=clock)
    assert growth.big < floor or growth.ratio < limit, (
        f"{what or 'the work'}: {factor}x the input cost {growth} - expected about "
        f"{factor}x, under {limit:g}x"
    )
    return growth


def assert_scales_linearly(
    run_at: Callable[[int], object],
    *,
    factor: int = FACTOR,
    limit: float = LIMIT,
    floor: float = FLOOR,
    runs: int = 3,
    clock: Callable[[], float] = time.thread_time,
    what: str = "",
) -> Growth:
    """Fail when ``factor`` times the work costs more than ``limit`` times as much.

    ``run_at(scale)`` does the work on an input ``scale`` times the base size;
    it is called with ``1`` and with ``factor``.
    """
    growth = measure(run_at, factor=factor, runs=runs, clock=clock)
    assert growth.big < floor or growth.ratio < limit, (
        f"{what or 'the work'}: {factor}x the input cost {growth} - expected about "
        f"{factor}x, under {limit:g}x"
    )
    return growth
