from django.apps import AppConfig


class DjustComponentsConfig(AppConfig):
    name = "djust.components"
    label = "djust_components"
    verbose_name = "djust Components"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from .rust_handlers import register_with_rust_engine

        register_with_rust_engine()
