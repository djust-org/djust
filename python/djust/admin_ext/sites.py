"""
DjustAdminSite - The main admin site class.

Manages both model registrations (CRUD) and plugin registrations (extensions).
Similar to Django's AdminSite but renders using djust LiveViews
for reactive, real-time admin interfaces.
"""

import logging
from django.apps import apps
from django.urls import path, reverse

logger = logging.getLogger(__name__)


class DjustAdminSite:
    """
    A reactive admin site powered by djust.

    Supports two registration systems:
    - Model registration: register(Model, ModelAdmin) for CRUD
    - Plugin registration: register_plugin(PluginClass) for extensions

    Usage:
        from djust.admin_ext import DjustAdminSite, DjustModelAdmin

        admin_site = DjustAdminSite(name='djust_admin')

        @admin_site.register(MyModel)
        class MyModelAdmin(DjustModelAdmin):
            list_display = ['name', 'created_at']
    """

    site_header = "djust administration"
    site_title = "djust admin"
    index_title = "Dashboard"

    def __init__(self, name="djust_admin"):
        self.name = name
        self._registry = {}  # model -> DjustModelAdmin
        self._plugins = {}  # name -> AdminPlugin instance

    # ---- Model registration (ported API) ----

    def register(self, model_or_iterable, admin_class=None, **options):
        """
        Register a model with the admin site.

        Can be used as a decorator:
            @admin_site.register(MyModel)
            class MyModelAdmin(DjustModelAdmin):
                pass

        Or called directly:
            admin_site.register(MyModel, MyModelAdmin)
        """

        def _model_admin_wrapper(admin_cls):
            if isinstance(model_or_iterable, (list, tuple)):
                models = model_or_iterable
            else:
                models = [model_or_iterable]

            for model in models:
                if model in self._registry:
                    raise ValueError(f"Model {model.__name__} is already registered")
                self._registry[model] = admin_cls(model, self)
            return admin_cls

        if admin_class is not None:
            return _model_admin_wrapper(admin_class)

        return _model_admin_wrapper

    def unregister(self, model_or_iterable):
        """Unregister a model from the admin site."""
        if isinstance(model_or_iterable, type):
            model_or_iterable = [model_or_iterable]

        for model in model_or_iterable:
            if model not in self._registry:
                raise ValueError(f"Model {model.__name__} is not registered")
            del self._registry[model]

    def is_registered(self, model):
        """Check if a model is registered with this site."""
        return model in self._registry

    # ---- Plugin registration (NEW) ----

    def register_plugin(self, plugin_class_or_instance):
        """
        Register a plugin with the admin site.

        Accepts a class (instantiated automatically) or an instance.

            site.register_plugin(AuthAdminPlugin)
            # or
            site.register_plugin(AuthAdminPlugin())
        """
        if isinstance(plugin_class_or_instance, type):
            plugin = plugin_class_or_instance()
        else:
            plugin = plugin_class_or_instance

        if not plugin.name:
            raise ValueError(f"Plugin {plugin.__class__.__name__} must have a 'name' attribute")

        if plugin.name in self._plugins:
            raise ValueError(f"Plugin '{plugin.name}' is already registered")

        self._plugins[plugin.name] = plugin
        plugin.ready()

    def unregister_plugin(self, name):
        """Unregister a plugin by name."""
        if name not in self._plugins:
            raise ValueError(f"Plugin '{name}' is not registered")
        del self._plugins[name]

    def get_plugin(self, name):
        """Get a registered plugin by name."""
        return self._plugins.get(name)

    # ---- URL generation ----

    def get_urls(self):
        """Return URL patterns for the admin site (models + plugins)."""
        from .progress import BulkActionProgressView
        from .views import (
            AdminIndexView,
            LoginView,
            LogoutView,
            ModelCreateView,
            ModelDeleteView,
            ModelDetailView,
            ModelListView,
            register_admin_view,
        )

        # Register login/logout views
        login_id = f"{self.name}_login"
        logout_id = f"{self.name}_logout"
        register_admin_view(login_id, admin_site=self)
        register_admin_view(logout_id, admin_site=self)

        # Register index view
        index_id = f"{self.name}_index"
        register_admin_view(index_id, admin_site=self)

        # Bulk-action progress view
        progress_id = f"{self.name}_progress"
        register_admin_view(progress_id, admin_site=self)
        urlpatterns = [
            path("login/", LoginView.as_view(_view_registry_id=login_id), name="login"),
            path("logout/", LogoutView.as_view(_view_registry_id=logout_id), name="logout"),
            path("", AdminIndexView.as_view(_view_registry_id=index_id), name="index"),
            path(
                "djust-progress/<str:job_id>/",
                BulkActionProgressView.as_view(_view_registry_id=progress_id),
                name="djust_progress",
            ),
        ]

        # Add URLs for each registered model
        for model, model_admin in self._registry.items():
            info = (model._meta.app_label, model._meta.model_name)
            base_id = f"{self.name}_{model._meta.app_label}_{model._meta.model_name}"

            list_id = f"{base_id}_list"
            add_id = f"{base_id}_add"
            change_id = f"{base_id}_change"
            delete_id = f"{base_id}_delete"

            register_admin_view(list_id, admin_site=self, model=model, model_admin=model_admin)
            register_admin_view(add_id, admin_site=self, model=model, model_admin=model_admin)
            register_admin_view(change_id, admin_site=self, model=model, model_admin=model_admin)
            register_admin_view(delete_id, admin_site=self, model=model, model_admin=model_admin)

            urlpatterns += [
                path(
                    f"{model._meta.app_label}/{model._meta.model_name}/",
                    ModelListView.as_view(_view_registry_id=list_id),
                    name="%s_%s_changelist" % info,
                ),
                path(
                    f"{model._meta.app_label}/{model._meta.model_name}/add/",
                    ModelCreateView.as_view(_view_registry_id=add_id),
                    name="%s_%s_add" % info,
                ),
                path(
                    f"{model._meta.app_label}/{model._meta.model_name}/<path:object_id>/change/",
                    ModelDetailView.as_view(_view_registry_id=change_id),
                    name="%s_%s_change" % info,
                ),
                path(
                    f"{model._meta.app_label}/{model._meta.model_name}/<path:object_id>/delete/",
                    ModelDeleteView.as_view(_view_registry_id=delete_id),
                    name="%s_%s_delete" % info,
                ),
            ]

        # Add URLs for plugin pages
        for plugin_name, plugin in self._plugins.items():
            for page in plugin.get_pages():
                page_view_id = f"{self.name}_plugin_{page.url_name}"
                register_admin_view(page_view_id, admin_site=self)

                # Wrap the plugin page view with admin chrome
                view_class = page.view_class
                urlpatterns.append(
                    path(
                        f"{page.url_path}/",
                        view_class.as_view(_view_registry_id=page_view_id),
                        name=page.url_name,
                    ),
                )

        return urlpatterns

    @property
    def urls(self):
        """Return (urlpatterns, app_name, namespace) tuple."""
        return self.get_urls(), "djust_admin", self.name

    # ---- Navigation data ----

    def get_app_list(self, request):
        """Return a sorted list of all installed apps with their models."""
        app_dict = {}

        for model, model_admin in self._registry.items():
            app_label = model._meta.app_label

            if app_label not in app_dict:
                app_config = apps.get_app_config(app_label)
                app_dict[app_label] = {
                    "name": app_config.verbose_name,
                    "app_label": app_label,
                    "models": [],
                }

            info = (app_label, model._meta.model_name)
            app_dict[app_label]["models"].append(
                {
                    "name": model._meta.verbose_name_plural,
                    "object_name": model._meta.object_name,
                    "model": model,
                    "admin_url": reverse(
                        f"{self.name}:%s_%s_changelist" % info,
                        current_app=self.name,
                    ),
                    "add_url": reverse(
                        f"{self.name}:%s_%s_add" % info,
                        current_app=self.name,
                    ),
                }
            )

        app_list = sorted(app_dict.values(), key=lambda x: x["name"])
        for app in app_list:
            app["models"].sort(key=lambda x: x["name"])

        return app_list

    def get_plugin_nav(self, request):
        """
        Return plugin nav items grouped by section.

        Returns a list of dicts:
        [
            {"section": "Authentication", "items": [{"label": ..., "url": ...}, ...]},
            ...
        ]
        """
        sections = {}

        for plugin_name, plugin in self._plugins.items():
            for nav_item in plugin.get_nav_items():
                if not nav_item.has_permission(request):
                    continue

                section_name = str(nav_item.section or plugin.verbose_name or plugin.name)
                if section_name not in sections:
                    sections[section_name] = []

                try:
                    url = reverse(
                        f"{self.name}:{nav_item.url_name}",
                        current_app=self.name,
                    )
                except Exception:
                    logger.debug("Failed to reverse URL for %s", nav_item.url_name, exc_info=True)
                    url = "#"

                sections[section_name].append(
                    {
                        "label": str(nav_item.label),
                        "url": str(url),
                        "icon": str(nav_item.icon),
                        "order": nav_item.order,
                    }
                )

        # Sort items within each section and build result
        result = []
        for section_name in sorted(sections.keys()):
            items = sorted(sections[section_name], key=lambda x: x["order"])
            result.append({"section": section_name, "items": items})

        return result

    def get_widgets(self, request):
        """
        Collect and render all widgets from registered plugins.

        Returns a list of dicts with pre-rendered HTML:
        [{"widget_id": ..., "label": ..., "html": ..., "size": ..., "order": ...}, ...]
        """
        widgets = []

        for plugin_name, plugin in self._plugins.items():
            for widget in plugin.get_widgets():
                if not widget.has_permission(request):
                    continue

                html = widget.render(request)
                widgets.append(
                    {
                        "widget_id": widget.widget_id,
                        "label": widget.label,
                        "html": html,
                        "size": widget.size,
                        "order": widget.order,
                    }
                )

        widgets.sort(key=lambda w: w["order"])
        return widgets
