"""
Tests for djust Admin Integration

Tests the LiveViewAdminMixin, LiveAdminView, live_action decorator,
and admin dashboard widgets.
"""

import json
import pytest
from unittest.mock import MagicMock, patch
from dataclasses import dataclass

# Test-only Django setup
import django
from django.conf import settings

if not settings.configured:
    settings.configure(
        DEBUG=True,
        DATABASES={
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": ":memory:",
            }
        },
        INSTALLED_APPS=[
            "django.contrib.contenttypes",
            "django.contrib.auth",
            "django.contrib.admin",
            "django.contrib.sessions",
        ],
        ROOT_URLCONF="tests.test_admin",
        SECRET_KEY="test-secret-key",
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "DIRS": [],
                "APP_DIRS": True,
                "OPTIONS": {
                    "context_processors": [
                        "django.template.context_processors.request",
                        "django.contrib.auth.context_processors.auth",
                    ],
                },
            },
        ],
        USE_TZ=True,
    )
    django.setup()

from django.contrib import admin
from django.contrib.admin.sites import AdminSite
from django.contrib.auth.models import User
from django.http import HttpRequest, JsonResponse
from django.test import RequestFactory, TestCase


class TestLiveActionDecorator:
    """Tests for the @live_action decorator."""

    def test_live_action_marks_method(self):
        """Test that @live_action marks method with correct attributes."""
        from djust.admin.decorators import live_action

        @live_action(description="Test action")
        def my_action(self, request, queryset):
            yield {"progress": 50}

        assert my_action._is_live_action is True
        assert my_action._live_action_config["description"] == "Test action"
        assert my_action.short_description == "Test action"

    def test_live_action_without_parentheses(self):
        """Test @live_action works without parentheses."""
        from djust.admin.decorators import live_action

        @live_action
        def my_action(self, request, queryset):
            yield {"progress": 50}

        assert my_action._is_live_action is True

    def test_live_action_generator_yields(self):
        """Test that live action generator yields progress updates."""
        from djust.admin.decorators import live_action

        @live_action(description="Test")
        def export_items(self, request, queryset):
            for i in range(3):
                yield {"current": i + 1, "total": 3, "percent": (i + 1) * 33}

        # Create mock self with model
        mock_self = MagicMock()
        mock_self.model._meta.app_label = "testapp"
        
        mock_request = MagicMock()
        mock_request.user.has_perm.return_value = True
        
        mock_queryset = MagicMock()

        results = list(export_items(mock_self, mock_request, mock_queryset))
        assert len(results) == 3
        assert results[0]["current"] == 1
        assert results[2]["percent"] == 99

    def test_live_action_with_permissions(self):
        """Test live action respects permissions."""
        from djust.admin.decorators import live_action

        @live_action(description="Test", permissions=["change_item"])
        def restricted_action(self, request, queryset):
            yield {"done": True}

        mock_self = MagicMock()
        mock_self.model._meta.app_label = "testapp"
        
        mock_request = MagicMock()
        mock_request.user.has_perm.return_value = False
        
        mock_queryset = MagicMock()

        results = list(restricted_action(mock_self, mock_request, mock_queryset))
        assert results[0]["error"] == "Permission denied: change_item"
        assert results[0]["status"] == "error"


class TestLiveViewAdminMixin:
    """Tests for LiveViewAdminMixin."""

    def test_mixin_adds_urls(self):
        """Test that mixin adds djust-specific URLs."""
        from djust.admin.mixin import LiveViewAdminMixin

        class MockModel:
            class _meta:
                model_name = "testmodel"
                app_label = "testapp"
                verbose_name_plural = "test models"

        class TestAdmin(LiveViewAdminMixin, admin.ModelAdmin):
            model = MockModel

        admin_instance = TestAdmin(MockModel, AdminSite())
        urls = admin_instance.get_urls()

        url_names = [url.name for url in urls if hasattr(url, 'name')]
        assert "testmodel_live_filter" in url_names
        assert "testmodel_live_inline_edit" in url_names
        assert "testmodel_live_action_progress" in url_names

    def test_mixin_configuration_defaults(self):
        """Test mixin has correct default configuration."""
        from djust.admin.mixin import LiveViewAdminMixin

        class TestAdmin(LiveViewAdminMixin, admin.ModelAdmin):
            pass

        assert TestAdmin.live_filters is False
        assert TestAdmin.live_inline_editing is False
        assert TestAdmin.live_editable_fields == []
        assert TestAdmin.live_refresh_interval is None

    def test_update_progress_helper(self):
        """Test update_progress helper method."""
        from djust.admin.mixin import LiveViewAdminMixin

        class MockModel:
            class _meta:
                model_name = "testmodel"

        class TestAdmin(LiveViewAdminMixin, admin.ModelAdmin):
            model = MockModel

        admin_instance = TestAdmin(MockModel, AdminSite())
        progress = admin_instance.update_progress(5, 10, "Processing...", "running")

        assert progress["current"] == 5
        assert progress["total"] == 10
        assert progress["percent"] == 50
        assert progress["message"] == "Processing..."
        assert progress["status"] == "running"

    def test_update_progress_zero_total(self):
        """Test update_progress handles zero total."""
        from djust.admin.mixin import LiveViewAdminMixin

        class MockModel:
            class _meta:
                model_name = "testmodel"

        class TestAdmin(LiveViewAdminMixin, admin.ModelAdmin):
            model = MockModel

        admin_instance = TestAdmin(MockModel, AdminSite())
        progress = admin_instance.update_progress(0, 0)

        assert progress["percent"] == 0


class TestLiveAdminView:
    """Tests for LiveAdminView."""

    def test_admin_context_includes_standard_fields(self):
        """Test admin context includes required Django admin fields."""
        from djust.admin.views import LiveAdminView

        class TestView(LiveAdminView):
            admin_title = "Test Dashboard"
            admin_subtitle = "Subtitle"

        view = TestView()
        view.admin_site = AdminSite()

        # Create mock request
        factory = RequestFactory()
        request = factory.get("/admin/test/")
        request.user = MagicMock()
        request.user.is_authenticated = True
        request.user.is_staff = True

        context = view.get_admin_context(request)

        assert "site_title" in context
        assert "site_header" in context
        assert "has_permission" in context
        assert context["admin_title"] == "Test Dashboard"
        assert context["admin_subtitle"] == "Subtitle"
        assert context["djust_admin_enabled"] is True

    def test_permission_check_requires_staff(self):
        """Test permission check requires staff status."""
        from djust.admin.views import LiveAdminView

        class TestView(LiveAdminView):
            pass

        view = TestView()

        factory = RequestFactory()
        request = factory.get("/admin/test/")
        
        # Non-staff user
        request.user = MagicMock()
        request.user.is_authenticated = True
        request.user.is_staff = False

        assert view.has_permission(request) is False

    def test_permission_check_with_custom_permission(self):
        """Test permission check with custom permission requirement."""
        from djust.admin.views import LiveAdminView

        class TestView(LiveAdminView):
            permission_required = "myapp.view_dashboard"

        view = TestView()

        factory = RequestFactory()
        request = factory.get("/admin/test/")
        request.user = MagicMock()
        request.user.is_authenticated = True
        request.user.is_staff = True
        request.user.has_perm.return_value = False

        assert view.has_permission(request) is False
        request.user.has_perm.assert_called_with("myapp.view_dashboard")


class TestAdminWidgets:
    """Tests for admin dashboard widgets."""

    def test_stats_widget_context(self):
        """Test AdminStatsWidget generates correct context."""
        from djust.admin.widgets import AdminStatsWidget

        widget = AdminStatsWidget(
            widget_id="revenue",
            title="Total Revenue",
            value=1000,
            prefix="$",
            trend=12.5,
            trend_label="vs last month",
            color="green",
        )

        context = widget.get_context()

        assert context["widget_id"] == "revenue"
        assert context["title"] == "Total Revenue"
        assert context["value"] == 1000
        assert context["prefix"] == "$"
        assert context["trend"] == 12.5
        assert context["trend_positive"] is True
        assert context["color"] == "green"

    def test_stats_widget_negative_trend(self):
        """Test stats widget handles negative trend."""
        from djust.admin.widgets import AdminStatsWidget

        widget = AdminStatsWidget(
            widget_id="users",
            title="Active Users",
            value=500,
            trend=-5.2,
        )

        context = widget.get_context()
        assert context["trend_positive"] is False

    def test_chart_widget_context(self):
        """Test AdminChartWidget generates correct context."""
        from djust.admin.widgets import AdminChartWidget

        widget = AdminChartWidget(
            widget_id="sales-chart",
            title="Sales Over Time",
            chart_type="line",
            labels=["Jan", "Feb", "Mar"],
            datasets=[
                {"label": "Sales", "data": [100, 120, 115]}
            ],
        )

        context = widget.get_context()

        assert context["chart_type"] == "line"
        assert context["labels"] == ["Jan", "Feb", "Mar"]
        assert len(context["datasets"]) == 1
        assert context["options"]["responsive"] is True

    def test_activity_widget_context(self):
        """Test AdminActivityWidget generates correct context."""
        from djust.admin.widgets import AdminActivityWidget

        widget = AdminActivityWidget(
            widget_id="activity",
            title="Recent Activity",
            items=[
                {"message": "User registered", "time": "2m ago"},
                {"message": "Order placed", "time": "5m ago"},
            ],
            max_items=10,
        )

        context = widget.get_context()

        assert len(context["items"]) == 2
        assert context["show_timestamps"] is True
        assert context["has_more"] is False

    def test_activity_widget_truncates(self):
        """Test activity widget truncates items beyond max."""
        from djust.admin.widgets import AdminActivityWidget

        items = [{"message": f"Item {i}"} for i in range(15)]
        widget = AdminActivityWidget(
            widget_id="activity",
            items=items,
            max_items=10,
        )

        context = widget.get_context()
        assert len(context["items"]) == 10
        assert context["has_more"] is True

    def test_progress_widget_calculates_percentages(self):
        """Test AdminProgressWidget calculates stage percentages."""
        from djust.admin.widgets import AdminProgressWidget

        widget = AdminProgressWidget(
            widget_id="progress",
            title="Order Status",
            stages=[
                {"label": "Pending", "count": 25, "color": "yellow"},
                {"label": "Shipped", "count": 75, "color": "green"},
            ],
        )

        context = widget.get_context()

        assert context["stages"][0]["percent"] == 25.0
        assert context["stages"][1]["percent"] == 75.0
        assert context["total"] == 100

    def test_table_widget_context(self):
        """Test AdminTableWidget generates correct context."""
        from djust.admin.widgets import AdminTableWidget

        widget = AdminTableWidget(
            widget_id="top-products",
            title="Top Products",
            columns=["Product", "Sales"],
            rows=[
                ["Widget A", 150],
                ["Widget B", 120],
            ],
        )

        context = widget.get_context()

        assert context["columns"] == ["Product", "Sales"]
        assert len(context["rows"]) == 2
        assert context["sortable"] is False


class TestAdminDashboard:
    """Tests for AdminDashboard container."""

    def test_dashboard_adds_widgets(self):
        """Test dashboard can add widgets."""
        from djust.admin.widgets import AdminDashboard, AdminStatsWidget

        dashboard = AdminDashboard(columns=4)
        
        widget = AdminStatsWidget(widget_id="test", value=100)
        dashboard.add_widget(widget, size="small")

        assert len(dashboard.widgets) == 1
        assert dashboard.widgets[0]["widget"] == widget
        assert dashboard.widgets[0]["size"] == "small"
        assert dashboard.widgets[0]["colspan"] == 1

    def test_dashboard_size_mapping(self):
        """Test dashboard maps sizes to column spans."""
        from djust.admin.widgets import AdminDashboard, AdminStatsWidget

        dashboard = AdminDashboard(columns=4)
        
        widget = AdminStatsWidget(widget_id="test", value=100)
        
        dashboard.add_widget(widget, size="small")
        dashboard.add_widget(widget, size="medium")
        dashboard.add_widget(widget, size="large")
        dashboard.add_widget(widget, size="full")

        assert dashboard.widgets[0]["colspan"] == 1
        assert dashboard.widgets[1]["colspan"] == 2
        assert dashboard.widgets[2]["colspan"] == 3
        assert dashboard.widgets[3]["colspan"] == 4


class TestAdminViewRegistry:
    """Tests for AdminViewRegistry."""

    def test_registry_registers_views(self):
        """Test registry can register views."""
        from djust.admin.views import AdminViewRegistry, LiveAdminView

        registry = AdminViewRegistry()

        @registry.register(app_label="myapp", title="Dashboard")
        class TestDashboard(LiveAdminView):
            pass

        assert len(registry._views) == 1
        assert registry._views[0]["app_label"] == "myapp"
        assert registry._views[0]["title"] == "Dashboard"
        assert TestDashboard.admin_title == "Dashboard"

    def test_registry_generates_urls(self):
        """Test registry generates URL patterns."""
        from djust.admin.views import AdminViewRegistry, LiveAdminView

        registry = AdminViewRegistry()

        @registry.register(app_label="myapp", title="Dashboard")
        class TestDashboard(LiveAdminView):
            pass

        urls = registry.get_urls()
        assert len(urls) == 1


# Integration test
class TestAdminIntegration:
    """Integration tests for admin components working together."""

    def test_full_admin_setup(self):
        """Test complete admin setup with mixin and live action."""
        from djust.admin import LiveViewAdminMixin, live_action

        class MockModel:
            class _meta:
                model_name = "product"
                app_label = "shop"
                verbose_name_plural = "products"

        class ProductAdmin(LiveViewAdminMixin, admin.ModelAdmin):
            model = MockModel
            live_filters = True
            live_inline_editing = True
            live_editable_fields = ["price", "stock"]

            @live_action(description="Export products")
            def export_products(self, request, queryset):
                total = 3  # Mock queryset count
                for i in range(total):
                    yield self.update_progress(i + 1, total, f"Exporting item {i + 1}")

        admin_instance = ProductAdmin(MockModel, AdminSite())

        # Verify configuration
        assert admin_instance.live_filters is True
        assert admin_instance.live_inline_editing is True
        assert "price" in admin_instance.live_editable_fields

        # Verify action is a live action
        assert admin_instance.export_products._is_live_action is True

        # Test action execution
        mock_request = MagicMock()
        mock_request.user.has_perm.return_value = True
        mock_queryset = MagicMock()

        results = list(admin_instance.export_products(mock_request, mock_queryset))
        assert len(results) == 3
        assert results[0]["message"] == "Exporting item 1"
        assert results[2]["percent"] == 100
