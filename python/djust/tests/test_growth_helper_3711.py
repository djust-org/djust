"""``assert_scales_linearly`` tells linear from quadratic and shrugs off one stall (#3711).

The helper replaces absolute wall-clock bounds across the suite, so it is the one
place a wrong implementation must be proven to go red: a helper that passes
everything would turn every converted test into a tautology.
"""

import time

import pytest

from djust.tests._growth import FLOOR, Growth, assert_scales_linearly, measure

#: Work units per scale step; sized so the base run costs milliseconds.
UNIT = 600


def linear(scale):
    sum(range(UNIT * 300 * scale))


def quadratic(scale):
    n = UNIT * scale
    for i in range(n):
        sum(range(i))


def _stalls_on_the_first_run_of_each_size(work, stall, sizes=None):
    """``work`` with a ``stall`` the first time each size in ``sizes`` is run.

    One stalled run per size is what a runner pause does; ``sizes`` narrows it to the
    one that matters most (a stall on the small size only shrinks the ratio).
    """
    seen = set()

    def run(scale):
        if scale not in seen and (sizes is None or scale in sizes):
            seen.add(scale)
            stall()
        work(scale)

    return run


def _sleep_half_a_second():
    time.sleep(0.5)


def _spin_half_a_second():
    end = time.thread_time() + 0.5
    while time.thread_time() < end:
        pass


def test_linear_work_passes():
    assert_scales_linearly(linear, what="linear work")


def test_quadratic_work_fails_and_names_the_growth():
    with pytest.raises(AssertionError, match="4x the input cost"):
        assert_scales_linearly(quadratic, what="quadratic work")


def test_a_sleeping_stall_on_one_run_of_each_size_is_dropped_by_best_of_three():
    # Wall clock, so the sleep is charged to the run it lands on.
    run = _stalls_on_the_first_run_of_each_size(linear, _sleep_half_a_second)
    assert_scales_linearly(run, clock=time.perf_counter, what="linear work with a stall")


def test_a_spinning_stall_on_one_run_of_each_size_is_dropped_by_best_of_three():
    # CPU clock: a busy stall IS charged to its run (a GC pause is the same shape).
    run = _stalls_on_the_first_run_of_each_size(linear, _spin_half_a_second)
    assert_scales_linearly(run, what="linear work with a spinning stall")


def test_the_same_stall_fails_a_single_run_so_best_of_three_is_what_absorbs_it():
    run = _stalls_on_the_first_run_of_each_size(linear, _spin_half_a_second, sizes={4})
    # One run per size: the stall lands on the large run and the ratio explodes.
    with pytest.raises(AssertionError, match="4x the input cost"):
        assert_scales_linearly(run, runs=1, what="linear work, one run")


def test_quadratic_work_still_fails_through_a_stall():
    run = _stalls_on_the_first_run_of_each_size(quadratic, _sleep_half_a_second)
    with pytest.raises(AssertionError):
        assert_scales_linearly(run, clock=time.perf_counter, what="quadratic with a stall")


def test_work_too_cheap_to_measure_is_not_judged_by_a_noisy_ratio():
    growth = measure(lambda scale: None)
    assert growth.big < FLOOR
    assert_scales_linearly(lambda scale: None)


def test_the_growth_reports_the_ratio_it_judged():
    g = Growth(small=0.002, big=0.01)
    assert g.ratio == pytest.approx(5.0)
    assert "5.0x" in str(g)
