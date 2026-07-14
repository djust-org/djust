"""#2067 — cross-loop fragment corruption, pinned on the REAL PyO3 path.

Two sibling ``{% for %}`` loops that reuse the same loop-variable name over
equal-content items collided in the shared fragment cache (``content_hash``
covered only ``(var_name, item_value)``), so the second loop silently rendered
the FIRST loop's fragments. The Rust-level reproducer lives in
``crates/djust_templates/tests/test_loop_cache_cross_loop_2067.rs``; this file
drives the same shape through ``RustLiveView.render_with_diff`` — the wired
production path (#1650 reproduction-fidelity) — with the cache flag ON.
"""

from djust._rust import RustLiveView

SIBLING_SRC = (
    "<div>"
    "<ul id='a'>{% for x in xs %}<li>A[{{ x.name }}]</li>{% endfor %}</ul>"
    "<ul id='b'>{% for x in xs %}<li>B[{{ x.name }}]</li>{% endfor %}</ul>"
    "</div>"
)

ITEMS = [{"id": "1", "name": "one"}, {"id": "2", "name": "two"}]
REORDERED = [{"id": "2", "name": "two"}, {"id": "1", "name": "one"}]


def _render(lv, state, first=False):
    if not first:
        lv.set_changed_keys(["xs"])
    lv.update_state({"xs": state})
    html, _patches, _ver = lv.render_with_diff()
    return html


class TestCrossLoopIsolation2067:
    def test_sibling_loops_do_not_cross_render_with_cache_on(self):
        lv = RustLiveView(SIBLING_SRC)
        lv.set_loop_render_cache_enabled(True)
        html = _render(lv, ITEMS, first=True)
        assert "B[one]" in html and "B[two]" in html, (
            f"#2067: second loop must render its OWN body: {html}"
        )
        assert html.count("A[") == 2, f"first loop's fragments leaked into the second loop: {html}"

    def test_cache_on_matches_cache_off_across_reorder(self):
        outputs = {}
        for enabled in (False, True):
            lv = RustLiveView(SIBLING_SRC)
            lv.set_loop_render_cache_enabled(enabled)
            first = _render(lv, ITEMS, first=True)
            second = _render(lv, REORDERED)
            outputs[enabled] = (first, second)
        assert outputs[False] == outputs[True], (
            "cache ON must be byte-identical to OFF for sibling same-var loops"
        )

    def test_reorder_still_hits_within_each_loop(self):
        """The per-loop reorder win survives the body-identity fold: 4 cached
        fragments exist (2 items x 2 loops); a pure reorder is 4 hits."""
        lv = RustLiveView(SIBLING_SRC)
        lv.set_loop_render_cache_enabled(True)
        _render(lv, ITEMS, first=True)
        assert lv.loop_render_cache_misses() == 4, "cold render: 4 misses"
        _render(lv, REORDERED)
        assert lv.loop_render_cache_hits() == 4, (
            "reorder must hit all 4 fragments (2 items x 2 loops)"
        )
        assert lv.loop_render_cache_misses() == 0
