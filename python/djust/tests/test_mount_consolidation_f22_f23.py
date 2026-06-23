"""Mount-path consolidation: F22 (unsafe reflection) + F23 (URL traversal).

These findings are two facets of the SAME mount surface, reached over THREE
transports (WebSocket ``handle_mount``, SSE ``_sse_mount_view``, generic
``ViewRuntime.dispatch_mount``):

* **F22** — the client-supplied ``view`` dotted path was ``__import__``-ed
  before validation; the ``LIVEVIEW_ALLOWED_MODULES`` allowlist was disabled
  when unset and used a boundary-less ``startswith`` match.
* **F23** — ``_validate_mount_url`` (#1819 traversal fix) ran on the WS mount
  paths but NOT on the ``ViewRuntime`` path used by SSE, so ``/%2e%2e/admin/``
  landed in ``request.path`` as ``/../admin/`` on SSE.

The structural cure (#1646) is a single shared module,
``djust.security.mount``, with :func:`resolve_view_class` (used by all three
mount entry points) and :func:`validate_mount_url` (used by the WS and runtime
paths). This file pins the behavior of that shared module and the parity of the
WS vs. runtime mount paths.

Gate-off (#1468): each behavioral assertion below would FAIL against the
pre-fix code (default-open allowlist / bare ``startswith`` / no
``validate_mount_url`` on the runtime path). See the gate-off notes inline.
"""

import sys

import pytest
from django.test import override_settings

from djust.security.mount import (
    ViewResolution,
    is_view_path_allowed,
    resolve_view_class,
    validate_mount_url,
)


# A module name that is NOT a stdlib module and NOT under any INSTALLED_APPS
# prefix — importing it would run its top-level code. We use it as a sentinel
# the resolver must REFUSE to import.
_SENTINEL_MODULE = "djust_f22_import_sentinel"


@pytest.fixture
def import_sentinel(tmp_path):
    """A fresh, not-yet-imported module whose top-level code sets a marker.

    Yields ``(module_name, marker_getter)``. The resolver must never import it,
    so the marker must stay unset.
    """
    import builtins

    marker = "_DJ_F22_SENTINEL_IMPORTED"
    (tmp_path / f"{_SENTINEL_MODULE}.py").write_text(f"import builtins\nbuiltins.{marker} = True\n")
    sys.path.insert(0, str(tmp_path))
    # Ensure a clean slate.
    sys.modules.pop(_SENTINEL_MODULE, None)
    if hasattr(builtins, marker):
        delattr(builtins, marker)
    try:
        yield _SENTINEL_MODULE, (lambda: getattr(builtins, marker, None))
    finally:
        sys.modules.pop(_SENTINEL_MODULE, None)
        if hasattr(builtins, marker):
            delattr(builtins, marker)
        try:
            sys.path.remove(str(tmp_path))
        except ValueError:
            pass


# A real, mountable demo-app LiveView path (the test settings' INSTALLED_APPS
# include the demo apps). Used as the NON-BREAKING regression guard: with the
# allowlist UNSET, an installed-app view must STILL resolve.
_DEMO_VIEW_PATH = "demo_app.views.CounterView"


# ---------------------------------------------------------------------------
# F22 — arbitrary import is refused WITHOUT importing the module
# ---------------------------------------------------------------------------
class TestF22ArbitraryImportRefused:
    @override_settings(LIVEVIEW_ALLOWED_MODULES=[])
    def test_resolve_does_not_import_arbitrary_module(self, import_sentinel):
        name, marker = import_sentinel
        assert name not in sys.modules

        result = resolve_view_class(f"{name}.Pwn")

        # Rejected, and crucially the module's top-level code never ran.
        assert not result
        assert result.generic == "View not found"
        assert name not in sys.modules, "resolver imported a disallowed module"
        assert marker() is None, "resolver ran a disallowed module's top-level code"

    @override_settings(LIVEVIEW_ALLOWED_MODULES=[])
    def test_stdlib_module_rejected_without_side_effects(self):
        # `os` / `os.system` is the canonical F22 payload. Even though `os` is
        # already loaded, it is not an installed-app prefix → rejected. (No new
        # import, but the point is it is not resolvable as a view.)
        assert not resolve_view_class("os.system")
        assert not resolve_view_class("antigravity.Foo")
        assert "antigravity" not in sys.modules

    @pytest.mark.asyncio
    @override_settings(LIVEVIEW_ALLOWED_MODULES=[])
    async def test_runtime_mount_does_not_import_arbitrary_module(self, import_sentinel):
        """Runtime/SSE path: dispatch_mount must refuse without importing."""
        from djust.runtime import ViewRuntime

        name, marker = import_sentinel
        sent = []

        class _T:
            session_id = "s"
            scope = None

            async def send(self, msg):
                sent.append(msg)

            async def send_error(self, *a, **k):
                sent.append({"type": "error"})

        runtime = ViewRuntime(_T())
        await runtime.dispatch_mount({"type": "mount", "view": f"{name}.Pwn", "url": "/"})

        assert runtime.view_instance is None
        assert name not in sys.modules, "runtime imported a disallowed module"
        assert marker() is None, "runtime ran a disallowed module's top-level code"


# ---------------------------------------------------------------------------
# F22 — allowlist: explicit + module-boundary + INSTALLED_APPS fallback
# ---------------------------------------------------------------------------
class TestF22Allowlist:
    @override_settings(LIVEVIEW_ALLOWED_MODULES=["myapp"])
    def test_boundary_match_rejects_sibling_prefix(self):
        # Boundary match: "myapp" must NOT admit "myapp_evil" (the bare
        # startswith bypass). The module won't actually import, but the
        # allowlist gate (is_view_path_allowed) is what we pin here.
        assert is_view_path_allowed("myapp.views.X") is True
        assert is_view_path_allowed("myapp_evil.views.Pwn") is False
        assert is_view_path_allowed("myapp") is False  # no class segment

    @override_settings(LIVEVIEW_ALLOWED_MODULES=[])
    def test_unset_allowlist_rejects_non_installed_app(self):
        # GATE-OFF: with the pre-fix default-open allowlist, this would be True.
        assert is_view_path_allowed("os.system") is False

    @override_settings(LIVEVIEW_ALLOWED_MODULES=[])
    def test_unset_allowlist_still_mounts_installed_app_view(self):
        # NON-BREAKING regression guard: an installed-app LiveView must STILL
        # resolve when the allowlist is unset (the INSTALLED_APPS + djust
        # fallback). This is the key non-breaking property of the default
        # change.
        result = resolve_view_class(_DEMO_VIEW_PATH)
        assert result, f"{_DEMO_VIEW_PATH} should resolve via INSTALLED_APPS fallback"
        assert result.view_class is not None
        from djust.live_view import LiveView

        assert issubclass(result.view_class, LiveView)

    @override_settings(LIVEVIEW_ALLOWED_MODULES=[])
    def test_unset_allowlist_allows_djust_framework_prefix(self):
        # "djust" is always in the fallback so framework/demo/test views work.
        assert is_view_path_allowed("djust.tests.test_runtime._FakeView") is True


# ---------------------------------------------------------------------------
# F22 — view-path SHAPE rejected before any import
# ---------------------------------------------------------------------------
class TestF22Shape:
    @override_settings(LIVEVIEW_ALLOWED_MODULES=["myapp"])
    @pytest.mark.parametrize(
        "bad",
        [
            "myapp..views.X",  # doubled dot (traversal-style)
            ".myapp.views.X",  # leading dot
            "myapp.views.X.",  # trailing dot
            "myapp/views.X",  # path separator
            "myapp.views-X",  # bad char
            "myapp",  # single segment (no class)
            "",  # empty
            "..",  # pure traversal
        ],
    )
    def test_malformed_paths_rejected(self, bad):
        assert is_view_path_allowed(bad) is False
        assert not resolve_view_class(bad)

    def test_non_string_rejected(self):
        assert is_view_path_allowed(None) is False  # type: ignore[arg-type]
        assert is_view_path_allowed(123) is False  # type: ignore[arg-type]
        assert not resolve_view_class(None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# F23 — runtime/SSE mount URL traversal neutralised; WS↔runtime parity
# ---------------------------------------------------------------------------
class TestF23Traversal:
    @pytest.mark.parametrize(
        "payload",
        [
            "/%2e%2e/%2e%2e/admin/",  # percent-encoded traversal (#1825 class)
            "/../../admin/",  # literal traversal
            "//evil.com/page",  # protocol-relative
            "https://evil.com/page",  # absolute
            "/ok\r\nX-Injected: 1",  # CRLF
        ],
    )
    def test_validate_mount_url_neutralises(self, payload):
        assert validate_mount_url(payload) == "/"

    def test_validate_mount_url_passes_legit(self):
        assert validate_mount_url("/items/42/") == "/items/42/"
        assert validate_mount_url("/dashboard?q=1") == "/dashboard?q=1"

    @pytest.mark.asyncio
    async def test_runtime_build_request_path_not_traversed(self):
        """A runtime mount with an encoded-traversal url must NOT land a `..`
        in request.path. GATE-OFF: pre-fix (raw page_url into RequestFactory)
        this leaves `/../../admin/` in request.path.
        """
        from djust.runtime import ViewRuntime

        class _T:
            session_id = "s"
            scope = None

            async def send(self, msg):
                pass

            async def send_error(self, *a, **k):
                pass

        runtime = ViewRuntime(_T())
        request = await runtime._build_request(page_url="/%2e%2e/%2e%2e/admin/", params={})
        assert ".." not in request.path
        # Neutralised to the safe fallback.
        assert request.path == "/"

    @pytest.mark.asyncio
    async def test_ws_runtime_traversal_parity(self):
        """Parity: the WS mount path and the runtime mount path neutralise the
        SAME payload to the SAME request.path.
        """
        from djust.runtime import ViewRuntime
        from djust.websocket import _validate_mount_url as ws_validate

        payload = "/%2e%2e/%2e%2e/admin/"

        # WS path: validates via the shared validator (re-exported alias).
        ws_url = ws_validate(payload)

        # Runtime path: builds the request from the (validated) url.
        class _T:
            session_id = "s"
            scope = None

            async def send(self, msg):
                pass

            async def send_error(self, *a, **k):
                pass

        runtime = ViewRuntime(_T())
        rt_request = await runtime._build_request(page_url=payload, params={})

        assert ws_url == rt_request.path == "/"

    def test_ws_alias_is_shared_validator(self):
        """The WS `_validate_mount_url` is the SAME object as the shared one
        (proves the consolidation, not a copy)."""
        from djust.websocket import _validate_mount_url as ws_validate

        assert ws_validate is validate_mount_url


# ---------------------------------------------------------------------------
# Resolver result shape
# ---------------------------------------------------------------------------
class TestViewResolution:
    @override_settings(LIVEVIEW_ALLOWED_MODULES=[])
    def test_success_is_truthy_with_view_class(self):
        result = resolve_view_class(_DEMO_VIEW_PATH)
        assert isinstance(result, ViewResolution)
        assert bool(result) is True
        assert result.view_class is not None

    @override_settings(LIVEVIEW_ALLOWED_MODULES=["djust"])
    def test_non_liveview_class_rejected_with_invalid_view_class(self):
        # `djust.tests.test_runtime._FakeView` exists but is NOT a LiveView
        # subclass → defense-in-depth subclass check rejects with the
        # "Invalid view class" generic.
        result = resolve_view_class("djust.tests.test_runtime._FakeView")
        assert not result
        assert result.generic == "Invalid view class"

    @override_settings(LIVEVIEW_ALLOWED_MODULES=["djust"])
    def test_missing_class_rejected_with_view_not_found(self):
        result = resolve_view_class("djust.tests.test_runtime.NoSuchClassXYZ")
        assert not result
        assert result.generic == "View not found"
