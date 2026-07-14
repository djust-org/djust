"""Tests for the five themes added in the v1.1 theme drop.

Covers sakura, obsidian, dune, mission_control, and art_nouveau:

- registration in all three registries (``THEME_PRESETS``, theme packs,
  design systems) — the three seams a new theme must be wired through
- preset structure (light + dark tokens, sensible ``default_mode``)
- CSS generation content (mirrors the cross-theme QA suite's assertions,
  which run on a curated combo list that does not include new themes)
- WCAG contrast on the load-bearing text pairs in BOTH modes, computed
  with the library's own ``AccessibilityValidator``
- palette distinctness — no new theme duplicates another preset's
  primary/background identity (guards against copy-paste authoring)
"""

import pytest

from djust.theming.accessibility import AccessibilityValidator
from djust.theming.presets import THEME_PRESETS, get_preset

NEW_THEMES = ["sakura", "obsidian", "dune", "mission_control", "art_nouveau"]

EXPECTED_DEFAULT_MODE = {
    "sakura": "light",
    "obsidian": "dark",
    "dune": "light",
    "mission_control": "dark",
    "art_nouveau": "light",
}

_validator = AccessibilityValidator()


@pytest.fixture(params=NEW_THEMES)
def theme_name(request):
    return request.param


class TestRegistration:
    def test_preset_registered(self, theme_name):
        assert theme_name in THEME_PRESETS
        preset = get_preset(theme_name)
        assert preset.name == theme_name

    def test_theme_pack_registered(self, theme_name):
        from djust.theming.theme_packs import get_theme_pack

        pack = get_theme_pack(theme_name)
        assert pack is not None, f"{theme_name} missing from THEME_PACKS"
        assert pack.color_preset == theme_name
        assert pack.design_theme == theme_name

    def test_design_system_registered(self, theme_name):
        from djust.theming.theme_packs import get_design_system

        ds = get_design_system(theme_name)
        assert ds is not None, f"{theme_name} missing from DESIGN_SYSTEMS"
        assert ds.name == theme_name

    def test_reexported_from_builtin_presets(self, theme_name):
        """The *_THEME constant is part of the back-compat __all__ surface."""
        from djust.theming import _builtin_presets

        const = f"{theme_name.upper()}_THEME"
        assert const in _builtin_presets.__all__
        assert getattr(_builtin_presets, const).name == theme_name


class TestPresetStructure:
    def test_light_and_dark_tokens_present(self, theme_name):
        preset = get_preset(theme_name)
        assert preset.light is not None
        assert preset.dark is not None
        # Light mode background must actually be lighter than dark mode's.
        assert preset.light.background.lightness > preset.dark.background.lightness

    def test_default_mode(self, theme_name):
        assert get_preset(theme_name).default_mode == EXPECTED_DEFAULT_MODE[theme_name]

    def test_display_name_and_description(self, theme_name):
        preset = get_preset(theme_name)
        assert preset.display_name
        assert preset.description


class TestCSSGeneration:
    """Mirror the cross-theme QA suite's content assertions for the new themes."""

    @pytest.fixture(scope="class")
    def css_by_theme(self):
        from djust.theming.css_generator import generate_theme_css

        return {name: generate_theme_css(name, name) for name in NEW_THEMES}

    def test_css_generates_and_meets_minimum_size(self, theme_name, css_by_theme):
        css = css_by_theme[theme_name]
        assert isinstance(css, str)
        assert len(css) >= 10_000, f"{theme_name} CSS suspiciously small: {len(css)}"

    def test_css_has_root_and_both_mode_selectors(self, theme_name, css_by_theme):
        css = css_by_theme[theme_name]
        assert ":root {" in css or ":root\n{" in css
        assert 'html[data-theme="dark"]' in css
        assert 'html[data-theme="light"]' in css
        # The generator emits the prefers-color-scheme fallback only for
        # light-default presets (dark-default presets carry dark in :root).
        if EXPECTED_DEFAULT_MODE[theme_name] == "light":
            assert "@media (prefers-color-scheme: dark)" in css


class TestContrast:
    """WCAG AA on the load-bearing text pairs, both modes, using the
    library's own validator (relative-luminance per the WCAG formula)."""

    # (foreground_token, background_token, minimum_ratio)
    PAIRS = [
        ("foreground", "background", 4.5),
        ("card_foreground", "card", 4.5),
        ("primary_foreground", "primary", 4.5),
        ("destructive_foreground", "destructive", 4.5),
        ("accent_foreground", "accent", 4.5),
        # Muted text is allowed to be quieter, but must stay readable.
        ("muted_foreground", "background", 3.0),
    ]

    @pytest.mark.parametrize("mode", ["light", "dark"])
    def test_text_pairs_meet_wcag(self, theme_name, mode):
        tokens = getattr(get_preset(theme_name), mode)
        failures = []
        for fg_name, bg_name, minimum in self.PAIRS:
            fg = getattr(tokens, fg_name)
            bg = getattr(tokens, bg_name)
            ratio = _validator.calculate_contrast_ratio(fg, bg)
            if ratio < minimum:
                failures.append(f"{fg_name} on {bg_name}: {ratio:.2f} < {minimum}")
        assert not failures, f"{theme_name}/{mode} contrast failures: {failures}"


class TestDistinctness:
    def test_new_theme_palettes_are_not_duplicates(self):
        """No new theme shares its (primary, background) identity with any
        other preset in either mode — guards copy-paste authoring."""
        seen = {}
        for name, preset in THEME_PRESETS.items():
            for mode in ("light", "dark"):
                tokens = getattr(preset, mode)
                key = (
                    mode,
                    tokens.primary.h,
                    tokens.primary.s,
                    tokens.primary.lightness,
                    tokens.background.h,
                    tokens.background.s,
                    tokens.background.lightness,
                )
                if key in seen and (name in NEW_THEMES or seen[key] in NEW_THEMES):
                    raise AssertionError(
                        f"{name}/{mode} duplicates {seen[key]}/{mode} palette identity"
                    )
                seen.setdefault(key, name)
