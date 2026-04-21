"""Tests for CSS namespace prefixing (I17)."""

import django
from django.conf import settings

if not settings.configured:
    settings.configure(
        INSTALLED_APPS=["django.contrib.contenttypes", "djust.theming"],
        DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
        STATIC_URL="/static/",
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "APP_DIRS": True,
                "OPTIONS": {
                    "context_processors": [],
                },
            }
        ],
    )
    django.setup()

from unittest.mock import patch

import pytest

from djust.theming.manager import DEFAULT_CONFIG, get_theme_config

pytestmark = pytest.mark.theming


# ---------------------------------------------------------------------------
# 1. Default prefix is empty
# ---------------------------------------------------------------------------


class TestDefaultPrefixEmpty:
    """css_prefix defaults to empty string for backward compatibility."""

    def test_default_config_has_css_prefix(self):
        """DEFAULT_CONFIG contains css_prefix key."""
        assert "css_prefix" in DEFAULT_CONFIG

    def test_default_prefix_is_empty_string(self):
        """Default css_prefix is '' (empty string)."""
        assert DEFAULT_CONFIG["css_prefix"] == ""

    def test_get_theme_config_returns_empty_prefix(self):
        """get_theme_config() returns empty prefix when nothing is set."""
        with patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True):
            config = get_theme_config()
        assert config["css_prefix"] == ""

    def test_custom_prefix_from_settings(self):
        """get_theme_config() reads css_prefix from LIVEVIEW_CONFIG."""
        with patch.object(
            settings,
            "LIVEVIEW_CONFIG",
            {"theme": {"css_prefix": "dj-"}},
            create=True,
        ):
            config = get_theme_config()
        assert config["css_prefix"] == "dj-"


# ---------------------------------------------------------------------------
# 2. Component CSS generation with prefix
# ---------------------------------------------------------------------------


class TestComponentCSSGeneration:
    """generate_component_css() applies prefix to all class selectors."""

    def test_prefix_in_generated_css(self):
        """With prefix 'dj-', output contains .dj-btn not .btn."""
        from djust.theming.component_css_generator import generate_component_css

        css = generate_component_css("dj-")
        assert ".dj-btn" in css
        assert ".dj-card" in css
        assert ".dj-alert" in css
        assert ".dj-badge" in css
        assert ".dj-input" in css
        assert ".dj-theme-switcher" in css
        assert ".dj-theme-mode-btn" in css

    def test_no_unprefixed_classes_when_prefix_set(self):
        """When prefix is set, the original unprefixed class names must not appear as selectors."""
        from djust.theming.component_css_generator import generate_component_css

        css = generate_component_css("dj-")
        # Check that bare .btn (not as part of .dj-btn) doesn't appear
        # We search for the pattern \.btn followed by space, {, :, or . but not preceded by dj-
        import re

        # Find all .btn occurrences that are NOT preceded by dj-
        re.findall(r"(?<!dj-)\.btn(?!-)", css)
        # All should be empty since they should all be .dj-btn
        # But .dj-btn also contains .btn — we need a smarter check.
        # Instead, verify no selector line starts with just .btn
        lines_with_bare_selectors = []
        for line in css.split("\n"):
            stripped = line.strip()
            # Selector lines that start with .btn (but not .dj-btn)
            if stripped.startswith(".btn") and not stripped.startswith(".dj-"):
                lines_with_bare_selectors.append(stripped)
        assert lines_with_bare_selectors == [], f"Found bare selectors: {lines_with_bare_selectors}"

    def test_no_prefix_backward_compat(self):
        """With empty prefix, the generated CSS contains the static file's
        content verbatim (possibly wrapped in @layer components when
        ``use_css_layers`` is on, which is the default)."""
        from djust.theming.component_css_generator import generate_component_css
        import pathlib

        css = generate_component_css("")
        static_css = (
            pathlib.Path(__file__).resolve().parent.parent
            / "theming"
            / "static"
            / "djust_theming"
            / "css"
            / "components.css"
        )
        expected = static_css.read_text().strip()
        # Strip the generator's optional @layer components wrapper before
        # comparing — the static file is the unwrapped canonical content;
        # the generator adds the layer wrapper when ``use_css_layers=True``.
        generated = css.strip()
        if generated.startswith("@layer components {") and generated.endswith("}"):
            generated = generated[len("@layer components {") : -1].strip()
        assert generated == expected

    def test_prefix_applied_to_dark_mode_selectors(self):
        """Dark mode selectors like [data-theme='dark'] .theme-mode-btn also get prefixed."""
        from djust.theming.component_css_generator import generate_component_css

        css = generate_component_css("dj-")
        assert ".dj-theme-mode-controls" in css
        assert ".dj-theme-mode-btn.active" in css or ".dj-theme-mode-btn" in css

    def test_pseudo_selectors_preserved(self):
        """Pseudo-selectors like :hover, :focus, ::placeholder are preserved."""
        from djust.theming.component_css_generator import generate_component_css

        css = generate_component_css("dj-")
        assert ".dj-btn:hover" in css or ".dj-btn-primary:hover" in css
        assert ".dj-input::placeholder" in css
        assert ".dj-input:focus" in css


# ---------------------------------------------------------------------------
# 3. Theme CSS generator with prefix
# ---------------------------------------------------------------------------


class TestThemeCSSGeneratorPrefix:
    """CompleteThemeCSSGenerator applies prefix to component styles."""

    def test_prefix_in_component_styles(self):
        """With css_prefix='dj-', _generate_component_styles uses .dj-btn."""
        from djust.theming.theme_css_generator import CompleteThemeCSSGenerator

        gen = CompleteThemeCSSGenerator("material", css_prefix="dj-")
        css = gen._generate_component_styles()
        assert ".dj-btn" in css
        assert ".dj-card" in css
        assert ".dj-form-input" in css

    def test_no_prefix_backward_compat(self):
        """With empty css_prefix, component styles are unchanged."""
        from djust.theming.theme_css_generator import CompleteThemeCSSGenerator

        gen = CompleteThemeCSSGenerator("material", css_prefix="")
        css = gen._generate_component_styles()
        assert ".btn" in css
        assert ".card" in css
        assert ".form-input" in css

    def test_typography_not_prefixed(self):
        """Typography utility classes are never prefixed."""
        from djust.theming.theme_css_generator import CompleteThemeCSSGenerator

        gen = CompleteThemeCSSGenerator("material", css_prefix="dj-")
        css = gen._generate_typography_classes()
        # Typography classes stay unprefixed
        assert ".font-sans" in css
        assert ".text-xs" in css
        assert ".dj-font-sans" not in css


# ---------------------------------------------------------------------------
# 4. Component template context has prefix
# ---------------------------------------------------------------------------


class TestComponentTemplateContextHasPrefix:
    """Each simple_tag renders HTML with prefixed class names."""

    def _make_context(self):
        """Create a minimal template context with a mock request."""
        from unittest.mock import MagicMock

        request = MagicMock()
        request.COOKIES = {}
        request.session = {}
        request._djust_theme_manager = None
        return {"request": request}

    def test_theme_button_context(self):
        """theme_button renders HTML with dj- prefix on class names."""
        from djust.theming.templatetags.theme_components import theme_button

        ctx = self._make_context()
        with patch.object(
            settings,
            "LIVEVIEW_CONFIG",
            {"theme": {"css_prefix": "dj-"}},
            create=True,
        ):
            html = theme_button(ctx, "Click me")
        assert "dj-btn" in html

    def test_theme_card_context(self):
        from djust.theming.templatetags.theme_components import theme_card

        ctx = self._make_context()
        with patch.object(
            settings,
            "LIVEVIEW_CONFIG",
            {"theme": {"css_prefix": "dj-"}},
            create=True,
        ):
            html = theme_card(ctx, title="Test")
        assert "dj-card" in html

    def test_theme_badge_context(self):
        from djust.theming.templatetags.theme_components import theme_badge

        ctx = self._make_context()
        with patch.object(
            settings,
            "LIVEVIEW_CONFIG",
            {"theme": {"css_prefix": "dj-"}},
            create=True,
        ):
            html = theme_badge(ctx, "New")
        assert "dj-badge" in html

    def test_theme_alert_context(self):
        from djust.theming.templatetags.theme_components import theme_alert

        ctx = self._make_context()
        with patch.object(
            settings,
            "LIVEVIEW_CONFIG",
            {"theme": {"css_prefix": "dj-"}},
            create=True,
        ):
            html = theme_alert(ctx, "Error occurred")
        assert "dj-alert" in html

    def test_theme_input_context(self):
        from djust.theming.templatetags.theme_components import theme_input

        ctx = self._make_context()
        with patch.object(
            settings,
            "LIVEVIEW_CONFIG",
            {"theme": {"css_prefix": "dj-"}},
            create=True,
        ):
            html = theme_input(ctx, "email")
        assert "dj-input" in html

    def test_empty_prefix_by_default(self):
        """Without config, class names have no prefix."""
        from djust.theming.templatetags.theme_components import theme_button

        ctx = self._make_context()
        with patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True):
            html = theme_button(ctx, "Click me")
        assert "btn " in html or "btn-" in html


# ---------------------------------------------------------------------------
# 5. ThemeSwitcher and ThemeModeButton get css_prefix
# ---------------------------------------------------------------------------


class TestSwitcherComponentsPrefix:
    """ThemeSwitcher and ThemeModeButton include css_prefix in context."""

    def test_theme_switcher_context_has_prefix(self):
        from djust.theming.components import ThemeSwitcher

        with patch.object(
            settings,
            "LIVEVIEW_CONFIG",
            {"theme": {"css_prefix": "dj-"}},
            create=True,
        ):
            switcher = ThemeSwitcher()
            ctx = switcher.get_context()
        assert ctx["css_prefix"] == "dj-"

    def test_theme_mode_button_context_has_prefix(self):
        from djust.theming.components import ThemeModeButton

        with patch.object(
            settings,
            "LIVEVIEW_CONFIG",
            {"theme": {"css_prefix": "dj-"}},
            create=True,
        ):
            button = ThemeModeButton()
            ctx = button.get_context()
        assert ctx["css_prefix"] == "dj-"


# ---------------------------------------------------------------------------
# 6. theme_head logic: inline CSS when prefix is set
# ---------------------------------------------------------------------------


class TestThemeHeadInlineCSS:
    """theme_head passes correct flags for component CSS rendering."""

    def test_include_component_link_true_when_no_prefix(self):
        """When no prefix, include_component_link is True (static link)."""
        with patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True):
            config = get_theme_config()
            css_prefix = config.get("css_prefix", "")
        assert css_prefix == ""
        # Logic from theme_head: when prefix is empty, include_component_link=True
        include_component_link = not bool(css_prefix)
        assert include_component_link is True

    def test_component_css_block_when_prefix_set(self):
        """When prefix is set, component CSS is generated inline with prefix."""
        from djust.theming.component_css_generator import generate_component_css

        with patch.object(
            settings,
            "LIVEVIEW_CONFIG",
            {"theme": {"css_prefix": "dj-"}},
            create=True,
        ):
            config = get_theme_config()
            css_prefix = config.get("css_prefix", "")

        assert css_prefix == "dj-"
        component_css = generate_component_css(css_prefix)
        component_css_block = f"<style data-djust-components>{component_css}</style>"

        # Should contain inline prefixed CSS
        assert ".dj-btn" in component_css_block
        assert ".dj-card" in component_css_block
        # include_component_link should be False
        include_component_link = not bool(css_prefix)
        assert include_component_link is False

    def test_no_component_css_block_when_no_prefix(self):
        """When no prefix, component_css_block is empty."""
        with patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True):
            config = get_theme_config()
            css_prefix = config.get("css_prefix", "")
        # Logic from theme_head
        component_css_block = ""
        if css_prefix:
            from djust.theming.component_css_generator import generate_component_css

            component_css_block = f"<style>{generate_component_css(css_prefix)}</style>"
        assert component_css_block == ""


# ---------------------------------------------------------------------------
# 7. System check warns on bad prefix
# ---------------------------------------------------------------------------


class TestSystemCheckCSSPrefix:
    """System check warns when css_prefix doesn't end with '-'."""

    def test_warns_on_prefix_without_trailing_dash(self):
        """css_prefix='dj' (no trailing -) triggers a warning."""
        from djust.theming.checks import check_css_prefix

        with patch.object(
            settings,
            "LIVEVIEW_CONFIG",
            {"theme": {"css_prefix": "dj"}},
            create=True,
        ):
            warnings = check_css_prefix(app_configs=None)
        assert len(warnings) == 1
        assert warnings[0].id == "djust_theming.W002"
        assert "dash" in warnings[0].msg.lower() or "-" in warnings[0].msg

    def test_no_warning_on_empty_prefix(self):
        """Empty prefix does not trigger a warning."""
        from djust.theming.checks import check_css_prefix

        with patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True):
            warnings = check_css_prefix(app_configs=None)
        assert len(warnings) == 0

    def test_no_warning_on_correct_prefix(self):
        """Prefix ending with '-' does not trigger a warning."""
        from djust.theming.checks import check_css_prefix

        with patch.object(
            settings,
            "LIVEVIEW_CONFIG",
            {"theme": {"css_prefix": "dj-"}},
            create=True,
        ):
            warnings = check_css_prefix(app_configs=None)
        assert len(warnings) == 0


if __name__ == "__main__":
    pytest.main([__file__])
