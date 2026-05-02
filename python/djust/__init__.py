"""
djust - Blazing fast reactive server-side rendering for Django

This package provides a Phoenix LiveView-style reactive framework for Django,
powered by Rust for maximum performance.
"""

from .utils import get_template_dirs, clear_template_dirs_cache
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

# Import Rust functions
try:
    from ._rust import render_template, diff_html, RustLiveView
except ImportError as e:
    # Fallback for when Rust extension isn't built
    import warnings

    warnings.warn(f"Could not import Rust extension: {e}. Performance will be degraded.")
    render_template = None
    diff_html = None
    RustLiveView = None

# Register template tag handlers (url, static, etc.)
# This imports the template_tags module which auto-registers handlers
try:
    from . import template_tags  # noqa: F401
except ImportError:
    # Template tags module not available (e.g., during initial install)
    pass

# Import Rust components (optional, requires separate build)
try:
    from . import rust_components  # noqa: F401 — re-exported as djust.rust_components
except ImportError:
    # Rust components not yet built - this is optional
    rust_components = None  # noqa: F841 — accessed as djust.rust_components by user code

__version__ = "0.9.2"


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
    # Rust components (optional)
    "rust_components",
    # Database change notifications (pg_notify bridge)
    "NotificationMixin",
    "notify_on_save",
    "send_pg_notify",
    # Safe server-side Markdown rendering
    "render_markdown",
]
