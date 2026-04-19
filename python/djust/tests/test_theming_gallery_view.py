"""Tests for the theme gallery view (Phase 8.1)."""

# Import conftest first to configure Django settings before we add ours.
import tests.conftest  # noqa: F401

import pytest


from django.test import RequestFactory, override_settings
from django.urls import resolve, reverse

from djust.theming.contracts import COMPONENT_CONTRACTS
from djust.theming.gallery.context import build_gallery_context
from djust.theming.gallery.views import gallery_view

pytestmark = pytest.mark.theming


@pytest.fixture
def rf():
    return RequestFactory()


# ---------------------------------------------------------------------------
# URL resolution
# ---------------------------------------------------------------------------


class TestGalleryURLResolution:
    @override_settings(ROOT_URLCONF="tests.gallery_test_urls")
    def test_gallery_url_resolves(self):
        """URL reverse for 'djust_theming:gallery' resolves correctly."""
        url = reverse("djust_theming:gallery")
        assert url == "/theming/gallery/"

    @override_settings(ROOT_URLCONF="tests.gallery_test_urls")
    def test_gallery_url_resolves_to_view(self):
        match = resolve("/theming/gallery/")
        assert match.func is gallery_view

    @override_settings(ROOT_URLCONF="tests.gallery_test_urls")
    def test_gallery_url_name(self):
        """Gallery URL name is 'gallery' within djust_theming namespace."""
        match = resolve("/theming/gallery/")
        assert match.url_name == "gallery"


# ---------------------------------------------------------------------------
# Access control
# ---------------------------------------------------------------------------


_URL_SETTINGS = {"ROOT_URLCONF": "tests.gallery_test_urls"}


class TestGalleryAccessControl:
    @override_settings(DEBUG=True, **_URL_SETTINGS)
    def test_gallery_view_accessible_in_debug(self, rf):
        """Returns 200 when DEBUG=True (no auth needed)."""
        request = rf.get("/theming/gallery/")
        request.session = {}
        response = gallery_view(request)
        assert response.status_code == 200

    @override_settings(DEBUG=False)
    def test_gallery_view_forbidden_non_debug_non_staff(self, rf):
        """Returns 403 when DEBUG=False and user is not staff."""

        class _AnonUser:
            is_staff = False
            is_authenticated = False

        request = rf.get("/theming/gallery/")
        request.user = _AnonUser()
        request.session = {}
        response = gallery_view(request)
        assert response.status_code == 403

    @override_settings(DEBUG=False, **_URL_SETTINGS)
    def test_gallery_view_accessible_for_staff(self, rf):
        """Returns 200 when DEBUG=False but user.is_staff=True."""

        class _StaffUser:
            is_staff = True
            is_authenticated = True

        request = rf.get("/theming/gallery/")
        request.user = _StaffUser()
        request.session = {}
        response = gallery_view(request)
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Content assertions
# ---------------------------------------------------------------------------


class TestGalleryContent:
    @override_settings(DEBUG=True, **_URL_SETTINGS)
    def test_gallery_view_contains_all_components(self, rf):
        """Response HTML contains a section heading for each of 24 components."""
        request = rf.get("/gallery/")
        request.session = {}
        response = gallery_view(request)
        content = response.content.decode()

        for name in COMPONENT_CONTRACTS:
            display = name.replace("_", " ").title()
            assert display in content, f"Missing component section: {display}"

    @override_settings(DEBUG=True, **_URL_SETTINGS)
    def test_gallery_view_contains_variant_examples(self, rf):
        """Button section shows primary, secondary, destructive variants."""
        request = rf.get("/gallery/")
        request.session = {}
        response = gallery_view(request)
        content = response.content.decode()

        # The button template renders the variant as a CSS class or data attribute.
        # At minimum, the gallery context should produce buttons with these texts.
        for variant in ("Primary", "Secondary", "Destructive", "Ghost", "Link"):
            assert variant in content, f"Missing button variant: {variant}"

    @override_settings(DEBUG=True, **_URL_SETTINGS)
    def test_gallery_view_contains_size_examples(self, rf):
        """Button section shows sm, md, lg sizes."""
        request = rf.get("/gallery/")
        request.session = {}
        response = gallery_view(request)
        content = response.content.decode()

        # Size labels appear in the gallery
        for size in ("sm", "md", "lg"):
            assert size in content, f"Missing size: {size}"

    @override_settings(DEBUG=True, **_URL_SETTINGS)
    def test_gallery_view_preset_switcher(self, rf):
        """Response contains a preset select form."""
        request = rf.get("/gallery/")
        request.session = {}
        response = gallery_view(request)
        content = response.content.decode()

        assert "<form" in content
        assert "preset" in content
        assert "<select" in content or "<option" in content

    @override_settings(DEBUG=True, **_URL_SETTINGS)
    def test_gallery_view_preset_param(self, rf):
        """?preset=nord changes the rendered preset context."""
        request = rf.get("/gallery/", {"preset": "nord"})
        request.session = {}
        response = gallery_view(request)
        content = response.content.decode()

        # The preset switcher should reflect "nord" as selected
        assert "nord" in content


# ---------------------------------------------------------------------------
# Context builder
# ---------------------------------------------------------------------------


class TestGalleryContextBuilder:
    def test_build_gallery_context_returns_all_components(self):
        """build_gallery_context() returns entries for all 24 components."""
        ctx = build_gallery_context()
        component_names = {section["name"] for section in ctx["sections"]}
        for name in COMPONENT_CONTRACTS:
            assert name in component_names, f"Missing: {name}"

    def test_build_gallery_context_has_24_sections(self):
        ctx = build_gallery_context()
        assert len(ctx["sections"]) == len(COMPONENT_CONTRACTS)

    def test_build_gallery_context_button_variants(self):
        """Button section includes all 5 variant values."""
        ctx = build_gallery_context()
        button_section = next(s for s in ctx["sections"] if s["name"] == "button")
        examples = button_section["examples"]
        # Should have examples for each variant
        variant_names = {ex.get("variant", "") for ex in examples}
        for v in ("primary", "secondary", "destructive", "ghost", "link"):
            assert v in variant_names, f"Missing variant: {v}"

    def test_build_gallery_context_button_sizes(self):
        """Button section includes sm, md, lg sizes."""
        ctx = build_gallery_context()
        button_section = next(s for s in ctx["sections"] if s["name"] == "button")
        examples = button_section["examples"]
        size_names = {ex.get("size", "") for ex in examples}
        for s in ("sm", "md", "lg"):
            assert s in size_names, f"Missing size: {s}"

    def test_build_gallery_context_alert_variants(self):
        """Alert section includes all variant values."""
        ctx = build_gallery_context()
        alert_section = next(s for s in ctx["sections"] if s["name"] == "alert")
        examples = alert_section["examples"]
        variant_names = {ex.get("variant", "") for ex in examples}
        for v in ("default", "success", "warning", "destructive"):
            assert v in variant_names, f"Missing variant: {v}"

    def test_build_gallery_context_presets_list(self):
        """Context includes a list of available presets."""
        ctx = build_gallery_context()
        assert "presets" in ctx
        assert len(ctx["presets"]) > 0
        preset_names = {p["name"] for p in ctx["presets"]}
        assert "default" in preset_names
        assert "nord" in preset_names
