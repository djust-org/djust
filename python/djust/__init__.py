"""
djust - Blazing fast reactive server-side rendering for Django

This package provides a Phoenix LiveView-style reactive framework for Django,
powered by Rust for maximum performance.

Lazy public names (#2559)
-------------------------
Only the templates-only surface is imported eagerly (``get_template_dirs``,
the Rust extension, ``template_tags`` registration, ``enable_hot_reload``).
Every other name in ``__all__`` -- ``LiveView``, the decorators, the mixins,
``push_to_view`` and friends -- is resolved on first attribute access via
PEP 562 ``__getattr__`` so that ``import djust`` or
``import djust.template.backend`` no longer drags in ``channels`` and the
whole LiveView stack. ``from djust import LiveView`` is unchanged in
behaviour; the object returned is the same one ``djust.live_view.LiveView``
is. Note that ``hasattr(djust, "PresenceMixin")`` therefore IMPORTS
``djust.presence`` (and ``channels``) as a side effect.

Two exported names collide with submodule names: ``live_view`` and
``rate_limit``. ``from djust import live_view`` resolves to the decorator
unless ``djust.live_view`` was imported as a module first, in which case it
is the module -- exactly what ``djust.rate_limit`` has always done. Import
the decorators from ``djust.live_view`` / ``djust.decorators`` directly.
"""

from __future__ import annotations

import importlib
import sys
import types
from typing import TYPE_CHECKING

from .utils import get_template_dirs, clear_template_dirs_cache

if TYPE_CHECKING:
    # Static-analysis view of the lazy names: mypy/pyright/IDEs see the real
    # types (PEP 562 alone types every lazy name as ``Any`` and reds the
    # ADR-023 strict-island gate, #1960). Never executed at runtime.
    from .async_result import AsyncResult
    from .live_view import LiveView, live_view
    from .components.base import Component, LiveComponent
    from .components.assigns import Assign, AssignValidationError, Slot
    from .components.function_component import component, clear_components
    from .decorators import (
        reactive,
        event_handler,
        event,
        is_event_handler,
        action,
        is_action,
        server_function,
        is_server_function,
        permission_required,
        rate_limit,
        state,
        computed,
        debounce,
        throttle,
        on_mount,
        optimistic,
        cache,
        client_state,
        background,
    )
    from .auth import LoginRequiredMixin, PermissionRequiredMixin
    from .react import react_components, register_react_component, ReactMixin
    from .forms import FormMixin, LiveViewForm
    from .wizard import WizardMixin
    from .drafts import DraftModeMixin
    from .push import push_to_view, apush_to_view
    from .presence import PresenceMixin, LiveCursorMixin, PresenceManager, CursorTracker
    from .routing import live_session, get_route_map_script, DjustMiddlewareStack
    from .streaming import StreamingMixin
    from .uploads import UploadMixin
    from .mixins.flash import FlashMixin
    from .mixins.page_metadata import PageMetadataMixin
    from .mixins.notifications import NotificationMixin
    from .db import notify_on_save, send_pg_notify
    from .markdown import render_markdown as render_markdown
    from . import rust_components  # noqa: F401

__version__ = "1.2.0rc1"

# Import Rust functions
try:
    from ._rust import render_template, diff_html, RustLiveView
except ImportError as e:
    # Fallback for when Rust extension isn't built
    import warnings

    warnings.warn(f"Could not import Rust extension: {e}. Performance will be degraded.")
    render_template = None  # type: ignore[assignment]
    diff_html = None  # type: ignore[assignment]
    RustLiveView = None  # type: ignore[assignment,misc]

# Register template tag handlers (url, static, etc.)
# This imports the template_tags module which auto-registers handlers with
# the Rust engine -- a templates-only project needs {% url %} / {% static %}.
try:
    from . import template_tags  # noqa: F401
except ImportError:
    # Template tags module not available (e.g., during initial install)
    pass

# Public name -> (module, attribute). Resolved on first access by ``__getattr__``.
# Every name here MUST also be in ``__all__`` and vice versa (minus the eager
# names); ``tests/test_lazy_package_init_2559.py`` pins both directions and
# that every entry resolves to the source-module object.
_LAZY: dict[str, tuple[str, str]] = {
    "AsyncResult": ("djust.async_result", "AsyncResult"),
    "LiveView": ("djust.live_view", "LiveView"),
    "live_view": ("djust.live_view", "live_view"),
    "Component": ("djust.components.base", "Component"),
    "LiveComponent": ("djust.components.base", "LiveComponent"),
    "Assign": ("djust.components.assigns", "Assign"),
    "AssignValidationError": ("djust.components.assigns", "AssignValidationError"),
    "Slot": ("djust.components.assigns", "Slot"),
    "component": ("djust.components.function_component", "component"),
    "clear_components": ("djust.components.function_component", "clear_components"),
    "reactive": ("djust.decorators", "reactive"),
    "event_handler": ("djust.decorators", "event_handler"),
    "event": ("djust.decorators", "event"),
    "is_event_handler": ("djust.decorators", "is_event_handler"),
    "action": ("djust.decorators", "action"),
    "is_action": ("djust.decorators", "is_action"),
    "server_function": ("djust.decorators", "server_function"),
    "is_server_function": ("djust.decorators", "is_server_function"),
    "permission_required": ("djust.decorators", "permission_required"),
    "rate_limit": ("djust.decorators", "rate_limit"),
    "state": ("djust.decorators", "state"),
    "computed": ("djust.decorators", "computed"),
    "debounce": ("djust.decorators", "debounce"),
    "throttle": ("djust.decorators", "throttle"),
    "on_mount": ("djust.decorators", "on_mount"),
    "optimistic": ("djust.decorators", "optimistic"),
    "cache": ("djust.decorators", "cache"),
    "client_state": ("djust.decorators", "client_state"),
    "background": ("djust.decorators", "background"),
    "LoginRequiredMixin": ("djust.auth", "LoginRequiredMixin"),
    "PermissionRequiredMixin": ("djust.auth", "PermissionRequiredMixin"),
    "react_components": ("djust.react", "react_components"),
    "register_react_component": ("djust.react", "register_react_component"),
    "ReactMixin": ("djust.react", "ReactMixin"),
    "FormMixin": ("djust.forms", "FormMixin"),
    "LiveViewForm": ("djust.forms", "LiveViewForm"),
    "WizardMixin": ("djust.wizard", "WizardMixin"),
    "DraftModeMixin": ("djust.drafts", "DraftModeMixin"),
    "push_to_view": ("djust.push", "push_to_view"),
    "apush_to_view": ("djust.push", "apush_to_view"),
    "PresenceMixin": ("djust.presence", "PresenceMixin"),
    "LiveCursorMixin": ("djust.presence", "LiveCursorMixin"),
    "PresenceManager": ("djust.presence", "PresenceManager"),
    "CursorTracker": ("djust.presence", "CursorTracker"),
    "live_session": ("djust.routing", "live_session"),
    "get_route_map_script": ("djust.routing", "get_route_map_script"),
    "DjustMiddlewareStack": ("djust.routing", "DjustMiddlewareStack"),
    "StreamingMixin": ("djust.streaming", "StreamingMixin"),
    "UploadMixin": ("djust.uploads", "UploadMixin"),
    "FlashMixin": ("djust.mixins.flash", "FlashMixin"),
    "PageMetadataMixin": ("djust.mixins.page_metadata", "PageMetadataMixin"),
    "NotificationMixin": ("djust.mixins.notifications", "NotificationMixin"),
    "notify_on_save": ("djust.db", "notify_on_save"),
    "send_pg_notify": ("djust.db", "send_pg_notify"),
    "render_markdown": ("djust.markdown", "render_markdown"),
    # The optional Rust-components submodule itself; ``None`` when not built.
    "rust_components": ("djust.rust_components", ""),
}


def __getattr__(name: str):  # PEP 562
    try:
        module_name, attr = _LAZY[name]
    except KeyError:
        raise AttributeError(f"module 'djust' has no attribute {name!r}") from None
    if not attr:
        # ``rust_components``: the submodule is the export; optional build.
        try:
            value = importlib.import_module(module_name)
        except ImportError:
            value = None
        globals()[name] = value
        return value
    module = importlib.import_module(module_name)
    value = getattr(module, attr)
    globals()[name] = value
    _rebind_live_view_decorator()
    return value


def _rebind_live_view_decorator() -> None:
    """Pin ``djust.live_view`` to the DECORATOR once the package is in use.

    Importing the ``djust.live_view`` submodule makes the import system bind
    ``djust.live_view`` to the MODULE (that ``setattr`` happens after the
    submodule body runs, so it cannot be intercepted). The eager pre-#2559
    init always re-bound the decorator afterwards; with the lazy init the
    re-bind happens here, on EVERY lazy resolution -- not only when a name
    from ``djust.live_view`` is resolved -- so the binding does not depend on
    WHICH public name was touched first. Contract: after any lazy name has
    resolved, ``djust.live_view`` is the decorator regardless of import
    order; only a submodule-first import with NO lazy resolution yet sees
    the module (the same behaviour ``djust.rate_limit`` has always had).
    """
    submodule = sys.modules.get("djust.live_view")
    if submodule is not None and isinstance(globals().get("live_view"), types.ModuleType):
        globals()["live_view"] = submodule.live_view


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_LAZY))


def enable_hot_reload():
    """
    Enable hot reload in development.

    This function starts a file watcher that monitors .py, .html, .css, and .js files
    for changes. When a change is detected, all connected WebSocket clients are sent
    a reload message, triggering an automatic page refresh.

    Auto-enabled by default (since v0.9.0):
        djust's own ``DjustConfig.ready()`` auto-calls this whenever
        ``DEBUG=True`` and the ``watchdog`` package is installed. You no
        longer need to call it explicitly from your own ``AppConfig.ready()``.
        The function is idempotent — calling it manually is a safe no-op
        when the server is already running, so existing per-consumer calls
        keep working unchanged.

        To opt out (e.g. you orchestrate the file watcher externally), set::

            LIVEVIEW_CONFIG = {"hot_reload_auto_enable": False}

    Manual usage (advanced — only needed if auto-enable is disabled):
        # In your Django app's AppConfig.ready() method:
        from djust import enable_hot_reload

        class MyAppConfig(AppConfig):
            def ready(self):
                enable_hot_reload()

        # Or in settings.py (after DJANGO_SETTINGS_MODULE is configured):
        if DEBUG:
            from djust import enable_hot_reload
            enable_hot_reload()

    Configuration (in settings.py):
        LIVEVIEW_CONFIG = {
            'hot_reload': True,  # Enable/disable hot reload
            'hot_reload_watch_dirs': None,  # Directories to watch (None = auto-detect BASE_DIR)
            'hot_reload_exclude_dirs': None,  # Additional directories to exclude
        }

    Requirements:
        - DEBUG = True (automatically disabled in production)
        - watchdog package installed (pip install watchdog)
        - Django Channels configured for WebSocket support

    Notes:
        - Only activates when DEBUG=True
        - Changes are debounced (500ms) to avoid excessive reloads
        - Excludes common directories: node_modules, .git, __pycache__, .venv, etc.
        - Hot reload messages are broadcast to all connected LiveView clients
    """
    import logging

    logger = logging.getLogger(__name__)

    try:
        from django.conf import settings
    except ImportError:
        logger.warning("[HotReload] Django not configured, hot reload disabled")
        return

    # Only enable in DEBUG mode
    if not getattr(settings, "DEBUG", False):
        return

    # Check config
    from djust.config import config

    if not config.get("hot_reload", True):
        logger.info("[HotReload] Hot reload disabled in config")
        return

    # Check if watchdog is available
    try:
        from djust.dev_server import hot_reload_server, WATCHDOG_AVAILABLE
    except ImportError:
        logger.warning("[HotReload] dev_server module not available, hot reload disabled")
        return

    if not WATCHDOG_AVAILABLE:
        logger.warning("[HotReload] watchdog not installed. Install with: pip install watchdog")
        return

    # Check if already started
    if hot_reload_server.is_running():
        logger.debug("[HotReload] Hot reload already running")
        return

    # Auto-detect watch directories
    watch_dirs = config.get("hot_reload_watch_dirs")
    if watch_dirs is None:
        watch_dirs = [settings.BASE_DIR]

    exclude_dirs = config.get("hot_reload_exclude_dirs")

    # Import WebSocket consumer for broadcasting
    from djust.websocket import LiveViewConsumer
    import asyncio

    # HVR is opt-out via LIVEVIEW_CONFIG["hvr_enabled"] (default True).
    # When disabled we fall back to the pre-v0.6.1 behavior (template +
    # full page reload for every file change).
    hvr_enabled = bool(config.get("hvr_enabled", True))

    # Callback to broadcast reload via WebSocket.
    #
    # v0.6.1: .py changes go through the HVR path — reload the module in
    # this process, then broadcast the resulting class-swap metadata so
    # every connected consumer can apply the swap in-place. Non-.py
    # changes (templates, CSS, JS, etc.) take the legacy template-refresh
    # path unchanged.
    def on_file_change(file_path: str):
        """Called when a file changes - broadcasts reload to all clients."""

        async def _dispatch():
            is_py = hvr_enabled and file_path.lower().endswith(".py")
            if is_py:
                try:
                    from djust.hot_view_replacement import (
                        broadcast_hvr_event,
                        reload_module_if_liveview,
                    )

                    result = reload_module_if_liveview(file_path)
                except Exception:  # noqa: BLE001 — dev-only safety net
                    logger.exception("[HotReload] HVR module reload failed")
                    result = None
                if result is not None:
                    await broadcast_hvr_event(result, file_path)
                    return
            await LiveViewConsumer.broadcast_reload(file_path)

        try:
            # Get or create event loop
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

            # Schedule the broadcast
            if loop.is_running():
                asyncio.create_task(_dispatch())
            else:
                loop.run_until_complete(_dispatch())
        except Exception as e:
            logger.error("[HotReload] Error broadcasting reload: %s", e)

    # Start the hot reload server
    try:
        hot_reload_server.start(
            watch_dirs=watch_dirs, on_change=on_file_change, exclude_dirs=exclude_dirs
        )
        print(
            f"[HotReload] Hot reload enabled for directories: {', '.join(str(d) for d in watch_dirs)}"
        )
        logger.info(
            f"[HotReload] Hot reload enabled for directories: {', '.join(str(d) for d in watch_dirs)}"
        )
    except Exception as e:
        print(f"[HotReload] Failed to start hot reload server: {e}")
        logger.error("[HotReload] Failed to start hot reload server: %s", e)


__all__ = [
    "LiveView",
    "live_view",
    "AsyncResult",
    "Component",
    "LiveComponent",
    # Declarative assigns & slots
    "Assign",
    "AssignValidationError",
    "Slot",
    # Function components
    "component",
    "clear_components",
    "reactive",
    "event_handler",
    "event",
    "is_event_handler",
    "action",
    "is_action",
    "server_function",
    "is_server_function",
    "permission_required",
    "rate_limit",
    "state",
    "computed",
    "debounce",
    "throttle",
    "render_template",
    "diff_html",
    "RustLiveView",
    "react_components",
    "register_react_component",
    "ReactMixin",
    "FormMixin",
    "WizardMixin",
    "LiveViewForm",
    "DraftModeMixin",
    "push_to_view",
    "apush_to_view",
    "enable_hot_reload",
    "get_template_dirs",
    "clear_template_dirs_cache",
    # Presence tracking
    "PresenceMixin",
    "LiveCursorMixin",
    "PresenceManager",
    "CursorTracker",
    # Navigation & URL state
    "live_session",
    "get_route_map_script",
    # Middleware
    "DjustMiddlewareStack",
    # Streaming
    "StreamingMixin",
    # File uploads
    "UploadMixin",
    # Flash messages
    "FlashMixin",
    # Page metadata
    "PageMetadataMixin",
    # Authentication & authorization
    "LoginRequiredMixin",
    "PermissionRequiredMixin",
    # on_mount hooks
    "on_mount",
    # Optimistic UI / caching / client-state / background-work decorators
    "optimistic",
    "cache",
    "client_state",
    "background",
    # Rust components (optional)
    "rust_components",
    # Database change notifications (pg_notify bridge)
    "NotificationMixin",
    "notify_on_save",
    "send_pg_notify",
    # Safe server-side Markdown rendering
    "render_markdown",
]
