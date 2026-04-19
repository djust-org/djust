"""
Tests for Phase 2.1 Batch 2a components: select, textarea, checkbox, radio.

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


import pytest

from djust.theming.contracts import COMPONENT_CONTRACTS, get_contract
from djust.tests.theming_component_test_base import ComponentTestCase

pytestmark = pytest.mark.theming


# ===================================================================
# Contract definitions for form components
# ===================================================================


class TestFormContractDefinitions:
    """Verify contracts exist and are well-formed for Batch 2a components."""

    def test_all_fourteen_components_have_contracts(self):
        for name in (
            "button",
            "card",
            "alert",
            "badge",
            "input",
            "modal",
            "dropdown",
            "tabs",
            "table",
            "pagination",
            "select",
            "textarea",
            "checkbox",
            "radio",
        ):
            assert name in COMPONENT_CONTRACTS, f"Missing contract for {name}"

    # -- select --

    def test_select_contract(self):
        c = get_contract("select")
        assert c.name == "select"
        assert any(cv.name == "name" and cv.required for cv in c.required_context)
        assert any(el.tag == "select" for el in c.required_elements)

    def test_select_slots(self):
        c = get_contract("select")
        assert "slot_label" in c.available_slots
        assert "slot_select" in c.available_slots
        assert "slot_help_text" in c.available_slots
        assert "slot_error" in c.available_slots

    def test_select_accessibility(self):
        c = get_contract("select")
        assert any(req.attr == "for" for req in c.accessibility)

    # -- textarea --

    def test_textarea_contract(self):
        c = get_contract("textarea")
        assert c.name == "textarea"
        assert any(cv.name == "name" and cv.required for cv in c.required_context)
        assert any(el.tag == "textarea" for el in c.required_elements)

    def test_textarea_slots(self):
        c = get_contract("textarea")
        assert "slot_label" in c.available_slots
        assert "slot_textarea" in c.available_slots
        assert "slot_help_text" in c.available_slots
        assert "slot_error" in c.available_slots

    def test_textarea_accessibility(self):
        c = get_contract("textarea")
        assert any(req.attr == "for" for req in c.accessibility)

    # -- checkbox --

    def test_checkbox_contract(self):
        c = get_contract("checkbox")
        assert c.name == "checkbox"
        assert any(cv.name == "name" and cv.required for cv in c.required_context)
        assert any(
            el.tag == "input" and el.attrs.get("type") == "checkbox" for el in c.required_elements
        )

    def test_checkbox_slots(self):
        c = get_contract("checkbox")
        assert "slot_label" in c.available_slots
        assert "slot_description" in c.available_slots

    def test_checkbox_accessibility(self):
        c = get_contract("checkbox")
        assert any(req.attr == "for" for req in c.accessibility)

    # -- radio --

    def test_radio_contract(self):
        c = get_contract("radio")
        assert c.name == "radio"
        assert any(cv.name == "name" and cv.required for cv in c.required_context)
        assert any(
            el.tag == "fieldset" and el.attrs.get("role") == "radiogroup"
            for el in c.required_elements
        )

    def test_radio_slots(self):
        c = get_contract("radio")
        assert "slot_label" in c.available_slots
        assert "slot_options" in c.available_slots

    def test_radio_accessibility(self):
        c = get_contract("radio")
        assert any(req.attr == "role" and req.value == "radiogroup" for req in c.accessibility)


# ===================================================================
# Select rendering tests
# ===================================================================


class TestSelectRendering(ComponentTestCase):
    """Test theme_select component rendering."""

    def test_renders_select_element(self):
        html = self.render_component("select", name="country")
        self.assert_has_element(html, "select")
        self.assert_has_element(html, "select", {"name": "country", "id": "country"})

    def test_renders_with_label(self):
        html = self.render_component("select", name="country", label="Country")
        self.assert_has_element(html, "label", {"for": "country"})
        self.assert_contains(html, "Country")

    def test_renders_without_label(self):
        html = self.render_component("select", name="country")
        self.assert_not_contains(html, "<label")

    def test_renders_placeholder_option(self):
        html = self.render_component("select", name="country", placeholder="Choose one")
        self.assert_contains(html, "Choose one")
        self.assert_contains(html, "disabled")

    def test_renders_options(self):
        opts = [
            {"value": "us", "label": "United States"},
            {"value": "ca", "label": "Canada"},
        ]
        html = self.render_component("select", name="country", options=opts)
        self.assert_contains(html, 'value="us"')
        self.assert_contains(html, "United States")
        self.assert_contains(html, 'value="ca"')
        self.assert_contains(html, "Canada")

    def test_renders_selected_option(self):
        opts = [
            {"value": "us", "label": "United States", "selected": True},
            {"value": "ca", "label": "Canada"},
        ]
        html = self.render_component("select", name="country", options=opts)
        self.assert_contains(html, "selected")

    def test_required_attribute(self):
        html = self.render_component("select", name="country", required=True)
        self.assert_contains(html, "required")

    def test_disabled_attribute(self):
        html = self.render_component("select", name="country", disabled=True)
        self.assert_contains(html, "disabled")

    def test_css_prefix(self):
        html = self.render_component("select", name="country")
        self.assert_has_element(html, "div")

    def test_contract_validation(self):
        opts = [{"value": "a", "label": "A"}]
        html = self.render_component("select", name="country", label="Country", options=opts)
        self.assert_contract(html, "select")


# ===================================================================
# Select slot tests
# ===================================================================


class TestSelectSlots(ComponentTestCase):
    """Test slot overrides for theme_select."""

    def test_slot_label_overrides_default(self):
        html = self.render_component(
            "select",
            name="x",
            label="Default",
            slot_label='<label for="x" class="custom">Custom Label</label>',
        )
        self.assert_contains(html, "Custom Label")
        self.assert_not_contains(html, "Default")

    def test_slot_select_overrides_default(self):
        html = self.render_component(
            "select",
            name="x",
            slot_select='<select name="x" id="x"><option>Custom</option></select>',
        )
        self.assert_contains(html, "Custom")

    def test_slot_help_text(self):
        html = self.render_component(
            "select",
            name="x",
            slot_help_text="<span>Pick one</span>",
        )
        self.assert_contains(html, "Pick one")

    def test_slot_error(self):
        html = self.render_component(
            "select",
            name="x",
            slot_error="<span>Required field</span>",
        )
        self.assert_contains(html, "Required field")


# ===================================================================
# Textarea rendering tests
# ===================================================================


class TestTextareaRendering(ComponentTestCase):
    """Test theme_textarea component rendering."""

    def test_renders_textarea_element(self):
        html = self.render_component("textarea", name="bio")
        self.assert_has_element(html, "textarea")
        self.assert_has_element(html, "textarea", {"name": "bio", "id": "bio"})

    def test_renders_with_label(self):
        html = self.render_component("textarea", name="bio", label="Biography")
        self.assert_has_element(html, "label", {"for": "bio"})
        self.assert_contains(html, "Biography")

    def test_renders_without_label(self):
        html = self.render_component("textarea", name="bio")
        self.assert_not_contains(html, "<label")

    def test_renders_with_placeholder(self):
        html = self.render_component("textarea", name="bio", placeholder="Tell us...")
        self.assert_contains(html, 'placeholder="Tell us..."')

    def test_renders_with_rows(self):
        html = self.render_component("textarea", name="bio", rows=8)
        self.assert_contains(html, 'rows="8"')

    def test_default_rows_is_4(self):
        html = self.render_component("textarea", name="bio")
        self.assert_contains(html, 'rows="4"')

    def test_required_attribute(self):
        html = self.render_component("textarea", name="bio", required=True)
        self.assert_contains(html, "required")

    def test_disabled_attribute(self):
        html = self.render_component("textarea", name="bio", disabled=True)
        self.assert_contains(html, "disabled")

    def test_readonly_attribute(self):
        html = self.render_component("textarea", name="bio", readonly=True)
        self.assert_contains(html, "readonly")

    def test_contract_validation(self):
        html = self.render_component("textarea", name="bio", label="Bio")
        self.assert_contract(html, "textarea")


# ===================================================================
# Textarea slot tests
# ===================================================================


class TestTextareaSlots(ComponentTestCase):
    """Test slot overrides for theme_textarea."""

    def test_slot_label_overrides_default(self):
        html = self.render_component(
            "textarea",
            name="x",
            label="Default",
            slot_label='<label for="x" class="custom">Custom</label>',
        )
        self.assert_contains(html, "Custom")
        self.assert_not_contains(html, "Default")

    def test_slot_textarea_overrides_default(self):
        html = self.render_component(
            "textarea",
            name="x",
            slot_textarea='<textarea name="x" id="x">Custom content</textarea>',
        )
        self.assert_contains(html, "Custom content")

    def test_slot_help_text(self):
        html = self.render_component(
            "textarea",
            name="x",
            slot_help_text="<span>Max 500 chars</span>",
        )
        self.assert_contains(html, "Max 500 chars")

    def test_slot_error(self):
        html = self.render_component(
            "textarea",
            name="x",
            slot_error="<span>Too long</span>",
        )
        self.assert_contains(html, "Too long")


# ===================================================================
# Checkbox rendering tests
# ===================================================================


class TestCheckboxRendering(ComponentTestCase):
    """Test theme_checkbox component rendering."""

    def test_renders_checkbox_input(self):
        html = self.render_component("checkbox", name="agree")
        self.assert_has_element(html, "input", {"type": "checkbox", "name": "agree"})

    def test_renders_with_label(self):
        html = self.render_component("checkbox", name="agree", label="I agree")
        self.assert_has_element(html, "label", {"for": "agree"})
        self.assert_contains(html, "I agree")

    def test_renders_without_label(self):
        html = self.render_component("checkbox", name="agree")
        self.assert_not_contains(html, "<label")

    def test_renders_with_description(self):
        html = self.render_component(
            "checkbox",
            name="agree",
            label="Agree",
            description="Read terms first",
        )
        self.assert_contains(html, "Read terms first")

    def test_checked_attribute(self):
        html = self.render_component("checkbox", name="agree", checked=True)
        self.assert_contains(html, "checked")

    def test_required_attribute(self):
        html = self.render_component("checkbox", name="agree", required=True)
        self.assert_contains(html, "required")

    def test_disabled_attribute(self):
        html = self.render_component("checkbox", name="agree", disabled=True)
        self.assert_contains(html, "disabled")

    def test_contract_validation(self):
        html = self.render_component("checkbox", name="agree", label="I agree")
        self.assert_contract(html, "checkbox")


# ===================================================================
# Checkbox slot tests
# ===================================================================


class TestCheckboxSlots(ComponentTestCase):
    """Test slot overrides for theme_checkbox."""

    def test_slot_label_overrides_default(self):
        html = self.render_component(
            "checkbox",
            name="x",
            label="Default",
            slot_label='<label for="x" class="custom">Custom</label>',
        )
        self.assert_contains(html, "Custom")
        self.assert_not_contains(html, "Default")

    def test_slot_description_overrides_default(self):
        html = self.render_component(
            "checkbox",
            name="x",
            description="Default desc",
            slot_description="<p>Custom description</p>",
        )
        self.assert_contains(html, "Custom description")
        self.assert_not_contains(html, "Default desc")


# ===================================================================
# Radio rendering tests
# ===================================================================


class TestRadioRendering(ComponentTestCase):
    """Test theme_radio component rendering."""

    SAMPLE_OPTIONS = [
        {"value": "sm", "label": "Small"},
        {"value": "md", "label": "Medium"},
        {"value": "lg", "label": "Large"},
    ]

    def test_renders_fieldset_with_radiogroup_role(self):
        html = self.render_component("radio", name="size", options=self.SAMPLE_OPTIONS)
        self.assert_has_element(html, "fieldset", {"role": "radiogroup"})

    def test_renders_with_label_as_legend(self):
        html = self.render_component(
            "radio",
            name="size",
            label="Size",
            options=self.SAMPLE_OPTIONS,
        )
        self.assert_contains(html, "<legend")
        self.assert_contains(html, "Size")

    def test_renders_without_label(self):
        html = self.render_component("radio", name="size", options=self.SAMPLE_OPTIONS)
        self.assert_not_contains(html, "<legend")

    def test_renders_radio_inputs(self):
        html = self.render_component("radio", name="size", options=self.SAMPLE_OPTIONS)
        self.assert_has_element(html, "input", {"type": "radio", "name": "size"})
        self.assert_contains(html, 'value="sm"')
        self.assert_contains(html, 'value="md"')
        self.assert_contains(html, 'value="lg"')

    def test_renders_labels_for_each_option(self):
        html = self.render_component("radio", name="size", options=self.SAMPLE_OPTIONS)
        self.assert_contains(html, "Small")
        self.assert_contains(html, "Medium")
        self.assert_contains(html, "Large")
        self.assert_has_element(html, "label", {"for": "size_sm"})
        self.assert_has_element(html, "label", {"for": "size_md"})

    def test_selected_option(self):
        html = self.render_component(
            "radio",
            name="size",
            options=self.SAMPLE_OPTIONS,
            selected="md",
        )
        self.assert_contains(html, "checked")

    def test_required_attribute(self):
        html = self.render_component(
            "radio",
            name="size",
            options=self.SAMPLE_OPTIONS,
            required=True,
        )
        self.assert_contains(html, "required")

    def test_disabled_attribute(self):
        html = self.render_component(
            "radio",
            name="size",
            options=self.SAMPLE_OPTIONS,
            disabled=True,
        )
        self.assert_contains(html, "disabled")

    def test_contract_validation(self):
        html = self.render_component(
            "radio",
            name="size",
            label="Size",
            options=self.SAMPLE_OPTIONS,
        )
        self.assert_contract(html, "radio")

    def test_empty_options_renders_fieldset(self):
        html = self.render_component("radio", name="size")
        self.assert_has_element(html, "fieldset", {"role": "radiogroup"})


# ===================================================================
# Radio slot tests
# ===================================================================


class TestRadioSlots(ComponentTestCase):
    """Test slot overrides for theme_radio."""

    def test_slot_label_overrides_legend(self):
        html = self.render_component(
            "radio",
            name="x",
            label="Default",
            slot_label="<legend>Custom Legend</legend>",
        )
        self.assert_contains(html, "Custom Legend")
        self.assert_not_contains(html, "Default")

    def test_slot_options_overrides_default(self):
        opts = [{"value": "a", "label": "A"}]
        html = self.render_component(
            "radio",
            name="x",
            options=opts,
            slot_options='<div><input type="radio" name="x" id="x_custom" value="custom" /><label for="x_custom">Custom</label></div>',
        )
        self.assert_contains(html, "Custom")
        self.assert_not_contains(html, ">A<")


# ===================================================================
# Accessibility tests
# ===================================================================


class TestFormComponentAccessibility(ComponentTestCase):
    """Cross-component accessibility tests."""

    def test_select_accessible(self):
        html = self.render_component("select", name="x", label="X")
        self.assert_accessible(html, "select")

    def test_textarea_accessible(self):
        html = self.render_component("textarea", name="x", label="X")
        self.assert_accessible(html, "textarea")

    def test_checkbox_accessible(self):
        html = self.render_component("checkbox", name="x", label="X")
        self.assert_accessible(html, "checkbox")

    def test_radio_accessible(self):
        opts = [{"value": "a", "label": "A"}]
        html = self.render_component("radio", name="x", label="X", options=opts)
        self.assert_accessible(html, "radio")
