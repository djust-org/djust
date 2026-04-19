"""Basic tests for djust.admin_ext."""

import pytest
from django.test import TestCase

from djust.admin_ext import (
    AdminPage,
    AdminPlugin,
    AdminWidget,
    DjustAdminSite,
    DjustModelAdmin,
    NavItem,
    site,
)

pytestmark = pytest.mark.admin


class TestDjustAdminSite(TestCase):
    """Tests for DjustAdminSite."""

    def test_default_site_exists(self):
        """Default admin site should be available."""
        assert site is not None
        assert isinstance(site, DjustAdminSite)

    def test_site_name(self):
        """Site should have default name."""
        assert site.name == "djust_admin"

    def test_custom_site(self):
        """Can create custom admin site."""
        custom_site = DjustAdminSite(name="custom")
        assert custom_site.name == "custom"

    def test_site_headers(self):
        """Site should have configurable headers."""
        custom_site = DjustAdminSite()
        custom_site.site_header = "Custom Header"
        custom_site.site_title = "Custom Title"
        assert custom_site.site_header == "Custom Header"
        assert custom_site.site_title == "Custom Title"

    def test_site_has_plugins_registry(self):
        """Site should have a plugins registry."""
        custom_site = DjustAdminSite()
        assert hasattr(custom_site, "_plugins")
        assert custom_site._plugins == {}


class TestDjustModelAdmin(TestCase):
    """Tests for DjustModelAdmin."""

    def test_default_list_display(self):
        """Default list_display should be __str__."""
        assert DjustModelAdmin.list_display == ["__str__"]

    def test_default_list_per_page(self):
        """Default list_per_page should be 25."""
        assert DjustModelAdmin.list_per_page == 25

    def test_default_actions(self):
        """Default actions should include delete_selected."""
        assert "delete_selected" in DjustModelAdmin.actions


class TestPluginExports(TestCase):
    """Tests that plugin classes are properly exported."""

    def test_admin_plugin_importable(self):
        assert AdminPlugin is not None

    def test_admin_page_importable(self):
        assert AdminPage is not None

    def test_admin_widget_importable(self):
        assert AdminWidget is not None

    def test_nav_item_importable(self):
        assert NavItem is not None
