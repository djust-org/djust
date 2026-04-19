from django.apps import AppConfig


class DjustAdminConfig(AppConfig):
    name = "djust.admin_ext"
    label = "djust_admin"
    verbose_name = "Djust Admin"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from . import autodiscover

        autodiscover()
