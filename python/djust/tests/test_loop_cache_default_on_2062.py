"""#2062 — the loop render+parse cache is default-ON.

Graduated after soaking flag-OFF since v1.1.0rc5 (byte-identity ON==OFF proven
across the template matrix) and after the #2067 cross-loop keyspace fix landed.
The flag stays supported as an explicit opt-OUT.

The config-default pin itself lives in ``test_loop_render_cache_1967.py``
(``test_config_default_is_true``); this file pins the WIRE — the real
``RustBridgeMixin._apply_loop_render_cache_flag`` path that carries the config
default onto every ``RustLiveView`` — and the opt-out kill-switch, which
doubles as the gate-off sibling (#1468): flipping ``DEFAULT_CONFIG`` back to
False turns ``test_default_config_enables_cache_via_wire`` red.
"""

from unittest.mock import patch

from djust._rust import RustLiveView
from djust.mixins.rust_bridge import RustBridgeMixin

SRC = "<ul>{% for x in xs %}<li>{{ x.name }}</li>{% endfor %}</ul>"
ITEMS = [{"id": "1", "name": "one"}, {"id": "2", "name": "two"}]
REORDERED = [{"id": "2", "name": "two"}, {"id": "1", "name": "one"}]


class _Host:
    """Minimal host carrying a _rust_view, as the mixin method expects."""

    def __init__(self, src):
        self._rust_view = RustLiveView(src)


def _wire(host):
    RustBridgeMixin._apply_loop_render_cache_flag(host)
    return host._rust_view


def _render(lv, state, first=False):
    if not first:
        lv.set_changed_keys(["xs"])
    lv.update_state({"xs": state})
    lv.render_with_diff()


class TestDefaultOn2062:
    def test_default_config_enables_cache_via_wire(self):
        """The REAL wire (config → _apply_loop_render_cache_flag → Rust)
        enables the cache with NO explicit configuration."""
        lv = _wire(_Host(SRC))
        assert lv.loop_render_cache_enabled() is True

    def test_default_on_reorder_hits(self):
        """Behavioral pin: under the default config, a pure reorder hits the
        cache (the graduated win, exercised end-to-end)."""
        lv = _wire(_Host(SRC))
        _render(lv, ITEMS, first=True)
        assert lv.loop_render_cache_misses() == 2, "cold render: all misses"
        _render(lv, REORDERED)
        assert lv.loop_render_cache_hits() == 2, "reorder: all hits by default"
        assert lv.loop_render_cache_misses() == 0

    def test_explicit_false_is_the_opt_out(self):
        """The kill-switch: an explicit False disables the cache through the
        same wire (and proves the wire is load-bearing, not decorative)."""
        with patch(
            "djust.config.get_config",
            return_value={"loop_render_cache_enabled": False},
        ):
            lv = _wire(_Host(SRC))
        assert lv.loop_render_cache_enabled() is False
        _render(lv, ITEMS, first=True)
        _render(lv, REORDERED)
        assert lv.loop_render_cache_hits() == 0, "opted out: no cache activity"

    def test_bare_rust_view_still_defaults_off_pre_wire(self):
        """The Rust-side constructor default stays False — the config wire is
        the single source of the ON default (no parallel-default drift,
        #1646): a RustLiveView that never passes through the mixin keeps the
        conservative OFF."""
        lv = RustLiveView(SRC)
        assert lv.loop_render_cache_enabled() is False
