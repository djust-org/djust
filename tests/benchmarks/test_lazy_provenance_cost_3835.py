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

    plan a fresh recursive tree (fan 2, fan 3)   0.06 ms, 0.05 ms
    15 plain renders (fan 2, fan 3)              0.30 ms, 0.37 ms
    15 tracked renders (fan 2, fan 3)            0.33 ms, 0.40 ms   (1.1x plain)

The targets are ~100x the plan and ~60x the tracked render: far above any environment's noise, far
below the blow-up they exist to catch (a plan that re-walks the recursion for every branch is seconds
at fan 2 and does not return at fan 3).
"""

from __future__ import annotations

import pytest

from tests.benchmarks.conftest import _assert_benchmark_under

TAG = '<div dj-view="x.Child" dj-lazy></div>'
TARGET_PLAN_S = 0.050  # 50 ms to plan a fresh recursive tree
TARGET_15_TRACKED_RENDERS_S = 0.020  # 20 ms for 15 renders with provenance tracking


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
