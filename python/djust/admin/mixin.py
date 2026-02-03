"""
LiveViewAdminMixin - Enable LiveView features in Django ModelAdmin

Provides real-time filtering, inline editing, and live action progress
for Django admin interfaces.
"""

import json
import logging
from functools import partial
from typing import Any, Callable, Dict, List, Optional, Type

from django.contrib import admin
from django.contrib.admin.views.main import ChangeList
from django.db.models import Model, QuerySet
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.template.response import TemplateResponse
from django.urls import path
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from ..serialization import DjangoJSONEncoder

logger = logging.getLogger(__name__)


class LiveChangeList(ChangeList):
    """
    Custom ChangeList that supports live filtering and updates.
    """

    def __init__(self, *args, live_filters: bool = False, **kwargs):
        self.live_filters = live_filters
        super().__init__(*args, **kwargs)


class LiveViewAdminMixin:
    """
    Mixin for Django ModelAdmin classes that enables LiveView functionality.

    Features:
        - live_filters: Enable real-time filtering without page reload
        - live_inline_editing: Enable inline field editing
        - live_actions: Show progress for slow admin actions

    Usage:
        from djust.admin import LiveViewAdminMixin

        class ProductAdmin(LiveViewAdminMixin, admin.ModelAdmin):
            list_display = ['name', 'price', 'stock']
            live_filters = True
            live_inline_editing = True
            live_editable_fields = ['stock', 'price']

            @live_action(description="Export selected items")
            def export_items(self, request, queryset):
                for i, item in enumerate(queryset):
                    yield self.update_progress(i, len(queryset))
                    # export logic
    """

    # LiveView features configuration
    live_filters: bool = False  # Enable real-time filtering
    live_inline_editing: bool = False  # Enable inline field editing
    live_editable_fields: List[str] = []  # Fields that can be edited inline
    live_refresh_interval: Optional[int] = None  # Auto-refresh interval in ms

    # Template overrides for admin LiveView
    change_list_template = "djust/admin/change_list.html"

    def get_urls(self):
        """Add LiveView-specific URLs to admin."""
        urls = super().get_urls()
        model_name = self.model._meta.model_name

        live_urls = [
            path(
                "djust/filter/",
                self.admin_site.admin_view(self.live_filter_view),
                name=f"{model_name}_live_filter",
            ),
            path(
                "djust/inline-edit/",
                self.admin_site.admin_view(self.live_inline_edit_view),
                name=f"{model_name}_live_inline_edit",
            ),
            path(
                "djust/action-progress/",
                self.admin_site.admin_view(self.live_action_progress_view),
                name=f"{model_name}_live_action_progress",
            ),
        ]
        return live_urls + urls

    def changelist_view(self, request: HttpRequest, extra_context=None):
        """Override changelist view to inject LiveView scripts."""
        extra_context = extra_context or {}

        # Add LiveView configuration to context
        extra_context.update(
            {
                "live_filters": self.live_filters,
                "live_inline_editing": self.live_inline_editing,
                "live_editable_fields": json.dumps(self.live_editable_fields),
                "live_refresh_interval": self.live_refresh_interval,
                "djust_admin_enabled": True,
            }
        )

        return super().changelist_view(request, extra_context)

    def get_changelist(self, request: HttpRequest, **kwargs):
        """Return custom ChangeList with live filter support."""
        return partial(LiveChangeList, live_filters=self.live_filters)

    def live_filter_view(self, request: HttpRequest) -> HttpResponse:
        """
        Handle live filter requests via AJAX.

        Accepts filter parameters and returns filtered results HTML.
        """
        if not self.live_filters:
            return JsonResponse({"error": "Live filters not enabled"}, status=400)

        try:
            # Get filter parameters from request
            filters = json.loads(request.body) if request.body else {}

            # Build queryset with filters
            queryset = self.get_queryset(request)
            for field, value in filters.items():
                if value:
                    # Support different filter types
                    if isinstance(value, str) and value:
                        queryset = queryset.filter(**{f"{field}__icontains": value})
                    else:
                        queryset = queryset.filter(**{field: value})

            # Render filtered results
            cl = self.get_changelist_instance(request)
            cl.queryset = queryset

            # Get result list HTML
            context = self.admin_site.each_context(request)
            context.update(
                {
                    "cl": cl,
                    "module_name": self.model._meta.verbose_name_plural,
                    "opts": self.model._meta,
                }
            )

            return JsonResponse(
                {
                    "count": queryset.count(),
                    "results": [
                        {
                            "pk": obj.pk,
                            "display": [
                                self._get_field_display(obj, field)
                                for field in self.list_display
                            ],
                        }
                        for obj in queryset[: self.list_per_page]
                    ],
                }
            )

        except Exception as e:
            logger.exception("Error in live filter view")
            return JsonResponse({"error": str(e)}, status=500)

    def _get_field_display(self, obj: Model, field_name: str) -> str:
        """Get display value for a field, handling callables and admin methods."""
        if callable(field_name):
            return str(field_name(obj))

        if hasattr(self, field_name):
            # Admin method
            method = getattr(self, field_name)
            if callable(method):
                return str(method(obj))

        if hasattr(obj, field_name):
            value = getattr(obj, field_name)
            if callable(value):
                return str(value())
            return str(value) if value is not None else ""

        return ""

    def live_inline_edit_view(self, request: HttpRequest) -> HttpResponse:
        """
        Handle inline edit requests via AJAX.

        Updates a single field on a model instance.
        """
        if not self.live_inline_editing:
            return JsonResponse({"error": "Inline editing not enabled"}, status=400)

        if request.method != "POST":
            return JsonResponse({"error": "POST required"}, status=405)

        try:
            data = json.loads(request.body)
            pk = data.get("pk")
            field = data.get("field")
            value = data.get("value")

            # Validate field is editable
            if field not in self.live_editable_fields:
                return JsonResponse(
                    {"error": f"Field '{field}' is not editable"}, status=400
                )

            # Get and update object
            obj = self.get_queryset(request).get(pk=pk)
            setattr(obj, field, value)
            obj.save(update_fields=[field])

            return JsonResponse(
                {
                    "success": True,
                    "pk": pk,
                    "field": field,
                    "value": self._get_field_display(obj, field),
                }
            )

        except self.model.DoesNotExist:
            return JsonResponse({"error": "Object not found"}, status=404)
        except Exception as e:
            logger.exception("Error in inline edit view")
            return JsonResponse({"error": str(e)}, status=500)

    def live_action_progress_view(self, request: HttpRequest) -> HttpResponse:
        """
        Handle live action progress streaming.

        Returns Server-Sent Events for action progress updates.
        """
        from django.http import StreamingHttpResponse

        action_name = request.GET.get("action")
        selected_ids = request.GET.getlist("ids")

        if not action_name or not selected_ids:
            return JsonResponse({"error": "Missing action or ids"}, status=400)

        # Get the action method
        action_method = getattr(self, action_name, None)
        if not action_method:
            return JsonResponse({"error": f"Action '{action_name}' not found"}, status=404)

        # Check if it's a live action
        if not getattr(action_method, "_is_live_action", False):
            return JsonResponse({"error": "Not a live action"}, status=400)

        queryset = self.get_queryset(request).filter(pk__in=selected_ids)

        def event_stream():
            """Generate SSE events from action generator."""
            try:
                for progress in action_method(request, queryset):
                    if isinstance(progress, dict):
                        yield f"data: {json.dumps(progress, cls=DjangoJSONEncoder)}\n\n"
                yield f"data: {json.dumps({'complete': True})}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'error': str(e)})}\n\n"

        response = StreamingHttpResponse(
            event_stream(), content_type="text/event-stream"
        )
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"
        return response

    def update_progress(
        self,
        current: int,
        total: int,
        message: str = "",
        status: str = "running",
    ) -> Dict[str, Any]:
        """
        Create a progress update dict for live actions.

        Args:
            current: Current item index (0-based)
            total: Total number of items
            message: Optional status message
            status: Status string ('running', 'complete', 'error')

        Returns:
            Dict suitable for SSE streaming
        """
        return {
            "current": current,
            "total": total,
            "percent": int((current / total) * 100) if total > 0 else 0,
            "message": message,
            "status": status,
        }

    def get_inline_edit_field(
        self, obj: Model, field_name: str
    ) -> str:
        """
        Render an inline-editable field.

        Use in list_display to make a field inline-editable:
            list_display = ['name', 'editable_stock']

            def editable_stock(self, obj):
                return self.get_inline_edit_field(obj, 'stock')
        """
        if field_name not in self.live_editable_fields:
            return self._get_field_display(obj, field_name)

        value = getattr(obj, field_name, "")
        return format_html(
            '<span class="djust-inline-edit" '
            'data-pk="{}" data-field="{}" contenteditable="true">{}</span>',
            obj.pk,
            field_name,
            value,
        )

    class Media:
        """Include LiveView admin JavaScript and CSS."""

        css = {"all": ("djust/admin/djust-admin.css",)}
        js = ("djust/admin/djust-admin.js",)
