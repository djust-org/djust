import logging

from django.apps import AppConfig


class DjustConfig(AppConfig):
    name = "djust"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        # Import checks module so @register() decorators are executed
        import djust.checks  # noqa: F401

        # Install log sanitizer filter on all djust.* loggers so every log
        # record emitted by the framework has user-controlled string args
        # sanitized before they reach any handler — preventing log injection
        # without per-callsite sanitization.
        from djust.security import DjustLogSanitizerFilter

        logging.getLogger("djust").addFilter(DjustLogSanitizerFilter())
