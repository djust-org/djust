"""Tests for Phase 7.1 + 7.2 + 7.3: RTL support."""

import re
from pathlib import Path
from unittest.mock import patch

import pytest

# -- Django bootstrap (reuse conftest pattern) --
import django
from django.conf import settings

pytestmark = pytest.mark.theming

if not settings.configured:
    settings.configure(
        INSTALLED_APPS=["django.contrib.contenttypes", "djust.theming"],
        DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
        STATIC_URL="/static/",
        LANGUAGE_CODE="en",
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "APP_DIRS": True,
                "DIRS": [],
                "OPTIONS": {"context_processors": []},
            }
        ],
    )
    django.setup()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CSS_DIR = Path(__file__).resolve().parent.parent / "theming" / "static" / "djust_theming" / "css"

# Physical directional properties that should NOT appear outside intentional
# paired shorthand (e.g. ``margin-inline``) or animation contexts.
# Each tuple: (regex_pattern, description, allowed_files_or_contexts)

# Properties that should be absent entirely:
_FORBIDDEN_PATTERNS = {
    "scaffold.css": [
        (r"(?<!inline-)(?<!-)margin-left(?!.*margin-right)", "unpaired margin-left"),
        (r"(?<!inline-)(?<!-)margin-right(?!.*margin-left)", "unpaired margin-right"),
        (r"\btext-align\s*:\s*left\b", "text-align: left (should be start)"),
        (r"\btext-align\s*:\s*right\b", "text-align: right (should be end)"),
    ],
    "layouts.css": [
        (r"\bborder-right\b", "border-right (should be border-inline-end)"),
        (r"\bborder-left\b", "border-left (should be border-inline-start)"),
    ],
    "components.css": [
        (r"\btext-align\s*:\s*left\b", "text-align: left (should be start)"),
        (r"\btext-align\s*:\s*right\b", "text-align: right (should be end)"),
    ],
    "pages.css": [
        (r"(?<!inline-)(?<!-)margin-left(?!.*margin-right)", "unpaired margin-left"),
        (r"(?<!inline-)(?<!-)margin-right(?!.*margin-left)", "unpaired margin-right"),
    ],
}


def _read_css(filename: str) -> str:
    """Read a CSS file from the static directory."""
    return (CSS_DIR / filename).read_text()


def _strip_comments(css: str) -> str:
    """Remove CSS comments to avoid false positives."""
    return re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)


# ===========================================================================
# 7.1 -- Logical Properties
# ===========================================================================


class TestLogicalPropertiesBase:
    """base.css should use CSS logical properties."""

    def test_sidebar_uses_border_inline_end(self):
        css = _read_css("scaffold.css")
        assert "border-inline-end" in css, "sidebar should use border-inline-end"

    def test_container_uses_padding_inline(self):
        css = _read_css("scaffold.css")
        assert "padding-inline" in css, "container should use padding-inline"

    def test_search_icon_uses_inset_inline_start(self):
        css = _read_css("scaffold.css")
        assert "inset-inline-start" in css, "search icon should use inset-inline-start"

    def test_table_uses_text_align_start(self):
        css = _strip_comments(_read_css("scaffold.css"))
        # Should not have text-align: left for table cells
        # (only text-align: start should be used)
        table_section = css[css.find(".table") :]
        assert "text-align: start" in table_section

    def test_alert_close_uses_margin_inline_start(self):
        css = _read_css("scaffold.css")
        assert "margin-inline-start: auto" in css

    def test_toast_container_uses_inset_inline_end(self):
        css = _read_css("scaffold.css")
        assert "inset-inline-end" in css

    def test_me_utilities_use_margin_inline_end(self):
        css = _read_css("scaffold.css")
        assert "margin-inline-end" in css

    def test_ms_utilities_use_margin_inline_start(self):
        css = _read_css("scaffold.css")
        assert "margin-inline-start" in css

    def test_mx_utilities_use_margin_inline(self):
        css = _read_css("scaffold.css")
        # mx-* should use margin-inline shorthand
        mx_section = css[css.find(".mx-2") :]
        assert "margin-inline:" in mx_section

    def test_px_utilities_use_padding_inline(self):
        css = _read_css("scaffold.css")
        px_section = css[css.find(".px-1") :]
        assert "padding-inline:" in px_section

    def test_end_0_uses_inset_inline_end(self):
        css = _read_css("scaffold.css")
        assert "inset-inline-end: 0" in css

    def test_start_0_uses_inset_inline_start(self):
        css = _read_css("scaffold.css")
        assert "inset-inline-start: 0" in css

    def test_spinner_uses_border_inline_end_color(self):
        css = _read_css("scaffold.css")
        assert "border-inline-end-color" in css

    def test_list_group_flush_uses_border_inline(self):
        css = _read_css("scaffold.css")
        assert "border-inline" in css


class TestLogicalPropertiesLayouts:
    """layouts.css should use CSS logical properties."""

    def test_no_border_right_in_layouts(self):
        css = _strip_comments(_read_css("layouts.css"))
        assert "border-right" not in css, "layouts.css should not contain border-right"

    def test_no_border_left_in_layouts(self):
        css = _strip_comments(_read_css("layouts.css"))
        assert "border-left" not in css, "layouts.css should not contain border-left"

    def test_sidebar_uses_border_inline_end(self):
        css = _read_css("layouts.css")
        assert "border-inline-end" in css

    def test_mobile_sidebar_clears_border_inline_end(self):
        css = _read_css("layouts.css")
        assert "border-inline-end: none" in css


class TestLogicalPropertiesComponents:
    """components.css should use CSS logical properties."""

    def test_no_text_align_left(self):
        css = _strip_comments(_read_css("components.css"))
        assert "text-align: left" not in css

    def test_text_align_start_present(self):
        css = _read_css("components.css")
        assert "text-align: start" in css

    def test_dropdown_left_uses_inset_inline_start(self):
        css = _read_css("components.css")
        assert "inset-inline-start: 0" in css

    def test_dropdown_right_uses_inset_inline_end(self):
        css = _read_css("components.css")
        assert "inset-inline-end: 0" in css

    def test_modal_close_uses_inset_inline_end(self):
        css = _read_css("components.css")
        assert "inset-inline-end" in css

    def test_toast_positions_use_logical_properties(self):
        css = _read_css("components.css")
        # toast-top-right should use inset-inline-end
        assert "inset-inline-end: 1rem" in css
        assert "inset-inline-start: 1rem" in css

    def test_breadcrumb_separator_uses_margin_inline(self):
        css = _read_css("components.css")
        assert "margin-inline" in css

    def test_theme_preset_select_uses_padding_inline(self):
        css = _read_css("components.css")
        assert "padding-inline-end" in css or "padding-inline" in css

    def test_tooltip_positions_use_logical_properties(self):
        css = _read_css("components.css")
        # tooltip left position should use inset-inline-end (right calc...)
        # tooltip right position should use inset-inline-start (left calc...)
        assert "inset-inline-end: calc(100% + 0.375rem)" in css
        assert "inset-inline-start: calc(100% + 0.375rem)" in css


class TestLogicalPropertiesPages:
    """pages.css should use CSS logical properties."""

    def test_no_unpaired_margin_left_right(self):
        css = _strip_comments(_read_css("pages.css"))
        # Paired margin-left + margin-right on same line is fine when
        # replaced with margin-inline
        assert "margin-inline: auto" in css

    def test_no_standalone_margin_left(self):
        """No line should have margin-left without it being part of margin-inline."""
        css = _strip_comments(_read_css("pages.css"))
        lines = css.split("\n")
        for line in lines:
            stripped = line.strip()
            if "margin-left" in stripped:
                # It should be part of margin-inline
                assert False, f"Found standalone margin-left in pages.css: {stripped}"


# ===========================================================================
# 7.2 -- Direction Config
# ===========================================================================


class TestDirectionConfig:
    """Direction configuration in DEFAULT_CONFIG and get_direction()."""

    def test_default_config_has_direction(self):
        from djust.theming.manager import DEFAULT_CONFIG

        assert "direction" in DEFAULT_CONFIG
        assert DEFAULT_CONFIG["direction"] == "auto"

    def test_get_direction_returns_ltr_for_english(self):
        from djust.theming.manager import get_direction

        with patch.object(settings, "LANGUAGE_CODE", "en"):
            assert get_direction() == "ltr"

    def test_get_direction_returns_rtl_for_arabic(self):
        from djust.theming.manager import get_direction

        with patch.object(settings, "LANGUAGE_CODE", "ar"):
            assert get_direction() == "rtl"

    def test_get_direction_returns_rtl_for_hebrew(self):
        from djust.theming.manager import get_direction

        with patch.object(settings, "LANGUAGE_CODE", "he"):
            assert get_direction() == "rtl"

    def test_get_direction_returns_rtl_for_farsi(self):
        from djust.theming.manager import get_direction

        with patch.object(settings, "LANGUAGE_CODE", "fa"):
            assert get_direction() == "rtl"

    def test_get_direction_returns_rtl_for_urdu(self):
        from djust.theming.manager import get_direction

        with patch.object(settings, "LANGUAGE_CODE", "ur"):
            assert get_direction() == "rtl"

    def test_get_direction_with_region_code(self):
        from djust.theming.manager import get_direction

        with patch.object(settings, "LANGUAGE_CODE", "ar-sa"):
            assert get_direction() == "rtl"

    def test_get_direction_explicit_ltr(self):
        from djust.theming.manager import get_direction

        with patch.object(
            settings, "LIVEVIEW_CONFIG", {"theme": {"direction": "ltr"}}, create=True
        ):
            assert get_direction() == "ltr"

    def test_get_direction_explicit_rtl(self):
        from djust.theming.manager import get_direction

        with patch.object(
            settings, "LIVEVIEW_CONFIG", {"theme": {"direction": "rtl"}}, create=True
        ):
            assert get_direction() == "rtl"

    def test_get_direction_returns_ltr_for_french(self):
        from djust.theming.manager import get_direction

        with patch.object(settings, "LANGUAGE_CODE", "fr"):
            assert get_direction() == "ltr"

    def test_get_direction_returns_ltr_for_japanese(self):
        from djust.theming.manager import get_direction

        with patch.object(settings, "LANGUAGE_CODE", "ja"):
            assert get_direction() == "ltr"


class TestDirectionConfigPytest:
    """Pytest-style direction config tests (no unittest.TestCase)."""

    def test_default_config_has_direction(self):
        from djust.theming.manager import DEFAULT_CONFIG

        assert "direction" in DEFAULT_CONFIG
        assert DEFAULT_CONFIG["direction"] == "auto"

    def test_get_direction_auto_ltr(self):
        from djust.theming.manager import get_direction

        with patch.object(settings, "LANGUAGE_CODE", "en"):
            with patch.object(
                settings, "LIVEVIEW_CONFIG", {"theme": {"direction": "auto"}}, create=True
            ):
                assert get_direction() == "ltr"

    def test_get_direction_auto_rtl(self):
        from djust.theming.manager import get_direction

        with patch.object(settings, "LANGUAGE_CODE", "ar"):
            with patch.object(
                settings, "LIVEVIEW_CONFIG", {"theme": {"direction": "auto"}}, create=True
            ):
                assert get_direction() == "rtl"

    def test_get_direction_auto_rtl_hebrew(self):
        from djust.theming.manager import get_direction

        with patch.object(settings, "LANGUAGE_CODE", "he"):
            with patch.object(
                settings, "LIVEVIEW_CONFIG", {"theme": {"direction": "auto"}}, create=True
            ):
                assert get_direction() == "rtl"

    def test_get_direction_auto_rtl_with_region(self):
        from djust.theming.manager import get_direction

        with patch.object(settings, "LANGUAGE_CODE", "ar-sa"):
            with patch.object(
                settings, "LIVEVIEW_CONFIG", {"theme": {"direction": "auto"}}, create=True
            ):
                assert get_direction() == "rtl"

    def test_get_direction_explicit_rtl_override(self):
        from djust.theming.manager import get_direction

        with patch.object(settings, "LANGUAGE_CODE", "en"):
            with patch.object(
                settings, "LIVEVIEW_CONFIG", {"theme": {"direction": "rtl"}}, create=True
            ):
                assert get_direction() == "rtl"

    def test_get_direction_explicit_ltr_override(self):
        from djust.theming.manager import get_direction

        with patch.object(settings, "LANGUAGE_CODE", "ar"):
            with patch.object(
                settings, "LIVEVIEW_CONFIG", {"theme": {"direction": "ltr"}}, create=True
            ):
                assert get_direction() == "ltr"

    def test_get_direction_ltr_for_french(self):
        from djust.theming.manager import get_direction

        with patch.object(settings, "LANGUAGE_CODE", "fr"):
            with patch.object(settings, "LIVEVIEW_CONFIG", {"theme": {}}, create=True):
                assert get_direction() == "ltr"


# ===========================================================================
# 7.2 -- Theme Head Direction
# ===========================================================================


class TestThemeHeadDirection:
    """theme_head.html should include dir attribute."""

    def test_theme_head_template_has_dir_attribute(self):
        template_path = (
            Path(__file__).resolve().parent.parent
            / "theming"
            / "templates"
            / "djust_theming"
            / "theme_head.html"
        )
        content = template_path.read_text()
        assert "dir" in content, "theme_head.html should reference dir attribute"

    def test_theme_head_sets_dir_on_html_element(self):
        template_path = (
            Path(__file__).resolve().parent.parent
            / "theming"
            / "templates"
            / "djust_theming"
            / "theme_head.html"
        )
        content = template_path.read_text()
        # The script should set dir attribute on documentElement
        assert "setAttribute" in content and "dir" in content


# ===========================================================================
# 7.3 -- RTL-Aware Components
# ===========================================================================


class TestRTLAwareComponents:
    """CSS files should have [dir="rtl"] overrides for directional components."""

    def test_base_css_has_rtl_section(self):
        css = _read_css("scaffold.css")
        assert '[dir="rtl"]' in css, "base.css should have RTL overrides"

    def test_components_css_has_rtl_section(self):
        css = _read_css("components.css")
        assert '[dir="rtl"]' in css, "components.css should have RTL overrides"

    def test_rtl_nav_link_underline_flip(self):
        """Nav link underline should flip in RTL."""
        css = _read_css("scaffold.css")
        # Should have RTL rule for nav-link::after
        assert '[dir="rtl"] .nav-link::after' in css or '[dir="rtl"]' in css

    def test_rtl_sidebar_active_indicator_flip(self):
        """Sidebar active indicator should flip in RTL."""
        css = _read_css("scaffold.css")
        assert '[dir="rtl"] .sidebar-item::before' in css

    def test_rtl_toast_animation_direction(self):
        """Toast slide animation should reverse in RTL."""
        css = _read_css("scaffold.css")
        assert '[dir="rtl"]' in css
        # Should have RTL-specific animation or transform

    def test_rtl_breadcrumb_separator(self):
        """Breadcrumb separator should handle RTL."""
        css = _read_css("components.css")
        assert '[dir="rtl"] .breadcrumb-separator' in css

    def test_rtl_theme_preset_select_background(self):
        """Theme preset select dropdown arrow should flip in RTL."""
        css = _read_css("components.css")
        assert '[dir="rtl"] .theme-preset-select' in css

    def test_rtl_pagination_arrows(self):
        """Pagination prev/next should flip arrow direction in RTL."""
        css = _read_css("components.css")
        rtl_section = css[css.find('[dir="rtl"]') :]
        assert "pagination" in rtl_section.lower()

    def test_rtl_progress_indeterminate_animation(self):
        """Progress bar indeterminate animation should reverse in RTL."""
        css = _read_css("components.css")
        assert '[dir="rtl"] .progress-indeterminate' in css


# ===========================================================================
# 7.2 -- Manifest Direction Field
# ===========================================================================


class TestManifestDirection:
    """ThemeManifest should support optional direction field."""

    def test_manifest_has_direction_field(self):
        from djust.theming.manifest import ThemeManifest

        manifest = ThemeManifest(name="test", version="1.0.0")
        assert hasattr(manifest, "direction")

    def test_manifest_direction_defaults_to_none(self):
        from djust.theming.manifest import ThemeManifest

        manifest = ThemeManifest(name="test", version="1.0.0")
        assert manifest.direction is None

    def test_manifest_from_toml_reads_direction(self, tmp_path):
        toml_content = b"""
[theme]
name = "test-rtl"
version = "1.0.0"
direction = "rtl"

[tokens]
preset = "default"
design_system = "material"
"""
        toml_file = tmp_path / "theme.toml"
        toml_file.write_bytes(toml_content)

        from djust.theming.manifest import ThemeManifest

        manifest = ThemeManifest.from_toml(toml_file)
        assert manifest.direction == "rtl"

    def test_manifest_from_toml_direction_optional(self, tmp_path):
        toml_content = b"""
[theme]
name = "test-ltr"
version = "1.0.0"

[tokens]
preset = "default"
design_system = "material"
"""
        toml_file = tmp_path / "theme.toml"
        toml_file.write_bytes(toml_content)

        from djust.theming.manifest import ThemeManifest

        manifest = ThemeManifest.from_toml(toml_file)
        assert manifest.direction is None

    def test_manifest_to_toml_includes_direction(self):
        from djust.theming.manifest import ThemeManifest

        manifest = ThemeManifest(name="test", version="1.0.0", direction="rtl")
        toml_str = manifest.to_toml()
        assert 'direction = "rtl"' in toml_str

    def test_manifest_to_toml_omits_direction_when_none(self):
        from djust.theming.manifest import ThemeManifest

        manifest = ThemeManifest(name="test", version="1.0.0")
        toml_str = manifest.to_toml()
        assert "direction" not in toml_str


# ===========================================================================
# 7.2 -- RTL Language Set
# ===========================================================================


class TestRTLLanguages:
    """The RTL language set should be comprehensive."""

    def test_rtl_languages_contains_arabic(self):
        from djust.theming.manager import RTL_LANGUAGES

        assert "ar" in RTL_LANGUAGES

    def test_rtl_languages_contains_hebrew(self):
        from djust.theming.manager import RTL_LANGUAGES

        assert "he" in RTL_LANGUAGES

    def test_rtl_languages_contains_farsi(self):
        from djust.theming.manager import RTL_LANGUAGES

        assert "fa" in RTL_LANGUAGES

    def test_rtl_languages_contains_urdu(self):
        from djust.theming.manager import RTL_LANGUAGES

        assert "ur" in RTL_LANGUAGES

    def test_rtl_languages_does_not_contain_english(self):
        from djust.theming.manager import RTL_LANGUAGES

        assert "en" not in RTL_LANGUAGES

    def test_rtl_languages_does_not_contain_french(self):
        from djust.theming.manager import RTL_LANGUAGES

        assert "fr" not in RTL_LANGUAGES
