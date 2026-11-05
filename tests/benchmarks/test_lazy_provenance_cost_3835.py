"""The cost of lazy-provenance planning and tracked rendering on a recursive tree (#3835).

``python/djust/tests/test_lazy_provenance_round2_3427.py`` used to assert, in the PR tier, two RATIOS
of measured durations: a tracked render under 20x a plain one, and a first plan under 1000x a
plain render. Both are properties of how long the Rust engine takes, and the Rust engine has no
hook to count what it does (no template-visit counter, no file-read counter reachable from Python),
so there is no operation to count instead. Per the repo's convention a latency bound belongs here,
in the serial ``benchmarks`` job, as a MEDIAN against a generous absolute target
(``_assert_benchmark_under``; skipped under xdist, where pytest-benchmark collects no stats).

What stays in the PR tier is what is exact: the tracked render produces the plain render's HTML with
no spans and no origins, and planning a recursive tree returns at all
(``test_recursive_tree_planning_and_render_are_bounded``).

Medians on one development machine, for the record, NOT for tuning (#2156: the same code spans ~11x
across environments)::

    plan a fresh recursive tree (fan 2, fan 3)   0.035 ms, 0.039 ms   (pytest-benchmark medians)
    15 plain renders (fan 2, fan 3)              0.30 ms, 0.37 ms
    15 tracked renders (fan 2, fan 3)            0.32 ms, 0.38 ms   (1.1x plain)

The targets are ~10x those medians (0.4 ms, 4 ms). They were ~5x (0.2 ms, 2 ms) when #3835 landed,
and the review of that change (#3844) pointed out that #2156 measured the same code spanning ~11x
across environments, so a 5x margin is inside the spread of the machines this runs on; it said to go
to 10x if the job ever flaked, and it is cheaper to do that before it does. What the bound is for is
unchanged: the regression it was written for (a re-walk of the recursion per branch) is seconds at fan 2
and does not return at fan 3, four to five orders of magnitude over either target. They are a LATENCY
bound, not a count: they catch a slowdown, not a change in how much work is done. Two limits to know:

* under pytest-xdist (``-n auto``, which every job but ``benchmarks`` uses) pytest-benchmark collects
  no stats, so ``_assert_benchmark_under`` returns without asserting; only the serial
  ``benchmarks`` job enforces these;
* a re-walk of the recursion for every branch (the blow-up the 1000x bound was written for) is
  seconds at fan 2 and does not return at fan 3, which the PR-tier test
  (``test_recursive_tree_planning_and_render_are_bounded``) would hang on rather than pass.
"""

from __future__ import annotations

import pytest

from tests.benchmarks.conftest import _assert_benchmark_under

TAG = '<div dj-view="x.Child" dj-lazy></div>'
TARGET_PLAN_S = 0.0004  # 0.4 ms to plan a fresh recursive tree (~10x the median)
TARGET_15_TRACKED_RENDERS_S = 0.004  # 4 ms for 15 renders with tracking (~10x the median)


def _source(fan: int) -> str:
    branches = ["l", "r", "m"][:fan]
    return (
        "{% if n %}<i>{{ n.v }}</i>"
        + "".join('{% include "tree.html" with n=n.' + branch + " %}" for branch in branches)
        + "{% endif %}"
    )


PAGE = '<div dj-root>{% include "tree.html" with n=tree %}</div>'
TREE = {"v": 1, "l": {"v": 2}, "r": None, "m": None}


@pytest.fixture(autouse=True)
def _needs_rust():
    pytest.importorskip("djust._rust")


@pytest.mark.parametrize("fan", [2, 3])
def test_planning_a_fresh_recursive_tree_is_not_exponential(benchmark, tmp_path, fan):
    from djust._rust import template_needs_provenance

    source = _source(fan)
    fresh = iter(range(10**6))

    def setup():
        # The plan is cached after its first call, so each round plans a copy it has not seen.
        directory = tmp_path / f"plan{next(fresh)}"
        directory.mkdir()
        (directory / "tree.html").write_text(source)
        return (str(directory),), {}

    result = benchmark.pedantic(
        lambda directory: template_needs_provenance(PAGE, [directory]),
        setup=setup,
        rounds=30,
        warmup_rounds=2,
        iterations=1,
    )
    assert result is False  # it returns, and the tree holds no lazy element
    _assert_benchmark_under(benchmark, TARGET_PLAN_S, f"plan a fan-{fan} recursive tree")


@pytest.mark.parametrize("fan", [2, 3])
def test_tracked_render_of_a_recursive_tree_stays_close_to_a_plain_one(benchmark, tmp_path, fan):
    from djust._rust import RustLiveView

    (tmp_path / "tree.html").write_text(_source(fan))
    rust = RustLiveView(PAGE, [str(tmp_path)])
    rust.update_state({"tree": TREE})
    plain = rust.render()

    def tracked_renders():
        for _ in range(15):
            html, spans, origins = rust.render_lazy_html()
            assert html == plain
            assert not spans and not origins

    benchmark.pedantic(tracked_renders, rounds=30, warmup_rounds=2, iterations=1)
    _assert_benchmark_under(
        benchmark, TARGET_15_TRACKED_RENDERS_S, f"15 tracked renders, fan {fan}"
    )
