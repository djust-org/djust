"""Tests for the djust.admin_ext plugin system."""

import pytest
from django.test import RequestFactory, TestCase

from djust.admin_ext import AdminPage, AdminPlugin, AdminWidget, NavItem
from djust.admin_ext.sites import DjustAdminSite

pytestmark = pytest.mark.admin


class TestNavItem(TestCase):
    """Tests for NavItem."""

    def test_basic_nav_item(self):
        item = NavItem(label="Users", url_name="users_list")
        assert item.label == "Users"
        assert item.url_name == "users_list"
        assert item.order == 0
        assert item.section is None
        assert item.permission is None

    def test_nav_item_with_options(self):
        item = NavItem(
            label="Settings",
            url_name="settings_page",
            icon="<svg>...</svg>",
            order=10,
            section="Config",
            permission="myapp.view_settings",
        )
        assert item.label == "Settings"
        assert item.order == 10
        assert item.section == "Config"
        assert item.permission == "myapp.view_settings"

    def test_has_permission_no_perm_required(self):
        item = NavItem(label="Test", url_name="test")
        factory = RequestFactory()
        request = factory.get("/")
        request.user = type("User", (), {"has_perm": lambda self, p: False})()
        assert item.has_permission(request) is True

    def test_has_permission_with_perm(self):
        item = NavItem(label="Test", url_name="test", permission="myapp.can_view")
        factory = RequestFactory()
        request = factory.get("/")
        # User without permission
        request.user = type("User", (), {"has_perm": lambda self, p: False})()
        assert item.has_permission(request) is False
        # User with permission
        request.user = type("User", (), {"has_perm": lambda self, p: True})()
        assert item.has_permission(request) is True

    def test_repr(self):
        item = NavItem(label="Users", url_name="users_list")
        assert "Users" in repr(item)
        assert "users_list" in repr(item)


class TestAdminPage(TestCase):
    """Tests for AdminPage."""

    def test_basic_page(self):
        page = AdminPage(
            url_path="auth/providers/",
            url_name="auth_providers",
            view_class=object,  # placeholder
            label="OAuth Providers",
        )
        assert page.url_path == "auth/providers"  # strips slashes
        assert page.url_name == "auth_providers"
        assert page.label == "OAuth Providers"
        assert page.show_in_nav is True

    def test_auto_label(self):
        page = AdminPage(
            url_path="my_page",
            url_name="my_page",
            view_class=object,
        )
        assert page.label == "My Page"

    def test_get_nav_item(self):
        page = AdminPage(
            url_path="providers/",
            url_name="providers",
            view_class=object,
            label="Providers",
            nav_section="Auth",
            nav_order=5,
            permission="auth.view_provider",
        )
        nav_item = page.get_nav_item()
        assert nav_item is not None
        assert nav_item.label == "Providers"
        assert nav_item.url_name == "providers"
        assert nav_item.section == "Auth"
        assert nav_item.order == 5
        assert nav_item.permission == "auth.view_provider"

    def test_hidden_page_no_nav(self):
        page = AdminPage(
            url_path="hidden/",
            url_name="hidden",
            view_class=object,
            show_in_nav=False,
        )
        assert page.get_nav_item() is None


class TestAdminWidget(TestCase):
    """Tests for AdminWidget."""

    def test_default_widget(self):
        widget = AdminWidget()
        assert widget.widget_id is None
        assert widget.label == ""
        assert widget.template_name is None
        assert widget.order == 0
        assert widget.size == "md"
        assert widget.permission is None

    def test_get_context_default(self):
        widget = AdminWidget()
        factory = RequestFactory()
        request = factory.get("/")
        assert widget.get_context(request) == {}

    def test_custom_widget(self):
        class StatsWidget(AdminWidget):
            widget_id = "stats"
            label = "Statistics"
            template_name = "test/stats.html"
            order = 10
            size = "lg"

            def get_context(self, request):
                return {"count": 42}

        widget = StatsWidget()
        assert widget.widget_id == "stats"
        assert widget.label == "Statistics"
        assert widget.order == 10
        assert widget.size == "lg"

        factory = RequestFactory()
        request = factory.get("/")
        ctx = widget.get_context(request)
        assert ctx == {"count": 42}

    def test_has_permission_no_perm(self):
        widget = AdminWidget()
        factory = RequestFactory()
        request = factory.get("/")
        request.user = type("User", (), {"has_perm": lambda self, p: False})()
        assert widget.has_permission(request) is True

    def test_render_no_template(self):
        widget = AdminWidget()
        factory = RequestFactory()
        request = factory.get("/")
        assert widget.render(request) == ""


class TestAdminPlugin(TestCase):
    """Tests for AdminPlugin."""

    def test_basic_plugin(self):
        class MyPlugin(AdminPlugin):
            name = "my_plugin"
            verbose_name = "My Plugin"

        plugin = MyPlugin()
        assert plugin.name == "my_plugin"
        assert plugin.verbose_name == "My Plugin"
        assert plugin.get_pages() == []
        assert plugin.get_widgets() == []
        assert plugin.get_nav_items() == []

    def test_plugin_with_pages(self):
        class MyPlugin(AdminPlugin):
            name = "test"
            verbose_name = "Test"

            def get_pages(self):
                return [
                    AdminPage("page1/", "page1", object, label="Page 1", nav_section="Test"),
                    AdminPage("page2/", "page2", object, label="Page 2", nav_section="Test"),
                ]

        plugin = MyPlugin()
        pages = plugin.get_pages()
        assert len(pages) == 2

        # Auto-generated nav items
        nav_items = plugin.get_nav_items()
        assert len(nav_items) == 2
        assert nav_items[0].label == "Page 1"
        assert nav_items[1].label == "Page 2"

    def test_plugin_with_hidden_page(self):
        class MyPlugin(AdminPlugin):
            name = "test"

            def get_pages(self):
                return [
                    AdminPage("visible/", "visible", object, label="Visible"),
                    AdminPage("hidden/", "hidden", object, show_in_nav=False),
                ]

        plugin = MyPlugin()
        nav_items = plugin.get_nav_items()
        assert len(nav_items) == 1
        assert nav_items[0].label == "Visible"

    def test_ready_called(self):
        ready_called = []

        class MyPlugin(AdminPlugin):
            name = "test"

            def ready(self):
                ready_called.append(True)

        plugin = MyPlugin()
        plugin.ready()
        assert len(ready_called) == 1


class TestSitePluginRegistration(TestCase):
    """Tests for plugin registration on DjustAdminSite."""

    def test_register_plugin_class(self):
        site = DjustAdminSite(name="test")

        class MyPlugin(AdminPlugin):
            name = "my_plugin"
            verbose_name = "My Plugin"

        site.register_plugin(MyPlugin)
        assert site.get_plugin("my_plugin") is not None
        assert site.get_plugin("my_plugin").verbose_name == "My Plugin"

    def test_register_plugin_instance(self):
        site = DjustAdminSite(name="test")

        class MyPlugin(AdminPlugin):
            name = "my_plugin"

        plugin = MyPlugin()
        site.register_plugin(plugin)
        assert site.get_plugin("my_plugin") is plugin

    def test_register_plugin_no_name(self):
        site = DjustAdminSite(name="test")

        class BadPlugin(AdminPlugin):
            pass  # name is None

        with pytest.raises(ValueError, match="must have a 'name'"):
            site.register_plugin(BadPlugin)

    def test_register_duplicate_plugin(self):
        site = DjustAdminSite(name="test")

        class MyPlugin(AdminPlugin):
            name = "my_plugin"

        site.register_plugin(MyPlugin)
        with pytest.raises(ValueError, match="already registered"):
            site.register_plugin(MyPlugin)

    def test_unregister_plugin(self):
        site = DjustAdminSite(name="test")

        class MyPlugin(AdminPlugin):
            name = "my_plugin"

        site.register_plugin(MyPlugin)
        assert site.get_plugin("my_plugin") is not None

        site.unregister_plugin("my_plugin")
        assert site.get_plugin("my_plugin") is None

    def test_unregister_nonexistent_plugin(self):
        site = DjustAdminSite(name="test")
        with pytest.raises(ValueError, match="not registered"):
            site.unregister_plugin("nonexistent")

    def test_ready_called_on_registration(self):
        site = DjustAdminSite(name="test")
        ready_calls = []

        class MyPlugin(AdminPlugin):
            name = "test_ready"

            def ready(self):
                ready_calls.append(True)

        site.register_plugin(MyPlugin)
        assert len(ready_calls) == 1

    def test_get_plugin_nav(self):
        site = DjustAdminSite(name="test")

        class MyPlugin(AdminPlugin):
            name = "auth"
            verbose_name = "Authentication"

            def get_pages(self):
                return [
                    AdminPage(
                        "auth/providers/",
                        "auth_providers",
                        object,
                        label="OAuth Providers",
                        nav_section="Authentication",
                    ),
                    AdminPage(
                        "auth/users/",
                        "auth_users",
                        object,
                        label="Users",
                        nav_section="Authentication",
                        nav_order=10,
                    ),
                ]

        site.register_plugin(MyPlugin)

        factory = RequestFactory()
        request = factory.get("/")
        request.user = type(
            "User",
            (),
            {
                "is_authenticated": True,
                "is_staff": True,
                "has_perm": lambda self, p: True,
            },
        )()

        nav = site.get_plugin_nav(request)
        assert len(nav) == 1
        assert nav[0]["section"] == "Authentication"
        assert len(nav[0]["items"]) == 2
        # Items should be sorted by order
        assert nav[0]["items"][0]["label"] == "OAuth Providers"
        assert nav[0]["items"][1]["label"] == "Users"

    def test_get_widgets(self):
        site = DjustAdminSite(name="test")

        class TestWidget(AdminWidget):
            widget_id = "test_stats"
            label = "Test Stats"
            order = 5

        class MyPlugin(AdminPlugin):
            name = "test"

            def get_widgets(self):
                return [TestWidget()]

        site.register_plugin(MyPlugin)

        factory = RequestFactory()
        request = factory.get("/")
        request.user = type(
            "User",
            (),
            {
                "is_authenticated": True,
                "has_perm": lambda self, p: True,
            },
        )()

        widgets = site.get_widgets(request)
        assert len(widgets) == 1
        assert widgets[0]["widget_id"] == "test_stats"
        assert widgets[0]["label"] == "Test Stats"
        assert widgets[0]["order"] == 5

    def test_get_widgets_permission_filtering(self):
        site = DjustAdminSite(name="test")

        class PublicWidget(AdminWidget):
            widget_id = "public"
            label = "Public"

        class PrivateWidget(AdminWidget):
            widget_id = "private"
            label = "Private"
            permission = "myapp.view_private"

        class MyPlugin(AdminPlugin):
            name = "test"

            def get_widgets(self):
                return [PublicWidget(), PrivateWidget()]

        site.register_plugin(MyPlugin)

        factory = RequestFactory()
        request = factory.get("/")
        # User without the permission
        request.user = type(
            "User",
            (),
            {
                "is_authenticated": True,
                "has_perm": lambda self, p: False,
            },
        )()

        widgets = site.get_widgets(request)
        assert len(widgets) == 1
        assert widgets[0]["widget_id"] == "public"
