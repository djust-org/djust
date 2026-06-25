"""
DjustModelAdmin - Configuration class for model admin interfaces.

Similar to Django's ModelAdmin but designed for reactive LiveView rendering.
"""

import logging
from typing import Any, Dict, List, Optional, Type

from django.db import models
from django.forms import BaseModelForm, modelform_factory
from django.http import HttpRequest

logger = logging.getLogger(__name__)


class DjustModelAdmin:
    """
    Configuration for how a model is displayed in djust admin.

    Usage:
        from djust.admin_ext import DjustModelAdmin, site

        @site.register(Article)
        class ArticleAdmin(DjustModelAdmin):
            list_display = ['title', 'author', 'published_date', 'status']
            list_filter = ['status', 'author']
            search_fields = ['title', 'content']
            ordering = ['-published_date']
    """

    # List view configuration
    list_display: List[str] = ["__str__"]
    list_display_links: Optional[List[str]] = None
    list_filter: List[Any] = []
    list_select_related: Any = False
    list_per_page = 25
    list_max_show_all = 200
    search_fields: List[str] = []
    ordering: Optional[List[str]] = None

    # Detail view configuration
    fields: Optional[List[str]] = None
    exclude: Optional[List[str]] = None
    readonly_fields: List[str] = []
    fieldsets: Optional[List[Any]] = None

    # Form configuration
    form: Optional[Type[BaseModelForm]] = None
    formfield_overrides: Dict[Any, Any] = {}

    # Actions
    actions: List[Any] = ["delete_selected"]

    # Per-page widget slots (v0.7.0). Each entry is a LiveView subclass
    # that will be embedded via ``{% live_render %}`` on the given admin
    # page. Honour ``permission_required`` on the widget class to filter
    # per-user. See docs/website/guides/admin-widgets.md.
    change_form_widgets: list = []
    change_list_widgets: list = []

    def get_change_form_widgets(
        self, request: HttpRequest, obj: Optional[models.Model] = None
    ) -> List[Any]:
        """Return widget classes eligible for the change form page.

        Filters ``change_form_widgets`` by each widget's
        ``permission_required`` attribute (if present).
        """
        return [w for w in self.change_form_widgets if self._widget_has_permission(w, request)]

    def get_change_list_widgets(self, request: HttpRequest) -> List[Any]:
        """Return widget classes eligible for the change list page."""
        return [w for w in self.change_list_widgets if self._widget_has_permission(w, request)]

    @staticmethod
    def _widget_has_permission(widget_cls: Any, request: HttpRequest) -> bool:
        """Check whether the request.user has permission to see the widget.

        A widget with no ``permission_required`` attribute is always
        visible. A string value is treated as a single perm; an
        iterable is treated as a set of perms that are ALL required.
        """
        perm = getattr(widget_cls, "permission_required", None)
        if perm is None:
            return True
        perms = (perm,) if isinstance(perm, str) else tuple(perm)
        user = getattr(request, "user", None)
        if user is None:
            return False
        return bool(user.has_perms(perms))

    # Permissions
    def has_add_permission(self, request: HttpRequest) -> bool:
        return True

    def has_change_permission(
        self, request: HttpRequest, obj: Optional[models.Model] = None
    ) -> bool:
        return True

    def has_delete_permission(
        self, request: HttpRequest, obj: Optional[models.Model] = None
    ) -> bool:
        return True

    def has_view_permission(
        self, request: HttpRequest, obj: Optional[models.Model] = None
    ) -> bool:
        return True

    def __init__(self, model: Type[models.Model], admin_site: Any) -> None:
        self.model = model
        self.admin_site = admin_site
        self.opts = model._meta

    def get_queryset(self, request: HttpRequest) -> "models.QuerySet[Any]":
        """Return the queryset for the list view."""
        qs = self.model._default_manager.get_queryset()

        # Apply select_related if configured
        if self.list_select_related:
            if isinstance(self.list_select_related, (list, tuple)):
                qs = qs.select_related(*self.list_select_related)
            else:
                qs = qs.select_related()
        else:
            # Auto-optimize: select_related for FK/O2O fields in list_display
            fk_fields = []
            for field_name in self.list_display:
                try:
                    field = self.opts.get_field(field_name)
                    if isinstance(field, (models.ForeignKey, models.OneToOneField)):
                        fk_fields.append(field_name)
                except Exception:
                    logger.debug("Failed to resolve FK for %s", field_name, exc_info=True)
            if fk_fields:
                qs = qs.select_related(*fk_fields)

        # Apply default ordering
        ordering = self.get_ordering(request)
        if ordering:
            qs = qs.order_by(*ordering)

        return qs

    def get_ordering(self, request: HttpRequest) -> Any:
        """Return the ordering for the list view."""
        return self.ordering or ()

    def get_list_display(self, request: HttpRequest) -> List[str]:
        """Return the list of fields to display in the list view."""
        return self.list_display

    def get_list_filter(self, request: HttpRequest) -> List[Any]:
        """Return the list of filters for the list view."""
        return self.list_filter

    def get_search_fields(self, request: HttpRequest) -> List[str]:
        """Return the list of fields to search."""
        return self.search_fields

    def get_fields(self, request: HttpRequest, obj: Optional[models.Model] = None) -> List[str]:
        """Return the fields to display in the detail form."""
        if self.fields:
            return self.fields

        # Auto-generate from model
        return [f.name for f in self.opts.get_fields() if f.editable and not f.auto_created]

    def get_readonly_fields(
        self, request: HttpRequest, obj: Optional[models.Model] = None
    ) -> List[str]:
        """Return the list of readonly fields."""
        return self.readonly_fields

    def get_exclude(self, request: HttpRequest, obj: Optional[models.Model] = None) -> Any:
        """Return the list of excluded fields."""
        return self.exclude or ()

    def get_form(
        self, request: HttpRequest, obj: Optional[models.Model] = None, **kwargs: Any
    ) -> Type[BaseModelForm]:
        """Return the form class for the detail view."""
        if self.form:
            return self.form

        # Generate form from model
        fields = self.get_fields(request, obj)
        exclude = self.get_exclude(request, obj)

        form_class: Type[BaseModelForm] = modelform_factory(
            self.model,
            fields=fields,
            exclude=exclude,
        )
        return form_class

    def get_fieldsets(self, request: HttpRequest, obj: Optional[models.Model] = None) -> List[Any]:
        """Return fieldsets for the detail form."""
        if self.fieldsets:
            return self.fieldsets

        # Default: single fieldset with all fields
        return [(None, {"fields": self.get_fields(request, obj)})]

    def get_actions(self, request: HttpRequest) -> Dict[str, Dict[str, Any]]:
        """Return the list of available actions."""
        actions: Dict[str, Dict[str, Any]] = {}

        for action_name in self.actions:
            if callable(action_name):
                func = action_name
                name = action_name.__name__
            else:
                func = getattr(self, action_name, None)
                name = action_name

            if func:
                actions[name] = {
                    "func": func,
                    "description": getattr(
                        func, "short_description", name.replace("_", " ").title()
                    ),
                }

        return actions

    def delete_selected(self, request: HttpRequest, queryset: "models.QuerySet[Any]") -> str:
        """Default action: delete selected objects."""
        count = queryset.count()
        queryset.delete()
        return f"Successfully deleted {count} items."

    delete_selected.short_description = "Delete selected items"  # type: ignore[attr-defined]

    # Field value rendering
    def get_field_value(self, obj: models.Model, field_name: str) -> Any:
        """Get the display value for a field."""
        if field_name == "__str__":
            return str(obj)

        # Check for custom method
        if hasattr(self, field_name):
            method = getattr(self, field_name)
            if callable(method):
                return method(obj)

        # Check for model attribute
        if hasattr(obj, field_name):
            value = getattr(obj, field_name)

            # Handle callables (methods)
            if callable(value):
                value = value()

            # Handle foreign keys
            if isinstance(value, models.Model):
                return str(value)

            # Handle booleans
            if isinstance(value, bool):
                return "Yes" if value else "No"

            # Handle None
            if value is None:
                return "-"

            return value

        return "-"

    def get_field_display_name(self, field_name: str) -> str:
        """Get the display name for a field column."""
        if field_name == "__str__":
            return str(self.opts.verbose_name.title())

        # Check for custom method with short_description
        if hasattr(self, field_name):
            method = getattr(self, field_name)
            if hasattr(method, "short_description"):
                return str(method.short_description)

        # Try to get from model field
        try:
            field = self.opts.get_field(field_name)
            return str(field.verbose_name.title())
        except Exception:
            logger.debug("Failed to get verbose name for %s", field_name, exc_info=True)
            return field_name.replace("_", " ").title()
