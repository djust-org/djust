from django.apps import AppConfig


class DjustDemosConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'djust_demos'

    def ready(self):
        """Import react components to register them"""
        try:
            from . import react_components
        except ImportError:
            pass
