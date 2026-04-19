"""
Tests for Phase 3.3 navigation components: nav_item, nav_group, nav, sidebar_nav.

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
# Contract definition tests
# ===================================================================


class TestNavigationContractDefinitions:
    """Verify contracts exist and are well-formed for navigation components."""

    # -- nav_item --

    def test_nav_item_contract_exists(self):
        assert "nav_item" in COMPONENT_CONTRACTS

    def test_nav_item_contract(self):
        c = get_contract("nav_item")
        assert c.name == "nav_item"
        assert any(cv.name == "label" and cv.required for cv in c.required_context)
        assert any(cv.name == "url" and cv.required for cv in c.required_context)
        assert any(el.tag == "a" for el in c.required_elements)

    def test_nav_item_slots(self):
        c = get_contract("nav_item")
        assert "slot_icon" in c.available_slots
        assert "slot_badge" in c.available_slots

    def test_nav_item_accessibility(self):
        c = get_contract("nav_item")
        assert any(req.attr == "aria-current" and req.value == "page" for req in c.accessibility)

    # -- nav_group --

    def test_nav_group_contract_exists(self):
        assert "nav_group" in COMPONENT_CONTRACTS

    def test_nav_group_contract(self):
        c = get_contract("nav_group")
        assert c.name == "nav_group"
        assert any(cv.name == "label" and cv.required for cv in c.required_context)
        assert any(el.tag == "details" for el in c.required_elements)
        assert any(el.tag == "summary" for el in c.required_elements)

    def test_nav_group_slots(self):
        c = get_contract("nav_group")
        assert "slot_label" in c.available_slots
        assert "slot_items" in c.available_slots

    # -- nav --

    def test_nav_contract_exists(self):
        assert "nav" in COMPONENT_CONTRACTS

    def test_nav_contract(self):
        c = get_contract("nav")
        assert c.name == "nav"
        assert any(
            el.tag == "nav" and el.attrs.get("role") == "navigation" for el in c.required_elements
        )

    def test_nav_slots(self):
        c = get_contract("nav")
        assert "slot_brand" in c.available_slots
        assert "slot_items" in c.available_slots
        assert "slot_actions" in c.available_slots

    def test_nav_accessibility(self):
        c = get_contract("nav")
        assert any(req.attr == "role" and req.value == "navigation" for req in c.accessibility)
        assert any(req.attr == "aria-label" and req.value == "Main" for req in c.accessibility)

    # -- sidebar_nav --

    def test_sidebar_nav_contract_exists(self):
        assert "sidebar_nav" in COMPONENT_CONTRACTS

    def test_sidebar_nav_contract(self):
        c = get_contract("sidebar_nav")
        assert c.name == "sidebar_nav"
        assert any(
            el.tag == "nav"
            and el.attrs.get("role") == "navigation"
            and el.attrs.get("aria-label") == "Sidebar"
            for el in c.required_elements
        )

    def test_sidebar_nav_slots(self):
        c = get_contract("sidebar_nav")
        assert "slot_header" in c.available_slots
        assert "slot_sections" in c.available_slots
        assert "slot_footer" in c.available_slots

    def test_sidebar_nav_accessibility(self):
        c = get_contract("sidebar_nav")
        assert any(req.attr == "role" and req.value == "navigation" for req in c.accessibility)
        assert any(req.attr == "aria-label" and req.value == "Sidebar" for req in c.accessibility)


# ===================================================================
# theme_nav_item rendering tests
# ===================================================================


class TestNavItemRendering(ComponentTestCase):
    """Test theme_nav_item component rendering."""

    def test_renders_anchor_element(self):
        html = self.render_component("nav_item", label="Home", url="/")
        self.assert_has_element(html, "a")

    def test_renders_href(self):
        html = self.render_component("nav_item", label="Home", url="/")
        self.assert_contains(html, 'href="/"')

    def test_renders_label_text(self):
        html = self.render_component("nav_item", label="Dashboard", url="/dash/")
        self.assert_contains(html, "Dashboard")

    def test_nav_link_class(self):
        html = self.render_component("nav_item", label="Home", url="/")
        self.assert_has_class(html, "nav-link")

    def test_active_explicit_true(self):
        html = self.render_component("nav_item", label="Home", url="/", active=True)
        self.assert_has_class(html, "active")
        self.assert_contains(html, 'aria-current="page"')

    def test_active_explicit_false(self):
        html = self.render_component("nav_item", label="Home", url="/", active=False)
        self.assert_not_contains(html, "active")
        self.assert_not_contains(html, 'aria-current="page"')

    def test_auto_active_matching_path(self):
        """Auto-detect active when request.path matches url."""
        html = self.render_component("nav_item", label="Home", url="/", request_path="/")
        self.assert_has_class(html, "active")

    def test_auto_active_non_matching_path(self):
        """Auto-detect: not active when paths differ."""
        html = self.render_component("nav_item", label="Home", url="/home/", request_path="/other/")
        self.assert_not_contains(html, 'aria-current="page"')

    def test_badge_rendered(self):
        html = self.render_component("nav_item", label="Inbox", url="/inbox/", badge="5")
        self.assert_contains(html, "5")

    def test_icon_rendered(self):
        html = self.render_component("nav_item", label="Home", url="/", icon="home")
        self.assert_contains(html, "home")

    def test_contract_validation(self):
        html = self.render_component("nav_item", label="Home", url="/", active=True)
        self.assert_contract(html, "nav_item")


# ===================================================================
# theme_nav_item slot tests
# ===================================================================


class TestNavItemSlots(ComponentTestCase):
    """Test slot overrides for theme_nav_item."""

    def test_slot_icon(self):
        html = self.render_component(
            "nav_item", label="Home", url="/", slot_icon='<svg class="custom-icon"></svg>'
        )
        self.assert_contains(html, '<svg class="custom-icon"></svg>')

    def test_slot_badge(self):
        html = self.render_component(
            "nav_item",
            label="Inbox",
            url="/inbox/",
            slot_badge='<span class="custom-badge">99+</span>',
        )
        self.assert_contains(html, '<span class="custom-badge">99+</span>')


# ===================================================================
# theme_nav_group rendering tests
# ===================================================================


class TestNavGroupRendering(ComponentTestCase):
    """Test theme_nav_group component rendering."""

    SAMPLE_ITEMS = [
        {"label": "Users", "url": "/admin/users/"},
        {"label": "Groups", "url": "/admin/groups/"},
    ]

    def test_renders_details_element(self):
        html = self.render_component("nav_group", label="Admin")
        self.assert_has_element(html, "details")

    def test_renders_summary_element(self):
        html = self.render_component("nav_group", label="Admin")
        self.assert_has_element(html, "summary")

    def test_renders_label_in_summary(self):
        html = self.render_component("nav_group", label="Admin")
        self.assert_contains(html, "Admin")

    def test_expanded_by_default(self):
        html = self.render_component("nav_group", label="Admin")
        self.assert_has_element(html, "details", {"open": ""})

    def test_collapsed_when_expanded_false(self):
        html = self.render_component("nav_group", label="Admin", expanded=False)
        self.assert_not_contains(html, " open")

    def test_renders_items(self):
        html = self.render_component("nav_group", label="Admin", items=self.SAMPLE_ITEMS)
        self.assert_contains(html, "Users")
        self.assert_contains(html, "Groups")
        self.assert_contains(html, 'href="/admin/users/"')

    def test_empty_items(self):
        html = self.render_component("nav_group", label="Empty")
        self.assert_has_element(html, "details")
        self.assert_has_element(html, "summary")

    def test_icon_rendered(self):
        html = self.render_component("nav_group", label="Admin", icon="settings")
        self.assert_contains(html, "settings")

    def test_contract_validation(self):
        html = self.render_component("nav_group", label="Admin", items=self.SAMPLE_ITEMS)
        self.assert_contract(html, "nav_group")


# ===================================================================
# theme_nav_group slot tests
# ===================================================================


class TestNavGroupSlots(ComponentTestCase):
    """Test slot overrides for theme_nav_group."""

    def test_slot_label(self):
        html = self.render_component(
            "nav_group", label="Admin", slot_label="<strong>Admin Panel</strong>"
        )
        self.assert_contains(html, "<strong>Admin Panel</strong>")

    def test_slot_items(self):
        html = self.render_component(
            "nav_group", label="Admin", slot_items='<a href="/custom/">Custom Link</a>'
        )
        self.assert_contains(html, '<a href="/custom/">Custom Link</a>')


# ===================================================================
# theme_nav rendering tests
# ===================================================================


class TestNavRendering(ComponentTestCase):
    """Test theme_nav component rendering."""

    SAMPLE_ITEMS = [
        {"label": "Home", "url": "/"},
        {"label": "About", "url": "/about/"},
        {"label": "Contact", "url": "/contact/"},
    ]

    def test_renders_nav_element(self):
        html = self.render_component("nav")
        self.assert_has_element(html, "nav", {"role": "navigation"})

    def test_renders_aria_label(self):
        html = self.render_component("nav")
        self.assert_has_element(html, "nav", {"aria-label": "Main"})

    def test_navbar_class(self):
        html = self.render_component("nav")
        self.assert_has_class(html, "navbar")

    def test_renders_brand(self):
        html = self.render_component("nav", brand="MyApp")
        self.assert_contains(html, "MyApp")

    def test_renders_items(self):
        html = self.render_component("nav", items=self.SAMPLE_ITEMS)
        self.assert_contains(html, "Home")
        self.assert_contains(html, "About")
        self.assert_contains(html, 'href="/about/"')

    def test_empty_items(self):
        html = self.render_component("nav")
        self.assert_has_element(html, "nav", {"role": "navigation"})

    def test_brand_class(self):
        html = self.render_component("nav", brand="MyApp")
        self.assert_has_class(html, "navbar-brand")

    def test_contract_validation(self):
        html = self.render_component("nav", items=self.SAMPLE_ITEMS)
        self.assert_contract(html, "nav")


# ===================================================================
# theme_nav slot tests
# ===================================================================


class TestNavSlots(ComponentTestCase):
    """Test slot overrides for theme_nav."""

    def test_slot_brand(self):
        html = self.render_component("nav", slot_brand='<img src="/logo.png" alt="Logo">')
        self.assert_contains(html, '<img src="/logo.png" alt="Logo">')

    def test_slot_items(self):
        html = self.render_component("nav", slot_items='<a href="/custom/">Custom</a>')
        self.assert_contains(html, '<a href="/custom/">Custom</a>')

    def test_slot_actions(self):
        html = self.render_component("nav", slot_actions="<button>Login</button>")
        self.assert_contains(html, "<button>Login</button>")


# ===================================================================
# theme_sidebar_nav rendering tests
# ===================================================================


class TestSidebarNavRendering(ComponentTestCase):
    """Test theme_sidebar_nav component rendering."""

    SAMPLE_SECTIONS = [
        {
            "title": "Main",
            "items": [
                {"label": "Dashboard", "url": "/dash/"},
                {"label": "Analytics", "url": "/analytics/"},
            ],
        },
        {
            "title": "Settings",
            "items": [
                {"label": "Profile", "url": "/settings/profile/"},
                {"label": "Billing", "url": "/settings/billing/"},
            ],
        },
    ]

    def test_renders_nav_element(self):
        html = self.render_component("sidebar_nav")
        self.assert_has_element(html, "nav", {"role": "navigation", "aria-label": "Sidebar"})

    def test_sidebar_class(self):
        html = self.render_component("sidebar_nav")
        self.assert_has_class(html, "sidebar")

    def test_renders_sections(self):
        html = self.render_component("sidebar_nav", sections=self.SAMPLE_SECTIONS)
        self.assert_contains(html, "Main")
        self.assert_contains(html, "Settings")
        self.assert_contains(html, "Dashboard")
        self.assert_contains(html, "Billing")

    def test_renders_section_titles(self):
        html = self.render_component("sidebar_nav", sections=self.SAMPLE_SECTIONS)
        self.assert_has_class(html, "sidebar-title")

    def test_renders_items_as_links(self):
        html = self.render_component("sidebar_nav", sections=self.SAMPLE_SECTIONS)
        self.assert_contains(html, 'href="/dash/"')
        self.assert_contains(html, 'href="/analytics/"')

    def test_sidebar_item_class(self):
        html = self.render_component("sidebar_nav", sections=self.SAMPLE_SECTIONS)
        self.assert_has_class(html, "sidebar-item")

    def test_empty_sections(self):
        html = self.render_component("sidebar_nav")
        self.assert_has_element(html, "nav", {"role": "navigation", "aria-label": "Sidebar"})

    def test_collapse_attribute(self):
        html = self.render_component("sidebar_nav", sections=self.SAMPLE_SECTIONS)
        self.assert_contains(html, "data-theme-sidebar-collapse")

    def test_contract_validation(self):
        html = self.render_component("sidebar_nav", sections=self.SAMPLE_SECTIONS)
        self.assert_contract(html, "sidebar_nav")


# ===================================================================
# theme_sidebar_nav slot tests
# ===================================================================


class TestSidebarNavSlots(ComponentTestCase):
    """Test slot overrides for theme_sidebar_nav."""

    def test_slot_header(self):
        html = self.render_component("sidebar_nav", slot_header='<div class="logo">App Logo</div>')
        self.assert_contains(html, '<div class="logo">App Logo</div>')

    def test_slot_sections(self):
        html = self.render_component(
            "sidebar_nav", slot_sections='<div class="custom-section">Custom Nav</div>'
        )
        self.assert_contains(html, '<div class="custom-section">Custom Nav</div>')

    def test_slot_footer(self):
        html = self.render_component(
            "sidebar_nav", slot_footer='<div class="sidebar-footer">v1.0</div>'
        )
        self.assert_contains(html, '<div class="sidebar-footer">v1.0</div>')
