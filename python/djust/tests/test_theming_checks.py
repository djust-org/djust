"""Tests for Django system checks (WCAG AA contrast validation)."""

import django
from django.conf import settings

if not settings.configured:
    settings.configure(
        INSTALLED_APPS=["django.contrib.contenttypes", "djust.theming"],
        DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
    )
    django.setup()

from unittest.mock import patch

import pytest

from djust.theming.accessibility import AccessibilityValidator
from djust.theming.checks import CONTRAST_PAIRS, check_preset_contrast
from djust.theming.presets import ColorScale, ThemePreset, ThemeTokens

pytestmark = pytest.mark.theming


class TestHslToRgbBugFix:
    """Verify the color_scale.l -> color_scale.lightness fix."""

    def test_hsl_to_rgb_uses_lightness_attribute(self):
        """AccessibilityValidator.hsl_to_rgb accesses .lightness, not .l."""
        validator = AccessibilityValidator()
        color = ColorScale(h=0, s=100, lightness=50)  # pure red
        r, g, b = validator.hsl_to_rgb(color)
        # Pure red in HSL(0, 100%, 50%) -> RGB(1, 0, 0)
        assert abs(r - 1.0) < 0.01
        assert abs(g - 0.0) < 0.01
        assert abs(b - 0.0) < 0.01

    def test_hsl_to_rgb_white(self):
        """White (0, 0%, 100%) should yield (1, 1, 1)."""
        validator = AccessibilityValidator()
        color = ColorScale(h=0, s=0, lightness=100)
        r, g, b = validator.hsl_to_rgb(color)
        assert abs(r - 1.0) < 0.01
        assert abs(g - 1.0) < 0.01
        assert abs(b - 1.0) < 0.01

    def test_hsl_to_rgb_black(self):
        """Black (0, 0%, 0%) should yield (0, 0, 0)."""
        validator = AccessibilityValidator()
        color = ColorScale(h=0, s=0, lightness=0)
        r, g, b = validator.hsl_to_rgb(color)
        assert abs(r) < 0.01
        assert abs(g) < 0.01
        assert abs(b) < 0.01


class TestCheckPresetContrast:
    """Tests for the check_preset_contrast system check.

    #1005 — these tests exercise the loop body (CONTRAST_PAIRS × mode)
    and need the "all" scope so a single fixture preset is checked
    regardless of `LIVEVIEW_CONFIG["theme"]["preset"]`. Scope-default
    behavior is locked separately in `TestCheckPresetContrastScope`.
    """

    @pytest.fixture(autouse=True)
    def _opt_into_all_scope(self, settings):
        """Opt into the exhaustive-sweep scope for these tests so a
        fixture-injected preset gets contrast-checked even when its
        name isn't the configured `LIVEVIEW_CONFIG.theme.preset`."""
        settings.DJUST_THEMING = {"contrast_check_scope": "all"}

    def test_check_runs_without_error(self):
        """The system check executes against all built-in presets without crashing."""
        warnings = check_preset_contrast(app_configs=None)
        assert isinstance(warnings, list)

    def test_all_warnings_have_correct_id(self):
        """Every warning emitted uses id='djust_theming.W001'."""
        warnings = check_preset_contrast(app_configs=None)
        for w in warnings:
            assert w.id == "djust_theming.W001"

    def test_bad_preset_triggers_warning(self):
        """A preset with near-identical fg/bg colors triggers W001."""
        # Create a deliberately bad preset: white-on-white
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
            link_hover=white,
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
        bad_preset = ThemePreset(
            name="bad",
            display_name="Bad",
            light=bad_tokens,
            dark=bad_tokens,
            description="Intentionally bad contrast",
        )

        patched_presets = {"bad": bad_preset}
        with patch("djust.theming.checks.get_registry") as mock_reg:
            mock_reg.return_value.list_presets.return_value = patched_presets
            warnings = check_preset_contrast(app_configs=None)

        # 12 pairs x 2 modes = 24 warnings expected
        assert len(warnings) == len(CONTRAST_PAIRS) * 2
        for w in warnings:
            assert w.id == "djust_theming.W001"
            assert "bad" in w.msg

    def test_good_preset_no_warnings(self):
        """A preset with high-contrast colors produces no warnings."""
        black = ColorScale(h=0, s=0, lightness=0)
        white = ColorScale(h=0, s=0, lightness=100)
        good_tokens = ThemeTokens(
            background=white,
            foreground=black,
            card=white,
            card_foreground=black,
            popover=white,
            popover_foreground=black,
            primary=black,
            primary_foreground=white,
            secondary=black,
            secondary_foreground=white,
            muted=white,
            muted_foreground=black,
            accent=black,
            accent_foreground=white,
            destructive=black,
            destructive_foreground=white,
            success=black,
            success_foreground=white,
            warning=black,
            warning_foreground=white,
            info=black,
            info_foreground=white,
            link=black,
            link_hover=black,
            code=white,
            code_foreground=black,
            selection=black,
            selection_foreground=white,
            brand=black,
            brand_foreground=white,
            border=black,
            input=black,
            ring=black,
            surface_1=black,
            surface_2=black,
            surface_3=black,
        )
        good_preset = ThemePreset(
            name="good",
            display_name="Good",
            light=good_tokens,
            dark=good_tokens,
            description="High contrast",
        )

        patched_presets = {"good": good_preset}
        with patch("djust.theming.checks.get_registry") as mock_reg:
            mock_reg.return_value.list_presets.return_value = patched_presets
            warnings = check_preset_contrast(app_configs=None)

        assert len(warnings) == 0

    def test_empty_presets_no_warnings(self):
        """When the registry has no presets, no warnings are produced."""
        with patch("djust.theming.checks.get_registry") as mock_reg:
            mock_reg.return_value.list_presets.return_value = {}
            warnings = check_preset_contrast(app_configs=None)

        assert warnings == []

    def test_warning_message_contains_preset_name_and_mode(self):
        """Warning messages include the preset name and mode for actionability."""
        warnings = check_preset_contrast(app_configs=None)
        if warnings:
            w = warnings[0]
            # Should mention a real preset name and light/dark
            assert "light" in w.msg or "dark" in w.msg
            assert "contrast ratio" in w.msg


# ---------------------------------------------------------------------------
# #1005 — W001 scope (active preset only by default, all if opted-in)
# ---------------------------------------------------------------------------


class TestCheckPresetContrastScope:
    """Tests for the #1005 scope rule: W001 only fires for the active preset
    by default, with an opt-in for theme-pack authors who want the full
    exhaustive sweep.

    These tests intentionally do NOT inherit the `_opt_into_all_scope`
    fixture from the broader behavior class — they exercise the
    scope-decision path itself and need the default ("active") to hold."""

    def _make_bad_preset(self, name: str) -> ThemePreset:
        """Return a preset whose every contrast pair is white-on-near-white
        (~0 contrast). Same fixture shape as the broader behavior tests."""
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
            link_hover=white,
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
            name=name,
            display_name=name,
            light=bad_tokens,
            dark=bad_tokens,
            description="Intentionally bad contrast",
        )

    def test_default_scope_is_active_only(self, settings):
        """#1005 default: W001 only fires for the configured active
        preset. Two bad presets registered, only ONE configured as
        active — only that one's warnings appear."""
        bad_active = self._make_bad_preset("active-bad")
        bad_inactive = self._make_bad_preset("inactive-bad")
        registry_dict = {"active-bad": bad_active, "inactive-bad": bad_inactive}

        # No DJUST_THEMING setting → default scope ("active")
        if hasattr(settings, "DJUST_THEMING"):
            del settings.DJUST_THEMING

        with (
            patch("djust.theming.checks.get_registry") as mock_reg,
            patch("djust.theming.checks.get_theme_config") as mock_cfg,
        ):
            mock_reg.return_value.list_presets.return_value = registry_dict
            mock_reg.return_value.has_preset.side_effect = lambda n: n in registry_dict
            mock_cfg.return_value = {"preset": "active-bad"}
            warnings = check_preset_contrast(app_configs=None)

        # Only `active-bad` should produce warnings (inactive-bad is
        # registered but not the configured active preset).
        assert all("active-bad" in w.msg for w in warnings)
        assert not any("inactive-bad" in w.msg for w in warnings)
        # 13 contrast pairs × 2 modes = 26 warnings for the bad active preset
        # (verifying we still get a full sweep on the active preset).
        assert len(warnings) == len(CONTRAST_PAIRS) * 2

    def test_opt_in_all_scope_checks_every_preset(self, settings):
        """`DJUST_THEMING = {"contrast_check_scope": "all"}` restores
        the v0.7.2-and-earlier behavior of checking every registered
        preset. Opt-in for theme-pack authors who explicitly want
        exhaustive validation."""
        bad_a = self._make_bad_preset("pack-a")
        bad_b = self._make_bad_preset("pack-b")
        registry_dict = {"pack-a": bad_a, "pack-b": bad_b}

        settings.DJUST_THEMING = {"contrast_check_scope": "all"}

        with (
            patch("djust.theming.checks.get_registry") as mock_reg,
            patch("djust.theming.checks.get_theme_config") as mock_cfg,
        ):
            mock_reg.return_value.list_presets.return_value = registry_dict
            mock_reg.return_value.has_preset.side_effect = lambda n: n in registry_dict
            mock_cfg.return_value = {"preset": "pack-a"}
            warnings = check_preset_contrast(app_configs=None)

        # Both presets are checked → 2 presets × 13 pairs × 2 modes = 52
        assert len(warnings) == 2 * len(CONTRAST_PAIRS) * 2
        assert any("pack-a" in w.msg for w in warnings)
        assert any("pack-b" in w.msg for w in warnings)

    def test_active_scope_when_active_preset_missing_yields_no_warnings(self, settings):
        """If `LIVEVIEW_CONFIG.theme.preset` names a preset that isn't
        registered, the contrast check produces no warnings —
        `check_preset_valid` already fires E002 for the misconfiguration,
        so we don't double-warn."""
        bad_other = self._make_bad_preset("registered-but-inactive")
        registry_dict = {"registered-but-inactive": bad_other}

        if hasattr(settings, "DJUST_THEMING"):
            del settings.DJUST_THEMING

        with (
            patch("djust.theming.checks.get_registry") as mock_reg,
            patch("djust.theming.checks.get_theme_config") as mock_cfg,
        ):
            mock_reg.return_value.list_presets.return_value = registry_dict
            mock_reg.return_value.has_preset.side_effect = lambda n: n in registry_dict
            mock_cfg.return_value = {"preset": "missing-preset-name"}
            warnings = check_preset_contrast(app_configs=None)

        assert warnings == []

    def test_unknown_scope_value_falls_back_to_active(self, settings):
        """Unknown values for `contrast_check_scope` fall back to the
        signal-preserving default ("active"), not "all". Locks the
        safe-default contract."""
        bad_a = self._make_bad_preset("active")
        bad_b = self._make_bad_preset("other")
        registry_dict = {"active": bad_a, "other": bad_b}

        settings.DJUST_THEMING = {"contrast_check_scope": "garbage-value"}

        with (
            patch("djust.theming.checks.get_registry") as mock_reg,
            patch("djust.theming.checks.get_theme_config") as mock_cfg,
        ):
            mock_reg.return_value.list_presets.return_value = registry_dict
            mock_reg.return_value.has_preset.side_effect = lambda n: n in registry_dict
            mock_cfg.return_value = {"preset": "active"}
            warnings = check_preset_contrast(app_configs=None)

        # Only "active" is checked — unknown scope falls back to active.
        assert all("active" in w.msg for w in warnings)
        assert not any('"other"' in w.msg for w in warnings)


class TestContrastPairsCompleteness:
    """Verify CONTRAST_PAIRS covers all foreground/background token pairs."""

    def test_all_fg_bg_pairs_covered(self):
        """CONTRAST_PAIRS should cover all *_foreground/*  semantic pairs."""
        expected_pairs = {
            "foreground",
            "card_foreground",
            "popover_foreground",
            "primary_foreground",
            "secondary_foreground",
            "muted_foreground",
            "accent_foreground",
            "destructive_foreground",
            "success_foreground",
            "warning_foreground",
            "info_foreground",
            "code_foreground",
            "brand_foreground",
        }
        actual_fg_attrs = {pair[0] for pair in CONTRAST_PAIRS}
        assert actual_fg_attrs == expected_pairs


class TestCheckContextProcessor:
    """Tests for E001: missing context_processor check."""

    def test_e001_fires_when_context_processor_missing(self):
        """E001 fires when no TEMPLATES backend has theme_context."""
        from djust.theming.checks import check_context_processor

        templates = [
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "OPTIONS": {"context_processors": ["django.template.context_processors.request"]},
            }
        ]
        with patch.object(settings, "TEMPLATES", templates):
            errors = check_context_processor(app_configs=None)
        assert len(errors) == 1
        assert errors[0].id == "djust_theming.E001"

    def test_e001_passes_when_context_processor_present(self):
        """E001 passes when theme_context is in context_processors."""
        from djust.theming.checks import check_context_processor

        templates = [
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "OPTIONS": {
                    "context_processors": [
                        "django.template.context_processors.request",
                        "djust.theming.context_processors.theme_context",
                    ],
                },
            }
        ]
        with patch.object(settings, "TEMPLATES", templates):
            errors = check_context_processor(app_configs=None)
        assert len(errors) == 0

    def test_e001_passes_with_multiple_backends(self):
        """E001 passes if at least one backend has theme_context."""
        from djust.theming.checks import check_context_processor

        templates = [
            {
                "BACKEND": "django.template.backends.jinja2.Jinja2",
                "OPTIONS": {"context_processors": []},
            },
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "OPTIONS": {
                    "context_processors": [
                        "djust.theming.context_processors.theme_context",
                    ],
                },
            },
        ]
        with patch.object(settings, "TEMPLATES", templates):
            errors = check_context_processor(app_configs=None)
        assert len(errors) == 0

    def test_e001_fires_when_templates_empty(self):
        """E001 fires when TEMPLATES is an empty list."""
        from djust.theming.checks import check_context_processor

        with patch.object(settings, "TEMPLATES", []):
            errors = check_context_processor(app_configs=None)
        assert len(errors) == 1
        assert errors[0].id == "djust_theming.E001"


class TestCheckPresetValid:
    """Tests for E002: invalid preset name check."""

    def test_e002_fires_on_invalid_preset(self):
        """E002 fires when preset is not in THEME_PRESETS."""
        from djust.theming.checks import check_preset_valid

        with patch.object(
            settings,
            "LIVEVIEW_CONFIG",
            {"theme": {"preset": "nonexistent"}},
            create=True,
        ):
            errors = check_preset_valid(app_configs=None)
        assert len(errors) == 1
        assert errors[0].id == "djust_theming.E002"
        assert "nonexistent" in errors[0].msg

    def test_e002_passes_on_valid_preset(self):
        """E002 passes when preset is a valid THEME_PRESETS key."""
        from djust.theming.checks import check_preset_valid

        with patch.object(
            settings,
            "LIVEVIEW_CONFIG",
            {"theme": {"preset": "default"}},
            create=True,
        ):
            errors = check_preset_valid(app_configs=None)
        assert len(errors) == 0

    def test_e002_passes_on_default_config(self):
        """E002 passes when no LIVEVIEW_CONFIG is set (defaults are valid)."""
        from djust.theming.checks import check_preset_valid

        with patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True):
            errors = check_preset_valid(app_configs=None)
        assert len(errors) == 0

    def test_e002_message_lists_valid_presets(self):
        """E002 hint lists some valid preset names."""
        from djust.theming.checks import check_preset_valid

        with patch.object(
            settings,
            "LIVEVIEW_CONFIG",
            {"theme": {"preset": "bogus"}},
            create=True,
        ):
            errors = check_preset_valid(app_configs=None)
        assert len(errors) == 1
        assert "default" in errors[0].hint


class TestCheckDesignSystemValid:
    """Tests for E003: invalid design system (theme) name check."""

    def test_e003_fires_on_invalid_design_system(self):
        """E003 fires when theme is not in DESIGN_SYSTEMS."""
        from djust.theming.checks import check_design_system_valid

        with patch.object(
            settings,
            "LIVEVIEW_CONFIG",
            {"theme": {"theme": "nonexistent"}},
            create=True,
        ):
            errors = check_design_system_valid(app_configs=None)
        assert len(errors) == 1
        assert errors[0].id == "djust_theming.E003"
        assert "nonexistent" in errors[0].msg

    def test_e003_passes_on_valid_design_system(self):
        """E003 passes when theme is a valid DESIGN_SYSTEMS key."""
        from djust.theming.checks import check_design_system_valid

        with patch.object(
            settings,
            "LIVEVIEW_CONFIG",
            {"theme": {"theme": "material"}},
            create=True,
        ):
            errors = check_design_system_valid(app_configs=None)
        assert len(errors) == 0

    def test_e003_passes_on_default_config(self):
        """E003 passes when no LIVEVIEW_CONFIG is set (defaults are valid)."""
        from djust.theming.checks import check_design_system_valid

        with patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True):
            errors = check_design_system_valid(app_configs=None)
        assert len(errors) == 0

    def test_e003_message_lists_valid_systems(self):
        """E003 hint lists some valid design system names."""
        from djust.theming.checks import check_design_system_valid

        with patch.object(
            settings,
            "LIVEVIEW_CONFIG",
            {"theme": {"theme": "bogus"}},
            create=True,
        ):
            errors = check_design_system_valid(app_configs=None)
        assert len(errors) == 1
        assert "material" in errors[0].hint


if __name__ == "__main__":
    pytest.main([__file__])
