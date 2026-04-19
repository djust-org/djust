"""
Tests for component contracts, slot system, and rendering (Phase 2.2 / I11 / I27).

Uses the ``ComponentTestCase`` harness from ``tests.component_test_base``.
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

from unittest.mock import patch

import pytest

from djust.theming.contracts import COMPONENT_CONTRACTS, get_contract
from djust.tests.theming_component_test_base import ComponentTestCase

pytestmark = pytest.mark.theming


# ===================================================================
# Contract data-structure tests
# ===================================================================


class TestContractDefinitions:
    """Verify contracts are well-formed and complete."""

    def test_all_five_components_have_contracts(self):
        for name in ("button", "card", "alert", "badge", "input"):
            assert name in COMPONENT_CONTRACTS, f"Missing contract for {name}"

    def test_get_contract_returns_correct_contract(self):
        c = get_contract("button")
        assert c.name == "button"

    def test_get_contract_raises_for_unknown(self):
        with pytest.raises(KeyError):
            get_contract("nonexistent")

    def test_button_has_slots(self):
        c = get_contract("button")
        assert "slot_icon" in c.available_slots
        assert "slot_content" in c.available_slots
        assert "slot_loading" in c.available_slots

    def test_card_has_slots(self):
        c = get_contract("card")
        assert "slot_header" in c.available_slots
        assert "slot_body" in c.available_slots
        assert "slot_footer" in c.available_slots

    def test_alert_has_slots(self):
        c = get_contract("alert")
        assert "slot_icon" in c.available_slots
        assert "slot_message" in c.available_slots
        assert "slot_actions" in c.available_slots
        assert "slot_dismiss" in c.available_slots

    def test_badge_has_slots(self):
        c = get_contract("badge")
        assert "slot_content" in c.available_slots

    def test_input_has_slots(self):
        c = get_contract("input")
        assert "slot_label" in c.available_slots
        assert "slot_input" in c.available_slots
        assert "slot_help_text" in c.available_slots
        assert "slot_error" in c.available_slots

    def test_alert_requires_role_alert(self):
        c = get_contract("alert")
        assert any(
            el.tag == "div" and el.attrs.get("role") == "alert" for el in c.required_elements
        )

    def test_alert_has_accessibility_requirements(self):
        c = get_contract("alert")
        assert len(c.accessibility) > 0
        assert any(req.attr == "role" and req.value == "alert" for req in c.accessibility)

    def test_input_has_accessibility_requirements(self):
        c = get_contract("input")
        assert any(req.attr == "for" for req in c.accessibility)


# ===================================================================
# Button component tests
# ===================================================================


class TestButtonComponent(ComponentTestCase):
    """Button rendering, contract, and slot tests."""

    def test_basic_render(self):
        html = self.render_component("button", text="Click me")
        self.assert_has_element(html, "button")
        self.assert_contains(html, "Click me")

    def test_contract(self):
        html = self.render_component("button", text="OK")
        self.assert_contract(html, "button")

    def test_variant_classes(self):
        html = self.render_component("button", text="Go", variant="destructive")
        self.assert_has_class(html, "btn-destructive")

    def test_size_classes(self):
        html = self.render_component("button", text="Go", size="lg")
        self.assert_has_class(html, "btn-lg")

    def test_css_prefix(self):
        """When css_prefix is set, class names are prefixed."""
        from djust.theming.templatetags.theme_components import theme_button

        request = self._make_mock_request()
        ctx = {"request": request}
        config = {"theme": {"css_prefix": "dj-"}}

        with patch.object(settings, "LIVEVIEW_CONFIG", config, create=True):
            html = str(theme_button(ctx, text="Go"))

        self.assert_has_class(html, "dj-btn")
        self.assert_has_class(html, "dj-btn-primary")

    def test_slot_icon(self):
        """slot_icon renders before text."""
        from djust.theming.template_resolver import resolve_component_template

        request = self._make_mock_request()
        with patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True):
            tmpl = resolve_component_template(request, "button")
            html = tmpl.render(
                {
                    "text": "Save",
                    "variant": "primary",
                    "size": "md",
                    "css_prefix": "",
                    "attrs": {},
                    "slot_icon": '<svg class="icon-save"></svg>',
                }
            )

        self.assert_contains(html, '<svg class="icon-save"></svg>')
        self.assert_contains(html, "Save")

    def test_slot_content_overrides_text(self):
        request = self._make_mock_request()
        with patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True):
            from djust.theming.template_resolver import resolve_component_template

            tmpl = resolve_component_template(request, "button")
            html = tmpl.render(
                {
                    "text": "Old Text",
                    "variant": "primary",
                    "size": "md",
                    "css_prefix": "",
                    "attrs": {},
                    "slot_content": "<strong>Custom Content</strong>",
                }
            )

        self.assert_contains(html, "<strong>Custom Content</strong>")
        self.assert_not_contains(html, "Old Text")

    def test_slot_loading_overrides_everything(self):
        request = self._make_mock_request()
        with patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True):
            from djust.theming.template_resolver import resolve_component_template

            tmpl = resolve_component_template(request, "button")
            html = tmpl.render(
                {
                    "text": "Save",
                    "variant": "primary",
                    "size": "md",
                    "css_prefix": "",
                    "attrs": {},
                    "slot_icon": "<svg></svg>",
                    "slot_loading": '<span class="spinner"></span>',
                }
            )

        self.assert_contains(html, '<span class="spinner"></span>')
        self.assert_not_contains(html, "Save")
        # Icon should also not appear when loading overrides
        self.assert_not_contains(html, "<svg></svg>")

    def test_default_render_backward_compatible(self):
        """Without slots, output matches original behavior."""
        html = self.render_component("button", text="Click", variant="primary", size="md")
        self.assert_has_element(html, "button")
        self.assert_has_class(html, "btn")
        self.assert_has_class(html, "btn-primary")
        self.assert_has_class(html, "btn-md")
        self.assert_contains(html, "Click")


# ===================================================================
# Card component tests
# ===================================================================


class TestCardComponent(ComponentTestCase):
    """Card rendering, contract, and slot tests."""

    def test_basic_render(self):
        html = self.render_component("card", title="My Card")
        self.assert_has_element(html, "div")
        self.assert_contains(html, "My Card")

    def test_contract(self):
        html = self.render_component("card", title="Test")
        self.assert_contract(html, "card")

    def test_with_footer(self):
        html = self.render_component("card", title="T", footer="Footer text")
        self.assert_contains(html, "Footer text")

    def test_slot_header_overrides_title(self):
        request = self._make_mock_request()
        with patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True):
            from djust.theming.template_resolver import resolve_component_template

            tmpl = resolve_component_template(request, "card")
            html = tmpl.render(
                {
                    "title": "Old Title",
                    "content": "",
                    "footer": None,
                    "css_prefix": "",
                    "attrs": {},
                    "slot_header": "<h2>Custom Header</h2>",
                }
            )

        self.assert_contains(html, "<h2>Custom Header</h2>")
        self.assert_not_contains(html, "Old Title")

    def test_slot_body_overrides_content(self):
        request = self._make_mock_request()
        with patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True):
            from djust.theming.template_resolver import resolve_component_template

            tmpl = resolve_component_template(request, "card")
            html = tmpl.render(
                {
                    "title": None,
                    "content": "Original body",
                    "footer": None,
                    "css_prefix": "",
                    "attrs": {},
                    "slot_body": "<p>Replaced body</p>",
                }
            )

        self.assert_contains(html, "<p>Replaced body</p>")
        self.assert_not_contains(html, "Original body")

    def test_slot_footer_overrides_footer(self):
        request = self._make_mock_request()
        with patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True):
            from djust.theming.template_resolver import resolve_component_template

            tmpl = resolve_component_template(request, "card")
            html = tmpl.render(
                {
                    "title": None,
                    "content": "",
                    "footer": "Old Footer",
                    "css_prefix": "",
                    "attrs": {},
                    "slot_footer": "<div>Custom Footer</div>",
                }
            )

        self.assert_contains(html, "<div>Custom Footer</div>")
        self.assert_not_contains(html, "Old Footer")

    def test_default_render_backward_compatible(self):
        html = self.render_component("card", title="Title", footer="Foot")
        self.assert_has_class(html, "card")
        self.assert_contains(html, "Title")
        self.assert_contains(html, "Foot")


# ===================================================================
# Alert component tests
# ===================================================================


class TestAlertComponent(ComponentTestCase):
    """Alert rendering, contract, and slot tests."""

    def test_basic_render(self):
        html = self.render_component("alert", message="Something happened")
        self.assert_has_element(html, "div", {"role": "alert"})
        self.assert_contains(html, "Something happened")

    def test_contract(self):
        html = self.render_component("alert", message="Error!")
        self.assert_contract(html, "alert")

    def test_accessible(self):
        html = self.render_component("alert", message="Warning!")
        self.assert_accessible(html, "alert")

    def test_variant_classes(self):
        html = self.render_component("alert", message="!", variant="destructive")
        self.assert_has_class(html, "alert-destructive")

    def test_dismissible(self):
        html = self.render_component("alert", message="x", dismissible=True)
        self.assert_has_element(html, "button", {"aria-label": "Dismiss"})

    def test_slot_icon(self):
        request = self._make_mock_request()
        with patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True):
            from djust.theming.template_resolver import resolve_component_template

            tmpl = resolve_component_template(request, "alert")
            html = tmpl.render(
                {
                    "message": "Info",
                    "title": None,
                    "variant": "default",
                    "dismissible": False,
                    "css_prefix": "",
                    "attrs": {},
                    "slot_icon": '<svg class="info-icon"></svg>',
                }
            )

        self.assert_contains(html, '<svg class="info-icon"></svg>')
        self.assert_contains(html, "alert-icon")

    def test_slot_message_overrides_message(self):
        request = self._make_mock_request()
        with patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True):
            from djust.theming.template_resolver import resolve_component_template

            tmpl = resolve_component_template(request, "alert")
            html = tmpl.render(
                {
                    "message": "Old message",
                    "title": None,
                    "variant": "default",
                    "dismissible": False,
                    "css_prefix": "",
                    "attrs": {},
                    "slot_message": "<p>Custom message with <a href='#'>link</a></p>",
                }
            )

        self.assert_contains(html, "Custom message with")
        self.assert_not_contains(html, "Old message")

    def test_slot_actions(self):
        request = self._make_mock_request()
        with patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True):
            from djust.theming.template_resolver import resolve_component_template

            tmpl = resolve_component_template(request, "alert")
            html = tmpl.render(
                {
                    "message": "Proceed?",
                    "title": None,
                    "variant": "default",
                    "dismissible": False,
                    "css_prefix": "",
                    "attrs": {},
                    "slot_actions": '<button class="confirm">Yes</button>',
                }
            )

        self.assert_contains(html, "alert-actions")
        self.assert_contains(html, '<button class="confirm">Yes</button>')

    def test_slot_dismiss_overrides_default(self):
        request = self._make_mock_request()
        with patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True):
            from djust.theming.template_resolver import resolve_component_template

            tmpl = resolve_component_template(request, "alert")
            html = tmpl.render(
                {
                    "message": "x",
                    "title": None,
                    "variant": "default",
                    "dismissible": True,
                    "css_prefix": "",
                    "attrs": {},
                    "slot_dismiss": '<button class="custom-dismiss">X</button>',
                }
            )

        self.assert_contains(html, '<button class="custom-dismiss">X</button>')
        # Default SVG dismiss should not be present
        self.assert_not_contains(html, "alert-dismiss")

    def test_default_render_backward_compatible(self):
        html = self.render_component("alert", message="Msg", variant="success", dismissible=True)
        self.assert_has_class(html, "alert-success")
        self.assert_contains(html, "Msg")
        self.assert_has_element(html, "button", {"aria-label": "Dismiss"})


# ===================================================================
# Badge component tests
# ===================================================================


class TestBadgeComponent(ComponentTestCase):
    """Badge rendering, contract, and slot tests."""

    def test_basic_render(self):
        html = self.render_component("badge", text="New")
        self.assert_has_element(html, "span")
        self.assert_contains(html, "New")

    def test_contract(self):
        html = self.render_component("badge", text="OK")
        self.assert_contract(html, "badge")

    def test_variant_classes(self):
        html = self.render_component("badge", text="!", variant="success")
        self.assert_has_class(html, "badge-success")

    def test_slot_content_overrides_text(self):
        request = self._make_mock_request()
        with patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True):
            from djust.theming.template_resolver import resolve_component_template

            tmpl = resolve_component_template(request, "badge")
            html = tmpl.render(
                {
                    "text": "Old",
                    "variant": "default",
                    "css_prefix": "",
                    "attrs": {},
                    "slot_content": '<img src="icon.png"/> Custom',
                }
            )

        self.assert_contains(html, "Custom")
        self.assert_not_contains(html, "Old")

    def test_default_render_backward_compatible(self):
        html = self.render_component("badge", text="Beta", variant="secondary")
        self.assert_has_class(html, "badge")
        self.assert_has_class(html, "badge-secondary")
        self.assert_contains(html, "Beta")


# ===================================================================
# Input component tests
# ===================================================================


class TestInputComponent(ComponentTestCase):
    """Input rendering, contract, and slot tests."""

    def test_basic_render(self):
        html = self.render_component("input", name="email")
        self.assert_has_element(html, "input")
        self.assert_has_element(html, "div")

    def test_contract(self):
        html = self.render_component("input", name="email", label="Email")
        self.assert_contract(html, "input")

    def test_accessible_label_for(self):
        html = self.render_component("input", name="email", label="Email")
        self.assert_accessible(html, "input")
        self.assert_has_element(html, "label", {"for": "email"})

    def test_input_type(self):
        html = self.render_component("input", name="pw", type="password")
        self.assert_has_element(html, "input", {"type": "password"})

    def test_placeholder(self):
        html = self.render_component("input", name="q", placeholder="Search...")
        self.assert_has_element(html, "input", {"placeholder": "Search..."})

    def test_slot_label_overrides_default(self):
        request = self._make_mock_request()
        with patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True):
            from djust.theming.template_resolver import resolve_component_template

            tmpl = resolve_component_template(request, "input")
            html = tmpl.render(
                {
                    "name": "email",
                    "label": "Old Label",
                    "placeholder": "",
                    "type": "text",
                    "css_prefix": "",
                    "attrs": {},
                    "slot_label": '<label for="email" class="custom-label">Custom Label</label>',
                }
            )

        self.assert_contains(html, "custom-label")
        self.assert_contains(html, "Custom Label")
        self.assert_not_contains(html, "Old Label")

    def test_slot_input_overrides_default(self):
        request = self._make_mock_request()
        with patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True):
            from djust.theming.template_resolver import resolve_component_template

            tmpl = resolve_component_template(request, "input")
            html = tmpl.render(
                {
                    "name": "bio",
                    "label": None,
                    "placeholder": "",
                    "type": "text",
                    "css_prefix": "",
                    "attrs": {},
                    "slot_input": '<textarea name="bio" id="bio" rows="4"></textarea>',
                }
            )

        self.assert_contains(html, "<textarea")
        self.assert_contains(html, 'name="bio"')

    def test_slot_help_text(self):
        request = self._make_mock_request()
        with patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True):
            from djust.theming.template_resolver import resolve_component_template

            tmpl = resolve_component_template(request, "input")
            html = tmpl.render(
                {
                    "name": "email",
                    "label": None,
                    "placeholder": "",
                    "type": "text",
                    "css_prefix": "",
                    "attrs": {},
                    "slot_help_text": "We will never share your email.",
                }
            )

        self.assert_contains(html, "input-help")
        self.assert_contains(html, "We will never share your email.")

    def test_slot_error(self):
        request = self._make_mock_request()
        with patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True):
            from djust.theming.template_resolver import resolve_component_template

            tmpl = resolve_component_template(request, "input")
            html = tmpl.render(
                {
                    "name": "email",
                    "label": None,
                    "placeholder": "",
                    "type": "text",
                    "css_prefix": "",
                    "attrs": {},
                    "slot_error": "This field is required.",
                }
            )

        self.assert_contains(html, "input-error")
        self.assert_contains(html, "This field is required.")

    def test_default_render_backward_compatible(self):
        html = self.render_component(
            "input", name="email", label="Email", placeholder="you@example.com", type="email"
        )
        self.assert_has_class(html, "input-group")
        self.assert_has_element(html, "label", {"for": "email"})
        self.assert_has_element(html, "input", {"type": "email", "name": "email"})
        self.assert_contains(html, "Email")


# ===================================================================
# Cross-component regression tests
# ===================================================================


class TestAllComponentsRender(ComponentTestCase):
    """Every component renders without error with minimal context."""

    def test_button_minimal(self):
        html = self.render_component("button", text="X")
        assert len(html) > 10

    def test_card_minimal(self):
        html = self.render_component("card")
        assert len(html) > 10

    def test_alert_minimal(self):
        html = self.render_component("alert", message="M")
        assert len(html) > 10

    def test_badge_minimal(self):
        html = self.render_component("badge", text="B")
        assert len(html) > 10

    def test_input_minimal(self):
        html = self.render_component("input", name="n")
        assert len(html) > 10


if __name__ == "__main__":
    pytest.main([__file__])
