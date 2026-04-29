from django.apps import AppConfig


class DemoAppConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "demo_app"

    # v0.9.0+ — djust auto-enables hot reload via its own DjustConfig.ready()
    # whenever DEBUG=True and watchdog is installed. No explicit
    # enable_hot_reload() call needed here.
