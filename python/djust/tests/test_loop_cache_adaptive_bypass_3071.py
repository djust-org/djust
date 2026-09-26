"""#3071: a view whose loop items change on every render stops paying for the
loop render cache.

The cache (default on) hashes and tracks every loop item. When the items change
on almost every render it rarely hits, and each render gets 20-30% slower.
After ``_LOOP_CACHE_BYPASS_AFTER`` consecutive renders whose hit rate is below
``_LOOP_CACHE_MIN_HIT_RATIO``, the view turns the cache off for its lifetime.

These tests assert on the cache's own hit/miss counters and its enabled flag,
never on wall-clock time.
"""

from __future__ import annotations

import itertools

import pytest

from djust import LiveView

_seq = itertools.count()


class _LoopView(LiveView):
    template = (
        '<div dj-root dj-view="djust.tests.test_loop_cache_adaptive_bypass_3071._LoopView"'
        ' dj-id="0"><ul>{% for x in xs %}<li>{{ x.name }}</li>{% endfor %}</ul></div>'
    )

    def mount(self, request, **kwargs):
        self.xs = self.fresh()

    @staticmethod
    def fresh():
        return [{"id": str(next(_seq)), "name": "n%d" % next(_seq)} for _ in range(20)]


def _mounted():
    view = _LoopView()
    view.mount(None)
    view.render_with_diff()
    return view


def _cache_on(view):
    return view._rust_view.loop_render_cache_enabled()


@pytest.mark.django_db
class TestLoopCacheAdaptiveBypass:
    def test_a_loop_that_changes_every_render_turns_the_cache_off(self):
        view = _mounted()
        assert _cache_on(view), "the cache starts on (the #2062 default)"
        renders = 0
        while _cache_on(view) and renders < 50:
            view.xs = _LoopView.fresh()
            view.render_with_diff()
            renders += 1
            assert view._rust_view.loop_render_cache_hits() == 0
        assert not _cache_on(view), "an all-miss loop must stop being cached"
        # One render (the mount) plus the ones above, and never before the limit.
        assert renders + 1 == _LoopView._LOOP_CACHE_BYPASS_AFTER

        # Off for good: later renders do no cache work at all.
        view.xs = _LoopView.fresh()
        view.render_with_diff()
        assert view._rust_view.loop_render_cache_misses() == 0
        # A re-wire (cache-hit restore, reconnect-time init) keeps it off.
        view._apply_loop_render_cache_flag()
        assert not _cache_on(view)

    def test_a_loop_that_mostly_hits_keeps_the_cache(self):
        """Gate-off sibling: a reorder-heavy loop is what the cache is for."""
        view = _mounted()
        for _ in range(3 * _LoopView._LOOP_CACHE_BYPASS_AFTER):
            view.xs = list(reversed(view.xs))
            view.render_with_diff()
            assert view._rust_view.loop_render_cache_hits() == len(view.xs)
        assert _cache_on(view)

    def test_one_miss_streak_broken_by_a_hit_starts_over(self):
        view = _mounted()
        limit = _LoopView._LOOP_CACHE_BYPASS_AFTER
        for _ in range(4):
            for _ in range(limit - 2):
                view.xs = _LoopView.fresh()
                view.render_with_diff()
            view.xs = list(reversed(view.xs))  # a render that hits
            view.render_with_diff()
            assert view._rust_view.loop_render_cache_hits() > 0
        assert _cache_on(view), "only CONSECUTIVE low-hit renders count"

    def test_a_view_without_cacheable_loops_is_left_alone(self):
        class NoLoop(LiveView):
            template = (
                '<div dj-root dj-view="djust.tests.test_loop_cache_adaptive_bypass_3071.NoLoop"'
                ' dj-id="0">{{ n }}</div>'
            )

            def mount(self, request, **kwargs):
                self.n = 0

        view = NoLoop()
        view.mount(None)
        for i in range(3 * NoLoop._LOOP_CACHE_BYPASS_AFTER):
            view.n = i
            view.render_with_diff()
        assert _cache_on(view)

    def test_the_bypass_can_be_disabled_per_view(self):
        class Always(_LoopView):
            _LOOP_CACHE_BYPASS_AFTER = 0

        view = Always()
        view.mount(None)
        for _ in range(20):
            view.xs = _LoopView.fresh()
            view.render_with_diff()
        assert _cache_on(view)
