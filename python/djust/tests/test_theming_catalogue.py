"""Tests for the component catalogue (Phase 9.2)."""

# Import conftest first to configure Django settings before we add ours.
import tests.conftest  # noqa: F401

import pytest

from django.test import RequestFactory, override_settings
from django.urls import resolve, reverse

from djust.theming.contracts import COMPONENT_CONTRACTS
from djust.theming.gallery.catalogue import (
    build_catalogue_detail_context,
    build_catalogue_index_context,
    extract_css_variables,
    get_component_template_source,
)
from djust.theming.gallery.live_views import ComponentsIndexView
from djust.theming.gallery.views import components_detail_view

pytestmark = pytest.mark.theming


@pytest.fixture
def rf():
    return RequestFactory()


# ---------------------------------------------------------------------------
# URL resolution
# ---------------------------------------------------------------------------


_URL_SETTINGS = {"ROOT_URLCONF": "tests.gallery_test_urls"}


class TestComponentsURLResolution:
    @override_settings(**_URL_SETTINGS)
    def test_components_index_url_resolves(self):
        """URL reverse for 'djust_theming:components' resolves correctly."""
        url = reverse("djust_theming:components")
        assert url == "/theming/components/"

    @override_settings(**_URL_SETTINGS)
    def test_components_index_url_resolves_to_view(self):
        """The index is a LiveView, like its siblings.

        It resolved to `views.components_index_view` — a plain Django function —
        which is why the index page had to re-implement its own filtering in a
        `<script>`: `dj-click` and `dj-input` are server events and a plain view
        has no server for them to reach.
        """
        match = resolve("/theming/components/")
        assert match.func.view_class is ComponentsIndexView

    @override_settings(**_URL_SETTINGS)
    def test_components_detail_url_resolves(self):
        """URL reverse for 'djust_theming:components_detail' resolves correctly."""
        url = reverse("djust_theming:components_detail", kwargs={"component_name": "button"})
        assert url == "/theming/components/button/"

    @override_settings(**_URL_SETTINGS)
    def test_components_detail_url_resolves_to_the_liveview(self):
        """It resolves to the LiveView, not the plain view it used to.

        The detail page has to be a `LiveView`: `dj-click` is a server event, and
        a plain view ships no server for it to reach, so the component previews
        rendered but did nothing when clicked.
        """
        from djust.theming.gallery.live_views import ComponentsDetailView

        match = resolve("/theming/components/button/")
        assert match.func.view_class is ComponentsDetailView
        assert match.kwargs["component_name"] == "button"


# ---------------------------------------------------------------------------
# Access control
# ---------------------------------------------------------------------------


class TestComponentsAccessControl:
    @override_settings(DEBUG=True, **_URL_SETTINGS)
    @pytest.mark.django_db
    def test_components_index_accessible_in_debug(self, client):
        """Returns 200 when DEBUG=True."""
        assert client.get("/theming/components/").status_code == 200

    @override_settings(DEBUG=False, **_URL_SETTINGS)
    @pytest.mark.django_db
    def test_components_index_denied_when_not_staff(self, client):
        """Denied when DEBUG=False and the user is not staff.

        The rule is unchanged — `ComponentsAccessMixin.check_permissions` is the
        same predicate the plain view's `_check_access` used, `DEBUG` (or
        `DJUST_THEMING_GALLERY_PUBLIC`) or `is_staff`. Only the shape of the
        refusal moved: the plain view returned a bare 403, and a LiveView
        expresses denial by raising `PermissionDenied`, which the framework
        answers with a redirect to the login page. Denied either way.

        The value of moving it: the plain view's gate covered only the initial
        HTTP GET, so the same page was reachable over the WebSocket without it.
        `check_permissions` is consulted on every transport.
        """
        response = client.get("/theming/components/")
        assert response.status_code in (302, 403)

    @override_settings(DEBUG=True, **_URL_SETTINGS)
    def test_components_detail_accessible_in_debug(self, rf):
        """Returns 200 when DEBUG=True for a valid component."""
        request = rf.get("/theming/components/button/")
        request.session = {}
        response = components_detail_view(request, "button")
        assert response.status_code == 200

    @override_settings(DEBUG=False)
    def test_components_detail_forbidden_non_staff(self, rf):
        """Returns 403 when DEBUG=False and user is not staff."""

        class _AnonUser:
            is_staff = False
            is_authenticated = False

        request = rf.get("/theming/components/button/")
        request.user = _AnonUser()
        request.session = {}
        response = components_detail_view(request, "button")
        assert response.status_code == 403

    @override_settings(DEBUG=True, **_URL_SETTINGS)
    def test_components_detail_404_unknown_component(self, rf):
        """Returns 404 for an unknown component name."""
        request = rf.get("/theming/components/nonexistent/")
        request.session = {}
        response = components_detail_view(request, "nonexistent")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Catalogue index content
# ---------------------------------------------------------------------------


class TestComponentsIndexContent:
    @override_settings(DEBUG=True, **_URL_SETTINGS)
    @pytest.mark.django_db
    def test_index_lists_all_components(self, client):
        """Index page contains all 24 component names."""
        content = client.get("/theming/components/").content.decode()

        for name in COMPONENT_CONTRACTS:
            display = name.replace("_", " ").title()
            assert display in content, f"Missing component: {display}"

    @override_settings(DEBUG=True, **_URL_SETTINGS)
    @pytest.mark.django_db
    def test_index_has_links_to_detail_pages(self, client):
        """Index page contains links to detail pages."""
        content = client.get("/theming/components/").content.decode()

        # Should contain at least some hrefs to detail pages
        assert "components/button/" in content
        assert "components/card/" in content


# ---------------------------------------------------------------------------
# Catalogue detail content
# ---------------------------------------------------------------------------


class TestComponentsDetailContent:
    @override_settings(DEBUG=True, **_URL_SETTINGS)
    def test_detail_shows_contract_table(self, rf):
        """Detail page shows context variable names from the contract."""
        request = rf.get("/theming/components/button/")
        request.session = {}
        response = components_detail_view(request, "button")
        content = response.content.decode()

        # Button contract has required context var "text"
        assert "text" in content
        # Button contract has optional context var "variant"
        assert "variant" in content

    @override_settings(DEBUG=True, **_URL_SETTINGS)
    def test_detail_shows_template_source(self, rf):
        """Detail page includes the raw template HTML source."""
        request = rf.get("/theming/components/button/")
        request.session = {}
        response = components_detail_view(request, "button")
        content = response.content.decode()

        # The button template contains <button class=
        assert "&lt;button" in content or "<code" in content or "<pre" in content

    @override_settings(DEBUG=True, **_URL_SETTINGS)
    def test_detail_shows_available_slots(self, rf):
        """Detail page lists available slots."""
        request = rf.get("/theming/components/button/")
        request.session = {}
        response = components_detail_view(request, "button")
        content = response.content.decode()

        # Button has slot_icon, slot_content, slot_loading
        assert "slot_icon" in content
        assert "slot_content" in content

    @override_settings(DEBUG=True, **_URL_SETTINGS)
    def test_detail_shows_accessibility_reqs(self, rf):
        """Detail page shows accessibility requirements for components that have them."""
        request = rf.get("/theming/components/alert/")
        request.session = {}
        response = components_detail_view(request, "alert")
        content = response.content.decode()

        # Alert requires role=alert
        assert "role" in content
        assert "alert" in content

    @override_settings(DEBUG=True, **_URL_SETTINGS)
    def test_detail_shows_css_variables(self, rf):
        """Detail page shows CSS variables section."""
        request = rf.get("/theming/components/button/")
        request.session = {}
        response = components_detail_view(request, "button")
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


class TestBuildCatalogueIndexContext:
    def test_returns_all_components(self):
        ctx = build_catalogue_index_context()
        assert "components" in ctx
        names = {c["name"] for c in ctx["components"]}
        for name in COMPONENT_CONTRACTS:
            assert name in names, f"Missing: {name}"

    def test_component_entries_have_required_fields(self):
        ctx = build_catalogue_index_context()
        for comp in ctx["components"]:
            assert "name" in comp
            assert "display_name" in comp
            assert "required_count" in comp
            assert "optional_count" in comp
            assert "slot_count" in comp


class TestBuildCatalogueDetailContext:
    def test_returns_contract_info(self):
        ctx = build_catalogue_detail_context("button")
        assert ctx["name"] == "button"
        assert "required_context" in ctx
        assert "optional_context" in ctx
        assert "available_slots" in ctx

    def test_returns_template_source(self):
        ctx = build_catalogue_detail_context("button")
        assert "template_source" in ctx
        assert "<button" in ctx["template_source"]

    def test_returns_css_variables(self):
        ctx = build_catalogue_detail_context("button")
        assert "css_variables" in ctx
        assert isinstance(ctx["css_variables"], list)

    def test_returns_examples(self):
        ctx = build_catalogue_detail_context("button")
        assert "examples" in ctx
        assert len(ctx["examples"]) > 0

    def test_returns_accessibility(self):
        ctx = build_catalogue_detail_context("alert")
        assert "accessibility" in ctx
        assert len(ctx["accessibility"]) > 0

    def test_raises_for_unknown_component(self):
        with pytest.raises(KeyError):
            build_catalogue_detail_context("nonexistent_widget")
