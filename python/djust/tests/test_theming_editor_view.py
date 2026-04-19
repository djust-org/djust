"""Tests for the live theme editor (Phase 8.2) and diff view (Phase 8.3)."""

# Import conftest first to configure Django settings before we add ours.
import tests.conftest  # noqa: F401

import json

import pytest

from django.test import RequestFactory, override_settings
from django.urls import resolve, reverse

from djust.theming.gallery.views import diff_view, editor_export_view, editor_view
from djust.theming.gallery.context import serialize_preset, serialize_tokens
from djust.theming.presets import get_preset

pytestmark = pytest.mark.theming


@pytest.fixture
def rf():
    return RequestFactory()


# ---------------------------------------------------------------------------
# Token serialization helpers
# ---------------------------------------------------------------------------


class TestSerializeTokens:
    def test_serialize_tokens_returns_dict(self):
        """serialize_tokens() returns a dict with all token field names."""
        preset = get_preset("default")
        result = serialize_tokens(preset.light)
        assert isinstance(result, dict)
        assert "background" in result
        assert "primary" in result
        assert "border" in result

    def test_serialize_tokens_hsl_values(self):
        """Each token value contains h, s, l integer keys."""
        preset = get_preset("default")
        result = serialize_tokens(preset.light)
        bg = result["background"]
        assert "h" in bg
        assert "s" in bg
        assert "l" in bg
        assert isinstance(bg["h"], int)

    def test_serialize_preset_structure(self):
        """serialize_preset() returns light, dark, and radius."""
        preset = get_preset("default")
        result = serialize_preset(preset)
        assert "light" in result
        assert "dark" in result
        assert "radius" in result
        assert isinstance(result["radius"], float)
        assert "background" in result["light"]
        assert "background" in result["dark"]


# ---------------------------------------------------------------------------
# URL resolution
# ---------------------------------------------------------------------------


_URL_SETTINGS = {"ROOT_URLCONF": "tests.gallery_test_urls"}


class TestEditorURLResolution:
    @override_settings(**_URL_SETTINGS)
    def test_editor_url_resolves_to_view(self):
        match = resolve("/theming/gallery/editor/")
        assert match.func is editor_view

    @override_settings(**_URL_SETTINGS)
    def test_editor_export_url_resolves_to_view(self):
        match = resolve("/theming/gallery/editor/export/")
        assert match.func is editor_export_view

    @override_settings(**_URL_SETTINGS)
    def test_diff_url_resolves_to_view(self):
        match = resolve("/theming/gallery/diff/")
        assert match.func is diff_view

    @override_settings(**_URL_SETTINGS)
    def test_editor_url_reverse(self):
        url = reverse("djust_theming:editor")
        assert url == "/theming/gallery/editor/"

    @override_settings(**_URL_SETTINGS)
    def test_diff_url_reverse(self):
        url = reverse("djust_theming:diff")
        assert url == "/theming/gallery/diff/"


# ---------------------------------------------------------------------------
# Access control -- Editor
# ---------------------------------------------------------------------------


class TestEditorAccessControl:
    @override_settings(DEBUG=True, **_URL_SETTINGS)
    def test_editor_accessible_in_debug(self, rf):
        request = rf.get("/theming/gallery/editor/")
        request.session = {}
        response = editor_view(request)
        assert response.status_code == 200

    @override_settings(DEBUG=False)
    def test_editor_forbidden_non_debug_non_staff(self, rf):
        class _AnonUser:
            is_staff = False
            is_authenticated = False

        request = rf.get("/theming/gallery/editor/")
        request.user = _AnonUser()
        request.session = {}
        response = editor_view(request)
        assert response.status_code == 403

    @override_settings(DEBUG=False, **_URL_SETTINGS)
    def test_editor_accessible_for_staff(self, rf):
        class _StaffUser:
            is_staff = True
            is_authenticated = True

        request = rf.get("/theming/gallery/editor/")
        request.user = _StaffUser()
        request.session = {}
        response = editor_view(request)
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Access control -- Diff
# ---------------------------------------------------------------------------


class TestDiffAccessControl:
    @override_settings(DEBUG=True, **_URL_SETTINGS)
    def test_diff_accessible_in_debug(self, rf):
        request = rf.get("/theming/gallery/diff/")
        request.session = {}
        response = diff_view(request)
        assert response.status_code == 200

    @override_settings(DEBUG=False)
    def test_diff_forbidden_non_debug_non_staff(self, rf):
        class _AnonUser:
            is_staff = False
            is_authenticated = False

        request = rf.get("/theming/gallery/diff/")
        request.user = _AnonUser()
        request.session = {}
        response = diff_view(request)
        assert response.status_code == 403

    @override_settings(DEBUG=False, **_URL_SETTINGS)
    def test_diff_accessible_for_staff(self, rf):
        class _StaffUser:
            is_staff = True
            is_authenticated = True

        request = rf.get("/theming/gallery/diff/")
        request.user = _StaffUser()
        request.session = {}
        response = diff_view(request)
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Editor content
# ---------------------------------------------------------------------------


class TestEditorContent:
    @override_settings(DEBUG=True, **_URL_SETTINGS)
    def test_editor_contains_color_inputs(self, rf):
        """Editor page contains color input controls."""
        request = rf.get("/theming/gallery/editor/")
        request.session = {}
        response = editor_view(request)
        content = response.content.decode()
        assert 'type="color"' in content

    @override_settings(DEBUG=True, **_URL_SETTINGS)
    def test_editor_contains_radius_slider(self, rf):
        """Editor page contains a range input for border radius."""
        request = rf.get("/theming/gallery/editor/")
        request.session = {}
        response = editor_view(request)
        content = response.content.decode()
        assert 'type="range"' in content
        assert "--radius" in content

    @override_settings(DEBUG=True, **_URL_SETTINGS)
    def test_editor_contains_preset_selector(self, rf):
        """Editor page contains a preset selector."""
        request = rf.get("/theming/gallery/editor/")
        request.session = {}
        response = editor_view(request)
        content = response.content.decode()
        assert "preset" in content.lower()
        assert "<select" in content

    @override_settings(DEBUG=True, **_URL_SETTINGS)
    def test_editor_contains_export_button(self, rf):
        """Editor page contains an export button."""
        request = rf.get("/theming/gallery/editor/")
        request.session = {}
        response = editor_view(request)
        content = response.content.decode()
        assert "export" in content.lower()

    @override_settings(DEBUG=True, **_URL_SETTINGS)
    def test_editor_contains_preset_json_data(self, rf):
        """Editor page contains serialized preset JSON for JS initialization."""
        request = rf.get("/theming/gallery/editor/")
        request.session = {}
        response = editor_view(request)
        content = response.content.decode()
        # The page should embed preset data as JSON
        assert "presetData" in content or "preset_data" in content or "PRESET_DATA" in content

    @override_settings(DEBUG=True, **_URL_SETTINGS)
    def test_editor_preset_param(self, rf):
        """?preset=nord loads nord as the initial preset."""
        request = rf.get("/theming/gallery/editor/", {"preset": "nord"})
        request.session = {}
        response = editor_view(request)
        content = response.content.decode()
        assert "nord" in content

    @override_settings(DEBUG=True, **_URL_SETTINGS)
    def test_editor_contains_preview_components(self, rf):
        """Editor page contains component preview sections (buttons, cards, etc)."""
        request = rf.get("/theming/gallery/editor/")
        request.session = {}
        response = editor_view(request)
        content = response.content.decode()
        # Should have some component examples rendered
        assert "Primary" in content  # Button variant
        assert "Card" in content


# ---------------------------------------------------------------------------
# Editor export endpoint
# ---------------------------------------------------------------------------


class TestEditorExport:
    @override_settings(DEBUG=True, **_URL_SETTINGS)
    def test_export_get_returns_405(self, rf):
        """GET to export endpoint returns 405 Method Not Allowed."""
        request = rf.get("/theming/gallery/editor/export/")
        request.session = {}
        response = editor_export_view(request)
        assert response.status_code == 405

    @override_settings(DEBUG=True, **_URL_SETTINGS)
    def test_export_post_returns_json(self, rf):
        """Valid POST returns 200 JSON with tokens_css and theme_toml."""
        payload = {
            "name": "custom",
            "radius": 0.5,
            "tokens": {
                "light": {
                    "background": {"h": 0, "s": 0, "l": 100},
                    "foreground": {"h": 240, "s": 10, "l": 4},
                    "primary": {"h": 240, "s": 6, "l": 10},
                    "primary_foreground": {"h": 0, "s": 0, "l": 98},
                },
                "dark": {
                    "background": {"h": 240, "s": 10, "l": 4},
                    "foreground": {"h": 0, "s": 0, "l": 98},
                    "primary": {"h": 0, "s": 0, "l": 98},
                    "primary_foreground": {"h": 240, "s": 6, "l": 10},
                },
            },
        }
        request = rf.post(
            "/theming/gallery/editor/export/",
            data=json.dumps(payload),
            content_type="application/json",
        )
        request.session = {}
        response = editor_export_view(request)
        assert response.status_code == 200
        data = json.loads(response.content)
        assert "tokens_css" in data
        assert "theme_toml" in data

    @override_settings(DEBUG=True, **_URL_SETTINGS)
    def test_export_tokens_css_contains_custom_properties(self, rf):
        """Exported tokens_css contains CSS custom property declarations."""
        payload = {
            "name": "custom",
            "radius": 0.75,
            "tokens": {
                "light": {"background": {"h": 0, "s": 0, "l": 100}},
                "dark": {"background": {"h": 240, "s": 10, "l": 4}},
            },
        }
        request = rf.post(
            "/theming/gallery/editor/export/",
            data=json.dumps(payload),
            content_type="application/json",
        )
        request.session = {}
        response = editor_export_view(request)
        data = json.loads(response.content)
        assert "--background:" in data["tokens_css"]
        assert "--radius:" in data["tokens_css"]

    @override_settings(DEBUG=True, **_URL_SETTINGS)
    def test_export_theme_toml_contains_theme_section(self, rf):
        """Exported theme_toml contains [theme] header."""
        payload = {
            "name": "my-theme",
            "radius": 0.5,
            "tokens": {
                "light": {"background": {"h": 0, "s": 0, "l": 100}},
                "dark": {"background": {"h": 240, "s": 10, "l": 4}},
            },
        }
        request = rf.post(
            "/theming/gallery/editor/export/",
            data=json.dumps(payload),
            content_type="application/json",
        )
        request.session = {}
        response = editor_export_view(request)
        data = json.loads(response.content)
        assert "[theme]" in data["theme_toml"]
        assert "my-theme" in data["theme_toml"]

    @override_settings(DEBUG=False)
    def test_export_forbidden_non_debug_non_staff(self, rf):
        """Export endpoint respects access control."""

        class _AnonUser:
            is_staff = False
            is_authenticated = False

        request = rf.post(
            "/theming/gallery/editor/export/",
            data=json.dumps({"name": "test", "radius": 0.5, "tokens": {"light": {}, "dark": {}}}),
            content_type="application/json",
        )
        request.user = _AnonUser()
        request.session = {}
        response = editor_export_view(request)
        assert response.status_code == 403

    @override_settings(DEBUG=True, **_URL_SETTINGS)
    def test_export_invalid_json(self, rf):
        """Malformed JSON body returns 400."""
        request = rf.post(
            "/theming/gallery/editor/export/",
            data="not-json{{{",
            content_type="application/json",
        )
        request.session = {}
        response = editor_export_view(request)
        assert response.status_code == 400

    @override_settings(DEBUG=True, **_URL_SETTINGS)
    def test_export_rejects_css_injection_in_token_name(self, rf):
        """Token names with CSS injection characters are rejected."""
        payload = {
            "name": "custom",
            "radius": 0.5,
            "tokens": {
                "light": {"}; body { display:none": {"h": 0, "s": 0, "l": 0}},
                "dark": {},
            },
        }
        request = rf.post(
            "/theming/gallery/editor/export/",
            data=json.dumps(payload),
            content_type="application/json",
        )
        request.session = {}
        response = editor_export_view(request)
        assert response.status_code == 400

    @override_settings(DEBUG=True, **_URL_SETTINGS)
    def test_export_rejects_invalid_hsl_value(self, rf):
        """Non-numeric HSL values are rejected."""
        payload = {
            "name": "custom",
            "radius": 0.5,
            "tokens": {
                "light": {"background": {"h": "injection", "s": 0, "l": 0}},
                "dark": {},
            },
        }
        request = rf.post(
            "/theming/gallery/editor/export/",
            data=json.dumps(payload),
            content_type="application/json",
        )
        request.session = {}
        response = editor_export_view(request)
        assert response.status_code == 400

    @override_settings(DEBUG=True, **_URL_SETTINGS)
    def test_export_rejects_invalid_theme_name(self, rf):
        """Theme names with special characters are rejected."""
        payload = {
            "name": '"; rm -rf /',
            "radius": 0.5,
            "tokens": {"light": {}, "dark": {}},
        }
        request = rf.post(
            "/theming/gallery/editor/export/",
            data=json.dumps(payload),
            content_type="application/json",
        )
        request.session = {}
        response = editor_export_view(request)
        assert response.status_code == 400

    @override_settings(DEBUG=True, **_URL_SETTINGS)
    def test_export_rejects_invalid_radius(self, rf):
        """Radius outside valid range is rejected."""
        payload = {
            "name": "custom",
            "radius": 999,
            "tokens": {"light": {}, "dark": {}},
        }
        request = rf.post(
            "/theming/gallery/editor/export/",
            data=json.dumps(payload),
            content_type="application/json",
        )
        request.session = {}
        response = editor_export_view(request)
        assert response.status_code == 400


# ---------------------------------------------------------------------------
# Diff content
# ---------------------------------------------------------------------------


class TestDiffContent:
    @override_settings(DEBUG=True, **_URL_SETTINGS)
    def test_diff_contains_iframes(self, rf):
        """Diff page contains two iframes for side-by-side comparison."""
        request = rf.get("/theming/gallery/diff/")
        request.session = {}
        response = diff_view(request)
        content = response.content.decode()
        assert content.count("<iframe") == 2

    @override_settings(DEBUG=True, **_URL_SETTINGS)
    def test_diff_contains_preset_selectors(self, rf):
        """Diff page contains left and right preset selectors."""
        request = rf.get("/theming/gallery/diff/")
        request.session = {}
        response = diff_view(request)
        content = response.content.decode()
        assert "left" in content.lower()
        assert "right" in content.lower()
        assert "<select" in content

    @override_settings(DEBUG=True, **_URL_SETTINGS)
    def test_diff_default_presets(self, rf):
        """Default left=default and right=nord are reflected in the page."""
        request = rf.get("/theming/gallery/diff/")
        request.session = {}
        response = diff_view(request)
        content = response.content.decode()
        # Iframes should load gallery with preset params
        assert "preset=default" in content
        assert "preset=nord" in content

    @override_settings(DEBUG=True, **_URL_SETTINGS)
    def test_diff_custom_presets(self, rf):
        """?left=blue&right=purple loads those presets."""
        request = rf.get("/theming/gallery/diff/", {"left": "blue", "right": "purple"})
        request.session = {}
        response = diff_view(request)
        content = response.content.decode()
        assert "preset=blue" in content
        assert "preset=purple" in content
