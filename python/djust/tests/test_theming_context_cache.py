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
        """Different preset → fresh render (cache miss).

        Asserts via `theme_switcher` since `theme_head` now goes
        through the classic simple_tag (not the cached function) per
        #1452. The cache infrastructure protects `theme_switcher`'s
        rendering work; `theme_head` is independently cached at the
        Django template-engine level.
        """
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
        # Compare via theme_switcher — that's still cached on the state tuple.
        assert (
            "css-a" in str(ctx1["theme_switcher"])
            or ctx1["theme_switcher"] != ctx2["theme_switcher"]
        )
        assert (
            "css-b" in str(ctx2["theme_switcher"])
            or ctx1["theme_switcher"] != ctx2["theme_switcher"]
        )

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

    def test_theme_head_context_string_matches_classic_tag(self):
        """#1452 regression: `{{ theme_head }}` (context string) MUST
        emit whatever `{% theme_head %}` (classic tag) emits for the
        default-args case.

        The 0.9.6rc2 version of `_render_theme_outputs` hand-built a
        small string ({anti_fouc} + <style> + theme.js script) that
        DROPPED the `<link>` to `djust_theming/css/components.css`,
        the print.css link, the components.js script, the deferred-CSS
        preload, the RTL direction, and the cookie-namespace JS prefix.
        Production saw unstyled theme panels because the
        `.theme-panel*` rules live in components.css.

        The fix is to call the existing `theme_head` simple_tag from
        `theme_context`. This test pins that wiring: whatever the
        classic tag returns is what the context-string emits.
        """
        from djust.theming.context_processors import theme_context

        mgr = _make_manager()
        # Stub the classic tag to a deterministic value containing the
        # output components #1452 requires (components.css link).
        STUB_HEAD = (
            "<style data-djust-theme>:root{}</style>\n"
            '<link rel="stylesheet" href="/static/djust_theming/css/components.css">\n'
            '<link rel="stylesheet" href="/static/djust_theming/css/print.css" media="print">\n'
            '<script src="/static/djust_theming/js/theme.js" defer></script>\n'
            '<script src="/static/djust_theming/js/components.js" defer></script>'
        )
        with (
            patch("djust.theming.context_processors.get_theme_manager", return_value=mgr),
            patch(
                "djust.theming.context_processors.generate_css_for_state",
                return_value="x",
            ),
            patch(
                "djust.theming.templatetags.theme_tags.theme_head",
                return_value=STUB_HEAD,
            ),
        ):
            ctx = theme_context(MagicMock())

        # Whatever the simple_tag returned must appear verbatim in the
        # context — that's the wiring contract the fix establishes.
        assert STUB_HEAD in str(ctx["theme_head"]), (
            "{{ theme_head }} context-string did not pass through the "
            "classic-tag output — the #1452 wiring is broken."
        )
        # And it must contain the components.css reference (the
        # specific output that was missing in 0.9.6rc2).
        assert "djust_theming/css/components.css" in str(ctx["theme_head"]), (
            "components.css link missing from theme_head — #1452 regression."
        )

    def test_one_tag_failure_does_not_blank_others(self):
        """Per-tag fail-soft: if `theme_panel` raises (broken manifest,
        downstream shadowing), `theme_head` / `theme_mode_toggle` /
        `theme_preset_selector` MUST still render. The 0.9.6rc2 broad-
        try/except wrapped all four tags in one block, so any one tag
        failing blanked the whole pre-render set. Fixed in the #1452
        commit by per-tag wrapping.
        """
        from djust.theming.context_processors import theme_context

        mgr = _make_manager()
        with (
            patch("djust.theming.context_processors.get_theme_manager", return_value=mgr),
            patch(
                "djust.theming.context_processors.generate_css_for_state",
                return_value="x",
            ),
            patch(
                "djust.theming.templatetags.theme_tags.theme_head",
                return_value="HEAD_OK",
            ),
            patch(
                "djust.theming.templatetags.theme_tags.theme_panel",
                side_effect=RuntimeError("manifest broken"),
            ),
            patch(
                "djust.theming.templatetags.theme_tags.theme_mode_toggle",
                return_value="TOGGLE_OK",
            ),
            patch(
                "djust.theming.templatetags.theme_tags.theme_preset_selector",
                return_value="SELECTOR_OK",
            ),
        ):
            ctx = theme_context(MagicMock())

        # The healthy tags survive — only the broken one is empty.
        assert "HEAD_OK" in str(ctx["theme_head"])
        assert ctx["theme_panel"] == ""
        assert ctx["theme_mode_toggle"] == "TOGGLE_OK"
        assert ctx["theme_preset_selector"] == "SELECTOR_OK"

    def test_pre_rendered_panel_keys_present_in_context(self):
        """#1435: theme_context returns pre-rendered theme_panel,
        theme_mode_toggle, theme_preset_selector strings so templates
        can use them as `{{ theme_panel }}` instead of `{% theme_panel %}`.
        """
        from djust.theming.context_processors import theme_context

        mgr = _make_manager()
        with (
            patch("djust.theming.context_processors.get_theme_manager", return_value=mgr),
            patch(
                "djust.theming.context_processors.generate_css_for_state",
                return_value="x",
            ),
        ):
            # Stub the four tag functions to deterministic values so we
            # don't depend on Django template-loading config in this
            # narrow unit test.
            with (
                patch(
                    "djust.theming.templatetags.theme_tags.theme_head",
                    return_value="HEAD_HTML",
                ),
                patch(
                    "djust.theming.templatetags.theme_tags.theme_panel",
                    return_value="PANEL_HTML",
                ),
                patch(
                    "djust.theming.templatetags.theme_tags.theme_mode_toggle",
                    return_value="MODE_TOGGLE_HTML",
                ),
                patch(
                    "djust.theming.templatetags.theme_tags.theme_preset_selector",
                    return_value="PRESET_SELECTOR_HTML",
                ),
            ):
                ctx = theme_context(MagicMock())

        assert "HEAD_HTML" in str(ctx["theme_head"])
        assert ctx["theme_panel"] == "PANEL_HTML"
        assert ctx["theme_mode_toggle"] == "MODE_TOGGLE_HTML"
        assert ctx["theme_preset_selector"] == "PRESET_SELECTOR_HTML"

    def test_pre_render_failure_does_not_break_request(self):
        """If a tag function raises (broken manifest, missing template,
        downstream shadowing), the context still returns — that ONE
        pre-render comes back as an empty string instead of 500-ing
        the whole request.

        Per-tag fail-soft (#1452): only the failing tag is empty; the
        healthy tags still render. The 0.9.6rc2 broad-try/except
        wrapped all four in one block so any one failing blanked all.
        """
        from djust.theming.context_processors import theme_context

        mgr = _make_manager()
        with (
            patch("djust.theming.context_processors.get_theme_manager", return_value=mgr),
            patch(
                "djust.theming.context_processors.generate_css_for_state",
                return_value="x",
            ),
            patch(
                "djust.theming.templatetags.theme_tags.theme_head",
                return_value="HEAD_OK",
            ),
            patch(
                "djust.theming.templatetags.theme_tags.theme_panel",
                side_effect=RuntimeError("manifest broken"),
            ),
            patch(
                "djust.theming.templatetags.theme_tags.theme_mode_toggle",
                return_value="MODE_OK",
            ),
            patch(
                "djust.theming.templatetags.theme_tags.theme_preset_selector",
                return_value="SELECTOR_OK",
            ),
        ):
            ctx = theme_context(MagicMock())

        # Healthy tags rendered; broken tag is empty; nothing raised.
        assert "HEAD_OK" in str(ctx["theme_head"])
        assert ctx["theme_panel"] == ""
        assert ctx["theme_mode_toggle"] == "MODE_OK"
        assert ctx["theme_preset_selector"] == "SELECTOR_OK"

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
