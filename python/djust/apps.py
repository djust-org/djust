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

        # Install the observability log-tail handler. Always safe to
        # install (the buffer is inert until the MCP tool fetches it);
        # DEBUG gating happens at the endpoint level.
        try:
            from djust.observability.log_handler import install_handler

            install_handler()
        except Exception as e:  # noqa: BLE001
            # Observability must never break AppConfig startup.
            logging.getLogger("djust").warning("Observability log handler install failed: %s", e)
