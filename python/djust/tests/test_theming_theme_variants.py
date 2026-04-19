"""Tests for Phase 5.1: Template variants for design systems.

Five design systems get HTML-different templates for button and card.
iOS also gets a tabs variant (segmented control).
The other 6 design systems (minimalist, corporate, retro, elegant, organic, dense)
inherit default templates -- CSS-only differences.
"""

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

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from djust.theming.registry import get_registry
from djust.theming.template_resolver import (
    _get_component_candidates,
    resolve_component_template,
)

pytestmark = pytest.mark.theming


@pytest.fixture(autouse=True)
def ensure_registry_populated():
    """Ensure the registry is populated with built-in themes/presets.

    Other test files may reset the singleton, so we re-discover here.
    """
    registry = get_registry()
    if not registry._discovered:
        with patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True):
            registry.discover()
    yield


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TEMPLATES_ROOT = Path(__file__).resolve().parent.parent / "theming" / "templates" / "djust_theming"

VARIANT_SYSTEMS = ["material", "ios", "fluent", "neo_brutalist", "playful"]
CSS_ONLY_SYSTEMS = ["minimalist", "corporate", "retro", "elegant", "organic", "dense"]


def _make_request(theme="material"):
    request = MagicMock()
    request.COOKIES = {"djust_theme": theme}
    request.session = {}
    request._djust_theme_manager = None
    return request


# ---------------------------------------------------------------------------
# 1. Variant template files exist
# ---------------------------------------------------------------------------


class TestVariantTemplateFilesExist:
    """Each of the 5 variant systems has button.html and card.html overrides."""

    @pytest.mark.parametrize("system", VARIANT_SYSTEMS)
    def test_button_variant_exists(self, system):
        path = TEMPLATES_ROOT / "themes" / system / "components" / "button.html"
        assert path.is_file(), f"Missing button variant for {system}"

    @pytest.mark.parametrize("system", VARIANT_SYSTEMS)
    def test_card_variant_exists(self, system):
        path = TEMPLATES_ROOT / "themes" / system / "components" / "card.html"
        assert path.is_file(), f"Missing card variant for {system}"

    def test_ios_tabs_variant_exists(self):
        path = TEMPLATES_ROOT / "themes" / "ios" / "components" / "tabs.html"
        assert path.is_file(), "Missing tabs variant for iOS"


# ---------------------------------------------------------------------------
# 2. Variant templates contain design-system-specific markup
# ---------------------------------------------------------------------------


class TestVariantMarkup:
    """Variant templates contain the design-system-specific HTML structures."""

    def _read(self, system, component):
        path = TEMPLATES_ROOT / "themes" / system / "components" / f"{component}.html"
        return path.read_text()

    # -- Material --
    def test_material_button_has_ripple(self):
        html = self._read("material", "button")
        assert "btn-ripple" in html

    def test_material_card_has_elevated(self):
        html = self._read("material", "card")
        assert "card-elevated" in html

    # -- iOS --
    def test_ios_button_has_sf_class(self):
        html = self._read("ios", "button")
        assert "btn-sf" in html

    def test_ios_card_has_grouped(self):
        html = self._read("ios", "card")
        assert "card-grouped" in html

    def test_ios_tabs_has_segmented_control(self):
        html = self._read("ios", "tabs")
        assert "segmented-control" in html

    # -- Fluent --
    def test_fluent_button_has_reveal(self):
        html = self._read("fluent", "button")
        assert "btn-reveal" in html

    def test_fluent_card_has_acrylic(self):
        html = self._read("fluent", "card")
        assert "card-acrylic" in html

    # -- Neo-Brutalist --
    def test_neo_brutalist_button_has_offset(self):
        html = self._read("neo_brutalist", "button")
        assert "btn-offset" in html

    def test_neo_brutalist_card_has_border_thick(self):
        html = self._read("neo_brutalist", "card")
        assert "card-border-thick" in html

    # -- Playful --
    def test_playful_button_has_bounce(self):
        html = self._read("playful", "button")
        assert "btn-bounce" in html

    def test_playful_card_has_decorated(self):
        html = self._read("playful", "card")
        assert "card-decorated" in html


# ---------------------------------------------------------------------------
# 3. Variant templates preserve required context variables
# ---------------------------------------------------------------------------


class TestVariantTemplatesPreserveContract:
    """All variant templates use the same context variables as the defaults."""

    BUTTON_VARS = ["css_prefix", "variant", "size", "text"]
    CARD_VARS = ["css_prefix", "title", "content"]

    def _read(self, system, component):
        path = TEMPLATES_ROOT / "themes" / system / "components" / f"{component}.html"
        return path.read_text()

    @pytest.mark.parametrize("system", VARIANT_SYSTEMS)
    def test_button_uses_css_prefix(self, system):
        html = self._read(system, "button")
        assert "css_prefix" in html

    @pytest.mark.parametrize("system", VARIANT_SYSTEMS)
    def test_button_uses_variant(self, system):
        html = self._read(system, "button")
        assert "variant" in html

    @pytest.mark.parametrize("system", VARIANT_SYSTEMS)
    def test_card_uses_css_prefix(self, system):
        html = self._read(system, "card")
        assert "css_prefix" in html


# ---------------------------------------------------------------------------
# 4. Fallback chain ordering
# ---------------------------------------------------------------------------


class TestFallbackChainWithVariants:
    """_get_component_candidates builds the correct 2-candidate chain."""

    @pytest.mark.parametrize("system", VARIANT_SYSTEMS)
    def test_variant_system_chain(self, system):
        candidates = _get_component_candidates(system, "button")
        assert len(candidates) == 2
        assert candidates[0] == f"djust_theming/themes/{system}/components/button.html"
        assert candidates[1] == "djust_theming/components/button.html"


# ---------------------------------------------------------------------------
# 5. Variant resolution via resolve_component_template
# ---------------------------------------------------------------------------


class TestVariantResolution:
    """resolve_component_template returns theme-specific variant when it exists."""

    @pytest.mark.parametrize("system", VARIANT_SYSTEMS)
    def test_resolves_variant_button(self, system):
        """select_template resolves the theme-specific button for variant systems."""
        request = _make_request(theme=system)
        with patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True):
            tmpl = resolve_component_template(request, "button")
        # Should resolve to the theme-specific variant
        assert f"themes/{system}/components/button.html" in tmpl.origin.name

    @pytest.mark.parametrize("system", VARIANT_SYSTEMS)
    def test_resolves_variant_card(self, system):
        request = _make_request(theme=system)
        with patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True):
            tmpl = resolve_component_template(request, "card")
        assert f"themes/{system}/components/card.html" in tmpl.origin.name

    def test_resolves_ios_tabs_variant(self):
        request = _make_request(theme="ios")
        with patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True):
            tmpl = resolve_component_template(request, "tabs")
        assert "themes/ios/components/tabs.html" in tmpl.origin.name


# ---------------------------------------------------------------------------
# 6. CSS-only systems fall back to default templates
# ---------------------------------------------------------------------------


class TestCSSOnlySystemsFallback:
    """The 6 CSS-only design systems fall back to default component templates."""

    @pytest.mark.parametrize("system", CSS_ONLY_SYSTEMS)
    def test_button_falls_back_to_default(self, system):
        request = _make_request(theme=system)
        with patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True):
            tmpl = resolve_component_template(request, "button")
        assert "themes/" not in tmpl.origin.name
        assert "djust_theming/components/button.html" in tmpl.origin.name

    @pytest.mark.parametrize("system", CSS_ONLY_SYSTEMS)
    def test_card_falls_back_to_default(self, system):
        request = _make_request(theme=system)
        with patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True):
            tmpl = resolve_component_template(request, "card")
        assert "themes/" not in tmpl.origin.name
        assert "djust_theming/components/card.html" in tmpl.origin.name


# ---------------------------------------------------------------------------
# 7. Variant systems still fall back for non-overridden components
# ---------------------------------------------------------------------------


class TestVariantSystemsFallbackForOtherComponents:
    """Variant systems fall back to default for components without overrides."""

    @pytest.mark.parametrize("system", VARIANT_SYSTEMS)
    def test_alert_falls_back_to_default(self, system):
        request = _make_request(theme=system)
        with patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True):
            tmpl = resolve_component_template(request, "alert")
        assert "themes/" not in tmpl.origin.name
        assert "djust_theming/components/alert.html" in tmpl.origin.name


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
