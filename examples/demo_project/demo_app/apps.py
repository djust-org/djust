from django.apps import AppConfig


class DemoAppConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'demo_app'

    def ready(self):
        """Initialize app - enable hot reload in development."""
        from djust import enable_hot_reload
        enable_hot_reload()
