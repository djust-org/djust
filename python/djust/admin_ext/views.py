"""
LiveView-based admin views for djust admin_ext.

These views use djust's LiveView to provide reactive, real-time admin interfaces.
Includes plugin-aware context (plugin_nav, widgets) for the admin shell.
"""

import logging
from functools import wraps
from typing import Any, Callable, Dict, List, Optional, Set
from urllib.parse import urlencode

from django.contrib.admin.forms import AdminAuthenticationForm
from django.contrib.auth import logout
from django.contrib.auth.views import LoginView as DjangoLoginView
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django.core.paginator import Paginator
from django.db.models import ForeignKey, OneToOneField, Q
from django.forms import CheckboxInput
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseRedirect
from django.urls import Resolver404, resolve, reverse
from djust import LiveView
from djust.decorators import StateProperty, debounce, event_handler, state

from .._log_utils import sanitize_for_log
from .forms import AdminFormMixin

logger = logging.getLogger(__name__)

#: The change form's submit buttons other than the plain Save (which goes to the change list).
_SAVE_BUTTONS = ("save_and_continue", "save_and_add_another")

#: Shown when a Save arrives without the form's values (#4218).
SAVE_NOT_RECEIVED = (
    "The form's values did not arrive with Save, so nothing was saved. "
    "Reload the page and try again."
)

# Global registry for admin view configurations
# This avoids storing non-serializable objects on view instances
_VIEW_REGISTRY: Dict[str, Dict[str, Any]] = {}


def register_admin_view(
    view_id: str,
    admin_site: Any,
    model: Optional[Any] = None,
    model_admin: Optional[Any] = None,
) -> None:
    """Register admin config for a view."""
    _VIEW_REGISTRY[view_id] = {
        "admin_site": admin_site,
        "model": model,
        "model_admin": model_admin,
    }


def get_admin_config(view_id: Optional[str]) -> Dict[str, Any]:
    """Get admin config for a view."""
    return _VIEW_REGISTRY.get(view_id, {}) if view_id is not None else {}


def _registry_id_from_route(view: Any) -> Optional[str]:
    """The ``_view_registry_id`` the route serving ``view.request`` passes to ``as_view()``.

    ``DjustAdminSite.get_urls`` binds each admin view to its site (and model)
    through an ``as_view(_view_registry_id=...)`` kwarg. The HTTP GET gets it
    from ``as_view``; a WebSocket or SSE mount builds the view with no
    kwargs, from the page URL alone (#3140). Resolve that URL and read the
    kwarg off the route, but only when the route serves this view's class:
    the client names both the view and the URL, so a URL routed to another
    view must not select its registration.
    """
    request = getattr(view, "request", None)
    path_info = getattr(request, "path_info", None)
    if not path_info:
        return None
    try:
        match = resolve(path_info)
    except Resolver404:
        return None
    if getattr(match.func, "view_class", None) is not type(view):
        return None
    initkwargs = getattr(match.func, "view_initkwargs", None) or {}
    registry_id = initkwargs.get("_view_registry_id")
    return registry_id if isinstance(registry_id, str) else None


class _AdminRegistryMixin:
    """Resolves an admin view's site registration on every transport."""

    # View ID for registry lookup - set via as_view()
    # Prefixed with underscore so LiveView's get_context_data() skips it
    _view_registry_id: Optional[str] = None

    # Set once the route lookup has run for this view's request, so a miss is
    # not re-resolved on every attribute read.
    _registry_route_checked: bool = False

    def _admin_config(self) -> Dict[str, Any]:
        if self._view_registry_id is None and not self._registry_route_checked:
            # A socket mount: recover the id from the page's route and keep it.
            # Only once a request exists; before that there is nothing to resolve.
            if getattr(self, "request", None) is not None:
                self._view_registry_id = _registry_id_from_route(self)
                self._registry_route_checked = True
        return get_admin_config(self._view_registry_id)

    @property
    def _admin_site(self) -> Any:
        """Admin site from registry."""
        return self._admin_config().get("admin_site")


def _serialize_widget_slots(
    widget_classes: List[Any], *, object_id: Optional[Any] = None
) -> List[Dict[str, Any]]:
    """Convert a list of LiveView widget classes into the dict shape
    that ``_change_form_widgets.html`` / ``_change_list_widgets.html``
    expect. Each entry carries the dotted ``view_path`` so
    ``{% live_render %}`` can resolve it, and the slot's ``view_id``: a stable
    id, so that over a socket the widget is the same live child on every
    re-render of the page (its state kept, ``mount()`` run once) rather than a
    new ``child_N`` mounted on each parent event (#4223).
    """
    out: List[Dict[str, Any]] = []
    for position, widget_cls in enumerate(widget_classes, start=1):
        entry = {
            "view_path": f"{widget_cls.__module__}.{widget_cls.__name__}",
            "view_id": f"djust_admin_widget_{position}",
            "label": getattr(widget_cls, "label", ""),
            "size": getattr(widget_cls, "size", "md"),
        }
        if object_id is not None:
            entry["object_id"] = object_id
        out.append(entry)
    return out


def admin_login_required(view_func: Callable[..., Any]) -> Callable[..., Any]:
    """
    Decorator that checks if the user is authenticated and is staff.
    Redirects to admin login if not.
    """

    @wraps(view_func)
    def wrapped_view(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        if (
            not request.user.is_authenticated
            or not request.user.is_active
            or not request.user.is_staff
        ):
            admin_site_name = kwargs.get("admin_site_name", "djust_admin")
            login_url = reverse(f"{admin_site_name}:login")
            # Construct query string separately so the redirect target is
            # clearly just login_url (from reverse(), trusted). request.path
            # flows into a QUERY STRING, not the redirect target itself.
            query = urlencode({"next": request.path})
            redirect_url = f"{login_url}?{query}"
            return HttpResponseRedirect(redirect_url)
        return view_func(request, *args, **kwargs)

    return wrapped_view


class AdminBaseMixin(_AdminRegistryMixin):
    """Base mixin for all admin views. Provides admin chrome context.

    Always combined with ``LiveView`` (e.g. ``AdminIndexView(AdminBaseMixin,
    LiveView)``); the ``request`` attribute is provided by that collaborator
    and declared here only so the strict type-checker sees the contract.
    """

    # Provided by the co-mixed ``LiveView`` at mount time. Annotation-only
    # (no runtime assignment) so it doesn't shadow LiveView's instance attr.
    request: Any

    # Declare djust-honored auth so the WebSocket/SSE mount path gates admin
    # views too. The ``admin_login_required`` wrapper below only protects the
    # HTTP ``as_view`` callable; the live path authorizes via
    # ``check_view_auth``, which honors ``login_required`` (auth) and the
    # ``check_permissions`` hook (staff). Without these, admin views were
    # staff-gated on the initial HTTP GET but open over WebSocket (finding #13).
    login_required = True

    def check_permissions(self, request: HttpRequest) -> None:
        """Staff gate honored on every transport (HTTP, WebSocket, SSE)."""
        user = getattr(request, "user", None)
        if not (user is not None and user.is_authenticated and user.is_active and user.is_staff):
            raise PermissionDenied("Admin access requires an active staff account.")
        if self._admin_site is None:
            # No site registration: a socket mount whose URL does not route to
            # this view (#3140). Refuse it rather than crash rendering the chrome.
            raise PermissionDenied("This admin page is not routed at the requested URL.")

    @classmethod
    def as_view(cls, **initkwargs: Any) -> Callable[..., Any]:
        """Wrap the view with login required check."""
        # ``super()`` resolves to the co-mixed ``LiveView`` at runtime, which
        # provides ``as_view`` (the bare mixin's MRO doesn't declare it).
        view = super().as_view(**initkwargs)  # type: ignore[misc]
        return admin_login_required(view)

    @property
    def _model(self) -> Any:
        """Model class from registry."""
        return self._admin_config().get("model")

    @property
    def _model_admin(self) -> Any:
        """ModelAdmin from registry."""
        return self._admin_config().get("model_admin")

    def get_admin_context(self) -> Dict[str, Any]:
        """Add common admin context (JSON serializable).

        All string values are forced through str() to resolve Django's
        lazy translation proxies (__proxy__), which are not JSON
        serializable.
        """
        # Build a serializable app list
        app_list: List[Dict[str, Any]] = []
        for app in self._admin_site.get_app_list(self.request):
            app_data: Dict[str, Any] = {
                "name": str(app["name"]),
                "app_label": str(app["app_label"]),
                "models": [],
            }
            for model in app["models"]:
                app_data["models"].append(
                    {
                        "name": str(model["name"]),
                        "object_name": str(model["object_name"]),
                        "admin_url": str(model["admin_url"]),
                        "add_url": str(model["add_url"]),
                    }
                )
            app_list.append(app_data)

        # Build serializable opts
        opts = None
        if self._model:
            opts = {
                "verbose_name": str(self._model._meta.verbose_name),
                "verbose_name_plural": str(self._model._meta.verbose_name_plural),
                "app_label": str(self._model._meta.app_label),
                "model_name": str(self._model._meta.model_name),
            }

        # Plugin navigation
        plugin_nav = self._admin_site.get_plugin_nav(self.request)

        return {
            "site_header": str(self._admin_site.site_header),
            "site_title": str(self._admin_site.site_title),
            "app_list": app_list,
            "plugin_nav": plugin_nav,
            "opts": opts,
            "admin_site_name": str(self._admin_site.name),
            "username": self.request.user.username if self.request.user.is_authenticated else None,
            "is_authenticated": self.request.user.is_authenticated,
        }


class AdminIndexView(AdminBaseMixin, LiveView):
    """
    Admin dashboard / index view.

    Shows widgets from plugins + all registered apps and models.
    """

    exposure_policy = "explicit"

    template_name = "djust_admin/index.html"

    def mount(self, request: HttpRequest, **kwargs: Any) -> None:
        self.request = request

    def get_context_data(self, **kwargs: Any) -> Dict[str, Any]:
        # Collect widgets from all plugins (pre-rendered HTML)
        widgets = self._admin_site.get_widgets(self.request)

        return {
            **self.get_admin_context(),
            "title": self._admin_site.index_title if self._admin_site else "Dashboard",
            "widgets": widgets,
            "has_widgets": len(widgets) > 0,
        }


class ModelListView(AdminBaseMixin, LiveView):
    """
    Model list view with real-time search, filtering, and bulk actions.
    """

    exposure_policy = "explicit"

    template_name = "djust_admin/model_list.html"

    # Reactive state
    search_query = state(default="")
    current_page = state(default=1)
    ordering: StateProperty[Optional[str]] = state(default=None)
    selected_ids: StateProperty[List[Any]] = state(default_factory=list)
    select_all = state(default=False)
    active_filters: StateProperty[Dict[str, Any]] = state(default_factory=dict)

    def check_permissions(self, request: HttpRequest) -> None:
        """Staff gate plus the ModelAdmin view permission."""
        super().check_permissions(request)
        model_admin = self._model_admin
        if model_admin is None or not model_admin.has_view_or_change_permission(request):
            raise PermissionDenied("You do not have permission to view these objects.")

    def mount(self, request: HttpRequest, **kwargs: Any) -> None:
        self.request = request
        self.selected_ids = []
        self.select_all = False
        self.active_filters = {}

    def _declared_filter_fields(self) -> Set[str]:
        """Field names declared in the ModelAdmin's ``list_filter``.

        ``apply_filter`` and :meth:`get_queryset` accept only these names, as
        Django's admin does: a filter cannot follow a relation or use a lookup
        that ``list_filter`` does not declare.
        """
        names: Set[str] = set()
        for entry in self._model_admin.get_list_filter(self.request) or ():
            if isinstance(entry, (list, tuple)) and entry:
                entry = entry[0]
            if isinstance(entry, str) and entry:
                names.add(entry)
        return names

    def _sortable_fields(self) -> Set[str]:
        """Field names the change list sorts on: the sortable ``list_display``
        columns plus the fields named in the ModelAdmin's ``ordering``."""
        names: Set[str] = {
            name
            for name in self._model_admin.get_list_display(self.request) or ()
            if isinstance(name, str) and name and name != "__str__"
        }
        for entry in self._model_admin.get_ordering(self.request) or ():
            if isinstance(entry, str) and entry:
                names.add(entry[1:] if entry.startswith("-") else entry)
        return names

    def _ordering_allowed(self, ordering: Any) -> bool:
        """True when ``ordering`` is a sortable field, optionally prefixed by ``-``."""
        if not isinstance(ordering, str) or not ordering:
            return False
        name = ordering[1:] if ordering.startswith("-") else ordering
        return name in self._sortable_fields()

    def get_queryset(self) -> Any:
        """Get filtered and sorted queryset."""
        qs = self._model_admin.get_queryset(self.request)

        # Apply search
        if self.search_query:
            search_fields = self._model_admin.get_search_fields(self.request)
            if search_fields:
                q_objects = Q()
                for field in search_fields:
                    q_objects |= Q(**{f"{field}__icontains": self.search_query})
                qs = qs.filter(q_objects)

        # Apply active filters (declared list_filter fields only)
        allowed_filters = self._declared_filter_fields()
        for field_name, value in self.active_filters.items():
            if field_name not in allowed_filters:
                logger.debug(
                    "Ignoring undeclared admin list filter %s", sanitize_for_log(field_name)
                )
                continue
            if value is not None and value != "":
                if value == "true":
                    qs = qs.filter(**{field_name: True})
                elif value == "false":
                    qs = qs.filter(**{field_name: False})
                else:
                    qs = qs.filter(**{field_name: value})

        # Apply ordering (sortable fields only)
        if self.ordering:
            if self._ordering_allowed(self.ordering):
                qs = qs.order_by(self.ordering)
            else:
                logger.debug(
                    "Ignoring undeclared admin list ordering %s", sanitize_for_log(self.ordering)
                )

        return qs

    def get_page(self) -> Any:
        """Get the current page of results."""
        qs = self.get_queryset()
        paginator = Paginator(qs, self._model_admin.list_per_page)
        return paginator.get_page(self.current_page)

    def get_context_data(self, **kwargs: Any) -> Dict[str, Any]:
        page = self.get_page()
        list_display = self._model_admin.get_list_display(self.request)

        # Build column headers
        columns: List[Dict[str, Any]] = []
        for field_name in list_display:
            columns.append(
                {
                    "name": field_name,
                    "label": self._model_admin.get_field_display_name(field_name),
                    "sortable": field_name != "__str__",
                }
            )

        # Build rows
        rows: List[Dict[str, Any]] = []
        for obj in page:
            row: Dict[str, Any] = {
                "pk": obj.pk,
                "selected": obj.pk in self.selected_ids,
                "edit_url": reverse(
                    f"{self._admin_site.name}:{self._model._meta.app_label}_{self._model._meta.model_name}_change",
                    args=[obj.pk],
                ),
                "values": [],
            }
            for field_name in list_display:
                row["values"].append(self._model_admin.get_field_value(obj, field_name))
            rows.append(row)

        # Serialize pagination data
        pagination = {
            "number": page.number,
            "has_previous": page.has_previous(),
            "has_next": page.has_next(),
            "previous_page_number": page.previous_page_number() if page.has_previous() else None,
            "next_page_number": page.next_page_number() if page.has_next() else None,
            "num_pages": page.paginator.num_pages,
            "count": page.paginator.count,
            "page_range": list(page.paginator.page_range),
        }

        # Serialize actions
        actions_raw = self._model_admin.get_actions(self.request)
        actions = [
            {"name": name, "label": info.get("label", name)} for name, info in actions_raw.items()
        ]

        # Build filter options
        filters: List[Dict[str, Any]] = []
        list_filter = self._model_admin.get_list_filter(self.request) or []
        for filter_field in list_filter:
            try:
                field = self._model._meta.get_field(filter_field)
                filter_data: Dict[str, Any] = {
                    "name": filter_field,
                    "label": field.verbose_name.title()
                    if hasattr(field, "verbose_name")
                    else filter_field.replace("_", " ").title(),
                    "choices": [],
                    "current_value": self.active_filters.get(filter_field, ""),
                }

                if (
                    hasattr(field, "get_internal_type")
                    and field.get_internal_type() == "BooleanField"
                ):
                    filter_data["choices"] = [
                        {"value": "true", "label": "Yes"},
                        {"value": "false", "label": "No"},
                    ]
                elif hasattr(field, "choices") and field.choices:
                    filter_data["choices"] = [
                        {"value": str(c[0]), "label": str(c[1])} for c in field.choices
                    ]
                elif isinstance(field, (ForeignKey, OneToOneField)):
                    related_model = field.remote_field.model
                    related_qs = related_model.objects.all()[:50]
                    filter_data["choices"] = [
                        {"value": str(obj.pk), "label": str(obj)} for obj in related_qs
                    ]
                else:
                    distinct_values = (
                        self.get_queryset().values_list(filter_field, flat=True).distinct()[:20]
                    )
                    filter_data["choices"] = [
                        {"value": str(v), "label": str(v)} for v in distinct_values if v is not None
                    ]

                filters.append(filter_data)
            except Exception:
                logger.debug("Failed to build filter for %s", filter_field, exc_info=True)

        # Per-page widget slots (v0.7.0)
        change_list_widgets = _serialize_widget_slots(
            self._model_admin.get_change_list_widgets(self.request)
        )

        return {
            **self.get_admin_context(),
            "title": f"Select {self._model._meta.verbose_name} to change",
            "columns": columns,
            "rows": rows,
            "pagination": pagination,
            "search_query": self.search_query,
            "ordering": self.ordering,
            "selected_ids": self.selected_ids,
            "select_all": self.select_all,
            "active_filters": self.active_filters,
            "filters": filters,
            "has_filters": len(filters) > 0,
            "actions": actions,
            "add_url": reverse(
                f"{self._admin_site.name}:{self._model._meta.app_label}_{self._model._meta.model_name}_add",
            ),
            "has_add_permission": self._model_admin.has_add_permission(self.request),
            "change_list_widgets": change_list_widgets,
        }

    @event_handler
    @debounce(300)
    def search(self, query: str) -> None:
        """Handle search input with debounce."""
        self.search_query = query
        self.current_page = 1
        self.selected_ids = []
        self.select_all = False

    @event_handler
    def sort_by(self, field: str) -> None:
        """Handle column sorting. Only sortable columns are accepted."""
        if not isinstance(field, str) or field not in self._sortable_fields():
            logger.debug("Ignoring sort on undeclared admin field %s", sanitize_for_log(field))
            return
        if self.ordering == field:
            self.ordering = f"-{field}"
        elif self.ordering == f"-{field}":
            self.ordering = None
        else:
            self.ordering = field
        self.current_page = 1

    @event_handler
    def go_to_page(self, page: int) -> None:
        """Handle pagination."""
        self.current_page = page
        self.selected_ids = []
        self.select_all = False

    @event_handler
    def toggle_select(self, pk: int) -> None:
        """Toggle selection of a single row."""
        if pk in self.selected_ids:
            self.selected_ids = [x for x in self.selected_ids if x != pk]
        else:
            self.selected_ids = self.selected_ids + [pk]
        self.select_all = False

    @event_handler
    def toggle_select_all(self) -> None:
        """Toggle selection of all visible rows."""
        if self.select_all:
            self.selected_ids = []
            self.select_all = False
        else:
            page = self.get_page()
            self.selected_ids = [obj.pk for obj in page]
            self.select_all = True

    @event_handler
    def run_action(self, action_name: str) -> Any:
        """Execute a bulk action on selected items.

        Enforces ``allowed_permissions`` metadata stamped by decorators
        (notably ``@admin_action_with_progress(permissions=[...])``).
        Actions without ``allowed_permissions`` run unchanged, so this
        is backward-compatible with actions decorated before v0.7.0.

        If the action returns an ``HttpResponseRedirect`` (as stock
        Django admin actions and ``@admin_action_with_progress``-
        decorated actions do), the redirect is intercepted and a
        ``redirect`` push_event is dispatched to the client. This is
        required because LiveView event handlers are invoked over the
        WebSocket — raw HTTP responses have nowhere to go.
        """
        if not self.selected_ids:
            return None

        actions = self._model_admin.get_actions(self.request)
        if action_name not in actions:
            return None

        action_func = actions[action_name]["func"]

        if action_name == "delete_selected" and not self._model_admin.has_delete_permission(
            self.request
        ):
            raise PermissionDenied("You do not have permission to delete these objects.")

        # If the action declares required perms (via
        # ``@admin_action_with_progress(permissions=[...])`` or an
        # equivalent ``allowed_permissions`` attribute), block users who
        # lack them BEFORE firing the action. The change list only needs
        # view permission, so without this a view-only staff user could
        # fire any action in the dropdown.
        allowed = getattr(action_func, "allowed_permissions", None) or []
        if allowed and not self.request.user.has_perms(allowed):
            raise PermissionDenied("User lacks required permissions for this action: %r" % allowed)

        # Only rows the ModelAdmin's queryset holds, as Django's admin acts on
        # get_queryset(request): a selected pk outside it is ignored.
        queryset = self._model_admin.get_queryset(self.request).filter(pk__in=self.selected_ids)
        result = action_func(self.request, queryset)

        self.selected_ids = []
        self.select_all = False

        # WS-side redirect shim: Django-style admin actions return
        # ``HttpResponseRedirect`` for post-action navigation. Over
        # WebSocket, such responses would be silently dropped by the
        # LiveView dispatcher — the browser would never navigate. Convert
        # to a ``redirect`` push_event so the client navigates to the
        # progress page (or wherever the action pointed).
        if isinstance(result, HttpResponseRedirect):
            self.push_event("redirect", {"url": result.url})
            return None
        return result

    @event_handler
    def apply_filter(self, field: str, value: str) -> None:
        """Apply a filter to the list. Only ``list_filter`` fields are accepted."""
        if not isinstance(field, str) or field not in self._declared_filter_fields():
            logger.debug("Ignoring undeclared admin list filter %s", sanitize_for_log(field))
            return
        if value:
            self.active_filters = {**self.active_filters, field: value}
        else:
            filters = dict(self.active_filters)
            if field in filters:
                del filters[field]
            self.active_filters = filters

        self.current_page = 1
        self.selected_ids = []
        self.select_all = False

    @event_handler
    def clear_filters(self) -> None:
        """Clear all active filters."""
        self.active_filters = {}
        self.current_page = 1
        self.selected_ids = []
        self.select_all = False


class ModelDetailView(AdminBaseMixin, AdminFormMixin, LiveView):
    """
    Model detail/edit view with real-time form validation.
    """

    exposure_policy = "explicit"

    template_name = "djust_admin/model_detail.html"

    is_saving = state(default=False)
    save_success = state(default=False)
    redirect_url: StateProperty[Optional[str]] = state(default=None)

    def mount(self, request: HttpRequest, object_id: Optional[Any] = None, **kwargs: Any) -> None:
        super().mount(request, object_id=object_id, **kwargs)

    def get_object(self) -> Any:
        """The object being edited, or None on the add page."""
        return self.object

    def has_object_permission(self, request: Any, obj: Any) -> bool:
        """Viewing an existing object needs the ModelAdmin view or change permission."""
        return bool(self._model_admin.has_view_or_change_permission(request, obj))

    def _check_save_permission(self) -> None:
        """Saving needs add permission for a new object, change permission otherwise."""
        obj = self.object
        if obj is None or obj.pk is None:
            allowed = self._model_admin.has_add_permission(self.request)
        else:
            allowed = self._model_admin.has_change_permission(self.request, obj)
        if not allowed:
            raise PermissionDenied("You do not have permission to save this object.")

    def get_context_data(self, **kwargs: Any) -> Dict[str, Any]:
        readonly_fields = set(self._model_admin.get_readonly_fields(self.request, self.object))

        # Each shown field is an input when the form has it (the editable ones), or text read
        # from the row when it is readonly; an excluded field is not on the page (#4218).
        fieldsets_data: List[Dict[str, Any]] = []
        for fieldset_name, fieldset_options in self._model_admin.get_fieldsets(
            self.request, self.object
        ):
            fields_data: List[Dict[str, Any]] = []
            for field_name in fieldset_options.get("fields", []):
                if field_name in readonly_fields:
                    readonly_html = self._readonly_field_html(field_name)
                    if readonly_html is not None:
                        # A callable entry is not serializable view state: name it (#4244).
                        fields_data.append(
                            {
                                "name": getattr(field_name, "__name__", str(field_name)),
                                "html": readonly_html,
                            }
                        )
                elif self.form_instance and field_name in self.form_instance.fields:
                    field_info = self.get_field_info(field_name)

                    field_html = self.as_live_field(
                        field_name,
                        framework="admin_tailwind",
                        options=field_info.get("options", []),
                        is_foreign_key=field_info.get("is_foreign_key", False),
                        is_many_to_many=field_info.get("is_many_to_many", False),
                        is_date=field_info.get("is_date", False),
                        is_datetime=field_info.get("is_datetime", False),
                        is_time=field_info.get("is_time", False),
                        input_type=field_info.get("input_type"),
                        render_label=True,
                        render_errors=True,
                        render_help_text=True,
                    )

                    fields_data.append(
                        {
                            "name": field_name,
                            "html": field_html,
                        }
                    )

            fieldsets_data.append(
                {
                    "name": fieldset_name,
                    "fields": fields_data,
                }
            )

        # Per-page widget slots (v0.7.0)
        object_pk = self.object.pk if self.object else None
        change_form_widgets = _serialize_widget_slots(
            self._model_admin.get_change_form_widgets(self.request, self.object),
            object_id=object_pk,
        )

        return {
            **self.get_admin_context(),
            "title": f"Change {self.object}"
            if self.object
            else f"Add {self._model._meta.verbose_name}",
            "object": str(self.object) if self.object else None,
            "object_pk": object_pk,
            "is_saving": self.is_saving,
            "save_success": self.save_success,
            "save_errors": self._save_errors(),
            "fieldsets": fieldsets_data,
            "has_delete_permission": self._model_admin.has_delete_permission(
                self.request, self.object
            ),
            "delete_url": reverse(
                f"{self._admin_site.name}:{self._model._meta.app_label}_{self._model._meta.model_name}_delete",
                args=[self.object.pk],
            )
            if self.object
            else None,
            "change_form_widgets": change_form_widgets,
        }

    def _readonly_field_html(self, field_name: Any) -> Optional[str]:
        """A readonly entry, as text (it is not a form field, #4218).

        A model field is read from the row. Any other ``readonly_fields`` entry -- a
        ModelAdmin method, a model attribute/method or a callable -- is shown as Django's
        admin shows it (#4244): the label from ``short_description`` /
        ``@admin.display(description=...)`` or the name, the value from calling it with
        the object. See ``_readonly_callable_html``.
        """
        from django.core.exceptions import FieldDoesNotExist
        from django.utils.html import escape

        from djust.frameworks import get_adapter
        from djust.serialization import model_field_value_withheld

        if not isinstance(field_name, str):
            return self._readonly_callable_html(field_name)
        try:
            model_field = self._model._meta.get_field(field_name)
        except FieldDoesNotExist:
            return self._readonly_callable_html(field_name)
        obj = self.object
        value: Any = None
        if obj is not None and not model_field_value_withheld(self._model, field_name):
            if getattr(model_field, "choices", None):
                value = getattr(obj, f"get_{field_name}_display", lambda: None)()
            else:
                value = getattr(obj, field_name, None)
            if isinstance(value, bool):
                value = "Yes" if value else "No"
        adapter = get_adapter("admin_tailwind")
        label = str(getattr(model_field, "verbose_name", field_name)).capitalize()
        return (
            f'<div class="{adapter.WRAPPER_CLASS}">'  # type: ignore[attr-defined]
            f'<span class="{adapter.LABEL_CLASS}">{escape(label)}</span>'  # type: ignore[attr-defined]
            f'<div class="mt-1">{adapter._render_readonly(field_name, value)}</div>'  # type: ignore[attr-defined]
            "</div>"
        )

    def _readonly_callable_html(self, name: Any) -> Optional[str]:
        """A readonly entry that is not a model field, rendered as Django's admin does (#4244).

        Escaping: the label and the value go through ``format_html`` (``conditional_escape``),
        so a plain string returned by the method is escaped; a value the ModelAdmin author
        marked safe (``format_html`` / ``mark_safe``) renders as HTML, exactly as Django's
        ``AdminReadonlyField.contents``. The callable runs only with the object this view
        already authorized, never on the add page (shown as ``-``). Unlike a model field,
        ``model_field_value_withheld`` does not apply: the method is developer code that shows
        what it chooses (Django parity). A name that resolves to nothing stays off the page.
        """
        from django.contrib.admin.utils import display_for_value, label_for_field, lookup_field
        from django.utils.html import format_html
        from django.utils.text import capfirst

        from djust.frameworks import get_adapter

        try:
            label = label_for_field(name, self._model, self._model_admin)
        except AttributeError:
            logger.debug("readonly field %s resolves to nothing; not shown", sanitize_for_log(name))
            return None
        obj = self.object
        empty: Any = "-"
        get_empty = getattr(self._model_admin, "get_empty_value_display", None)
        if callable(get_empty):
            empty = get_empty()
        display: Any = empty
        if obj is not None:
            # Django's AdminReadonlyField.contents shows the empty value when the lookup
            # raises these, instead of failing the page (e.g. a method touching a missing
            # related row).
            try:
                _f, attr, value = lookup_field(name, obj, self._model_admin)
            except (AttributeError, ValueError, ObjectDoesNotExist):
                display = empty
            else:
                if getattr(attr, "boolean", False):
                    display = "Yes" if value else ("No" if value is not None else empty)
                else:
                    display = display_for_value(value, empty)
        adapter = get_adapter("admin_tailwind")
        html: str = format_html(
            '<div class="{}"><span class="{}">{}</span>'
            '<div class="mt-1"><p class="py-2 text-gray-900">{}</p></div></div>',
            adapter.WRAPPER_CLASS,  # type: ignore[attr-defined]
            adapter.LABEL_CLASS,  # type: ignore[attr-defined]
            capfirst(label),
            display,
        )
        return html

    def _save_errors(self) -> List[str]:
        """What a failed Save tells the person: the refusal, and the form's own errors."""
        errors = [self.error_message] if getattr(self, "error_message", "") else []
        form_errors = getattr(self, "form_errors", None)
        if isinstance(form_errors, (list, tuple)):
            errors.extend(str(error) for error in form_errors)
        return errors

    @event_handler
    def update_field(self, field: str, value: Any) -> None:
        """Handle field value change."""
        self.save_success = False
        self.validate_field(field=field, value=value)

    def form_valid(self, form: Any) -> None:
        self._check_save_permission()
        self.object = form.save()
        self.save_success = True

    def form_invalid(self, form: Any) -> None:
        self.save_success = False

    # The change form is one ``<form dj-submit="save">``: every Save carries the values on the
    # page, so what is saved is what the person sees, whether or not each field's live
    # ``validate_field`` event arrived (#4218). The handlers take strict parameters whatever the
    # project's policy: the pressed button then arrives as its own ``name=value`` on every
    # transport (ADR-036; under the legacy policy its only channel was ``_target``, which the
    # HTTP fallback does not carry).

    @event_handler(parameter_policy="strict")
    def save(self, **form_values: Any) -> None:
        """Save the submitted form, then go where the pressed button says (the change list)."""
        then = None
        for button in _SAVE_BUTTONS:
            if form_values.pop(button, None) is not None:
                then = button
        self._save_submitted(form_values, then)

    @event_handler(parameter_policy="strict")
    def save_and_continue(self, **form_values: Any) -> None:
        """Save the submitted form and stay on the page."""
        self._save_submitted(form_values, "save_and_continue")

    @event_handler(parameter_policy="strict")
    def save_and_add_another(self, **form_values: Any) -> None:
        """Save the submitted form and go to a new add page."""
        self._save_submitted(form_values, "save_and_add_another")

    def _save_submitted(self, form_values: Dict[str, Any], then: Optional[str]) -> None:
        """Save the values a Save carried. A Save that carries none of the form's fields saves
        nothing and says so: saving the values the server already holds would report "saved"
        for edits that never arrived (#4218)."""
        self._check_save_permission()
        self.is_saving = True
        self.save_success = False
        self.redirect_url = None
        self.error_message = ""

        # The form holds only the fields the page lets the person edit (``_create_form``): a
        # value sent for any other name (readonly, excluded, not shown) is not the form's.
        fields = self._create_form().fields
        received = {name: value for name, value in form_values.items() if name in fields}
        if fields and not received:
            logger.warning(
                "djust admin: a Save for %s carried none of the form's values; nothing was saved",
                sanitize_for_log(self._model._meta.label),
            )
            self.error_message = SAVE_NOT_RECEIVED
            self.is_saving = False
            return

        # A field the submission leaves out means what it means in a native post: an unchecked
        # checkbox is not in the form data, and is False.
        for name, form_field in fields.items():
            if name not in received and isinstance(form_field.widget, CheckboxInput):
                received[name] = False
        adding = self.object is None or getattr(self.object, "pk", None) is None
        self.submit_form(**received)

        if not self.save_success:
            self.is_saving = False
            return
        info = (
            f"{self._admin_site.name}:{self._model._meta.app_label}_{self._model._meta.model_name}"
        )
        if then == "save_and_continue":
            # Staying on an add page would add the row again on the next Save (the HTTP fallback
            # builds the page afresh): go to the new object's change page, as Django does.
            if adding and self.object is not None:
                self.redirect_url = reverse(f"{info}_change", args=[self.object.pk])
        else:
            page = "add" if then == "save_and_add_another" else "changelist"
            self.redirect_url = reverse(f"{info}_{page}")
        if self.redirect_url:
            # Navigation every transport carries; the template's former inline
            # ``<script>`` read a ``redirect_url`` the context never held.
            self.live_redirect(self.redirect_url)
        self.is_saving = False


class ModelCreateView(ModelDetailView):
    """Model create view - reuses detail view with no object."""

    def check_permissions(self, request: HttpRequest) -> None:
        """Staff gate plus the ModelAdmin add permission."""
        super().check_permissions(request)
        model_admin = self._model_admin
        if model_admin is None or not model_admin.has_add_permission(request):
            raise PermissionDenied("You do not have permission to add this object.")

    def mount(self, request: HttpRequest, object_id: Optional[Any] = None, **kwargs: Any) -> None:
        # Create view always starts with no object regardless of any
        # incoming object_id (LSP-compatible with ModelDetailView.mount).
        super().mount(request, object_id=None, **kwargs)


class ModelDeleteView(AdminBaseMixin, LiveView):
    """Model delete confirmation view."""

    exposure_policy = "explicit"

    template_name = "djust_admin/model_delete.html"

    confirmed = state(default=False)
    is_deleting = state(default=False)
    redirect_url: StateProperty[Optional[str]] = state(default=None)

    def mount(self, request: HttpRequest, object_id: Optional[Any] = None, **kwargs: Any) -> None:
        self.request = request
        self.object_id = object_id
        # Through the ModelAdmin's queryset, as Django's delete view does.
        self.object = self._model_admin.get_object(request, object_id)
        if self.object is None:
            raise Http404("No such object.")
        self.object_str = str(self.object)

    def get_object(self) -> Any:
        """The row being deleted, loaded by its key through the ModelAdmin's queryset.

        Not ``self.object``: over the HTTP fallback that is the view's restored state, not a
        model instance, and the object permission (``has_delete_permission(request, obj)``) and
        the delete read the row (#4218)."""
        return self._model_admin.get_object(self.request, self.object_id)

    def has_object_permission(self, request: Any, obj: Any) -> bool:
        """The confirmation page and the delete need the ModelAdmin delete permission."""
        return bool(self._model_admin.has_delete_permission(request, obj))

    def get_context_data(self, **kwargs: Any) -> Dict[str, Any]:
        return {
            **self.get_admin_context(),
            "title": f"Delete {self.object_str}",
            "object_str": self.object_str,
            "is_deleting": self.is_deleting,
            "redirect_url": self.redirect_url,
            "list_url": reverse(
                f"{self._admin_site.name}:{self._model._meta.app_label}_{self._model._meta.model_name}_changelist",
            ),
        }

    @event_handler
    def confirm_delete(self) -> None:
        """Confirm and execute deletion."""
        # The row by its key (``get_object``). An event has already been authorized against it
        # (the view's object permission is ``has_delete_permission(request, row)``); the check
        # here is for a direct call, and reads the same row.
        obj = self.get_object()
        if obj is None:
            raise Http404("No such object.")
        if not self._model_admin.has_delete_permission(self.request, obj):
            raise PermissionDenied("You do not have permission to delete this object.")
        self.is_deleting = True
        obj.delete()
        self.redirect_url = reverse(
            f"{self._admin_site.name}:{self._model._meta.app_label}_{self._model._meta.model_name}_changelist"
        )
        self.is_deleting = False
        # Navigation every transport carries; the template's inline ``<script>`` never ran.
        self.live_redirect(self.redirect_url)


class LoginView(_AdminRegistryMixin, DjangoLoginView):
    """Complete admin authentication over HTTP, including cookie rotation.

    Django's login view supplies CSRF protection, sensitive-parameter handling,
    safe ``next`` validation, and ``login()``. A WebSocket cannot deliver the
    rotated session cookie, so this page deliberately is not a LiveView.
    """

    template_name = "djust_admin/login.html"
    authentication_form = AdminAuthenticationForm

    def get_default_redirect_url(self) -> str:
        admin_name = self._admin_site.name if self._admin_site else "djust_admin"
        return str(reverse(f"{admin_name}:index"))

    def get_context_data(self, **kwargs: Any) -> Dict[str, Any]:
        context: Dict[str, Any] = super().get_context_data(**kwargs)
        form = context["form"]
        context.update(
            site_header=self._admin_site.site_header
            if self._admin_site
            else "djust administration",
            site_title=self._admin_site.site_title if self._admin_site else "djust admin",
            username=form["username"].value() or "",
            error=" ".join(str(error) for errors in form.errors.values() for error in errors),
        )
        return context


class LogoutView(_AdminRegistryMixin, LiveView):
    """Admin logout view."""

    exposure_policy = "explicit"

    template_name = "djust_admin/logout.html"

    def mount(self, request: HttpRequest, **kwargs: Any) -> None:
        self.request = request
        logout(request)

    def get_context_data(self, **kwargs: Any) -> Dict[str, Any]:
        return {
            "site_header": self._admin_site.site_header
            if self._admin_site
            else "djust administration",
            "site_title": self._admin_site.site_title if self._admin_site else "djust admin",
            "login_url": reverse(f"{self._admin_site.name}:login")
            if self._admin_site
            else "/djust-admin/login/",
        }
