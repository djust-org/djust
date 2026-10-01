"""The contrast matrix covers the surfaces components actually paint (#3281, #3165).

W001 and the all-presets gate only measured ``*_foreground`` labels on their
fill, so a preset could pass while failing in places a user sees: destructive
text on its 10% alert tint, ``primary`` / ``link`` as text on the page or a
card, and the 1.2:1 ``--input`` border (WCAG 1.4.11 asks for 3:1). The ``legal``
preset failed all of those and W001 stayed silent.
"""

from __future__ import annotations

import pytest
from django.core.checks import Warning

from djust.theming.a11y_exemptions import (
    A11Y_EXEMPTIONS,
    CONTRAST_PAIRS,
    NEW_PAIR_KEYS,
)
from djust.theming.accessibility import AccessibilityValidator
from djust.theming.checks import check_preset_contrast
from djust.theming.presets import THEME_PRESETS, ColorScale

pytestmark = pytest.mark.theming

_validator = AccessibilityValidator()
MODES = ("light", "dark")


def _ratio(preset: str, mode: str, fg: str, bg: str) -> float:
    tokens = getattr(THEME_PRESETS[preset], mode)
    return _validator.calculate_contrast_ratio(getattr(tokens, fg), getattr(tokens, bg))


def test_the_matrix_names_every_new_pair_and_only_those():
    in_matrix = {(fg, bg) for fg, bg, _m, _l in CONTRAST_PAIRS}
    assert NEW_PAIR_KEYS <= in_matrix
    assert len(in_matrix) == len(CONTRAST_PAIRS) == 14 + len(NEW_PAIR_KEYS)


def test_input_border_needs_three_to_one_not_four_and_a_half():
    minimum = {(fg, bg): m for fg, bg, m, _l in CONTRAST_PAIRS}
    assert minimum[("input", "background")] == 3.0
    assert minimum[("link", "background")] == 4.5


class TestAlertTint:
    """``*_tint`` is the colour ``hsl(var(--x) / 0.1)`` composites to over the page."""

    def test_a_tint_is_the_status_colour_at_ten_percent_over_the_background(self):
        tokens = THEME_PRESETS["default"].light
        r, g, b = tokens.destructive_tint.to_rgb()
        fr, fg_, fb = tokens.destructive.to_rgb()
        br, bg_, bb = tokens.background.to_rgb()
        for got, fore, back in ((r, fr, br), (g, fg_, bg_), (b, fb, bb)):
            assert abs(got - (0.1 * fore + 0.9 * back)) <= 4  # HSL round-trip

    def test_a_tint_is_a_derived_surface_not_a_css_token(self):
        from dataclasses import fields

        from djust.theming.presets import ThemeTokens

        names = {f.name for f in fields(ThemeTokens)}
        assert not {n for n in names if n.endswith("_tint")}

    def test_it_follows_the_background(self):
        light = THEME_PRESETS["legal"].light
        assert light.destructive_tint.lightness > 85
        assert THEME_PRESETS["legal"].dark.destructive_tint.lightness < 25


class TestLegalPreset:
    """Every issue claim for ``legal`` is fixed, with no exemption left."""

    @pytest.mark.parametrize("mode", MODES)
    @pytest.mark.parametrize(
        "fg,bg,minimum,label", CONTRAST_PAIRS, ids=[p[3] for p in CONTRAST_PAIRS]
    )
    def test_every_pair_passes(self, mode, fg, bg, minimum, label):
        ratio = _ratio("legal", mode, fg, bg)
        assert ratio >= minimum, f"legal/{mode}: {label} = {ratio:.2f} < {minimum}"

    def test_it_carries_no_exemption(self):
        assert not [k for k in A11Y_EXEMPTIONS if k[0] == "legal"]

    def test_the_identity_survives(self):
        """Navy primary, burgundy links and brand: only lightness moved."""
        light, dark = THEME_PRESETS["legal"].light, THEME_PRESETS["legal"].dark
        assert (light.primary.h, dark.primary.h) == (220, 220)
        assert (light.link.h, dark.link.h, light.brand.h) == (345, 345, 345)
        assert (light.success.h, dark.success.h) == (145, 145)


class TestW001SeesThemNow:
    def _bad_preset(self, **overrides):
        base = THEME_PRESETS["legal"]
        from dataclasses import replace

        weak_input = ColorScale(40, 8, 90)  # the old legal light value: 1.19:1
        light = replace(base.light, input=weak_input, **overrides)
        return replace(base, name="user_weak_input", light=light)

    def test_a_user_preset_with_a_faint_input_border_is_warned(self, settings):
        from unittest.mock import patch

        settings.LIVEVIEW_CONFIG = {"theme": {"preset": "user_weak_input"}}
        settings.DJUST_THEMING = {}
        preset = self._bad_preset()
        with patch("djust.theming.checks.get_registry") as registry:
            registry.return_value.list_presets.return_value = {"user_weak_input": preset}
            registry.return_value.has_preset.return_value = True
            warnings = check_preset_contrast(app_configs=None)
        assert [w.id for w in warnings] == ["djust_theming.W001"]
        assert "input border on background" in warnings[0].msg
        assert isinstance(warnings[0], Warning)

    def test_the_shipped_presets_stay_warning_clean(self, settings):
        settings.DJUST_THEMING = {"contrast_check_scope": "all"}
        assert check_preset_contrast(app_configs=None) == []


class TestLegacyDebtIsDocumented:
    """Presets that still fail a new pair carry a row that points at #2885."""

    def test_every_new_pair_failure_has_a_row_citing_2885(self):
        missing = []
        for name, preset in THEME_PRESETS.items():
            for mode in MODES:
                for fg, bg, minimum, _label in CONTRAST_PAIRS:
                    if (fg, bg) not in NEW_PAIR_KEYS:
                        continue
                    if _ratio(name, mode, fg, bg) >= minimum:
                        continue
                    reason = A11Y_EXEMPTIONS.get((name, mode, fg, bg))
                    if reason is None or "#2885" not in reason:
                        missing.append((name, mode, fg, bg))
        assert not missing

    def test_djust_light_primary_as_text_is_exempt_not_recoloured(self):
        """#3165: ``primary`` is the brand orange carrying dark ink labels."""
        assert _ratio("djust", "light", "primary", "background") < 4.5
        assert ("djust", "light", "primary", "background") in A11Y_EXEMPTIONS
