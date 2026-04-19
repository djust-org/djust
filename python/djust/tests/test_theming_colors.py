"""
Tests for color conversion utilities and ColorScale format methods.

TDD: these tests are written before the implementation.
"""

import pytest
from djust.theming.colors import (
    hsl_to_rgb,
    rgb_to_hsl,
    hex_to_rgb,
    rgb_to_hex,
    hex_to_hsl,
    hsl_to_hex,
)
from djust.theming.presets import ColorScale

pytestmark = pytest.mark.theming


# ---------------------------------------------------------------------------
# Unit tests for colors.py pure functions
# ---------------------------------------------------------------------------


class TestHslToRgb:
    """hsl_to_rgb: known color values."""

    def test_pure_red(self):
        assert hsl_to_rgb(0, 100, 50) == (255, 0, 0)

    def test_pure_green(self):
        assert hsl_to_rgb(120, 100, 50) == (0, 255, 0)

    def test_pure_blue(self):
        assert hsl_to_rgb(240, 100, 50) == (0, 0, 255)

    def test_black(self):
        assert hsl_to_rgb(0, 0, 0) == (0, 0, 0)

    def test_white(self):
        assert hsl_to_rgb(0, 0, 100) == (255, 255, 255)

    def test_gray(self):
        r, g, b = hsl_to_rgb(0, 0, 50)
        # Gray is approximately 128 — colorsys may round to 127 or 128
        assert abs(r - 128) <= 1
        assert abs(g - 128) <= 1
        assert abs(b - 128) <= 1

    def test_hue_360_same_as_0(self):
        """Hue 360 wraps to same as hue 0."""
        assert hsl_to_rgb(360, 100, 50) == hsl_to_rgb(0, 100, 50)


class TestRgbToHsl:
    """rgb_to_hsl: known color values."""

    def test_pure_red(self):
        assert rgb_to_hsl(255, 0, 0) == (0, 100, 50)

    def test_pure_green(self):
        assert rgb_to_hsl(0, 255, 0) == (120, 100, 50)

    def test_pure_blue(self):
        assert rgb_to_hsl(0, 0, 255) == (240, 100, 50)

    def test_black(self):
        assert rgb_to_hsl(0, 0, 0) == (0, 0, 0)

    def test_white(self):
        assert rgb_to_hsl(255, 255, 255) == (0, 0, 100)


class TestHexToRgb:
    """hex_to_rgb: parsing hex strings."""

    def test_red_lowercase(self):
        assert hex_to_rgb("#ff0000") == (255, 0, 0)

    def test_red_uppercase(self):
        assert hex_to_rgb("#FF0000") == (255, 0, 0)

    def test_without_hash(self):
        assert hex_to_rgb("ff0000") == (255, 0, 0)

    def test_shorthand_3_digit(self):
        assert hex_to_rgb("#fff") == (255, 255, 255)

    def test_shorthand_3_digit_color(self):
        assert hex_to_rgb("#f00") == (255, 0, 0)

    def test_known_color(self):
        assert hex_to_rgb("#3b82f6") == (59, 130, 246)

    def test_invalid_empty(self):
        with pytest.raises(ValueError):
            hex_to_rgb("")

    def test_invalid_chars(self):
        with pytest.raises(ValueError):
            hex_to_rgb("#GGG")

    def test_invalid_length_5(self):
        with pytest.raises(ValueError):
            hex_to_rgb("#12345")

    def test_invalid_length_1(self):
        with pytest.raises(ValueError):
            hex_to_rgb("#1")


class TestRgbToHex:
    """rgb_to_hex: formatting hex strings."""

    def test_red(self):
        assert rgb_to_hex(255, 0, 0) == "#ff0000"

    def test_white(self):
        assert rgb_to_hex(255, 255, 255) == "#ffffff"

    def test_black(self):
        assert rgb_to_hex(0, 0, 0) == "#000000"

    def test_known_color(self):
        assert rgb_to_hex(59, 130, 246) == "#3b82f6"


class TestComposedConversions:
    """hex_to_hsl and hsl_to_hex (composed from the above)."""

    def test_hex_to_hsl_red(self):
        assert hex_to_hsl("#ff0000") == (0, 100, 50)

    def test_hsl_to_hex_red(self):
        assert hsl_to_hex(0, 100, 50) == "#ff0000"

    def test_hsl_to_hex_white(self):
        assert hsl_to_hex(0, 0, 100) == "#ffffff"

    def test_hsl_to_hex_black(self):
        assert hsl_to_hex(0, 0, 0) == "#000000"


# ---------------------------------------------------------------------------
# Round-trip tests
# ---------------------------------------------------------------------------


class TestRoundTrips:
    """Verify conversions survive round-trips."""

    def test_hex_rgb_hex_exact(self):
        """hex -> RGB -> hex is lossless."""
        assert rgb_to_hex(*hex_to_rgb("#3b82f6")) == "#3b82f6"

    def test_rgb_hex_rgb_exact(self):
        """RGB -> hex -> RGB is lossless."""
        assert hex_to_rgb(rgb_to_hex(59, 130, 246)) == (59, 130, 246)

    def test_hex_hsl_hex_tolerance(self):
        """hex -> HSL -> hex may round; check per-channel tolerance."""
        original = "#3b82f6"
        roundtripped = hsl_to_hex(*hex_to_hsl(original))
        orig_rgb = hex_to_rgb(original)
        rt_rgb = hex_to_rgb(roundtripped)
        for o, r in zip(orig_rgb, rt_rgb):
            assert abs(o - r) <= 2, f"Channel diff too large: {o} vs {r}"

    def test_hsl_rgb_hsl_known_safe(self):
        """HSL -> RGB -> HSL for primary colors (no rounding issues)."""
        for h, s, l in [(0, 100, 50), (120, 100, 50), (240, 100, 50)]:  # noqa: E741
            assert rgb_to_hsl(*hsl_to_rgb(h, s, l)) == (h, s, l)

    def test_rgb_boundary_values(self):
        """Boundary RGB values round-trip through hex."""
        for val in [(0, 0, 0), (255, 255, 255), (0, 128, 255)]:
            assert hex_to_rgb(rgb_to_hex(*val)) == val


# ---------------------------------------------------------------------------
# ColorScale method tests
# ---------------------------------------------------------------------------


class TestColorScaleOutputMethods:
    """Instance methods: to_hex, to_rgb, to_rgb_func."""

    def test_to_hex_red(self):
        assert ColorScale(0, 100, 50).to_hex() == "#ff0000"

    def test_to_rgb_red(self):
        assert ColorScale(0, 100, 50).to_rgb() == (255, 0, 0)

    def test_to_rgb_func_red(self):
        assert ColorScale(0, 100, 50).to_rgb_func() == "rgb(255, 0, 0)"

    def test_existing_to_hsl_still_works(self):
        cs = ColorScale(221, 83, 53)
        assert cs.to_hsl() == "221 83% 53%"

    def test_existing_to_hsl_func_still_works(self):
        cs = ColorScale(221, 83, 53)
        assert cs.to_hsl_func() == "hsl(221, 83%, 53%)"


class TestColorScaleFactoryMethods:
    """Class methods: from_hex, from_rgb."""

    def test_from_hex_red(self):
        cs = ColorScale.from_hex("#ff0000")
        assert cs == ColorScale(0, 100, 50)

    def test_from_rgb_red(self):
        cs = ColorScale.from_rgb(255, 0, 0)
        assert cs == ColorScale(0, 100, 50)

    def test_from_hex_black(self):
        cs = ColorScale.from_hex("#000000")
        assert cs == ColorScale(0, 0, 0)

    def test_from_hex_white(self):
        cs = ColorScale.from_hex("#ffffff")
        assert cs == ColorScale(0, 0, 100)

    def test_from_hex_shorthand(self):
        cs = ColorScale.from_hex("#f00")
        assert cs == ColorScale(0, 100, 50)

    def test_round_trip_colorscale(self):
        """ColorScale -> hex -> ColorScale is near-lossless."""
        original = ColorScale(221, 83, 53)
        roundtripped = ColorScale.from_hex(original.to_hex())
        assert abs(roundtripped.h - original.h) <= 1
        assert abs(roundtripped.s - original.s) <= 1
        assert abs(roundtripped.lightness - original.lightness) <= 1


# ---------------------------------------------------------------------------
# Integration: accessibility.py regression
# ---------------------------------------------------------------------------


class TestAccessibilityRegression:
    """Ensure refactored hsl_to_rgb in accessibility.py still works."""

    def test_contrast_ratio_unchanged(self):
        """A known color pair should produce the same contrast ratio."""
        from djust.theming.accessibility import AccessibilityValidator

        validator = AccessibilityValidator()
        white = ColorScale(0, 0, 100)
        black = ColorScale(0, 0, 0)
        ratio = validator.calculate_contrast_ratio(white, black)
        assert ratio == pytest.approx(21.0, abs=0.1)

    def test_hsl_to_rgb_returns_float_tuple(self):
        """Method still returns 0-1 float range for backward compat."""
        from djust.theming.accessibility import AccessibilityValidator

        validator = AccessibilityValidator()
        red = ColorScale(0, 100, 50)
        r, g, b = validator.hsl_to_rgb(red)
        assert isinstance(r, float)
        assert r == pytest.approx(1.0, abs=0.01)
        assert g == pytest.approx(0.0, abs=0.01)
        assert b == pytest.approx(0.0, abs=0.01)
