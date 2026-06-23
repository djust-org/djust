"""Security regression: client-controlled `view` path must NOT trigger a fresh
__import__ of a not-yet-loaded module (unauthenticated arbitrary module import).

GHSA — unauthenticated arbitrary module import via the live WebSocket/SSE mount
path. Before the fix, `handle_mount` (and the SSE/url_change `dispatch_mount`
path) called `__import__(client_module_path)` — executing the module's top-level
code — before the LiveView-subclass check / auth, and the allowlist gate was
fail-open (`if allowed_modules:`, skipped when LIVEVIEW_ALLOWED_MODULES unset).

The fix (`djust._view_resolution.is_view_import_allowed`) is fail-CLOSED: a
client view path resolves only if its module is already loaded (no new code
runs) or it is explicitly allowlisted on a module-segment boundary.
"""

from __future__ import annotations

import asyncio
import os
import sys

import pytest
from django.test import override_settings

from djust._view_resolution import is_view_import_allowed


# --------------------------------------------------------------------------
# Unit tests of the fail-closed gate (gate-off-sensitive by construction).
# --------------------------------------------------------------------------
class TestIsViewImportAllowed:
    @override_settings(LIVEVIEW_ALLOWED_MODULES=[])
    def test_stdlib_module_is_rejected_when_allowlist_unset(self):
        # BEHAVIOR CHANGE (F22 consolidation): the gate no longer allows a path
        # merely because the module is already imported (the old sys.modules
        # rule, which `os` — always loaded — defeated). With the allowlist unset
        # it falls back to INSTALLED_APPS roots + "djust", so a stdlib module
        # like `os` is REJECTED even though it is loaded. This is the F22 fix.
        assert "os" in sys.modules
        assert is_view_import_allowed("os.SomethingNotAView") is False

    @override_settings(LIVEVIEW_ALLOWED_MODULES=[])
    def test_djust_and_installed_apps_allowed_when_allowlist_unset(self):
        # The non-breaking fallback admits the framework package + installed
        # apps (demo apps are in the test settings' INSTALLED_APPS).
        assert is_view_import_allowed("djust.tests.test_runtime._FakeView") is True

    @override_settings(LIVEVIEW_ALLOWED_MODULES=[])
    def test_not_yet_imported_module_is_rejected_when_allowlist_unset(self):
        # A module name guaranteed not to be loaded, allowlist unset (default).
        name = "djust_nonexistent_sentinel_mod_xyz"
        sys.modules.pop(name, None)
        assert is_view_import_allowed(f"{name}.NotAView") is False

    @override_settings(LIVEVIEW_ALLOWED_MODULES=["myapp.views"])
    def test_allowlisted_path_is_allowed_even_if_not_loaded(self):
        sys.modules.pop("myapp.views.lazy", None)
        assert is_view_import_allowed("myapp.views.lazy.SomeView") is True
        assert is_view_import_allowed("myapp.views.SomeView") is True

    @override_settings(LIVEVIEW_ALLOWED_MODULES=["app"])
    def test_segment_boundary_not_startswith(self):
        # "app" must NOT admit "app_evil" / "apphacks" (the startswith weakness).
        sys.modules.pop("app_evil", None)
        sys.modules.pop("apphacks", None)
        assert is_view_import_allowed("app_evil.X") is False
        assert is_view_import_allowed("apphacks.X") is False
        # but the real "app" package + its children are allowed
        assert is_view_import_allowed("app.X") is True
        assert is_view_import_allowed("app.sub.X") is True

    def test_malformed_paths_rejected(self):
        assert is_view_import_allowed("noseparator") is False
        assert is_view_import_allowed("") is False
        assert is_view_import_allowed(None) is False  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# End-to-end: an unauthenticated WS `mount` frame must NOT import a sentinel
# module (the exploit), even with the allowlist unset.
# --------------------------------------------------------------------------
@pytest.fixture
def sentinel_module(tmp_path):
    """Plant a module on sys.path whose import writes a marker file. NOT a
    LiveView. NOT pre-imported. Cleaned up after."""
    name = "djust_sec_repro_sentinel"
    marker = tmp_path / "imported.marker"
    (tmp_path / f"{name}.py").write_text(
        f"import os\nopen({str(marker)!r}, 'w').write('IMPORTED')\nclass NotAView:\n    pass\n"
    )
    sys.path.insert(0, str(tmp_path))
    sys.modules.pop(name, None)
    try:
        yield name, marker
    finally:
        sys.modules.pop(name, None)
        try:
            sys.path.remove(str(tmp_path))
        except ValueError:
            pass


@pytest.mark.django_db
@pytest.mark.asyncio
@override_settings(LIVEVIEW_ALLOWED_MODULES=[])
async def test_ws_mount_does_not_import_arbitrary_module(sentinel_module):
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    name, marker = sentinel_module

    comm = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    connected, _ = await comm.connect()
    assert connected, "WS connects without authentication (mount-time auth only)"

    await comm.send_json_to({"type": "mount", "view": f"{name}.NotAView", "url": "/"})
    # Give the consumer time to process (and, pre-fix, to import the sentinel).
    for _ in range(3):
        try:
            await asyncio.wait_for(comm.receive_from(), timeout=1)
        except BaseException:  # noqa: BLE001 — TimeoutError / CancelledError teardown noise
            break

    # Capture THE INVARIANT before teardown (asgiref disconnect can raise
    # CancelledError, which is a BaseException, not an Exception).
    imported = name in sys.modules
    marker_ran = os.path.exists(marker)
    try:
        await asyncio.wait_for(comm.disconnect(), timeout=1)
    except BaseException:  # noqa: BLE001 — teardown noise must not mask the assertion
        pass

    # The sentinel module was NOT imported and its top-level code did NOT run.
    # (Pre-fix, both of these failed.)
    assert not imported, (
        "client-controlled `view` path triggered a fresh __import__ of an "
        "arbitrary module — unauthenticated arbitrary module import (GHSA)"
    )
    assert not marker_ran, "sentinel module's top-level code executed"


# --------------------------------------------------------------------------
# GHSA-7prp follow-up: a client-controlled class_name on an ALREADY-loaded
# module must not trigger a module-level __getattr__ (PEP 562). Several stdlib
# / framework modules import a submodule on attribute access; the sinks must
# resolve the class via the module __dict__ (vars().get), not getattr.
# --------------------------------------------------------------------------
@pytest.fixture
def getattr_sentinel_module(tmp_path):
    name = "djust_sec_getattr_sentinel"
    (tmp_path / f"{name}.py").write_text(
        "import builtins\n"
        "def __getattr__(attr):\n"
        "    builtins._DJ_GETATTR_HIT = attr\n"  # records that __getattr__ ran
        "    raise AttributeError(attr)\n"
    )
    sys.path.insert(0, str(tmp_path))
    import importlib

    importlib.import_module(name)  # now in sys.modules → passes the fail-closed gate
    if hasattr(__import__("builtins"), "_DJ_GETATTR_HIT"):
        delattr(__import__("builtins"), "_DJ_GETATTR_HIT")
    try:
        yield name
    finally:
        sys.modules.pop(name, None)
        if hasattr(__import__("builtins"), "_DJ_GETATTR_HIT"):
            delattr(__import__("builtins"), "_DJ_GETATTR_HIT")
        try:
            sys.path.remove(str(tmp_path))
        except ValueError:
            pass


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_ws_mount_does_not_trigger_module_getattr(getattr_sentinel_module):
    import builtins

    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    name = getattr_sentinel_module
    # Allowlist the sentinel module explicitly so resolution actually reaches the
    # class-lookup step — otherwise the F22 allowlist would reject this
    # non-installed-app module BEFORE the import/getattr, making the
    # "__getattr__ didn't run" assertion tautological (it would also pass if
    # resolution used getattr, because resolution is never reached).
    comm = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    connected, _ = await comm.connect()
    assert connected

    # Module is already loaded + allowlisted (gate passes); class_name is
    # attacker-chosen.
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[name]):
        await comm.send_json_to({"type": "mount", "view": f"{name}.AnyAttr", "url": "/"})
        for _ in range(3):
            try:
                await asyncio.wait_for(comm.receive_from(), timeout=1)
            except BaseException:  # noqa: BLE001
                break

    # Resolution must use vars()/__dict__, NOT getattr — so __getattr__ never ran.
    # (With the pre-fix getattr, _DJ_GETATTR_HIT would be set to "AnyAttr".)
    hit = getattr(builtins, "_DJ_GETATTR_HIT", None)
    try:
        await asyncio.wait_for(comm.disconnect(), timeout=1)
    except BaseException:  # noqa: BLE001
        pass

    assert hit is None, (
        "client class_name triggered a module-level __getattr__ (PEP 562) — "
        "use vars(module).get(class_name), not getattr (GHSA-7prp follow-up)"
    )
