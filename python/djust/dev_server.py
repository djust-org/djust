"""
Development server utilities for hot reload functionality.

This module provides file watching and hot reload capabilities for djust development.
"""

import logging
import threading
import time
from pathlib import Path
from typing import Set, Callable, Optional

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler, FileSystemEvent

    WATCHDOG_AVAILABLE = True
except ImportError:
    WATCHDOG_AVAILABLE = False


logger = logging.getLogger(__name__)


class DjustFileChangeHandler(FileSystemEventHandler):
    """
    Handles file system events and triggers reload callbacks.

    Watches for changes to .py, .html, .css, and .js files and debounces
    rapid changes to avoid excessive reloads.
    """

    WATCHED_EXTENSIONS = {".py", ".html", ".css", ".js"}
    DEBOUNCE_SECONDS = 0.5  # Wait 500ms after last change before reloading

    def __init__(self, on_change_callback: Callable[[str], None]):
        """
        Initialize the file change handler.

        Args:
            on_change_callback: Function to call when files change (receives file path)
        """
        super().__init__()
        self.on_change_callback = on_change_callback
        self._last_change_time = 0
        self._pending_reload = False
        self._reload_lock = threading.Lock()
        self._debounce_thread: Optional[threading.Thread] = None

    def should_reload_for_path(self, path: str) -> bool:
        """Check if file should trigger a reload."""
        # Ignore hidden files, __pycache__, .pyc files
        if "/__pycache__/" in path or path.endswith(".pyc") or "/.git/" in path:
            return False

        # Check if file has a watched extension
        return Path(path).suffix in self.WATCHED_EXTENSIONS

    def schedule_reload(self, path: str):
        """Schedule a reload after debounce period."""
        with self._reload_lock:
            self._last_change_time = time.time()
            self._pending_reload = True

            # Cancel existing debounce thread if any
            if self._debounce_thread and self._debounce_thread.is_alive():
                return  # Already waiting

            # Start new debounce thread
            self._debounce_thread = threading.Thread(
                target=self._debounced_reload, args=(path,), daemon=True
            )
            self._debounce_thread.start()

    def _debounced_reload(self, path: str):
        """Wait for debounce period then trigger reload."""
        while True:
            time.sleep(self.DEBOUNCE_SECONDS)

            with self._reload_lock:
                # Check if enough time has passed since last change
                if time.time() - self._last_change_time >= self.DEBOUNCE_SECONDS:
                    if self._pending_reload:
                        self._pending_reload = False
                        logger.info(f"[HotReload] File changed: {path}")
                        self.on_change_callback(path)
                    break

    def on_modified(self, event: FileSystemEvent):
        """Handle file modification events."""
        if not event.is_directory and self.should_reload_for_path(event.src_path):
            self.schedule_reload(event.src_path)

    def on_created(self, event: FileSystemEvent):
        """Handle file creation events."""
        if not event.is_directory and self.should_reload_for_path(event.src_path):
            self.schedule_reload(event.src_path)


class HotReloadServer:
    """
    Hot reload server that watches for file changes and broadcasts reload messages.

    Usage:
        # In your Django app startup (apps.py or settings.py):
        from djust.dev_server import hot_reload_server

        if settings.DEBUG and settings.LIVEVIEW_CONFIG.get('hot_reload', True):
            hot_reload_server.start(
                watch_dirs=[settings.BASE_DIR],
                on_change=broadcast_reload_to_clients
            )
    """

    def __init__(self):
        """Initialize the hot reload server."""
        self.observer: Optional[Observer] = None
        self.watch_dirs: Set[Path] = set()
        self.on_change_callback: Optional[Callable[[str], None]] = None
        self._started = False

    def start(
        self,
        watch_dirs: list,
        on_change: Callable[[str], None],
        exclude_dirs: Optional[Set[str]] = None,
    ):
        """
        Start watching for file changes.

        Args:
            watch_dirs: List of directory paths to watch
            on_change: Callback function to call when files change
            exclude_dirs: Set of directory names to exclude (e.g., {'node_modules', '.git'})
        """
        if not WATCHDOG_AVAILABLE:
            logger.warning(
                "[HotReload] watchdog not installed. " "Install with: pip install watchdog"
            )
            return

        if self._started:
            logger.warning("[HotReload] Server already started")
            return

        self.on_change_callback = on_change
        self.observer = Observer()

        # Store exclude dirs for potential future handler extension
        # Currently the handler has built-in exclusions
        if exclude_dirs is None:
            exclude_dirs = {
                "node_modules",
                ".git",
                "__pycache__",
                ".venv",
                "venv",
                "staticfiles",
                "media",
                ".pytest_cache",
                ".mypy_cache",
            }
        self.exclude_dirs = exclude_dirs

        # Set up file change handler
        event_handler = DjustFileChangeHandler(on_change_callback=on_change)

        # Schedule observers for each watch directory
        for watch_dir in watch_dirs:
            watch_path = Path(watch_dir).resolve()

            if not watch_path.exists():
                logger.warning(f"[HotReload] Watch directory does not exist: {watch_path}")
                continue

            self.watch_dirs.add(watch_path)
            self.observer.schedule(event_handler, str(watch_path), recursive=True)
            logger.info(f"[HotReload] Watching: {watch_path}")

        # Start the observer
        self.observer.start()
        self._started = True
        logger.info("[HotReload] Hot reload server started")

    def stop(self):
        """Stop watching for file changes."""
        if self.observer and self._started:
            self.observer.stop()
            self.observer.join(timeout=2)
            self._started = False
            logger.info("[HotReload] Hot reload server stopped")

    def is_running(self) -> bool:
        """Check if the hot reload server is running."""
        return self._started and self.observer is not None


# Global singleton instance
hot_reload_server = HotReloadServer()
