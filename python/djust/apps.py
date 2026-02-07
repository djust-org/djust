from django.apps import AppConfig


class DjustConfig(AppConfig):
    name = "djust"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        # Import checks module so @register() decorators are executed
        import djust.checks  # noqa: F401
