"""Regression test for #994 — `djust.dev_server` must be importable even
when the optional `watchdog` package is not installed.

Before the fix, `DjustFileChangeHandler(FileSystemEventHandler)` on
line 25 referenced an undefined symbol whenever the `try: import
watchdog` block fell through to `except ImportError`. The class
statement crashed with `NameError: name 'FileSystemEventHandler' is
not defined`, which in turn broke `python manage.py check` in any
djust install without the `[dev]` extra (because
`djust.checks.check_hot_view_replacement` imports `WATCHDOG_AVAILABLE`
from `djust.dev_server`).

This test blocks `watchdog` at the import-hook level, reloads
`djust.dev_server`, and asserts the module loads cleanly and
`HotReloadServer.start()` short-circuits with the documented warning.
"""

from __future__ import annotations

import importlib
import importlib.abc
import logging
import sys

import pytest


class _BlockWatchdog(importlib.abc.MetaPathFinder):
    """Meta-path finder that raises ImportError for any `watchdog*` import."""

    def find_spec(self, name, path, target=None):  # noqa: ARG002
        if name == "watchdog" or name.startswith("watchdog."):
            raise ImportError(f"blocked for #994 regression test: {name}")
        return None


@pytest.fixture
def block_watchdog():
    """Force `import watchdog` (and submodules) to raise ImportError."""
    # Drop any cached watchdog modules first so the re-import sees
    # our blocker, not the previously-imported real module.
    for mod in list(sys.modules):
        if mod == "watchdog" or mod.startswith("watchdog."):
            del sys.modules[mod]

    blocker = _BlockWatchdog()
    sys.meta_path.insert(0, blocker)
    try:
        yield
    finally:
        sys.meta_path.remove(blocker)


def test_dev_server_imports_without_watchdog(block_watchdog):
    """#994: module must load — not raise NameError — when watchdog is absent.

    The fix adds stub `FileSystemEventHandler`, `FileSystemEvent`, and
    `Observer` classes in the `except ImportError` branch so the class
    statements at module scope can resolve their base / annotation
    references.
    """
    if "djust.dev_server" in sys.modules:
        del sys.modules["djust.dev_server"]
    # Importing must not raise NameError or any exception.
    module = importlib.import_module("djust.dev_server")
    assert module.WATCHDOG_AVAILABLE is False
    # Class statements must have executed — DjustFileChangeHandler
    # exists as a class, not a module-level NameError crash.
    assert isinstance(module.DjustFileChangeHandler, type)
    assert isinstance(module.HotReloadServer, type)
    assert module.hot_reload_server is not None


def test_hot_reload_server_start_no_ops_without_watchdog(block_watchdog, caplog):
    """#994: HotReloadServer.start() must short-circuit with a warning.

    Even though the module is now importable, starting the server
    without watchdog must be a clean no-op (the existing
    `if not WATCHDOG_AVAILABLE` branch at dev_server.py:136 handles
    this). Locking the behavior so a future refactor doesn't drop the
    guard.
    """
    if "djust.dev_server" in sys.modules:
        del sys.modules["djust.dev_server"]
    module = importlib.import_module("djust.dev_server")

    with caplog.at_level(logging.WARNING, logger="djust.dev_server"):
        module.hot_reload_server.start(watch_dirs=["/tmp"], on_change=lambda _p: None)

    assert "watchdog not installed" in caplog.text
    # And no observer was ever created — short-circuit occurred before
    # `self.observer = Observer()` ran.
    assert module.hot_reload_server.observer is None
    assert module.hot_reload_server._started is False


def test_check_hot_view_replacement_survives_without_watchdog(block_watchdog):
    """#994 downstream: `djust.checks.check_hot_view_replacement`
    imports `WATCHDOG_AVAILABLE` from `dev_server`; before the fix the
    whole module import would crash, breaking `manage.py check`. After
    the fix, the import succeeds and the check runs normally."""
    # Evict both modules so checks.py re-imports dev_server through
    # our blocker.
    for mod in ("djust.dev_server", "djust.checks"):
        if mod in sys.modules:
            del sys.modules[mod]
    # Must not raise.
    checks_mod = importlib.import_module("djust.checks")
    # Function must still be callable — we're not asserting its
    # output here (that would require full Django setup). The claim
    # under test is "import succeeds", which is the bug.
    assert callable(checks_mod.check_hot_view_replacement)
