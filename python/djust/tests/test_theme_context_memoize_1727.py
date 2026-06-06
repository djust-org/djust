"""Request-scoped memoization of theme_context tag bodies (#1727).

#1722 made ``_apply_context_processors`` run ``theme_context`` on EVERY
WebSocket event (rust_bridge.py:548). ``theme_context`` re-renders four
uncached tag bodies per call via ``_safe_render`` (theme_head, theme_panel,
theme_mode_toggle, theme_preset_selector). ``_render_theme_outputs`` (the
CSS/switcher) is already ``@lru_cache``'d; these four were not — so theming
users paid four uncached tag renders on every WS event.

The fix memoizes the four ``_safe_render`` outputs **request-scoped**,
keyed on the resolved theme-state tuple (theme, preset, pack, mode,
resolved_mode, layout, presets_key — the same shape ``_render_theme_outputs``
keys on). When theme state is unchanged across WS events the four tag bodies
are NOT re-rendered; when theme state changes (a live theme/mode/preset
switch) they ARE recomputed, so dynamic switching still works.

Request-scoped (cache stored on the request object) was chosen over a
cross-request module-level cache because the issue title specifies it and it
is strictly safe: even though none of the four tag outputs currently embed
per-request data (no CSP nonce; ``cookie_prefix_js`` derives from the
``cookie_namespace`` *config*, not the request), a request-scoped cache
cannot leak a future per-request value across requests. On the WS path the
``request`` is a long-lived instance attr set once in ``handle_connect``, so
the cache naturally spans all WS events of a connection and is invalidated by
the state-key changing on a theme switch.
"""

from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _clear_cache_per_test():
    from djust.theming.context_processors import clear_theme_context_cache

    clear_theme_context_cache()
    yield
    clear_theme_context_cache()


def _make_manager(preset="default", mode="light", resolved_mode="light", pack=None, presets=None):
    """Stub theme manager (mirrors test_theming_context_cache._make_manager)."""
    from djust.theming.manager import ThemeState

    state = ThemeState(
        theme="default",
        preset=preset,
        mode=mode,
        resolved_mode=resolved_mode,
        pack=pack,
    )
    if presets is None:
        presets = [
            {"name": "default", "display_name": "Default", "is_active": preset == "default"},
            {"name": "ocean", "display_name": "Ocean", "is_active": preset == "ocean"},
        ]
    mgr = MagicMock()
    mgr.get_state.return_value = state
    mgr.get_available_presets.return_value = presets
    return mgr


def _tag_spies():
    """Return a dict of {tag_name: MagicMock} that count invocations and
    return a deterministic per-tag string. Patch them onto the theme_tags
    module so theme_context's local import picks them up."""
    return {
        "theme_head": MagicMock(return_value="HEAD_HTML"),
        "theme_panel": MagicMock(return_value="PANEL_HTML"),
        "theme_mode_toggle": MagicMock(return_value="TOGGLE_HTML"),
        "theme_preset_selector": MagicMock(return_value="SELECTOR_HTML"),
    }


def _enter_patches(stack, mgr, css_value, spies):
    """Enter the standard patch set (manager, css, four tag spies) on the
    given ExitStack so theme_context picks up the spies on its local import."""
    stack.enter_context(
        patch("djust.theming.context_processors.get_theme_manager", return_value=mgr)
    )
    stack.enter_context(
        patch("djust.theming.context_processors.generate_css_for_state", return_value=css_value)
    )
    for name, spy in spies.items():
        stack.enter_context(patch(f"djust.theming.templatetags.theme_tags.{name}", spy))


class TestThemeContextMemoize1727:
    def test_unchanged_state_renders_tag_bodies_once(self):
        """Two ``theme_context(request)`` calls with the SAME request and
        unchanged theme state → each of the four tag bodies is invoked
        exactly ONCE total (memoized on the 2nd call), and both calls
        return identical output. This is the #1727 acceptance criterion.
        """
        from djust.theming.context_processors import theme_context

        mgr = _make_manager()
        spies = _tag_spies()
        request = MagicMock()
        with ExitStack() as stack:
            _enter_patches(stack, mgr, "x", spies)
            ctx1 = theme_context(request)
            ctx2 = theme_context(request)

        for name, spy in spies.items():
            assert spy.call_count == 1, (
                f"{name} tag body re-rendered on the 2nd call (count="
                f"{spy.call_count}); expected memoized request-scoped cache to "
                f"serve it once."
            )
        assert ctx1["theme_head"] == ctx2["theme_head"]
        assert ctx1["theme_panel"] == ctx2["theme_panel"]
        assert ctx1["theme_mode_toggle"] == ctx2["theme_mode_toggle"]
        assert ctx1["theme_preset_selector"] == ctx2["theme_preset_selector"]

    def test_theme_switch_recomputes_tag_bodies(self):
        """A live theme switch (mode/preset change) on the SAME request
        MUST recompute the tag bodies and yield UPDATED output — dynamic
        switching is preserved (NOT first-sync-gated)."""
        from djust.theming.context_processors import theme_context

        request = MagicMock()
        spies = _tag_spies()

        # First call: light mode.
        mgr_light = _make_manager(mode="light", resolved_mode="light", preset="default")
        with ExitStack() as stack:
            _enter_patches(stack, mgr_light, "x", spies)
            theme_context(request)
        first_counts = {name: spy.call_count for name, spy in spies.items()}

        # Second call on the SAME request, but theme state changed
        # (dark + ocean) and tag bodies now return updated strings.
        spies2 = {
            "theme_head": MagicMock(return_value="HEAD_DARK"),
            "theme_panel": MagicMock(return_value="PANEL_DARK"),
            "theme_mode_toggle": MagicMock(return_value="TOGGLE_DARK"),
            "theme_preset_selector": MagicMock(return_value="SELECTOR_DARK"),
        }
        mgr_dark = _make_manager(mode="dark", resolved_mode="dark", preset="ocean")
        with ExitStack() as stack:
            _enter_patches(stack, mgr_dark, "y", spies2)
            ctx2 = theme_context(request)

        # Each tag was invoked on the FIRST call...
        for name, c in first_counts.items():
            assert c == 1, f"{name} not rendered on first call"
        # ...and AGAIN on the switch (recompute, not stale-cache hit).
        for name, spy in spies2.items():
            assert spy.call_count == 1, (
                f"{name} was NOT recomputed after a theme switch — dynamic "
                f"switching broken (stale cache served)."
            )
        # And the output reflects the NEW theme state.
        assert "HEAD_DARK" in str(ctx2["theme_head"])
        assert ctx2["theme_panel"] == "PANEL_DARK"
        assert ctx2["theme_mode_toggle"] == "TOGGLE_DARK"
        assert ctx2["theme_preset_selector"] == "SELECTOR_DARK"

    def test_distinct_requests_do_not_share_cache(self):
        """Request-scoped isolation: two DISTINCT request objects each get
        their own render (no cross-request sharing). Even with identical
        theme state, the cache lives on the request, so a per-request value
        (a future nonce) could never leak. Each request renders its tags."""
        from djust.theming.context_processors import theme_context

        mgr = _make_manager()
        spies = _tag_spies()
        req1 = MagicMock()
        req2 = MagicMock()
        with ExitStack() as stack:
            _enter_patches(stack, mgr, "x", spies)
            ctx1 = theme_context(req1)
            ctx2 = theme_context(req2)

        # Two distinct requests → each renders once → 2 total per tag (no
        # cross-request cache sharing).
        for name, spy in spies.items():
            assert spy.call_count == 2, (
                f"{name} rendered {spy.call_count} times across two distinct "
                f"requests; request-scoped cache must NOT span requests "
                f"(expected 2)."
            )
        # Deterministic theme state → identical bytes (no leakage either way).
        assert ctx1["theme_head"] == ctx2["theme_head"]
        assert ctx1["theme_panel"] == ctx2["theme_panel"]

    def test_same_request_unchanged_state_returns_identical_keys(self):
        """The memoized second call must return the full context dict with
        the same non-tag keys too (theme_switcher, theme_preset, etc.) —
        memoization only short-circuits the four tag bodies, not the rest
        of the processor's contract."""
        from djust.theming.context_processors import theme_context

        mgr = _make_manager()
        spies = _tag_spies()
        request = MagicMock()
        with ExitStack() as stack:
            _enter_patches(stack, mgr, "x", spies)
            ctx1 = theme_context(request)
            ctx2 = theme_context(request)

        assert set(ctx1.keys()) == set(ctx2.keys())
        for key in ("theme_switcher", "theme_preset", "theme_mode", "theme_resolved_mode"):
            assert ctx1[key] == ctx2[key]

    def test_no_request_attr_does_not_crash(self):
        """A request object that cannot hold attributes (edge case) must
        not crash theme_context — it falls back to rendering each call."""
        from djust.theming.context_processors import theme_context

        mgr = _make_manager()
        spies = _tag_spies()
        # A request whose attribute set raises (e.g. __slots__ object).
        request = object()
        with ExitStack() as stack:
            _enter_patches(stack, mgr, "x", spies)
            ctx = theme_context(request)

        assert "HEAD_HTML" in str(ctx["theme_head"])
        assert ctx["theme_panel"] == "PANEL_HTML"
