"""Tests for the theme_context per-process cache (#1437)."""

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _clear_cache_per_test():
    """Each test starts with a clean cache so warm-state from a prior
    test doesn't mask cold-path bugs."""
    from djust.theming.context_processors import clear_theme_context_cache

    clear_theme_context_cache()
    yield
    clear_theme_context_cache()


def _make_manager(preset="default", mode="light", resolved_mode="light", pack=None, presets=None):
    """Build a stub theme manager whose `get_state()` returns a
    ThemeState with the given fields and `get_available_presets()`
    returns the given preset list."""
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


class TestThemeContextCache:
    def test_two_identical_calls_render_once(self):
        """Same (preset, pack, mode, resolved_mode, presets) on two
        calls → CSS generation runs exactly once. Cache hit on the
        second call."""
        from djust.theming.context_processors import theme_context

        mgr = _make_manager()
        with (
            patch("djust.theming.context_processors.get_theme_manager", return_value=mgr),
            patch(
                "djust.theming.context_processors.generate_css_for_state",
                return_value=":root { --color: blue; }",
            ) as mock_css,
        ):
            ctx1 = theme_context(MagicMock())
            ctx2 = theme_context(MagicMock())
        assert mock_css.call_count == 1, (
            f"expected CSS generation to be called once (cache hit on 2nd), "
            f"got {mock_css.call_count}"
        )
        # And the rendered HTML matches across both calls.
        assert ctx1["theme_head"] == ctx2["theme_head"]
        assert ctx1["theme_switcher"] == ctx2["theme_switcher"]

    def test_different_preset_misses_cache(self):
        """Different preset → fresh render (cache miss)."""
        from djust.theming.context_processors import theme_context

        mgr_a = _make_manager(preset="default")
        mgr_b = _make_manager(preset="ocean")
        with patch("djust.theming.context_processors.generate_css_for_state") as mock_css:
            mock_css.side_effect = ["css-a", "css-b"]
            with patch("djust.theming.context_processors.get_theme_manager", return_value=mgr_a):
                ctx1 = theme_context(MagicMock())
            with patch("djust.theming.context_processors.get_theme_manager", return_value=mgr_b):
                ctx2 = theme_context(MagicMock())
        assert mock_css.call_count == 2
        assert "css-a" in ctx1["theme_head"]
        assert "css-b" in ctx2["theme_head"]

    def test_different_mode_misses_cache(self):
        """Different mode (light vs dark) → fresh render."""
        from djust.theming.context_processors import theme_context

        mgr_light = _make_manager(mode="light", resolved_mode="light")
        mgr_dark = _make_manager(mode="dark", resolved_mode="dark")
        with patch(
            "djust.theming.context_processors.generate_css_for_state", return_value="x"
        ) as mock_css:
            with patch(
                "djust.theming.context_processors.get_theme_manager", return_value=mgr_light
            ):
                theme_context(MagicMock())
            with patch("djust.theming.context_processors.get_theme_manager", return_value=mgr_dark):
                theme_context(MagicMock())
        assert mock_css.call_count == 2

    def test_different_pack_misses_cache(self):
        """Different pack → fresh render."""
        from djust.theming.context_processors import theme_context

        mgr_a = _make_manager(pack=None)
        mgr_b = _make_manager(pack="shadcn-default")
        with patch(
            "djust.theming.context_processors.generate_css_for_state", return_value="x"
        ) as mock_css:
            with patch("djust.theming.context_processors.get_theme_manager", return_value=mgr_a):
                theme_context(MagicMock())
            with patch("djust.theming.context_processors.get_theme_manager", return_value=mgr_b):
                theme_context(MagicMock())
        assert mock_css.call_count == 2

    def test_different_presets_list_misses_cache(self):
        """Adding/removing a theme preset (e.g., a hot-reload of the
        manifest) must invalidate the cache for that key. The presets
        list is part of the cache key."""
        from djust.theming.context_processors import theme_context

        presets_a = [
            {"name": "default", "display_name": "Default", "is_active": True},
        ]
        presets_b = [
            {"name": "default", "display_name": "Default", "is_active": True},
            {"name": "newly-added", "display_name": "New One", "is_active": False},
        ]
        mgr_a = _make_manager(presets=presets_a)
        mgr_b = _make_manager(presets=presets_b)
        with patch(
            "djust.theming.context_processors.generate_css_for_state", return_value="x"
        ) as mock_css:
            with patch("djust.theming.context_processors.get_theme_manager", return_value=mgr_a):
                theme_context(MagicMock())
            with patch("djust.theming.context_processors.get_theme_manager", return_value=mgr_b):
                theme_context(MagicMock())
        assert mock_css.call_count == 2

    def test_clear_cache_drops_warm_state(self):
        """clear_theme_context_cache() forces a fresh render on next
        call — used for theme-pack hot-reload."""
        from djust.theming.context_processors import (
            clear_theme_context_cache,
            theme_context,
        )

        mgr = _make_manager()
        with (
            patch("djust.theming.context_processors.get_theme_manager", return_value=mgr),
            patch(
                "djust.theming.context_processors.generate_css_for_state", return_value="x"
            ) as mock_css,
        ):
            theme_context(MagicMock())
            clear_theme_context_cache()
            theme_context(MagicMock())
        assert mock_css.call_count == 2

    def test_request_object_does_not_leak_into_cached_output(self):
        """The cached function takes only the (preset, pack, mode,
        resolved_mode, presets_key) tuple — nothing from request flows
        in. Two requests with different `request.user`, `request.path`,
        etc. but same theme state get the SAME bytes."""
        from djust.theming.context_processors import theme_context

        mgr = _make_manager()
        with (
            patch("djust.theming.context_processors.get_theme_manager", return_value=mgr),
            patch("djust.theming.context_processors.generate_css_for_state", return_value="x"),
        ):
            req1 = MagicMock()
            req1.user.id = 1
            req1.path = "/a"
            req2 = MagicMock()
            req2.user.id = 999
            req2.path = "/b/different"
            ctx1 = theme_context(req1)
            ctx2 = theme_context(req2)
        # Identical bytes → no per-request leakage.
        assert ctx1["theme_head"] == ctx2["theme_head"]
        assert ctx1["theme_switcher"] == ctx2["theme_switcher"]
