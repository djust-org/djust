"""
LiveAdminView - Custom admin pages with LiveView functionality

Provides a base class for creating custom admin pages that use LiveView
for real-time interactivity.
"""

import logging
from typing import Any, Dict, List, Optional, Type

from django.contrib import admin
from django.contrib.admin import AdminSite
from django.contrib.auth import get_permission_codename
from django.http import HttpRequest, HttpResponse
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.views import View

from ..live_view import LiveView

logger = logging.getLogger(__name__)


class LiveAdminView(LiveView):
    """
    Base class for custom admin pages with LiveView functionality.

    Creates interactive admin pages that integrate with Django admin styling
    and permissions.

    Usage:
        from djust.admin import LiveAdminView

        class DashboardView(LiveAdminView):
            template_name = "myapp/admin/dashboard.html"
            admin_title = "Dashboard"
            admin_site = admin.site

            def mount(self, request, **kwargs):
                self.stats = self.get_stats()

            def get_stats(self):
                return {
                    'total_orders': Order.objects.count(),
                    'revenue': Order.objects.aggregate(Sum('total'))['total__sum'],
                }

            @event_handler
            def refresh_stats(self):
                self.stats = self.get_stats()

        # In urls.py:
        urlpatterns = [
            path('admin/dashboard/', DashboardView.as_view(), name='admin-dashboard'),
        ]
    """

    # Admin configuration
    admin_site: Optional[AdminSite] = None
    admin_title: str = ""
    admin_subtitle: str = ""

    # Permission settings
    permission_required: Optional[str] = None  # e.g., 'myapp.view_dashboard'
    login_required: bool = True

    # Use admin base template
    admin_base_template: str = "admin/base_site.html"

    def get_admin_context(self, request: HttpRequest) -> Dict[str, Any]:
        """
        Get context data for admin template rendering.

        Includes all standard Django admin context plus LiveView-specific data.
        """
        site = self.admin_site or admin.site

        context = {
            # Django admin context
            "site_title": site.site_title,
            "site_header": site.site_header,
            "site_url": site.site_url,
            "has_permission": self.has_permission(request),
            "available_apps": site.get_app_list(request),
            "is_popup": False,
            "is_nav_sidebar_enabled": True,
            # LiveView admin context
            "admin_title": self.admin_title,
            "admin_subtitle": self.admin_subtitle,
            "djust_admin_enabled": True,
        }

        return context

    def has_permission(self, request: HttpRequest) -> bool:
        """
        Check if user has permission to access this view.

        Override for custom permission logic.
        """
        if not self.login_required:
            return True

        if not request.user.is_authenticated:
            return False

        if not request.user.is_staff:
            return False

        if self.permission_required:
            return request.user.has_perm(self.permission_required)

        return True

    def get_context_data(self, **kwargs) -> Dict[str, Any]:
        """Add admin context to the standard LiveView context."""
        context = super().get_context_data(**kwargs)

        # Get request from stored reference
        request = getattr(self, "_request", None)
        if request:
            context.update(self.get_admin_context(request))

        return context

    def get(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        """Handle GET request with permission check."""
        if not self.has_permission(request):
            from django.contrib.admin.views.decorators import staff_member_required
            from django.contrib.auth.views import redirect_to_login

            return redirect_to_login(
                request.get_full_path(),
                login_url=f"{admin.site.name}:login",
            )

        # Store request reference for context
        self._request = request
        return super().get(request, *args, **kwargs)

    def post(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        """Handle POST request with permission check."""
        if not self.has_permission(request):
            from django.http import HttpResponseForbidden

            return HttpResponseForbidden("Permission denied")

        self._request = request
        return super().post(request, *args, **kwargs)


class AdminViewRegistry:
    """
    Registry for custom admin LiveViews.

    Allows registering custom LiveView pages to appear in the admin sidebar.

    Usage:
        from djust.admin import admin_views

        @admin_views.register(app_label='myapp', title='Dashboard')
        class DashboardView(LiveAdminView):
            template_name = "myapp/admin/dashboard.html"
            ...
    """

    def __init__(self):
        self._views: List[Dict[str, Any]] = []

    def register(
        self,
        app_label: str,
        title: str,
        url_name: Optional[str] = None,
        icon: str = "",
        order: int = 0,
    ):
        """
        Decorator to register a LiveAdminView.

        Args:
            app_label: App label for grouping in sidebar
            title: Display title for the link
            url_name: URL name (defaults to view class name)
            icon: Optional icon class
            order: Sort order within app
        """

        def decorator(view_class: Type[LiveAdminView]):
            view_class.admin_title = title

            self._views.append(
                {
                    "app_label": app_label,
                    "title": title,
                    "url_name": url_name or view_class.__name__.lower(),
                    "view_class": view_class,
                    "icon": icon,
                    "order": order,
                }
            )
            return view_class

        return decorator

    def get_urls(self) -> List:
        """Get URL patterns for all registered views."""
        urls = []
        for view_info in self._views:
            view_class = view_info["view_class"]
            url_name = view_info["url_name"]
            urls.append(
                path(
                    f"djust/{url_name}/",
                    view_class.as_view(),
                    name=f"djust_{url_name}",
                )
            )
        return urls

    def get_app_list_additions(self) -> Dict[str, List[Dict[str, Any]]]:
        """
        Get additions to the admin app list for the sidebar.

        Returns dict mapping app_label to list of link dicts.
        """
        additions = {}
        for view_info in sorted(self._views, key=lambda x: x["order"]):
            app_label = view_info["app_label"]
            if app_label not in additions:
                additions[app_label] = []

            additions[app_label].append(
                {
                    "name": view_info["title"],
                    "admin_url": reverse(f"admin:djust_{view_info['url_name']}"),
                    "icon": view_info["icon"],
                    "view_only": True,
                }
            )
        return additions


# Global registry instance
admin_views = AdminViewRegistry()
