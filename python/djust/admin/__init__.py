"""
djust Admin Integration

Provides LiveView functionality inside Django admin for interactive admin pages.

Usage:
    from djust.admin import LiveViewAdminMixin, LiveAdminView, live_action

    class ProductAdmin(LiveViewAdminMixin, admin.ModelAdmin):
        list_display = ['name', 'price', 'stock']
        
        # LiveView features in admin
        live_filters = True  # Real-time filtering
        live_inline_editing = True  # Edit fields inline
        
        @live_action(description="Export selected items")
        def export_items(self, request, queryset):
            for i, item in enumerate(queryset):
                yield self.update_progress(i, len(queryset))
                # export logic
"""

from .mixin import LiveViewAdminMixin
from .views import LiveAdminView
from .decorators import live_action
from .widgets import (
    AdminStatsWidget,
    AdminChartWidget,
    AdminActivityWidget,
    AdminDashboard,
)

__all__ = [
    "LiveViewAdminMixin",
    "LiveAdminView",
    "live_action",
    "AdminStatsWidget",
    "AdminChartWidget",
    "AdminActivityWidget",
    "AdminDashboard",
]
