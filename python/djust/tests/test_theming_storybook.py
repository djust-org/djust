"""Tests for the component storybook (Phase 9.2)."""

# Import conftest first to configure Django settings before we add ours.
import tests.conftest  # noqa: F401

import pytest

from django.test import RequestFactory, override_settings
from django.urls import resolve, reverse

from djust.theming.contracts import COMPONENT_CONTRACTS
from djust.theming.gallery.storybook import (
    build_storybook_detail_context,
    build_storybook_index_context,
    extract_css_variables,
    get_component_template_source,
)
from djust.theming.gallery.views import storybook_detail_view, storybook_index_view

pytestmark = pytest.mark.theming


@pytest.fixture
def rf():
    return RequestFactory()


# ---------------------------------------------------------------------------
# URL resolution
# ---------------------------------------------------------------------------


_URL_SETTINGS = {"ROOT_URLCONF": "tests.gallery_test_urls"}


class TestStorybookURLResolution:
    @override_settings(**_URL_SETTINGS)
    def test_storybook_index_url_resolves(self):
        """URL reverse for 'djust_theming:storybook' resolves correctly."""
        url = reverse("djust_theming:storybook")
        assert url == "/theming/gallery/storybook/"

    @override_settings(**_URL_SETTINGS)
    def test_storybook_index_url_resolves_to_view(self):
        match = resolve("/theming/gallery/storybook/")
        assert match.func is storybook_index_view

    @override_settings(**_URL_SETTINGS)
    def test_storybook_detail_url_resolves(self):
        """URL reverse for 'djust_theming:storybook_detail' resolves correctly."""
        url = reverse("djust_theming:storybook_detail", kwargs={"component_name": "button"})
        assert url == "/theming/gallery/storybook/button/"

    @override_settings(**_URL_SETTINGS)
    def test_storybook_detail_url_resolves_to_view(self):
        match = resolve("/theming/gallery/storybook/button/")
        assert match.func is storybook_detail_view


# ---------------------------------------------------------------------------
# Access control
# ---------------------------------------------------------------------------


class TestStorybookAccessControl:
    @override_settings(DEBUG=True, **_URL_SETTINGS)
    def test_storybook_index_accessible_in_debug(self, rf):
        """Returns 200 when DEBUG=True."""
        request = rf.get("/theming/gallery/storybook/")
        request.session = {}
        response = storybook_index_view(request)
        assert response.status_code == 200

    @override_settings(DEBUG=False)
    def test_storybook_index_forbidden_non_staff(self, rf):
        """Returns 403 when DEBUG=False and user is not staff."""

        class _AnonUser:
            is_staff = False
            is_authenticated = False

        request = rf.get("/theming/gallery/storybook/")
        request.user = _AnonUser()
        request.session = {}
        response = storybook_index_view(request)
        assert response.status_code == 403

    @override_settings(DEBUG=True, **_URL_SETTINGS)
    def test_storybook_detail_accessible_in_debug(self, rf):
        """Returns 200 when DEBUG=True for a valid component."""
        request = rf.get("/theming/gallery/storybook/button/")
        request.session = {}
        response = storybook_detail_view(request, "button")
        assert response.status_code == 200

    @override_settings(DEBUG=False)
    def test_storybook_detail_forbidden_non_staff(self, rf):
        """Returns 403 when DEBUG=False and user is not staff."""

        class _AnonUser:
            is_staff = False
            is_authenticated = False

        request = rf.get("/theming/gallery/storybook/button/")
        request.user = _AnonUser()
        request.session = {}
        response = storybook_detail_view(request, "button")
        assert response.status_code == 403

    @override_settings(DEBUG=True, **_URL_SETTINGS)
    def test_storybook_detail_404_unknown_component(self, rf):
        """Returns 404 for an unknown component name."""
        request = rf.get("/theming/gallery/storybook/nonexistent/")
        request.session = {}
        response = storybook_detail_view(request, "nonexistent")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Storybook index content
# ---------------------------------------------------------------------------


class TestStorybookIndexContent:
    @override_settings(DEBUG=True, **_URL_SETTINGS)
    def test_index_lists_all_components(self, rf):
        """Index page contains all 24 component names."""
        request = rf.get("/theming/gallery/storybook/")
        request.session = {}
        response = storybook_index_view(request)
        content = response.content.decode()

        for name in COMPONENT_CONTRACTS:
            display = name.replace("_", " ").title()
            assert display in content, f"Missing component: {display}"

    @override_settings(DEBUG=True, **_URL_SETTINGS)
    def test_index_has_links_to_detail_pages(self, rf):
        """Index page contains links to detail pages."""
        request = rf.get("/theming/gallery/storybook/")
        request.session = {}
        response = storybook_index_view(request)
        content = response.content.decode()

        # Should contain at least some hrefs to detail pages
        assert "storybook/button/" in content
        assert "storybook/card/" in content


# ---------------------------------------------------------------------------
# Storybook detail content
# ---------------------------------------------------------------------------


class TestStorybookDetailContent:
    @override_settings(DEBUG=True, **_URL_SETTINGS)
    def test_detail_shows_contract_table(self, rf):
        """Detail page shows context variable names from the contract."""
        request = rf.get("/theming/gallery/storybook/button/")
        request.session = {}
        response = storybook_detail_view(request, "button")
        content = response.content.decode()

        # Button contract has required context var "text"
        assert "text" in content
        # Button contract has optional context var "variant"
        assert "variant" in content

    @override_settings(DEBUG=True, **_URL_SETTINGS)
    def test_detail_shows_template_source(self, rf):
        """Detail page includes the raw template HTML source."""
        request = rf.get("/theming/gallery/storybook/button/")
        request.session = {}
        response = storybook_detail_view(request, "button")
        content = response.content.decode()

        # The button template contains <button class=
        assert "&lt;button" in content or "<code" in content or "<pre" in content

    @override_settings(DEBUG=True, **_URL_SETTINGS)
    def test_detail_shows_available_slots(self, rf):
        """Detail page lists available slots."""
        request = rf.get("/theming/gallery/storybook/button/")
        request.session = {}
        response = storybook_detail_view(request, "button")
        content = response.content.decode()

        # Button has slot_icon, slot_content, slot_loading
        assert "slot_icon" in content
        assert "slot_content" in content

    @override_settings(DEBUG=True, **_URL_SETTINGS)
    def test_detail_shows_accessibility_reqs(self, rf):
        """Detail page shows accessibility requirements for components that have them."""
        request = rf.get("/theming/gallery/storybook/alert/")
        request.session = {}
        response = storybook_detail_view(request, "alert")
        content = response.content.decode()

        # Alert requires role=alert
        assert "role" in content
        assert "alert" in content

    @override_settings(DEBUG=True, **_URL_SETTINGS)
    def test_detail_shows_css_variables(self, rf):
        """Detail page shows CSS variables section."""
        request = rf.get("/theming/gallery/storybook/button/")
        request.session = {}
        response = storybook_detail_view(request, "button")
        content = response.content.decode()

        # Should have a CSS variables section heading (template renders in all-caps)
        assert "CSS VARIABLES" in content or "css-variables" in content


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


class TestGetComponentTemplateSource:
    def test_returns_html_for_valid_component(self):
        """get_component_template_source('button') returns HTML containing <button."""
        source = get_component_template_source("button")
        assert "<button" in source

    def test_returns_html_for_card(self):
        source = get_component_template_source("card")
        assert "card" in source

    def test_returns_empty_for_unknown(self):
        """Returns empty string for unknown component."""
        source = get_component_template_source("nonexistent_widget")
        assert source == ""


class TestExtractCssVariables:
    def test_extracts_var_references(self):
        """Finds --var-name inside var() references."""
        source = 'style="color: hsl(var(--primary, 220 80% 50%));"'
        result = extract_css_variables(source)
        assert "--primary" in result

    def test_extracts_multiple_vars(self):
        source = "var(--background) and var(--foreground) and var(--border)"
        result = extract_css_variables(source)
        assert "--background" in result
        assert "--foreground" in result
        assert "--border" in result

    def test_returns_empty_for_no_vars(self):
        source = "<button>Click me</button>"
        result = extract_css_variables(source)
        assert result == []

    def test_deduplicates(self):
        source = "var(--primary) var(--primary) var(--primary)"
        result = extract_css_variables(source)
        assert result.count("--primary") == 1


class TestBuildStorybookIndexContext:
    def test_returns_all_components(self):
        ctx = build_storybook_index_context()
        assert "components" in ctx
        names = {c["name"] for c in ctx["components"]}
        for name in COMPONENT_CONTRACTS:
            assert name in names, f"Missing: {name}"

    def test_component_entries_have_required_fields(self):
        ctx = build_storybook_index_context()
        for comp in ctx["components"]:
            assert "name" in comp
            assert "display_name" in comp
            assert "required_count" in comp
            assert "optional_count" in comp
            assert "slot_count" in comp


class TestBuildStorybookDetailContext:
    def test_returns_contract_info(self):
        ctx = build_storybook_detail_context("button")
        assert ctx["name"] == "button"
        assert "required_context" in ctx
        assert "optional_context" in ctx
        assert "available_slots" in ctx

    def test_returns_template_source(self):
        ctx = build_storybook_detail_context("button")
        assert "template_source" in ctx
        assert "<button" in ctx["template_source"]

    def test_returns_css_variables(self):
        ctx = build_storybook_detail_context("button")
        assert "css_variables" in ctx
        assert isinstance(ctx["css_variables"], list)

    def test_returns_examples(self):
        ctx = build_storybook_detail_context("button")
        assert "examples" in ctx
        assert len(ctx["examples"]) > 0

    def test_returns_accessibility(self):
        ctx = build_storybook_detail_context("alert")
        assert "accessibility" in ctx
        assert len(ctx["accessibility"]) > 0

    def test_raises_for_unknown_component(self):
        with pytest.raises(KeyError):
            build_storybook_detail_context("nonexistent_widget")
