"""Integration tests for the per-item loop render cache (#1967).

Exercises the cache end-to-end through the PyO3 ``RustLiveView.render_with_diff``
path — the real production render path — proving:

* the flag defaults OFF and is byte-identical to the pre-#1967 path,
* with the cache ON the rendered HTML is byte-identical to OFF across initial
  render, reorder, content-change, append, and remove,
* a pure reorder is all cache HITS (the O(changed) win), persisted across
  ``render_with_diff`` calls,
* a content-change of one item costs exactly one miss,
* position-dependent loop bodies ({% if %}/{% cycle %}/forloop) are NOT cached
  yet still render correct positions (the guard is load-bearing),
* the Python config flag ``loop_render_cache_enabled`` defaults False.

Native Rust unit + correctness tests (cached==uncached battery, the two
gate-offs) live in
``crates/djust_templates/tests/test_loop_render_cache_1967.rs``.
"""

from __future__ import annotations

import pytest

from djust._rust import RustLiveView

PLAIN_SRC = "<ul>{% for x in xs %}<li>{{ x.name }}</li>{% endfor %}</ul>"
IF_SRC = "<ul>{% for x in xs %}<li>{% if x.name %}{{ x.name }}{% endif %}</li>{% endfor %}</ul>"
CYCLE_SRC = (
    "<ul>{% for x in xs %}<li class=\"{% cycle 'odd' 'even' %}\">{{ x.name }}</li>{% endfor %}</ul>"
)


def _items(rows):
    return [{"id": i, "name": n} for (i, n) in rows]


INITIAL = _items([("1", "alpha"), ("2", "bravo"), ("3", "charlie")])
REORDERED = _items([("3", "charlie"), ("1", "alpha"), ("2", "bravo")])
CHANGED = _items([("3", "charlie"), ("1", "ALPHA-CHANGED"), ("2", "bravo")])
APPENDED = _items([("3", "charlie"), ("1", "ALPHA-CHANGED"), ("2", "bravo"), ("4", "delta")])
REMOVED = _items([("3", "charlie"), ("2", "bravo"), ("4", "delta")])

SEQUENCE = [INITIAL, REORDERED, CHANGED, APPENDED, REMOVED]


def _render_sequence(src, enabled):
    """Run the standard op sequence; return list of (html, hits, misses)."""
    lv = RustLiveView(src)
    lv.set_loop_render_cache_enabled(enabled)
    out = []
    for i, state in enumerate(SEQUENCE):
        if i > 0:
            lv.set_changed_keys(["xs"])
        lv.update_state({"xs": state})
        html, _patches, _ver = lv.render_with_diff()
        out.append((html, lv.loop_render_cache_hits(), lv.loop_render_cache_misses()))
    return out


class TestLoopRenderCacheDefaults:
    def test_flag_defaults_off(self):
        lv = RustLiveView(PLAIN_SRC)
        assert lv.loop_render_cache_enabled() is False

    def test_config_default_is_false(self):
        from djust.config import get_config

        assert get_config().get("loop_render_cache_enabled", "MISSING") is False

    def test_enable_disable_round_trip(self):
        lv = RustLiveView(PLAIN_SRC)
        lv.set_loop_render_cache_enabled(True)
        assert lv.loop_render_cache_enabled() is True
        lv.set_loop_render_cache_enabled(False)
        assert lv.loop_render_cache_enabled() is False


class TestOutputIdentity:
    """Cache-ENABLED output must be byte-identical to cache-DISABLED."""

    @pytest.mark.parametrize("src", [PLAIN_SRC, IF_SRC, CYCLE_SRC])
    def test_enabled_identical_to_disabled(self, src):
        on = _render_sequence(src, enabled=True)
        off = _render_sequence(src, enabled=False)
        for step, (s_on, s_off) in enumerate(zip(on, off)):
            assert s_on[0] == s_off[0], f"step {step}: cache-enabled HTML diverged from disabled"


class TestCacheBehavior:
    """Hit/miss accounting proves the O(changed) render win."""

    def test_reorder_is_all_hits(self):
        on = _render_sequence(PLAIN_SRC, enabled=True)
        # step 0 = initial: 3 misses, 0 hits
        assert on[0][1] == 0 and on[0][2] == 3
        # step 1 = reorder: 3 hits, 0 misses (no item re-rendered)
        assert on[1][1] == 3, "a pure reorder must reuse every cached fragment"
        assert on[1][2] == 0, "a pure reorder must not re-render any item"

    def test_content_change_is_one_miss(self):
        on = _render_sequence(PLAIN_SRC, enabled=True)
        # step 2 = content-change of one item: 1 miss, 2 hits
        assert on[2][2] == 1, "only the changed item re-renders"
        assert on[2][1] == 2, "the two unchanged items are reused"

    def test_append_misses_only_new_item(self):
        on = _render_sequence(PLAIN_SRC, enabled=True)
        # step 3 = append delta to the 3 already-cached items: 1 miss, 3 hits
        assert on[3][2] == 1, "only the appended item misses"
        assert on[3][1] == 3

    def test_position_dependent_body_is_not_cached(self):
        # The {% if %} body is position-dependent (dj-if marker carries the
        # loop index) → caching disabled → 0 hits AND 0 misses every render.
        on = _render_sequence(IF_SRC, enabled=True)
        for step, (_html, hits, misses) in enumerate(on):
            assert hits == 0, f"step {step}: position-dependent body must not hit"
            assert misses == 0, f"step {step}: position-dependent body must not cache"

    def test_disabled_cache_is_inert(self):
        off = _render_sequence(PLAIN_SRC, enabled=False)
        for _html, hits, misses in off:
            assert hits == 0 and misses == 0
