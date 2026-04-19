"""Tests for Phase 1.1: Template namespace resolution."""

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
                "DIRS": [],
                "OPTIONS": {
                    "context_processors": [],
                },
            }
        ],
    )
    django.setup()

from unittest.mock import MagicMock, patch

import pytest

from djust.theming.template_resolver import resolve_component_template, resolve_theme_template

pytestmark = pytest.mark.theming


# ---------------------------------------------------------------------------
# 1. Default template fallback (no theme override)
# ---------------------------------------------------------------------------


class TestDefaultTemplateFallback:
    """When no theme-specific override exists, the default template is used."""

    def _make_request(self):
        request = MagicMock()
        request.COOKIES = {}
        request.session = {}
        request._djust_theme_manager = None
        return request

    def test_resolve_component_returns_default_button(self):
        """resolve_component_template returns button.html (default or theme variant)."""
        with patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True):
            tmpl = resolve_component_template(self._make_request(), "button")
        # Default theme is "material", which now has a variant template.
        # Accept either default or theme-specific button.
        assert tmpl.origin.name.endswith("button.html")

    def test_resolve_component_returns_default_card(self):
        """resolve_component_template returns card.html (default or theme variant)."""
        with patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True):
            tmpl = resolve_component_template(self._make_request(), "card")
        assert tmpl.origin.name.endswith("card.html")

    def test_resolve_component_returns_default_badge(self):
        with patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True):
            tmpl = resolve_component_template(self._make_request(), "badge")
        assert tmpl.origin.name.endswith("djust_theming/components/badge.html")

    def test_resolve_component_returns_default_alert(self):
        with patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True):
            tmpl = resolve_component_template(self._make_request(), "alert")
        assert tmpl.origin.name.endswith("djust_theming/components/alert.html")

    def test_resolve_component_returns_default_input(self):
        with patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True):
            tmpl = resolve_component_template(self._make_request(), "input")
        assert tmpl.origin.name.endswith("djust_theming/components/input.html")

    def test_resolve_theme_template_returns_default_theme_switcher(self):
        """resolve_theme_template returns default theme_switcher.html."""
        with patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True):
            tmpl = resolve_theme_template(self._make_request(), "theme_switcher")
        assert tmpl.origin.name.endswith("djust_theming/theme_switcher.html")


# ---------------------------------------------------------------------------
# 2. Theme-specific override resolution
# ---------------------------------------------------------------------------


class TestThemeSpecificOverride:
    """When a theme-specific template exists, it takes priority."""

    def test_theme_override_used_when_exists(self):
        """A theme override template is returned when it exists via select_template."""
        request = MagicMock()
        request.COOKIES = {"djust_theme": "material"}
        request.session = {}
        request._djust_theme_manager = None  # Force fresh ThemeManager

        with patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True):
            with patch("djust.theming.template_resolver.select_template") as mock_select:
                mock_select.return_value = MagicMock()
                resolve_component_template(request, "button")

                # Verify select_template was called with the right candidates
                call_args = mock_select.call_args[0][0]
                assert call_args[0] == "djust_theming/themes/material/components/button.html"
                assert call_args[1] == "djust_theming/components/button.html"

    def test_fallback_when_theme_override_missing(self):
        """Falls back to default when theme override does not exist."""
        request = MagicMock()
        request.COOKIES = {"djust_theme": "material"}
        request.session = {}
        request._djust_theme_manager = None

        # Use a component that has no material-specific variant (alert)
        with patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True):
            tmpl = resolve_component_template(request, "alert")
        # Should fall back to default
        assert "djust_theming/components/alert.html" in tmpl.origin.name
        assert "themes/" not in tmpl.origin.name


# ---------------------------------------------------------------------------
# 3. Fallback chain ordering
# ---------------------------------------------------------------------------


class TestFallbackChainOrdering:
    """The fallback chain tries theme-specific first, then default."""

    def test_fallback_chain_has_two_candidates(self):
        """The resolver constructs correct candidate list."""
        from djust.theming.template_resolver import _get_component_candidates

        candidates = _get_component_candidates("material", "button")
        assert len(candidates) == 2
        assert candidates[0] == "djust_theming/themes/material/components/button.html"
        assert candidates[1] == "djust_theming/components/button.html"

    def test_theme_template_chain_has_two_candidates(self):
        """The theme template resolver constructs correct candidate list."""
        from djust.theming.template_resolver import _get_theme_template_candidates

        candidates = _get_theme_template_candidates("material", "theme_switcher")
        assert len(candidates) == 2
        assert candidates[0] == "djust_theming/themes/material/theme_switcher.html"
        assert candidates[1] == "djust_theming/theme_switcher.html"


# ---------------------------------------------------------------------------
# 4. Component tags produce HTML output (simple_tag conversion)
# ---------------------------------------------------------------------------


class TestComponentTagsRenderHTML:
    """After conversion to simple_tags, tags return HTML strings."""

    def _make_context(self):
        """Create a minimal template context with a mock request."""
        request = MagicMock()
        request.COOKIES = {}
        request.session = {}
        # Cache a ThemeManager on request
        request._djust_theme_manager = None
        return {"request": request}

    def test_theme_button_returns_html_string(self):
        """theme_button returns an HTML string containing button element."""
        from djust.theming.templatetags.theme_components import theme_button

        ctx = self._make_context()
        with patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True):
            result = theme_button(ctx, "Click me")
        assert "<button" in result
        assert "Click me" in result

    def test_theme_card_returns_html_string(self):
        from djust.theming.templatetags.theme_components import theme_card

        ctx = self._make_context()
        with patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True):
            result = theme_card(ctx, title="Test Card")
        assert "Test Card" in result

    def test_theme_badge_returns_html_string(self):
        from djust.theming.templatetags.theme_components import theme_badge

        ctx = self._make_context()
        with patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True):
            result = theme_badge(ctx, "New")
        assert "New" in result

    def test_theme_alert_returns_html_string(self):
        from djust.theming.templatetags.theme_components import theme_alert

        ctx = self._make_context()
        with patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True):
            result = theme_alert(ctx, "Error occurred")
        assert "Error occurred" in result

    def test_theme_input_returns_html_string(self):
        from djust.theming.templatetags.theme_components import theme_input

        ctx = self._make_context()
        with patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True):
            result = theme_input(ctx, "email")
        assert "email" in result


# ---------------------------------------------------------------------------
# 5. Theme switcher tag produces HTML output
# ---------------------------------------------------------------------------


class TestThemeSwitcherTagHTML:
    """theme_switcher produces HTML after conversion to simple_tag."""

    def test_theme_switcher_returns_html(self):
        from djust.theming.templatetags.theme_tags import theme_switcher

        request = MagicMock()
        request.COOKIES = {}
        request.session = {}
        request._djust_theme_manager = None
        ctx = {"request": request}

        with patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True):
            result = theme_switcher(ctx)
        assert "theme-switcher" in result
        assert "data-theme-switcher" in result


if __name__ == "__main__":
    pytest.main([__file__])
