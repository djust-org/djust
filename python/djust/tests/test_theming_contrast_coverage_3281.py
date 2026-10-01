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
    _PAIR_DEBT_2885,
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

    @pytest.mark.parametrize("name", ["legal", "default", "dracula"])
    @pytest.mark.parametrize("mode", MODES)
    def test_it_is_the_worse_of_the_page_and_a_card(self, name, mode):
        """An alert sits on either; the matrix must hold for both placements."""
        from djust.theming.presets import ColorScale as C

        tokens = getattr(THEME_PRESETS[name], mode)
        for status in ("success", "warning", "destructive"):
            colour = getattr(tokens, status)
            fg = colour.to_rgb()
            ratios = []
            for surface in (tokens.background, tokens.card):
                base = surface.to_rgb()
                tint = C.from_rgb(*(round(0.1 * f + 0.9 * b) for f, b in zip(fg, base)))
                ratios.append(_validator.calculate_contrast_ratio(colour, tint))
            assert _ratio(name, mode, status, f"{status}_tint") == pytest.approx(min(ratios))

    def test_the_local_luminance_matches_the_validator(self):
        """``_types`` may not import ``accessibility`` (cyclic-import rule), so it
        carries its own copy of the WCAG formula. They must agree."""
        from djust.theming import _types

        for name, preset in THEME_PRESETS.items():
            for mode in MODES:
                tokens = getattr(preset, mode)
                for a, b in ((tokens.foreground, tokens.background), (tokens.primary, tokens.card)):
                    assert _types._contrast(a, b) == pytest.approx(
                        _validator.calculate_contrast_ratio(a, b), abs=1e-6
                    ), name


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


# The framework's own presets are the ones people copy. Their debt is pinned
# exactly, so a new row or a worse ratio there is a conscious edit.
FLAGSHIP_DEBT = {
    "djust": {
        ("light", "primary", "background"),
        ("light", "primary", "card"),
        ("light", "success", "success_tint"),
        ("light", "warning", "warning_tint"),
        ("light", "destructive", "destructive_tint"),
        ("light", "input", "background"),
        ("dark", "destructive", "destructive_tint"),
        ("dark", "input", "background"),
    },
    "default": {
        ("light", "success", "success_tint"),
        ("light", "warning", "warning_tint"),
        ("light", "destructive", "destructive_tint"),
        ("light", "input", "background"),
        ("dark", "success", "success_tint"),
        ("dark", "destructive", "destructive_tint"),
        ("dark", "input", "background"),
    },
}
FLAGSHIP_DEBT["shadcn"] = FLAGSHIP_DEBT["blue"] = FLAGSHIP_DEBT["default"]
FLAGSHIP_DEBT["slate"] = {
    ("light", "success", "success_tint"),
    ("light", "warning", "warning_tint"),
    ("light", "destructive", "destructive_tint"),
    ("light", "input", "background"),
    ("dark", "input", "background"),
}


@pytest.mark.parametrize("name", sorted(FLAGSHIP_DEBT))
def test_flagship_presets_carry_exactly_the_known_new_pair_debt(name):
    rows = {
        (mode, fg, bg)
        for (preset, mode, fg, bg) in A11Y_EXEMPTIONS
        if preset == name and (fg, bg) in NEW_PAIR_KEYS
    }
    assert rows == FLAGSHIP_DEBT[name], (
        f"{name}: the set of exempted new pairs changed. If a palette edit fixed a pair "
        f"remove its row and this pin; if it broke one, fix the palette."
    )


@pytest.mark.parametrize(
    "key",
    sorted(_PAIR_DEBT_2885),
    ids=lambda k: f"{k[0]}-{k[1]}-{k[2]}_on_{k[3]}",
)
def test_a_recorded_ratio_is_the_measured_ratio(key):
    """ "Do not hand-edit ratios" made true: a palette that got worse fails here, and
    one that got better asks for the row to be regenerated."""
    recorded = _PAIR_DEBT_2885[key]
    measured = _ratio(*key)
    assert measured >= recorded - 0.006, (
        f"{key} got WORSE: recorded {recorded:.2f}, measured {measured:.2f}"
    )
    assert measured <= recorded + 0.006, (
        f"{key} improved: recorded {recorded:.2f}, measured {measured:.2f}; regenerate with "
        f"scripts/report_theme_contrast.py --debt-table (and delete the row if it now passes)"
    )
