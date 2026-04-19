"""
LiveView-based admin views for djust admin_ext.

These views use djust's LiveView to provide reactive, real-time admin interfaces.
Includes plugin-aware context (plugin_nav, widgets) for the admin shell.
"""

from functools import wraps

from django.contrib.auth import authenticate, logout
from django.core.paginator import Paginator
from django.db.models import ForeignKey, OneToOneField, Q
from django.http import HttpResponseRedirect
from django.urls import reverse
from djust import LiveView
from djust.decorators import debounce, event_handler, state

from .forms import AdminFormMixin

# Global registry for admin view configurations
# This avoids storing non-serializable objects on view instances
_VIEW_REGISTRY = {}


def register_admin_view(view_id, admin_site, model=None, model_admin=None):
    """Register admin config for a view."""
    _VIEW_REGISTRY[view_id] = {
        "admin_site": admin_site,
        "model": model,
        "model_admin": model_admin,
    }


def get_admin_config(view_id):
    """Get admin config for a view."""
    return _VIEW_REGISTRY.get(view_id, {})


def admin_login_required(view_func):
    """
    Decorator that checks if the user is authenticated and is staff.
    Redirects to admin login if not.
    """

    @wraps(view_func)
    def wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated or not request.user.is_staff:
            admin_site_name = kwargs.get("admin_site_name", "djust_admin")
            login_url = reverse(f"{admin_site_name}:login")
            return HttpResponseRedirect(f"{login_url}?next={request.path}")
        return view_func(request, *args, **kwargs)

    return wrapped_view


class AdminBaseMixin:
    """Base mixin for all admin views. Provides admin chrome context."""

    # View ID for registry lookup - set via as_view()
    # Prefixed with underscore so LiveView's get_context_data() skips it
    _view_registry_id = None

    @classmethod
    def as_view(cls, **initkwargs):
        """Wrap the view with login required check."""
        view = super().as_view(**initkwargs)
        return admin_login_required(view)

    @property
    def _admin_site(self):
        """Admin site from registry."""
        config = get_admin_config(self._view_registry_id)
        return config.get("admin_site")

    @property
    def _model(self):
        """Model class from registry."""
        config = get_admin_config(self._view_registry_id)
        return config.get("model")

    @property
    def _model_admin(self):
        """ModelAdmin from registry."""
        config = get_admin_config(self._view_registry_id)
        return config.get("model_admin")

    def get_admin_context(self):
        """Add common admin context (JSON serializable).

        All string values are forced through str() to resolve Django's
        lazy translation proxies (__proxy__), which are not JSON
        serializable.
        """
        # Build a serializable app list
        app_list = []
        for app in self._admin_site.get_app_list(self.request):
            app_data = {"name": str(app["name"]), "app_label": str(app["app_label"]), "models": []}
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

    template_name = "djust_admin/index.html"

    def mount(self, request, **kwargs):
        self.request = request

    def get_context_data(self, **kwargs):
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

    template_name = "djust_admin/model_list.html"

    # Reactive state
    search_query = state(default="")
    current_page = state(default=1)
    ordering = state(default=None)
    selected_ids = state(default=[])
    select_all = state(default=False)
    active_filters = state(default={})

    def mount(self, request, **kwargs):
        self.request = request
        self.selected_ids = []
        self.select_all = False
        self.active_filters = {}

    def get_queryset(self):
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

        # Apply active filters
        for field_name, value in self.active_filters.items():
            if value is not None and value != "":
                if value == "true":
                    qs = qs.filter(**{field_name: True})
                elif value == "false":
                    qs = qs.filter(**{field_name: False})
                else:
                    qs = qs.filter(**{field_name: value})

        # Apply ordering
        if self.ordering:
            qs = qs.order_by(self.ordering)

        return qs

    def get_page(self):
        """Get the current page of results."""
        qs = self.get_queryset()
        paginator = Paginator(qs, self._model_admin.list_per_page)
        return paginator.get_page(self.current_page)

    def get_context_data(self, **kwargs):
        page = self.get_page()
        list_display = self._model_admin.get_list_display(self.request)

        # Build column headers
        columns = []
        for field_name in list_display:
            columns.append(
                {
                    "name": field_name,
                    "label": self._model_admin.get_field_display_name(field_name),
                    "sortable": field_name != "__str__",
                }
            )

        # Build rows
        rows = []
        for obj in page:
            row = {
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
        filters = []
        list_filter = getattr(self._model_admin, "list_filter", [])
        for filter_field in list_filter:
            try:
                field = self._model._meta.get_field(filter_field)
                filter_data = {
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
                pass

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
        }

    @event_handler
    @debounce(300)
    def search(self, query: str):
        """Handle search input with debounce."""
        self.search_query = query
        self.current_page = 1
        self.selected_ids = []
        self.select_all = False

    @event_handler
    def sort_by(self, field: str):
        """Handle column sorting."""
        if self.ordering == field:
            self.ordering = f"-{field}"
        elif self.ordering == f"-{field}":
            self.ordering = None
        else:
            self.ordering = field
        self.current_page = 1

    @event_handler
    def go_to_page(self, page: int):
        """Handle pagination."""
        self.current_page = page
        self.selected_ids = []
        self.select_all = False

    @event_handler
    def toggle_select(self, pk: int):
        """Toggle selection of a single row."""
        if pk in self.selected_ids:
            self.selected_ids = [x for x in self.selected_ids if x != pk]
        else:
            self.selected_ids = self.selected_ids + [pk]
        self.select_all = False

    @event_handler
    def toggle_select_all(self):
        """Toggle selection of all visible rows."""
        if self.select_all:
            self.selected_ids = []
            self.select_all = False
        else:
            page = self.get_page()
            self.selected_ids = [obj.pk for obj in page]
            self.select_all = True

    @event_handler
    def run_action(self, action_name: str):
        """Execute a bulk action on selected items."""
        if not self.selected_ids:
            return

        actions = self._model_admin.get_actions(self.request)
        if action_name not in actions:
            return

        queryset = self._model.objects.filter(pk__in=self.selected_ids)
        action_func = actions[action_name]["func"]
        result = action_func(self.request, queryset)

        self.selected_ids = []
        self.select_all = False
        return result

    @event_handler
    def apply_filter(self, field: str, value: str):
        """Apply a filter to the list."""
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
    def clear_filters(self):
        """Clear all active filters."""
        self.active_filters = {}
        self.current_page = 1
        self.selected_ids = []
        self.select_all = False


class ModelDetailView(AdminBaseMixin, AdminFormMixin, LiveView):
    """
    Model detail/edit view with real-time form validation.
    """

    template_name = "djust_admin/model_detail.html"

    is_saving = state(default=False)
    save_success = state(default=False)
    redirect_url = state(default=None)

    def mount(self, request, object_id=None, **kwargs):
        super().mount(request, object_id=object_id, **kwargs)

    def get_context_data(self, **kwargs):
        readonly_fields = self._model_admin.get_readonly_fields(self.request, self.object)

        fieldsets_data = []
        for fieldset_name, fieldset_options in self._model_admin.get_fieldsets(
            self.request, self.object
        ):
            fields_data = []
            for field_name in fieldset_options.get("fields", []):
                if self.form_instance and field_name in self.form_instance.fields:
                    field_info = self.get_field_info(field_name)
                    is_readonly = field_name in readonly_fields

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
                        readonly=is_readonly,
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

        return {
            **self.get_admin_context(),
            "title": f"Change {self.object}"
            if self.object
            else f"Add {self._model._meta.verbose_name}",
            "object": str(self.object) if self.object else None,
            "object_pk": self.object.pk if self.object else None,
            "is_saving": self.is_saving,
            "save_success": self.save_success,
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
        }

    @event_handler
    def update_field(self, field: str, value):
        """Handle field value change."""
        self.save_success = False
        self.validate_field(field_name=field, value=value)

    def form_valid(self, form):
        self.object = form.save()
        self.save_success = True

    def form_invalid(self, form):
        self.save_success = False

    @event_handler
    def save(self, redirect=True):
        """Save the form."""
        self.is_saving = True
        self.save_success = False
        self.redirect_url = None

        self.submit_form()

        if self.save_success and redirect:
            self.redirect_url = reverse(
                f"{self._admin_site.name}:{self._model._meta.app_label}_{self._model._meta.model_name}_changelist"
            )

        self.is_saving = False

    @event_handler
    def save_and_continue(self):
        """Save and stay on the same page."""
        self.save(redirect=False)

    @event_handler
    def save_and_add_another(self):
        """Save and redirect to add another."""
        self.save(redirect=False)
        if self.save_success:
            self.redirect_url = reverse(
                f"{self._admin_site.name}:{self._model._meta.app_label}_{self._model._meta.model_name}_add"
            )


class ModelCreateView(ModelDetailView):
    """Model create view - reuses detail view with no object."""

    def mount(self, request, **kwargs):
        super().mount(request, object_id=None, **kwargs)


class ModelDeleteView(AdminBaseMixin, LiveView):
    """Model delete confirmation view."""

    template_name = "djust_admin/model_delete.html"

    confirmed = state(default=False)
    is_deleting = state(default=False)
    redirect_url = state(default=None)

    def mount(self, request, object_id=None, **kwargs):
        self.request = request
        self.object_id = object_id
        self.object = self._model.objects.get(pk=object_id)
        self.object_str = str(self.object)

    def get_context_data(self, **kwargs):
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
    def confirm_delete(self):
        """Confirm and execute deletion."""
        self.is_deleting = True
        self.object.delete()
        self.redirect_url = reverse(
            f"{self._admin_site.name}:{self._model._meta.app_label}_{self._model._meta.model_name}_changelist"
        )
        self.is_deleting = False


class LoginView(LiveView):
    """Admin login view."""

    template_name = "djust_admin/login.html"

    _view_registry_id = None

    username = state(default="")
    password = state(default="")
    error = state(default="")

    def mount(self, request, **kwargs):
        self.request = request
        self.next_url = request.GET.get("next", "")

    @property
    def _admin_site(self):
        config = get_admin_config(self._view_registry_id)
        return config.get("admin_site")

    def get_context_data(self, **kwargs):
        return {
            "site_header": self._admin_site.site_header
            if self._admin_site
            else "djust administration",
            "site_title": self._admin_site.site_title if self._admin_site else "djust admin",
            "username": self.username,
            "error": self.error,
        }

    @event_handler
    def update_username(self, value: str, field: str = None):
        self.username = value
        self.error = ""

    @event_handler
    def update_password(self, value: str, field: str = None):
        self.password = value
        self.error = ""

    @event_handler
    def do_login(self, **kwargs):
        """Attempt to log in the user."""
        from django.contrib.auth import BACKEND_SESSION_KEY, HASH_SESSION_KEY, SESSION_KEY

        if not self.username or not self.password:
            self.error = "Please enter both username and password."
            return

        user = authenticate(self.request, username=self.username, password=self.password)

        if user is not None:
            if user.is_active and user.is_staff:
                # Manual session login for WebSocket context
                session = self.request.session
                session[SESSION_KEY] = str(user.pk)
                session[BACKEND_SESSION_KEY] = user.backend
                session[HASH_SESSION_KEY] = user.get_session_auth_hash()
                session.save()

                if self.next_url:
                    redirect_url = self.next_url
                else:
                    admin_name = self._admin_site.name if self._admin_site else "djust_admin"
                    redirect_url = reverse(f"{admin_name}:index")
                self.push_event("redirect", {"url": redirect_url})
            else:
                self.error = "Your account is not authorized to access the admin."
        else:
            self.error = "Invalid username or password."

        self.password = ""


class LogoutView(LiveView):
    """Admin logout view."""

    template_name = "djust_admin/logout.html"

    _view_registry_id = None

    def mount(self, request, **kwargs):
        self.request = request
        logout(request)

    @property
    def _admin_site(self):
        config = get_admin_config(self._view_registry_id)
        return config.get("admin_site")

    def get_context_data(self, **kwargs):
        return {
            "site_header": self._admin_site.site_header
            if self._admin_site
            else "djust administration",
            "site_title": self._admin_site.site_title if self._admin_site else "djust admin",
            "login_url": reverse(f"{self._admin_site.name}:login")
            if self._admin_site
            else "/djust-admin/login/",
        }
