"""
Tests for Phase 2.1 Batch 1 components: modal, dropdown, tabs, table, pagination.

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
# Contract definitions for new components
# ===================================================================


class TestNewContractDefinitions:
    """Verify contracts exist and are well-formed for Batch 1 components."""

    def test_all_ten_components_have_contracts(self):
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
        ):
            assert name in COMPONENT_CONTRACTS, f"Missing contract for {name}"

    def test_modal_contract(self):
        c = get_contract("modal")
        assert c.name == "modal"
        assert any(cv.name == "id" and cv.required for cv in c.required_context)
        assert any(
            el.tag == "div" and el.attrs.get("role") == "dialog" for el in c.required_elements
        )
        assert any(req.attr == "aria-modal" and req.value == "true" for req in c.accessibility)

    def test_modal_slots(self):
        c = get_contract("modal")
        assert "slot_header" in c.available_slots
        assert "slot_body" in c.available_slots
        assert "slot_footer" in c.available_slots
        assert "slot_close" in c.available_slots

    def test_dropdown_contract(self):
        c = get_contract("dropdown")
        assert c.name == "dropdown"
        assert any(cv.name == "id" and cv.required for cv in c.required_context)
        assert any(cv.name == "label" and cv.required for cv in c.required_context)
        assert any(
            el.tag == "button" and el.attrs.get("aria-haspopup") == "true"
            for el in c.required_elements
        )

    def test_dropdown_accessibility(self):
        c = get_contract("dropdown")
        assert any(req.attr == "aria-haspopup" and req.value == "true" for req in c.accessibility)
        assert any(req.attr == "aria-expanded" for req in c.accessibility)

    def test_dropdown_slots(self):
        c = get_contract("dropdown")
        assert "slot_trigger" in c.available_slots
        assert "slot_menu" in c.available_slots

    def test_tabs_contract(self):
        c = get_contract("tabs")
        assert c.name == "tabs"
        assert any(cv.name == "id" and cv.required for cv in c.required_context)
        assert any(cv.name == "tabs" and cv.required for cv in c.required_context)
        assert any(req.attr == "role" and req.value == "tablist" for req in c.accessibility)

    def test_table_contract(self):
        c = get_contract("table")
        assert c.name == "table"
        assert any(cv.name == "headers" and cv.required for cv in c.required_context)
        assert any(cv.name == "rows" and cv.required for cv in c.required_context)
        assert any(el.tag == "table" for el in c.required_elements)
        assert any(el.tag == "div" for el in c.required_elements)

    def test_table_slots(self):
        c = get_contract("table")
        assert "slot_caption" in c.available_slots
        assert "slot_header" in c.available_slots
        assert "slot_body" in c.available_slots
        assert "slot_footer" in c.available_slots

    def test_pagination_contract(self):
        c = get_contract("pagination")
        assert c.name == "pagination"
        assert any(cv.name == "current_page" and cv.required for cv in c.required_context)
        assert any(cv.name == "total_pages" and cv.required for cv in c.required_context)
        assert any(
            el.tag == "nav" and el.attrs.get("aria-label") == "Pagination"
            for el in c.required_elements
        )

    def test_pagination_slots(self):
        c = get_contract("pagination")
        assert "slot_prev" in c.available_slots
        assert "slot_next" in c.available_slots


# ===================================================================
# Modal component tests
# ===================================================================


class TestModalComponent(ComponentTestCase):
    """Modal rendering, contract, and accessibility tests."""

    def test_basic_render(self):
        html = self.render_component("modal", id="test-modal", title="Test Modal")
        self.assert_has_element(html, "div", {"role": "dialog"})
        self.assert_contains(html, "Test Modal")
        self.assert_contains(html, 'data-theme-modal="test-modal"')

    def test_contract(self):
        html = self.render_component("modal", id="m1", title="Title")
        self.assert_contract(html, "modal")

    def test_accessible(self):
        html = self.render_component("modal", id="m2", title="Accessible")
        self.assert_accessible(html, "modal")
        self.assert_has_element(html, "div", {"aria-modal": "true"})

    def test_aria_labelledby(self):
        html = self.render_component("modal", id="m3", title="Labeled")
        self.assert_has_element(html, "div", {"aria-labelledby": "m3-title"})

    def test_size_classes(self):
        html = self.render_component("modal", id="m4", size="lg")
        self.assert_has_class(html, "modal-lg")

    def test_default_close_button(self):
        html = self.render_component("modal", id="m5")
        self.assert_has_element(html, "button", {"data-theme-modal-close": None})
        self.assert_has_element(html, "button", {"aria-label": "Close"})

    def test_slot_header(self):
        request = self._make_mock_request()
        with patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True):
            from djust.theming.template_resolver import resolve_component_template

            tmpl = resolve_component_template(request, "modal")
            html = tmpl.render(
                {
                    "id": "m6",
                    "title": "Old",
                    "size": "md",
                    "css_prefix": "",
                    "attrs": {},
                    "slot_header": "<h3>Custom Header</h3>",
                }
            )
        self.assert_contains(html, "<h3>Custom Header</h3>")
        self.assert_not_contains(html, "Old")

    def test_slot_body(self):
        request = self._make_mock_request()
        with patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True):
            from djust.theming.template_resolver import resolve_component_template

            tmpl = resolve_component_template(request, "modal")
            html = tmpl.render(
                {
                    "id": "m7",
                    "title": None,
                    "size": "md",
                    "css_prefix": "",
                    "attrs": {},
                    "slot_body": "<p>Custom body content</p>",
                }
            )
        self.assert_contains(html, "<p>Custom body content</p>")

    def test_slot_footer(self):
        request = self._make_mock_request()
        with patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True):
            from djust.theming.template_resolver import resolve_component_template

            tmpl = resolve_component_template(request, "modal")
            html = tmpl.render(
                {
                    "id": "m8",
                    "title": None,
                    "size": "md",
                    "css_prefix": "",
                    "attrs": {},
                    "slot_footer": '<button class="confirm">OK</button>',
                }
            )
        self.assert_contains(html, "modal-footer")
        self.assert_contains(html, '<button class="confirm">OK</button>')

    def test_slot_close(self):
        request = self._make_mock_request()
        with patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True):
            from djust.theming.template_resolver import resolve_component_template

            tmpl = resolve_component_template(request, "modal")
            html = tmpl.render(
                {
                    "id": "m9",
                    "title": None,
                    "size": "md",
                    "css_prefix": "",
                    "attrs": {},
                    "slot_close": '<button class="custom-close">X</button>',
                }
            )
        self.assert_contains(html, '<button class="custom-close">X</button>')
        self.assert_not_contains(html, "modal-close")


# ===================================================================
# Dropdown component tests
# ===================================================================


class TestDropdownComponent(ComponentTestCase):
    """Dropdown rendering, contract, and accessibility tests."""

    def test_basic_render(self):
        html = self.render_component("dropdown", id="dd1", label="Menu")
        self.assert_has_element(html, "div")
        self.assert_has_element(html, "button", {"aria-haspopup": "true"})
        self.assert_contains(html, "Menu")
        self.assert_contains(html, 'data-theme-dropdown="dd1"')

    def test_contract(self):
        html = self.render_component("dropdown", id="dd2", label="Actions")
        self.assert_contract(html, "dropdown")

    def test_accessible(self):
        html = self.render_component("dropdown", id="dd3", label="Options")
        self.assert_accessible(html, "dropdown")
        self.assert_has_element(html, "button", {"aria-expanded": "false"})

    def test_menu_role(self):
        html = self.render_component("dropdown", id="dd4", label="Test")
        self.assert_has_element(html, "div", {"role": "menu"})

    def test_align_right(self):
        html = self.render_component("dropdown", id="dd5", label="Test", align="right")
        self.assert_has_class(html, "dropdown-right")

    def test_align_left(self):
        html = self.render_component("dropdown", id="dd6", label="Test", align="left")
        self.assert_has_class(html, "dropdown-left")

    def test_slot_trigger(self):
        request = self._make_mock_request()
        with patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True):
            from djust.theming.template_resolver import resolve_component_template

            tmpl = resolve_component_template(request, "dropdown")
            html = tmpl.render(
                {
                    "id": "dd7",
                    "label": "Old",
                    "align": "left",
                    "css_prefix": "",
                    "attrs": {},
                    "slot_trigger": '<button class="custom-trigger" aria-haspopup="true" aria-expanded="false">Custom</button>',
                }
            )
        self.assert_contains(html, "custom-trigger")
        self.assert_not_contains(html, "dropdown-trigger")

    def test_slot_menu(self):
        request = self._make_mock_request()
        with patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True):
            from djust.theming.template_resolver import resolve_component_template

            tmpl = resolve_component_template(request, "dropdown")
            html = tmpl.render(
                {
                    "id": "dd8",
                    "label": "Menu",
                    "align": "left",
                    "css_prefix": "",
                    "attrs": {},
                    "slot_menu": '<a class="dropdown-item" role="menuitem">Edit</a>',
                }
            )
        self.assert_contains(html, 'role="menuitem"')
        self.assert_contains(html, "Edit")


# ===================================================================
# Tabs component tests
# ===================================================================


class TestTabsComponent(ComponentTestCase):
    """Tabs rendering, contract, and accessibility tests."""

    def _sample_tabs(self):
        return [
            {"label": "Tab 1", "content": "<p>Content 1</p>"},
            {"label": "Tab 2", "content": "<p>Content 2</p>"},
            {"label": "Tab 3", "content": "<p>Content 3</p>"},
        ]

    def test_basic_render(self):
        html = self.render_component("tabs", id="t1", tabs=self._sample_tabs())
        self.assert_has_element(html, "div", {"role": "tablist"})
        self.assert_contains(html, "Tab 1")
        self.assert_contains(html, "Tab 2")
        self.assert_contains(html, "Content 1")

    def test_contract(self):
        html = self.render_component("tabs", id="t2", tabs=self._sample_tabs())
        self.assert_contract(html, "tabs")

    def test_accessible(self):
        html = self.render_component("tabs", id="t3", tabs=self._sample_tabs())
        self.assert_accessible(html, "tabs")

    def test_tab_roles(self):
        html = self.render_component("tabs", id="t4", tabs=self._sample_tabs())
        self.assert_has_element(html, "button", {"role": "tab"})
        self.assert_has_element(html, "div", {"role": "tabpanel"})

    def test_active_tab(self):
        html = self.render_component("tabs", id="t5", tabs=self._sample_tabs(), active=1)
        self.assert_has_element(html, "button", {"aria-selected": "true", "id": "t5-tab-1"})

    def test_aria_controls(self):
        html = self.render_component("tabs", id="t6", tabs=self._sample_tabs())
        self.assert_has_element(html, "button", {"aria-controls": "t6-panel-0"})
        self.assert_has_element(html, "div", {"aria-labelledby": "t6-tab-0"})

    def test_inactive_tabs_have_tabindex(self):
        html = self.render_component("tabs", id="t7", tabs=self._sample_tabs())
        self.assert_has_element(html, "button", {"tabindex": "-1"})

    def test_data_theme_tabs_attr(self):
        html = self.render_component("tabs", id="t8", tabs=self._sample_tabs())
        self.assert_contains(html, 'data-theme-tabs="t8"')


# ===================================================================
# Table component tests
# ===================================================================


class TestTableComponent(ComponentTestCase):
    """Table rendering, contract, and slot tests."""

    def _sample_data(self):
        return {
            "headers": ["Name", "Email", "Role"],
            "rows": [
                ["Alice", "alice@example.com", "Admin"],
                ["Bob", "bob@example.com", "User"],
            ],
        }

    def test_basic_render(self):
        d = self._sample_data()
        html = self.render_component("table", headers=d["headers"], rows=d["rows"])
        self.assert_has_element(html, "table")
        self.assert_has_element(html, "div")
        self.assert_contains(html, "Alice")
        self.assert_contains(html, "Email")

    def test_contract(self):
        d = self._sample_data()
        html = self.render_component("table", headers=d["headers"], rows=d["rows"])
        self.assert_contract(html, "table")

    def test_variant_striped(self):
        d = self._sample_data()
        html = self.render_component(
            "table", headers=d["headers"], rows=d["rows"], variant="striped"
        )
        self.assert_has_class(html, "table-striped")

    def test_variant_hover(self):
        d = self._sample_data()
        html = self.render_component("table", headers=d["headers"], rows=d["rows"], variant="hover")
        self.assert_has_class(html, "table-hover")

    def test_caption(self):
        d = self._sample_data()
        html = self.render_component(
            "table", headers=d["headers"], rows=d["rows"], caption="User List"
        )
        self.assert_contains(html, "User List")
        self.assert_has_element(html, "caption")

    def test_all_rows_rendered(self):
        d = self._sample_data()
        html = self.render_component("table", headers=d["headers"], rows=d["rows"])
        self.assert_contains(html, "Alice")
        self.assert_contains(html, "Bob")
        self.assert_contains(html, "alice@example.com")

    def test_all_headers_rendered(self):
        d = self._sample_data()
        html = self.render_component("table", headers=d["headers"], rows=d["rows"])
        self.assert_contains(html, "Name")
        self.assert_contains(html, "Email")
        self.assert_contains(html, "Role")

    def test_slot_header(self):
        request = self._make_mock_request()
        with patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True):
            from djust.theming.template_resolver import resolve_component_template

            tmpl = resolve_component_template(request, "table")
            html = tmpl.render(
                {
                    "headers": ["A"],
                    "rows": [["1"]],
                    "variant": "default",
                    "caption": None,
                    "css_prefix": "",
                    "attrs": {},
                    "slot_header": '<tr><th class="custom-header">Custom</th></tr>',
                }
            )
        self.assert_contains(html, "custom-header")
        self.assert_not_contains(html, ">A<")

    def test_slot_body(self):
        request = self._make_mock_request()
        with patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True):
            from djust.theming.template_resolver import resolve_component_template

            tmpl = resolve_component_template(request, "table")
            html = tmpl.render(
                {
                    "headers": ["A"],
                    "rows": [["1"]],
                    "variant": "default",
                    "caption": None,
                    "css_prefix": "",
                    "attrs": {},
                    "slot_body": '<tr><td class="custom-cell">Custom</td></tr>',
                }
            )
        self.assert_contains(html, "custom-cell")

    def test_slot_footer(self):
        request = self._make_mock_request()
        with patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True):
            from djust.theming.template_resolver import resolve_component_template

            tmpl = resolve_component_template(request, "table")
            html = tmpl.render(
                {
                    "headers": ["A"],
                    "rows": [["1"]],
                    "variant": "default",
                    "caption": None,
                    "css_prefix": "",
                    "attrs": {},
                    "slot_footer": "<tr><td>Total: 1</td></tr>",
                }
            )
        self.assert_contains(html, "Total: 1")
        self.assert_has_element(html, "tfoot")

    def test_slot_caption(self):
        request = self._make_mock_request()
        with patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True):
            from djust.theming.template_resolver import resolve_component_template

            tmpl = resolve_component_template(request, "table")
            html = tmpl.render(
                {
                    "headers": ["A"],
                    "rows": [["1"]],
                    "variant": "default",
                    "caption": "Old Caption",
                    "css_prefix": "",
                    "attrs": {},
                    "slot_caption": "<strong>Custom Caption</strong>",
                }
            )
        self.assert_contains(html, "Custom Caption")
        self.assert_not_contains(html, "Old Caption")


# ===================================================================
# Pagination component tests
# ===================================================================


class TestPaginationComponent(ComponentTestCase):
    """Pagination rendering, contract, and accessibility tests."""

    def test_basic_render(self):
        html = self.render_component(
            "pagination", current_page=1, total_pages=5, url_pattern="/items/?page={}"
        )
        self.assert_has_element(html, "nav", {"aria-label": "Pagination"})
        self.assert_contains(html, "Prev")
        self.assert_contains(html, "Next")

    def test_contract(self):
        html = self.render_component(
            "pagination", current_page=3, total_pages=10, url_pattern="/p/{}"
        )
        self.assert_contract(html, "pagination")

    def test_accessible(self):
        html = self.render_component(
            "pagination", current_page=1, total_pages=5, url_pattern="/p/{}"
        )
        self.assert_accessible(html, "pagination")

    def test_active_page_aria_current(self):
        html = self.render_component(
            "pagination", current_page=3, total_pages=5, url_pattern="/p/{}"
        )
        self.assert_has_element(html, "span", {"aria-current": "page"})
        self.assert_contains(html, ">3<")

    def test_first_page_prev_disabled(self):
        html = self.render_component(
            "pagination", current_page=1, total_pages=5, url_pattern="/p/{}"
        )
        self.assert_has_class(html, "pagination-disabled")

    def test_last_page_next_disabled(self):
        html = self.render_component(
            "pagination", current_page=5, total_pages=5, url_pattern="/p/{}"
        )
        # Next should be disabled
        self.assert_contains(html, "pagination-disabled")

    def test_middle_page_both_enabled(self):
        html = self.render_component(
            "pagination", current_page=3, total_pages=5, url_pattern="/p/{}"
        )
        self.assert_contains(html, "/p/2")  # prev URL
        self.assert_contains(html, "/p/4")  # next URL

    def test_single_page(self):
        html = self.render_component(
            "pagination", current_page=1, total_pages=1, url_pattern="/p/{}"
        )
        self.assert_has_element(html, "nav", {"aria-label": "Pagination"})

    def test_url_pattern_substitution(self):
        html = self.render_component(
            "pagination", current_page=2, total_pages=5, url_pattern="/items/?page={}"
        )
        self.assert_contains(html, "/items/?page=1")
        self.assert_contains(html, "/items/?page=3")

    def test_slot_prev(self):
        request = self._make_mock_request()
        with patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True):
            # Use render via template directly
            from djust.theming.template_resolver import resolve_component_template

            tmpl = resolve_component_template(request, "pagination")
            html = tmpl.render(
                {
                    "current_page": 2,
                    "total_pages": 5,
                    "url_pattern": "/p/{}",
                    "show_edges": True,
                    "page_range": [
                        {"number": 1, "url": "/p/1"},
                        {"number": 2, "url": "/p/2"},
                        {"number": 3, "url": "/p/3"},
                    ],
                    "first_page": None,
                    "first_url": None,
                    "first_ellipsis": False,
                    "last_page": 5,
                    "last_url": "/p/5",
                    "last_ellipsis": True,
                    "prev_url": "/p/1",
                    "next_url": "/p/3",
                    "css_prefix": "",
                    "attrs": {},
                    "slot_prev": '<a href="/p/1" class="custom-prev">Back</a>',
                }
            )
        self.assert_contains(html, "custom-prev")
        self.assert_contains(html, "Back")

    def test_slot_next(self):
        request = self._make_mock_request()
        with patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True):
            from djust.theming.template_resolver import resolve_component_template

            tmpl = resolve_component_template(request, "pagination")
            html = tmpl.render(
                {
                    "current_page": 2,
                    "total_pages": 5,
                    "url_pattern": "/p/{}",
                    "show_edges": True,
                    "page_range": [{"number": 2, "url": "/p/2"}],
                    "first_page": 1,
                    "first_url": "/p/1",
                    "first_ellipsis": False,
                    "last_page": 5,
                    "last_url": "/p/5",
                    "last_ellipsis": True,
                    "prev_url": "/p/1",
                    "next_url": "/p/3",
                    "css_prefix": "",
                    "attrs": {},
                    "slot_next": '<a href="/p/3" class="custom-next">Forward</a>',
                }
            )
        self.assert_contains(html, "custom-next")
        self.assert_contains(html, "Forward")


# ===================================================================
# Cross-component: all new components render without error
# ===================================================================


class TestAllNewComponentsRender(ComponentTestCase):
    """Every new component renders without error with minimal context."""

    def test_modal_minimal(self):
        html = self.render_component("modal", id="m")
        assert len(html) > 10

    def test_dropdown_minimal(self):
        html = self.render_component("dropdown", id="d", label="D")
        assert len(html) > 10

    def test_tabs_minimal(self):
        html = self.render_component("tabs", id="t", tabs=[{"label": "A", "content": "B"}])
        assert len(html) > 10

    def test_table_minimal(self):
        html = self.render_component("table", headers=["H"], rows=[["V"]])
        assert len(html) > 10

    def test_pagination_minimal(self):
        html = self.render_component(
            "pagination", current_page=1, total_pages=1, url_pattern="/p/{}"
        )
        assert len(html) > 10


# ===================================================================
# Backward compatibility: existing components still work
# ===================================================================


class TestExistingComponentsStillWork(ComponentTestCase):
    """Existing 5 components render correctly after adding new ones."""

    def test_button_renders(self):
        html = self.render_component("button", text="Go")
        self.assert_contract(html, "button")

    def test_card_renders(self):
        html = self.render_component("card", title="T")
        self.assert_contract(html, "card")

    def test_alert_renders(self):
        html = self.render_component("alert", message="M")
        self.assert_contract(html, "alert")

    def test_badge_renders(self):
        html = self.render_component("badge", text="B")
        self.assert_contract(html, "badge")

    def test_input_renders(self):
        html = self.render_component("input", name="n", label="L")
        self.assert_contract(html, "input")


if __name__ == "__main__":
    pytest.main([__file__])
