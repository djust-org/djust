from django.apps import AppConfig


class DjustThemingConfig(AppConfig):
    name = "djust.theming"
    label = "djust_theming"
    verbose_name = "Djust Theming"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        from . import checks  # noqa: F401 -- triggers @register

        # Import ``registry`` (not the leaf) so its discovery hook is installed
        # via ``set_discovery_hook`` before ``discover()`` runs (#1662).
        from .registry import get_registry

        get_registry().discover()

        # Register the documented {% theme_X %} tags as Rust template-engine
        # tag handlers so they work in LiveView templates (rendered by the
        # Rust engine), matching the docs and the {{ theme_X }} context-string
        # form (#1721). Degrades gracefully when the Rust extension is absent.
        from .rust_handlers import register_with_rust_engine

        register_with_rust_engine()
