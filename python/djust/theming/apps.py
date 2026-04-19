from django.apps import AppConfig


class DjustThemingConfig(AppConfig):
    name = "djust.theming"
    label = "djust_theming"
    verbose_name = "Djust Theming"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from . import checks  # noqa: F401 -- triggers @register
        from .registry import get_registry

        get_registry().discover()
