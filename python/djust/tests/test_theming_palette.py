"""
Tests for PaletteGenerator — Brand Color Auto-Palette Generator (I21).

TDD: these tests are written before the implementation.
"""

import pytest

from djust.theming.presets import ColorScale, ThemePreset, ThemeTokens
from djust.theming.accessibility import AccessibilityValidator
from djust.theming.palette import PaletteGenerator

pytestmark = pytest.mark.theming


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _all_fg_bg_pairs(tokens: ThemeTokens):
    """Yield (name, fg, bg) for every foreground/background pair."""
    return [
        ("foreground/background", tokens.foreground, tokens.background),
        ("card_foreground/card", tokens.card_foreground, tokens.card),
        ("popover_foreground/popover", tokens.popover_foreground, tokens.popover),
        ("primary_foreground/primary", tokens.primary_foreground, tokens.primary),
        ("secondary_foreground/secondary", tokens.secondary_foreground, tokens.secondary),
        ("muted_foreground/muted", tokens.muted_foreground, tokens.muted),
        ("accent_foreground/accent", tokens.accent_foreground, tokens.accent),
        ("destructive_foreground/destructive", tokens.destructive_foreground, tokens.destructive),
        ("success_foreground/success", tokens.success_foreground, tokens.success),
        ("warning_foreground/warning", tokens.warning_foreground, tokens.warning),
        ("info_foreground/info", tokens.info_foreground, tokens.info),
        ("code_foreground/code", tokens.code_foreground, tokens.code),
        ("selection_foreground/selection", tokens.selection_foreground, tokens.selection),
    ]


# ---------------------------------------------------------------------------
# Basic generation tests
# ---------------------------------------------------------------------------


class TestBasicGeneration:
    """Test that from_brand_colors produces output for various inputs."""

    def test_from_single_brand_color(self):
        preset = PaletteGenerator.from_brand_colors("#3b82f6")
        assert isinstance(preset, ThemePreset)

    def test_from_two_brand_colors(self):
        preset = PaletteGenerator.from_brand_colors("#3b82f6", secondary="#10b981")
        assert isinstance(preset, ThemePreset)

    def test_from_three_brand_colors(self):
        preset = PaletteGenerator.from_brand_colors(
            "#3b82f6", secondary="#10b981", accent="#f59e0b"
        )
        assert isinstance(preset, ThemePreset)

    @pytest.mark.parametrize("mode", PaletteGenerator.MODES)
    def test_all_modes(self, mode):
        preset = PaletteGenerator.from_brand_colors("#3b82f6", mode=mode)
        assert isinstance(preset, ThemePreset)

    def test_invalid_mode_raises(self):
        with pytest.raises(ValueError, match="mode"):
            PaletteGenerator.from_brand_colors("#3b82f6", mode="neon")

    def test_invalid_hex_raises(self):
        with pytest.raises(ValueError):
            PaletteGenerator.from_brand_colors("not-a-color")

    def test_short_hex_accepted(self):
        preset = PaletteGenerator.from_brand_colors("#f00")
        assert isinstance(preset, ThemePreset)


# ---------------------------------------------------------------------------
# Output validation tests
# ---------------------------------------------------------------------------


class TestOutputValidation:
    """Validate the structure of the generated ThemePreset."""

    @pytest.fixture()
    def preset(self):
        return PaletteGenerator.from_brand_colors("#3b82f6")

    def test_output_is_theme_preset(self, preset):
        assert isinstance(preset, ThemePreset)

    def test_has_light_and_dark(self, preset):
        assert isinstance(preset.light, ThemeTokens)
        assert isinstance(preset.dark, ThemeTokens)

    def test_all_31_fields_present(self, preset):
        """Every ThemeTokens field must be a ColorScale instance."""
        field_names = [f.name for f in ThemeTokens.__dataclass_fields__.values()]
        assert len(field_names) == 36

        for mode_label, tokens in [("light", preset.light), ("dark", preset.dark)]:
            for field_name in field_names:
                val = getattr(tokens, field_name)
                assert isinstance(val, ColorScale), (
                    f"{mode_label}.{field_name} is {type(val).__name__}, expected ColorScale"
                )

    def test_preset_name_generated(self, preset):
        assert isinstance(preset.name, str) and len(preset.name) > 0
        assert isinstance(preset.display_name, str) and len(preset.display_name) > 0

    def test_radius_varies_by_mode(self):
        professional = PaletteGenerator.from_brand_colors("#3b82f6", mode="professional")
        playful = PaletteGenerator.from_brand_colors("#3b82f6", mode="playful")
        muted = PaletteGenerator.from_brand_colors("#3b82f6", mode="muted")
        assert professional.radius != playful.radius or professional.radius != muted.radius


# ---------------------------------------------------------------------------
# Color derivation tests
# ---------------------------------------------------------------------------


class TestColorDerivation:
    """Verify hue relationships between input and output colors."""

    def test_primary_hue_preserved(self):
        preset = PaletteGenerator.from_brand_colors("#3b82f6")  # hue ~217
        input_hsl = ColorScale.from_hex("#3b82f6")
        # Allow +-5 degrees of rounding
        assert abs(preset.light.primary.h - input_hsl.h) <= 5

    def test_secondary_derived_complementary(self):
        """When no secondary is given, it should be ~180 degrees from primary."""
        preset = PaletteGenerator.from_brand_colors("#3b82f6", mode="professional")
        input_hsl = ColorScale.from_hex("#3b82f6")
        expected_hue = (input_hsl.h + 180) % 360
        # secondary_foreground carries the derived hue at high saturation;
        # secondary itself is a subtle tinted background, so check foreground hue.
        actual_hue = preset.light.secondary_foreground.h
        diff = min(abs(actual_hue - expected_hue), 360 - abs(actual_hue - expected_hue))
        assert diff <= 15, f"secondary hue {actual_hue} too far from expected {expected_hue}"

    def test_accent_derived_analogous(self):
        """When no accent is given, it should be ~30 degrees from primary."""
        preset = PaletteGenerator.from_brand_colors("#3b82f6", mode="professional")
        input_hsl = ColorScale.from_hex("#3b82f6")
        expected_hue = (input_hsl.h + 30) % 360
        actual_hue = preset.light.accent_foreground.h
        diff = min(abs(actual_hue - expected_hue), 360 - abs(actual_hue - expected_hue))
        assert diff <= 15, f"accent hue {actual_hue} too far from expected {expected_hue}"

    def test_user_provided_secondary_used(self):
        preset = PaletteGenerator.from_brand_colors("#3b82f6", secondary="#10b981")
        sec_hsl = ColorScale.from_hex("#10b981")
        actual_hue = preset.light.secondary_foreground.h
        diff = min(abs(actual_hue - sec_hsl.h), 360 - abs(actual_hue - sec_hsl.h))
        assert diff <= 10, f"Expected user secondary hue ~{sec_hsl.h}, got {actual_hue}"

    def test_user_provided_accent_used(self):
        preset = PaletteGenerator.from_brand_colors("#3b82f6", accent="#f59e0b")
        acc_hsl = ColorScale.from_hex("#f59e0b")
        actual_hue = preset.light.accent_foreground.h
        diff = min(abs(actual_hue - acc_hsl.h), 360 - abs(actual_hue - acc_hsl.h))
        assert diff <= 10, f"Expected user accent hue ~{acc_hsl.h}, got {actual_hue}"


# ---------------------------------------------------------------------------
# WCAG contrast validation tests
# ---------------------------------------------------------------------------


class TestWCAGContrast:
    """Every fg/bg pair must pass WCAG AA (4.5:1)."""

    validator = AccessibilityValidator()

    @pytest.fixture(params=PaletteGenerator.MODES)
    def preset(self, request):
        return PaletteGenerator.from_brand_colors("#3b82f6", mode=request.param)

    def test_all_fg_bg_pairs_pass_aa_light(self, preset):
        for name, fg, bg in _all_fg_bg_pairs(preset.light):
            ratio = self.validator.calculate_contrast_ratio(fg, bg)
            assert ratio >= 4.5, (
                f"Light {name}: contrast {ratio:.2f} < 4.5:1 (fg={fg.to_hsl()}, bg={bg.to_hsl()})"
            )

    def test_all_fg_bg_pairs_pass_aa_dark(self, preset):
        for name, fg, bg in _all_fg_bg_pairs(preset.dark):
            ratio = self.validator.calculate_contrast_ratio(fg, bg)
            assert ratio >= 4.5, (
                f"Dark {name}: contrast {ratio:.2f} < 4.5:1 (fg={fg.to_hsl()}, bg={bg.to_hsl()})"
            )

    def test_border_contrast_minimum_light(self, preset):
        ratio = self.validator.calculate_contrast_ratio(
            preset.light.border, preset.light.background
        )
        assert ratio >= 3.0, f"Light border contrast {ratio:.2f} < 3:1"

    def test_border_contrast_minimum_dark(self, preset):
        ratio = self.validator.calculate_contrast_ratio(preset.dark.border, preset.dark.background)
        assert ratio >= 3.0, f"Dark border contrast {ratio:.2f} < 3:1"

    def test_link_readable_on_background(self, preset):
        for mode_label, tokens in [("light", preset.light), ("dark", preset.dark)]:
            ratio = self.validator.calculate_contrast_ratio(tokens.link, tokens.background)
            assert ratio >= 4.5, f"{mode_label} link contrast {ratio:.2f} < 4.5:1"


class TestContrastAutofix:
    """The auto-fix should rescue deliberately bad inputs."""

    def test_contrast_autofix_works(self):
        """Even a very light primary on white should produce passing contrast."""
        # #eeeeee is very light — hard to get contrast on white background
        preset = PaletteGenerator.from_brand_colors("#eeeeee")
        validator = AccessibilityValidator()
        for name, fg, bg in _all_fg_bg_pairs(preset.light):
            ratio = validator.calculate_contrast_ratio(fg, bg)
            assert ratio >= 4.5, f"Light {name}: contrast {ratio:.2f} < 4.5:1 after autofix"

    def test_contrast_autofix_mid_lightness_bg(self):
        """A fg color near bg lightness ~50 should still get fixed via fallback direction."""
        # Mid-gray bg with mid-gray fg — preferred direction may not work,
        # fallback to opposite direction should succeed
        preset = PaletteGenerator.from_brand_colors("#808080")
        validator = AccessibilityValidator()
        for name, fg, bg in _all_fg_bg_pairs(preset.light):
            ratio = validator.calculate_contrast_ratio(fg, bg)
            assert ratio >= 4.5, f"Light {name}: contrast {ratio:.2f} < 4.5:1 after autofix"
        for name, fg, bg in _all_fg_bg_pairs(preset.dark):
            ratio = validator.calculate_contrast_ratio(fg, bg)
            assert ratio >= 4.5, f"Dark {name}: contrast {ratio:.2f} < 4.5:1 after autofix"


class TestKnownBrandColors:
    """Smoke tests with recognizable brand colors."""

    validator = AccessibilityValidator()

    @pytest.mark.parametrize(
        "hex_color,label",
        [
            ("#1DB954", "Spotify green"),
            ("#F40009", "Coca-Cola red"),
            ("#1877F2", "Facebook blue"),
        ],
    )
    def test_known_brand_colors(self, hex_color, label):
        preset = PaletteGenerator.from_brand_colors(hex_color)
        assert isinstance(preset, ThemePreset), f"Failed for {label}"
        # Spot-check a few critical pairs in both modes
        for mode_label, tokens in [("light", preset.light), ("dark", preset.dark)]:
            ratio = self.validator.calculate_contrast_ratio(tokens.foreground, tokens.background)
            assert ratio >= 4.5, f"{label} {mode_label} text contrast {ratio:.2f} < 4.5:1"


# ---------------------------------------------------------------------------
# Mode differentiation tests
# ---------------------------------------------------------------------------


class TestModeDifferentiation:
    """Different modes should produce measurably different palettes."""

    def test_muted_mode_lower_saturation(self):
        pro = PaletteGenerator.from_brand_colors("#3b82f6", mode="professional")
        muted = PaletteGenerator.from_brand_colors("#3b82f6", mode="muted")
        assert muted.light.primary.s <= pro.light.primary.s

    def test_vibrant_mode_higher_saturation(self):
        pro = PaletteGenerator.from_brand_colors("#3b82f6", mode="professional")
        vibrant = PaletteGenerator.from_brand_colors("#3b82f6", mode="vibrant")
        assert vibrant.light.primary.s >= pro.light.primary.s

    def test_playful_wider_hue_range(self):
        """Playful accent hue offset > professional accent hue offset."""
        pro = PaletteGenerator.from_brand_colors("#3b82f6", mode="professional")
        playful = PaletteGenerator.from_brand_colors("#3b82f6", mode="playful")
        input_hue = ColorScale.from_hex("#3b82f6").h

        def hue_dist(h1, h2):
            return min(abs(h1 - h2), 360 - abs(h1 - h2))

        pro_dist = hue_dist(pro.light.accent_foreground.h, input_hue)
        play_dist = hue_dist(playful.light.accent_foreground.h, input_hue)
        assert play_dist >= pro_dist


# ---------------------------------------------------------------------------
# Integration / round-trip tests
# ---------------------------------------------------------------------------


class TestIntegration:
    """Generated presets should work with the rest of the theming system."""

    def test_preset_works_with_css_generator(self):
        from djust.theming.css_generator import ThemeCSSGenerator

        preset = PaletteGenerator.from_brand_colors("#3b82f6")
        # ThemeCSSGenerator expects a preset name registered in THEME_PRESETS,
        # so we test the internal _tokens_to_css_vars method directly.
        generator = ThemeCSSGenerator.__new__(ThemeCSSGenerator)
        generator.preset = preset
        generator.custom_tokens = {}
        generator.include_base_styles = False
        generator.include_utilities = False
        generator.include_design_tokens = False
        css = generator.generate_css()
        assert isinstance(css, str)
        assert "--primary" in css
        assert "--background" in css

    def test_preset_works_with_accessibility_validator(self):
        """The generated preset should be validatable (no missing fields)."""
        preset = PaletteGenerator.from_brand_colors("#3b82f6")
        validator = AccessibilityValidator()
        # Just check we can calculate contrast on all pairs without error
        for name, fg, bg in _all_fg_bg_pairs(preset.light):
            ratio = validator.calculate_contrast_ratio(fg, bg)
            assert ratio > 0

    def test_exported_from_package(self):
        """PaletteGenerator should be importable from the top-level package."""
        from djust.theming import PaletteGenerator as PG

        assert PG is PaletteGenerator
