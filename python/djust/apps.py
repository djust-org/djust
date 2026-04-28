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

        # Auto-enable hot reload in DEBUG. ``enable_hot_reload()`` has its
        # own DEBUG / watchdog / config gates and is idempotent via
        # ``hot_reload_server.is_running()``, so this is safe in production
        # (early-return) and safe alongside an explicit consumer call.
        # Skip during pytest runs to avoid spawning a watchdog thread for
        # every test session — pytest sets ``PYTEST_CURRENT_TEST`` for the
        # duration of every test invocation. (Tests that need to exercise
        # the auto-enable path itself temporarily clear this env var; see
        # ``_no_pytest_env()`` in
        # ``python/djust/tests/test_auto_hot_reload.py``.)
        import os

        if not os.environ.get("PYTEST_CURRENT_TEST"):
            try:
                from djust.config import config

                if config.get("hot_reload_auto_enable", True):
                    from djust import enable_hot_reload

                    enable_hot_reload()
            except Exception:  # noqa: BLE001
                logging.getLogger("djust").exception(
                    "[HotReload] auto-enable in DjustConfig.ready() failed"
                )
