"""
Tests for Phase 2.1 Batch 2b components: breadcrumb, avatar, toast, progress, skeleton, tooltip.

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
# Contract definitions for utility components
# ===================================================================


class TestUtilityContractDefinitions:
    """Verify contracts exist and are well-formed for Batch 2b components."""

    def test_all_twenty_components_have_contracts(self):
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
            "breadcrumb",
            "avatar",
            "toast",
            "progress",
            "skeleton",
            "tooltip",
        ):
            assert name in COMPONENT_CONTRACTS, f"Missing contract for {name}"

    # -- breadcrumb --

    def test_breadcrumb_contract(self):
        c = get_contract("breadcrumb")
        assert c.name == "breadcrumb"
        assert any(cv.name == "items" and cv.required for cv in c.required_context)
        assert any(
            el.tag == "nav" and el.attrs.get("aria-label") == "Breadcrumb"
            for el in c.required_elements
        )
        assert any(el.tag == "ol" for el in c.required_elements)

    def test_breadcrumb_slots(self):
        c = get_contract("breadcrumb")
        assert "slot_separator" in c.available_slots

    def test_breadcrumb_accessibility(self):
        c = get_contract("breadcrumb")
        assert any(
            req.attr == "aria-label" and req.value == "Breadcrumb" for req in c.accessibility
        )

    # -- avatar --

    def test_avatar_contract(self):
        c = get_contract("avatar")
        assert c.name == "avatar"
        assert any(el.tag == "div" for el in c.required_elements)

    def test_avatar_slots(self):
        c = get_contract("avatar")
        assert "slot_image" in c.available_slots
        assert "slot_fallback" in c.available_slots

    # -- toast --

    def test_toast_contract(self):
        c = get_contract("toast")
        assert c.name == "toast"
        assert any(cv.name == "message" and cv.required for cv in c.required_context)
        assert any(
            el.tag == "div" and el.attrs.get("role") == "status" for el in c.required_elements
        )

    def test_toast_slots(self):
        c = get_contract("toast")
        assert "slot_message" in c.available_slots
        assert "slot_actions" in c.available_slots

    def test_toast_accessibility(self):
        c = get_contract("toast")
        assert any(req.attr == "role" and req.value == "status" for req in c.accessibility)
        assert any(req.attr == "aria-live" and req.value == "polite" for req in c.accessibility)

    # -- progress --

    def test_progress_contract(self):
        c = get_contract("progress")
        assert c.name == "progress"
        assert any(
            el.tag == "div" and el.attrs.get("role") == "progressbar" for el in c.required_elements
        )

    def test_progress_slots(self):
        c = get_contract("progress")
        assert "slot_label" in c.available_slots

    def test_progress_accessibility(self):
        c = get_contract("progress")
        assert any(req.attr == "role" and req.value == "progressbar" for req in c.accessibility)

    # -- skeleton --

    def test_skeleton_contract(self):
        c = get_contract("skeleton")
        assert c.name == "skeleton"
        assert any(
            el.tag == "div" and el.attrs.get("aria-hidden") == "true" for el in c.required_elements
        )

    def test_skeleton_no_slots(self):
        c = get_contract("skeleton")
        assert len(c.available_slots) == 0

    # -- tooltip --

    def test_tooltip_contract(self):
        c = get_contract("tooltip")
        assert c.name == "tooltip"
        assert any(cv.name == "text" and cv.required for cv in c.required_context)
        assert any(el.tag == "span" for el in c.required_elements)

    def test_tooltip_slots(self):
        c = get_contract("tooltip")
        assert "slot_content" in c.available_slots


# ===================================================================
# Breadcrumb rendering tests
# ===================================================================


class TestBreadcrumbRendering(ComponentTestCase):
    """Test theme_breadcrumb component rendering."""

    SAMPLE_ITEMS = [
        {"label": "Home", "url": "/"},
        {"label": "Products", "url": "/products/"},
        {"label": "Widget", "url": "/products/widget/"},
    ]

    def test_renders_nav_with_aria_label(self):
        html = self.render_component("breadcrumb", items=self.SAMPLE_ITEMS)
        self.assert_has_element(html, "nav", {"aria-label": "Breadcrumb"})

    def test_renders_ordered_list(self):
        html = self.render_component("breadcrumb", items=self.SAMPLE_ITEMS)
        self.assert_has_element(html, "ol")

    def test_renders_links_for_non_last_items(self):
        html = self.render_component("breadcrumb", items=self.SAMPLE_ITEMS)
        self.assert_contains(html, 'href="/"')
        self.assert_contains(html, "Home")
        self.assert_contains(html, 'href="/products/"')
        self.assert_contains(html, "Products")

    def test_last_item_has_aria_current(self):
        html = self.render_component("breadcrumb", items=self.SAMPLE_ITEMS)
        self.assert_contains(html, 'aria-current="page"')
        self.assert_contains(html, "Widget")

    def test_last_item_is_not_a_link(self):
        html = self.render_component("breadcrumb", items=self.SAMPLE_ITEMS)
        # The last item should not be wrapped in <a>
        self.assert_not_contains(html, 'href="/products/widget/"')

    def test_default_separator(self):
        html = self.render_component("breadcrumb", items=self.SAMPLE_ITEMS)
        self.assert_contains(html, "/")

    def test_custom_separator(self):
        html = self.render_component("breadcrumb", items=self.SAMPLE_ITEMS, separator=">")
        self.assert_contains(html, ">")

    def test_empty_items(self):
        html = self.render_component("breadcrumb")
        self.assert_has_element(html, "nav", {"aria-label": "Breadcrumb"})

    def test_single_item(self):
        items = [{"label": "Home", "url": "/"}]
        html = self.render_component("breadcrumb", items=items)
        self.assert_contains(html, 'aria-current="page"')
        self.assert_contains(html, "Home")

    def test_contract_validation(self):
        html = self.render_component("breadcrumb", items=self.SAMPLE_ITEMS)
        self.assert_contract(html, "breadcrumb")


# ===================================================================
# Breadcrumb slot tests
# ===================================================================


class TestBreadcrumbSlots(ComponentTestCase):
    """Test slot overrides for theme_breadcrumb."""

    SAMPLE_ITEMS = [
        {"label": "Home", "url": "/"},
        {"label": "Current", "url": "/current/"},
    ]

    def test_slot_separator_overrides_default(self):
        html = self.render_component(
            "breadcrumb",
            items=self.SAMPLE_ITEMS,
            slot_separator='<span class="custom-sep">&rarr;</span>',
        )
        self.assert_contains(html, "custom-sep")


# ===================================================================
# Avatar rendering tests
# ===================================================================


class TestAvatarRendering(ComponentTestCase):
    """Test theme_avatar component rendering."""

    def test_renders_div_wrapper(self):
        html = self.render_component("avatar", src="/img/user.jpg", alt="User")
        self.assert_has_element(html, "div")

    def test_renders_image_when_src_provided(self):
        html = self.render_component("avatar", src="/img/user.jpg", alt="User")
        self.assert_has_element(html, "img", {"src": "/img/user.jpg", "alt": "User"})

    def test_renders_initials_when_name_provided(self):
        html = self.render_component("avatar", name="John Doe")
        self.assert_contains(html, "JD")

    def test_renders_single_initial(self):
        html = self.render_component("avatar", name="Alice")
        self.assert_contains(html, "A")

    def test_size_sm(self):
        html = self.render_component("avatar", name="X", size="sm")
        self.assert_contains(html, "avatar-sm")

    def test_size_md(self):
        html = self.render_component("avatar", name="X", size="md")
        self.assert_contains(html, "avatar-md")

    def test_size_lg(self):
        html = self.render_component("avatar", name="X", size="lg")
        self.assert_contains(html, "avatar-lg")

    def test_image_takes_precedence_over_initials(self):
        html = self.render_component("avatar", src="/img/user.jpg", alt="User", name="John Doe")
        self.assert_has_element(html, "img")
        self.assert_not_contains(html, "JD")

    def test_contract_validation(self):
        html = self.render_component("avatar", name="John Doe")
        self.assert_contract(html, "avatar")


# ===================================================================
# Avatar slot tests
# ===================================================================


class TestAvatarSlots(ComponentTestCase):
    """Test slot overrides for theme_avatar."""

    def test_slot_image_overrides_default(self):
        html = self.render_component(
            "avatar",
            slot_image='<img src="/custom.jpg" alt="Custom" />',
        )
        self.assert_contains(html, "/custom.jpg")

    def test_slot_fallback_overrides_initials(self):
        html = self.render_component(
            "avatar",
            name="John Doe",
            slot_fallback='<span class="custom-fallback">??</span>',
        )
        self.assert_contains(html, "custom-fallback")
        self.assert_not_contains(html, "JD")


# ===================================================================
# Toast rendering tests
# ===================================================================


class TestToastRendering(ComponentTestCase):
    """Test theme_toast component rendering."""

    def test_renders_with_role_status(self):
        html = self.render_component("toast", message="Saved!")
        self.assert_has_element(html, "div", {"role": "status"})

    def test_renders_aria_live(self):
        html = self.render_component("toast", message="Saved!")
        self.assert_has_element(html, "div", {"aria-live": "polite"})

    def test_renders_data_theme_toast(self):
        html = self.render_component("toast", message="Saved!")
        self.assert_contains(html, "data-theme-toast")

    def test_renders_message(self):
        html = self.render_component("toast", message="File saved!")
        self.assert_contains(html, "File saved!")

    def test_variant_success(self):
        html = self.render_component("toast", message="Done", variant="success")
        self.assert_contains(html, "toast-success")

    def test_variant_warning(self):
        html = self.render_component("toast", message="Warning", variant="warning")
        self.assert_contains(html, "toast-warning")

    def test_variant_error(self):
        html = self.render_component("toast", message="Error", variant="error")
        self.assert_contains(html, "toast-error")

    def test_variant_info(self):
        html = self.render_component("toast", message="Info", variant="info")
        self.assert_contains(html, "toast-info")

    def test_position_top_right(self):
        html = self.render_component("toast", message="X", position="top-right")
        self.assert_contains(html, "toast-top-right")

    def test_position_bottom_left(self):
        html = self.render_component("toast", message="X", position="bottom-left")
        self.assert_contains(html, "toast-bottom-left")

    def test_duration_attribute(self):
        html = self.render_component("toast", message="X", duration=3000)
        self.assert_contains(html, 'data-duration="3000"')

    def test_close_button(self):
        html = self.render_component("toast", message="X")
        self.assert_contains(html, 'aria-label="Dismiss"')

    def test_contract_validation(self):
        html = self.render_component("toast", message="Saved!")
        self.assert_contract(html, "toast")


# ===================================================================
# Toast slot tests
# ===================================================================


class TestToastSlots(ComponentTestCase):
    """Test slot overrides for theme_toast."""

    def test_slot_message_overrides_default(self):
        html = self.render_component(
            "toast",
            message="Default",
            slot_message="<strong>Custom message</strong>",
        )
        self.assert_contains(html, "Custom message")
        self.assert_not_contains(html, "Default")

    def test_slot_actions(self):
        html = self.render_component(
            "toast",
            message="X",
            slot_actions="<button>Undo</button>",
        )
        self.assert_contains(html, "Undo")


# ===================================================================
# Progress rendering tests
# ===================================================================


class TestProgressRendering(ComponentTestCase):
    """Test theme_progress component rendering."""

    def test_renders_progressbar_role(self):
        html = self.render_component("progress", value=50)
        self.assert_has_element(html, "div", {"role": "progressbar"})

    def test_renders_aria_valuenow(self):
        html = self.render_component("progress", value=75, max=100)
        self.assert_contains(html, 'aria-valuenow="75"')

    def test_renders_aria_valuemax(self):
        html = self.render_component("progress", value=50, max=200)
        self.assert_contains(html, 'aria-valuemax="200"')

    def test_renders_aria_valuemin(self):
        html = self.render_component("progress", value=50)
        self.assert_contains(html, 'aria-valuemin="0"')

    def test_renders_progress_bar_width(self):
        html = self.render_component("progress", value=75, max=100)
        self.assert_contains(html, "width: 75")

    def test_renders_with_label(self):
        html = self.render_component("progress", value=50, label="Upload")
        self.assert_contains(html, "Upload")

    def test_indeterminate_when_no_value(self):
        html = self.render_component("progress")
        self.assert_contains(html, "progress-indeterminate")
        self.assert_not_contains(html, "aria-valuenow")

    def test_determinate_when_value_set(self):
        html = self.render_component("progress", value=50)
        self.assert_not_contains(html, "progress-indeterminate")

    def test_zero_value(self):
        html = self.render_component("progress", value=0, max=100)
        self.assert_contains(html, 'aria-valuenow="0"')
        self.assert_contains(html, "width: 0")

    def test_contract_validation(self):
        html = self.render_component("progress", value=50)
        self.assert_contract(html, "progress")


# ===================================================================
# Progress slot tests
# ===================================================================


class TestProgressSlots(ComponentTestCase):
    """Test slot overrides for theme_progress."""

    def test_slot_label_overrides_default(self):
        html = self.render_component(
            "progress",
            value=50,
            label="Default Label",
            slot_label='<div class="custom-label">Custom Progress</div>',
        )
        self.assert_contains(html, "Custom Progress")
        # The slot replaces the visible label div, but aria-label may still use label text
        self.assert_not_contains(html, "progress-label")  # default label div not rendered


# ===================================================================
# Skeleton rendering tests
# ===================================================================


class TestSkeletonRendering(ComponentTestCase):
    """Test theme_skeleton component rendering."""

    def test_renders_div_with_aria_hidden(self):
        html = self.render_component("skeleton")
        self.assert_has_element(html, "div", {"aria-hidden": "true"})

    def test_variant_text(self):
        html = self.render_component("skeleton", variant="text")
        self.assert_contains(html, "skeleton-text")

    def test_variant_circle(self):
        html = self.render_component("skeleton", variant="circle")
        self.assert_contains(html, "skeleton-circle")

    def test_variant_rect(self):
        html = self.render_component("skeleton", variant="rect")
        self.assert_contains(html, "skeleton-rect")

    def test_custom_width(self):
        html = self.render_component("skeleton", width="200px")
        self.assert_contains(html, "width: 200px")

    def test_custom_height(self):
        html = self.render_component("skeleton", height="2rem")
        self.assert_contains(html, "height: 2rem")

    def test_default_dimensions(self):
        html = self.render_component("skeleton")
        self.assert_contains(html, "width: 100%")
        self.assert_contains(html, "height: 1rem")

    def test_contract_validation(self):
        html = self.render_component("skeleton")
        self.assert_contract(html, "skeleton")


# ===================================================================
# Tooltip rendering tests
# ===================================================================


class TestTooltipRendering(ComponentTestCase):
    """Test theme_tooltip component rendering."""

    def test_renders_span_with_data_tooltip(self):
        html = self.render_component("tooltip", text="Help text")
        self.assert_has_element(html, "span", {"data-tooltip": "Help text"})

    def test_renders_position_attribute(self):
        html = self.render_component("tooltip", text="Help", position="bottom")
        self.assert_has_element(html, "span", {"data-tooltip-position": "bottom"})

    def test_default_position_is_top(self):
        html = self.render_component("tooltip", text="Help")
        self.assert_has_element(html, "span", {"data-tooltip-position": "top"})

    def test_renders_text_as_content(self):
        html = self.render_component("tooltip", text="Tooltip text")
        self.assert_contains(html, "Tooltip text")

    def test_position_left(self):
        html = self.render_component("tooltip", text="X", position="left")
        self.assert_contains(html, 'data-tooltip-position="left"')

    def test_position_right(self):
        html = self.render_component("tooltip", text="X", position="right")
        self.assert_contains(html, 'data-tooltip-position="right"')

    def test_contract_validation(self):
        html = self.render_component("tooltip", text="Help text")
        self.assert_contract(html, "tooltip")


# ===================================================================
# Tooltip slot tests
# ===================================================================


class TestTooltipSlots(ComponentTestCase):
    """Test slot overrides for theme_tooltip."""

    def test_slot_content_overrides_text_display(self):
        html = self.render_component(
            "tooltip",
            text="Tooltip help",
            slot_content="<button>Hover me</button>",
        )
        self.assert_contains(html, "Hover me")
        # The data-tooltip attribute still has the tooltip text
        self.assert_has_element(html, "span", {"data-tooltip": "Tooltip help"})


# ===================================================================
# Accessibility tests
# ===================================================================


class TestUtilityComponentAccessibility(ComponentTestCase):
    """Cross-component accessibility tests."""

    BREADCRUMB_ITEMS = [
        {"label": "Home", "url": "/"},
        {"label": "Page", "url": "/page/"},
    ]

    def test_breadcrumb_accessible(self):
        html = self.render_component("breadcrumb", items=self.BREADCRUMB_ITEMS)
        self.assert_accessible(html, "breadcrumb")

    def test_toast_accessible(self):
        html = self.render_component("toast", message="Done")
        self.assert_accessible(html, "toast")

    def test_progress_accessible(self):
        html = self.render_component("progress", value=50)
        self.assert_accessible(html, "progress")
