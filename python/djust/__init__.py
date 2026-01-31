"""
djust - Blazing fast reactive server-side rendering for Django

This package provides a Phoenix LiveView-style reactive framework for Django,
powered by Rust for maximum performance.
"""

from .utils import get_template_dirs, clear_template_dirs_cache
from .live_view import LiveView, live_view
from .components.base import Component, LiveComponent
from .decorators import (
    reactive,
    event_handler,
    event,
    is_event_handler,
    rate_limit,
    state,
    computed,
    debounce,
    throttle,
)
from .react import react_components, register_react_component, ReactMixin
from .forms import FormMixin, LiveViewForm, form_field
from .drafts import DraftModeMixin

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
    from . import rust_components
except ImportError:
    # Rust components not yet built - this is optional
    rust_components = None

__version__ = "0.2.2rc3"


def enable_hot_reload():
    """
    Enable hot reload in development.

    This function starts a file watcher that monitors .py, .html, .css, and .js files
    for changes. When a change is detected, all connected WebSocket clients are sent
    a reload message, triggering an automatic page refresh.

    Usage:
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

    # Callback to broadcast reload via WebSocket
    def on_file_change(file_path: str):
        """Called when a file changes - broadcasts reload to all clients."""
        try:
            # Get or create event loop
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

            # Schedule the broadcast
            if loop.is_running():
                asyncio.create_task(LiveViewConsumer.broadcast_reload(file_path))
            else:
                loop.run_until_complete(LiveViewConsumer.broadcast_reload(file_path))
        except Exception as e:
            logger.error(f"[HotReload] Error broadcasting reload: {e}")

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
        logger.error(f"[HotReload] Failed to start hot reload server: {e}")


__all__ = [
    "LiveView",
    "live_view",
    "Component",
    "LiveComponent",
    "reactive",
    "event_handler",
    "event",
    "is_event_handler",
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
    "LiveViewForm",
    "form_field",
    "DraftModeMixin",
    "enable_hot_reload",
    "get_template_dirs",
    "clear_template_dirs_cache",
]
