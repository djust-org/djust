import logging

from django.apps import AppConfig


class DjustConfig(AppConfig):
    name = "djust"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
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
