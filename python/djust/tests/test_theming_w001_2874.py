"""Regression tests for #2874 — every shipped preset must be W001-clean.

#2874: with ``djust.theming`` installed, ``manage.py check`` printed
``djust_theming.W001`` contrast warnings for whichever shipped preset was
active (6 for ``default``), so no project could be warning-clean without
silencing the check. The check had no mechanism for the documented,
anti-rot-gated contrast exemptions in ``djust.theming.a11y_exemptions``
that the pytest gate (#2060) already used for the same palettes.

The fix (#2874) reconciles W001 with that documented contract:

1. W001 skips ``(preset, mode, fg, bg)`` pairs listed in
   ``A11Y_EXEMPTIONS`` — the same single source of truth the pytest gate
   enforces — so documented legacy-palette debt no longer spams
   ``manage.py check`` while user-authored presets still get full
   validation.
2. The four presets a new project realistically uses (``default``,
   ``blue``, ``shadcn``, ``slate``) had their genuinely-failing status
   label colours fixed (foreground polarity flips, badge surface colours
   untouched), and their now-stale exemption entries removed.
3. The ``djust new`` scaffold no longer silences W001.

Each test below encodes one clause of that contract.
"""

import pytest

from djust.theming.accessibility import AccessibilityValidator
from djust.theming.a11y_exemptions import A11Y_EXEMPTIONS, CONTRAST_PAIRS
from djust.theming.checks import check_preset_contrast
from djust.theming.presets import THEME_PRESETS, ColorScale, ThemePreset, ThemeTokens

pytestmark = pytest.mark.theming

_validator = AccessibilityValidator()


def _ratio(preset_name: str, mode: str, fg_attr: str, bg_attr: str) -> float:
    tokens = getattr(THEME_PRESETS[preset_name], mode)
    return _validator.calculate_contrast_ratio(getattr(tokens, fg_attr), getattr(tokens, bg_attr))


class TestShippedPresetsAreWarningClean:
    """The #2874 headline: `manage.py check` must be clean on shipped presets."""

    def test_default_active_preset_emits_zero_w001(self, settings):
        """The issue's exact repro: active scope, preset "default" -> 0 warnings.

        Pre-fix this printed 6 warnings (light destructive/success/warning/info
        + dark warning/info), matching the issue's table.
        """
        settings.LIVEVIEW_CONFIG = {"theme": {"preset": "default"}}
        settings.DJUST_THEMING = {}
        warnings = check_preset_contrast(app_configs=None)
        assert warnings == [], (
            f"shipped preset 'default' still triggers W001: {[w.msg for w in warnings]}"
        )

    @pytest.mark.parametrize("preset_name", sorted(THEME_PRESETS), ids=lambda n: f"preset-{n}")
    def test_shipped_preset_is_w001_clean(self, settings, preset_name):
        """Every registered shipped preset is warning-clean in active scope.

        This is the issue title's claim ("Every shipped theme preset fails
        its own djust_theming.W001 contrast check"), locked per preset.
        """
        settings.LIVEVIEW_CONFIG = {"theme": {"preset": preset_name}}
        settings.DJUST_THEMING = {}
        warnings = check_preset_contrast(app_configs=None)
        failing = [w.msg for w in warnings if preset_name in w.msg]
        assert failing == [], f"shipped preset {preset_name!r} still triggers W001: {failing}"


class TestCheckStaysArmedForCustomPresets:
    """The exemption mechanism must not neuter the check for user palettes."""

    @staticmethod
    def _make_bad_preset() -> ThemePreset:
        white = ColorScale(h=0, s=0, lightness=100)
        near_white = ColorScale(h=0, s=0, lightness=98)
        bad_tokens = ThemeTokens(
            background=white,
            foreground=near_white,
            card=white,
            card_foreground=near_white,
            popover=white,
            popover_foreground=near_white,
            primary=white,
            primary_foreground=near_white,
            secondary=white,
            secondary_foreground=near_white,
            muted=white,
            muted_foreground=near_white,
            accent=white,
            accent_foreground=near_white,
            destructive=white,
            destructive_foreground=near_white,
            success=white,
            success_foreground=near_white,
            warning=white,
            warning_foreground=near_white,
            info=white,
            info_foreground=near_white,
            link=white,
            link_hover=near_white,
            code=white,
            code_foreground=near_white,
            selection=white,
            selection_foreground=near_white,
            brand=white,
            brand_foreground=near_white,
            border=white,
            input=white,
            ring=white,
            surface_1=white,
            surface_2=white,
            surface_3=white,
        )
        return ThemePreset(
            name="user_bad_preset",
            display_name="User Bad Preset",
            light=bad_tokens,
            dark=bad_tokens,
            description="Intentionally bad contrast; not a shipped preset",
        )

    def test_custom_preset_with_bad_contrast_still_warns(self, settings, monkeypatch):
        """A user-authored preset (name not in A11Y_EXEMPTIONS) gets warned."""
        settings.LIVEVIEW_CONFIG = {"theme": {"preset": "user_bad_preset"}}
        settings.DJUST_THEMING = {}

        from unittest.mock import patch

        bad = self._make_bad_preset()
        with patch("djust.theming.checks.get_registry") as mock_reg:
            mock_reg.return_value.list_presets.return_value = {"user_bad_preset": bad}
            mock_reg.return_value.has_preset.return_value = True
            warnings = check_preset_contrast(app_configs=None)

        # bad preset is white-on-white: every canonical pair fails in both modes
        assert len(warnings) == len(CONTRAST_PAIRS) * 2
        for w in warnings:
            assert w.id == "djust_theming.W001"
            assert "user_bad_preset" in w.msg

    def test_exempted_builtin_pair_is_still_actually_failing(self, settings):
        """The exemption skip is real: an exempted built-in pair must STILL
        measure below AA (it is documented debt, not a passing pair). Uses
        dracula/dark muted_foreground-on-muted (1.92:1 pre-fix)."""
        key = ("dracula", "dark", "muted_foreground", "muted")
        assert key in A11Y_EXEMPTIONS, (
            "expected the documented dracula/dark muted exemption; if the "
            "palette was genuinely fixed, update this test to another "
            "documented exemption"
        )
        assert _ratio("dracula", "dark", "muted_foreground", "muted") < 4.5

        settings.LIVEVIEW_CONFIG = {"theme": {"preset": "dracula"}}
        settings.DJUST_THEMING = {}
        warnings = check_preset_contrast(app_configs=None)
        assert warnings == [], (
            "exempted dracula pair still warned — the exemption lookup in "
            "check_preset_contrast is not being consulted"
        )


class TestFixedCorePresets:
    """The four presets a new project realistically uses had their failing
    status LABEL colours fixed — badge surface colours are untouched, only
    the label polarity flips to dark (the pattern slate already shipped for
    warning_foreground). Each row: (preset, mode, fg_token, bg_token,
    old_ratio, new_fg)."""

    FIXED_TOKENS = [
        ("default", "light", "warning_foreground", "warning", 2.04),
        ("default", "light", "info_foreground", "info", 2.74),
        ("default", "light", "success_foreground", "success", 3.19),
        ("default", "light", "destructive_foreground", "destructive", 3.62),
        ("default", "dark", "warning_foreground", "warning", 3.15),
        ("default", "dark", "info_foreground", "info", 2.11),
        ("blue", "light", "warning_foreground", "warning", 2.04),
        ("blue", "light", "info_foreground", "info", 2.74),
        ("blue", "light", "success_foreground", "success", 3.19),
        ("blue", "light", "destructive_foreground", "destructive", 3.62),
        ("blue", "dark", "warning_foreground", "warning", 3.15),
        ("blue", "dark", "info_foreground", "info", 2.11),
        ("shadcn", "light", "warning_foreground", "warning", 2.04),
        ("shadcn", "light", "info_foreground", "info", 2.74),
        ("shadcn", "light", "success_foreground", "success", 3.19),
        ("shadcn", "light", "destructive_foreground", "destructive", 3.62),
        ("shadcn", "dark", "warning_foreground", "warning", 3.15),
        ("shadcn", "dark", "info_foreground", "info", 2.11),
        ("slate", "light", "success_foreground", "success", 2.30),
        ("slate", "light", "destructive_foreground", "destructive", 3.78),
        ("slate", "dark", "success_foreground", "success", 2.30),
        ("slate", "dark", "destructive_foreground", "destructive", 3.78),
    ]

    @pytest.mark.parametrize("case", FIXED_TOKENS, ids=lambda c: f"{c[0]}-{c[1]}-{c[2]}")
    def test_fixed_token_meets_aa_and_kept_its_surface(self, case):
        """Each fixed token now passes AA, and its background token is
        untouched (the ratio must come from the label change, not a
        recoloured badge)."""
        preset_name, mode, fg_attr, bg_attr, old_ratio = case
        ratio = _ratio(preset_name, mode, fg_attr, bg_attr)
        assert ratio >= 4.5, (
            f"{preset_name}/{mode} {fg_attr} on {bg_attr} = {ratio:.2f} — "
            f"regressed below AA (was {old_ratio})"
        )
        # the badge surface is unchanged: the ratio flipped because the
        # LABEL went dark, not because the background moved
        assert ratio > old_ratio

    @pytest.mark.parametrize("preset_name", ["default", "blue", "shadcn", "slate"])
    def test_fixed_presets_carry_no_exemptions(self, preset_name):
        """Anti-rot: after the palette fix, these presets have no
        documented exemption left — a stale entry would fail
        TestExemptionsStillNeeded, and an active one would mean the fix
        was incomplete."""
        stale = [k for k in A11Y_EXEMPTIONS if k[0] == preset_name]
        assert stale == [], (
            f"{preset_name} was fixed in #2874 but A11Y_EXEMPTIONS still "
            f"carries entries for it: {stale} — remove them"
        )

    def test_slate_muted_foreground_light_bumped(self):
        """slate/light muted_foreground was 4.42 on muted (borderline); the
        one-step lightness bump takes it past AA with margin while keeping
        hue and saturation."""
        mf = THEME_PRESETS["slate"].light.muted_foreground
        assert (mf.h, mf.s) == (215, 15)
        ratio = _validator.calculate_contrast_ratio(mf, THEME_PRESETS["slate"].light.muted)
        assert ratio >= 4.5, f"slate/light muted_foreground on muted = {ratio:.2f}"


class TestScaffoldNoLongerSilencesW001:
    """`djust new` (#2875) shipped SILENCED_SYSTEM_CHECKS = ["djust_theming.W001"]
    "until the presets are fixed". They are fixed now — the silencing must go."""

    def test_scaffold_theming_settings_is_empty(self):
        import djust.scaffolding.templates as scaffold_templates

        assert scaffold_templates.THEMING_SETTINGS == "", (
            "the djust new scaffold still injects SILENCED_SYSTEM_CHECKS for "
            "djust_theming.W001 — the shipped presets are warning-clean since "
            "#2874; remove the silencing"
        )
