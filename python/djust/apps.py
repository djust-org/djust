import logging

from django.apps import AppConfig


class DjustConfig(AppConfig):
    name = "djust"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        # FIRST, before anything reads config: recover the settings load if it
        # could not happen at import time (#2164/#2166).
        #
        # ``djust.config.config`` is built at MODULE IMPORT and reads Django
        # settings exactly once. When a project's asgi.py imports djust BEFORE
        # setting ``DJANGO_SETTINGS_MODULE``, that read raises
        # ``ImproperlyConfigured``, the broad except swallows it, and the
        # singleton serves pure defaults for the life of the process — so every
        # ``LIVEVIEW_CONFIG`` key is silently ignored. It fails asymmetrically:
        # correct in tests (which import late, after django.setup()), wrong in
        # that server. Django runs ready() strictly after settings resolve, so
        # this is the one place a recovery read is guaranteed safe.
        #
        # Doing it here rather than at each call site is deliberate: the readers
        # are spread across ~40 sites, and fixing them one at a time is the
        # 2-of-N shape #1646 exists to reject. One recovery fixes them all.
        try:
            from djust.config import config as _djust_config

            _djust_config.ensure_settings_loaded()
        except Exception:  # noqa: BLE001 - config recovery must never break startup
            logging.getLogger("djust").exception(
                "[djust] recovering the config settings load in ready() failed"
            )

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

        # Wire LIVEVIEW_CONFIG['virtual_keyed_ops'] -> the process-global Rust
        # switch (ADR-026 iteration 3, #2017). Done HERE rather than in
        # rust_bridge's per-view `_apply_*_flag` hooks because the Rust side is
        # a process-global AtomicBool, and applying a global from a per-view
        # hook is last-view-wins.
        #
        # Unconditional (not gated on PYTEST_CURRENT_TEST): the flag must hold
        # the same value in tests as in production, or the suite verifies a
        # configuration nobody runs.
        try:
            from djust import _rust
            from djust.config import config as _cfg

            # Reads the singleton, which the ensure_settings_loaded() at the top
            # of ready() has already made trustworthy. Deliberately NOT a direct
            # `settings.LIVEVIEW_CONFIG` read: that would skip the documented
            # `DJUST_CONFIG` fallback (#1993) and the flat `DJUST_*` aliases, so
            # a value set there would read ON through `config.get()` while the
            # differ ran OFF — a NEW silent asymmetry, the exact class this is
            # fixing. It also keeps `_defaults` the single source of the default
            # (ADR-026 iteration 3 flips it there, and this must follow).
            if hasattr(_rust, "set_virtual_keyed_ops"):
                _rust.set_virtual_keyed_ops(bool(_cfg.get("virtual_keyed_ops", False)))
        except Exception:  # noqa: BLE001 - never let a flag break startup
            logging.getLogger("djust").exception(
                "[djust] applying virtual_keyed_ops to the Rust differ failed"
            )

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

            # Warm the Django→Rust custom-filter bridge at startup so the FIRST
            # mount/render doesn't pay the one-time ~20ms cost of lazily importing
            # every Django templatetag library on the request path. The bridge is
            # memoized after the first call (``_CUSTOM_FILTERS_BRIDGED``), so this
            # only SHIFTS that cost from first-request latency to startup. Skipped
            # during pytest (above guard) so the suite isn't slowed and so test
            # bootstrap orderings without a configured template engine are
            # unaffected. Opt out via ``LIVEVIEW_CONFIG['filter_bridge_warm'] = False``.
            self._warm_filter_bridge()

    def _warm_filter_bridge(self) -> bool:
        """Eagerly run the Django→Rust filter bridge (off the request path).

        Returns ``True`` if the warm ran, ``False`` if opted out or it failed.
        Idempotent (the underlying bootstrap guards itself) and non-fatal —
        startup must never break if the template engine isn't bridgeable.
        """
        try:
            from djust.config import config

            if not config.get("filter_bridge_warm", True):
                return False
            from djust.mixins.rust_bridge import _ensure_custom_filters_bridged

            _ensure_custom_filters_bridged()
            return True
        except Exception:  # noqa: BLE001 — startup warm must never break ready()
            logging.getLogger("djust").exception(
                "[FilterBridge] startup warm in DjustConfig.ready() failed"
            )
            return False
