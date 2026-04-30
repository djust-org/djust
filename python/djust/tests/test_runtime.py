"""
Unit tests for the transport-agnostic ViewRuntime.

Tests the runtime against a MockTransport that captures send() calls into a
list. This isolates the runtime's dispatch logic from any wire concerns
(WebSocket frames, SSE queues, HTTP responses).

See ``python/tests/test_sse_ws_symmetry.py`` for end-to-end tests that drive
both the WS consumer and the SSE message endpoint through the same runtime.
"""

import django
from django.conf import settings

# Configure Django before importing anything that needs settings.
if not settings.configured:
    settings.configure(
        DATABASES={
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": ":memory:",
            }
        },
        INSTALLED_APPS=[
            "django.contrib.contenttypes",
            "django.contrib.auth",
            "django.contrib.sessions",
        ],
        SECRET_KEY="test-secret-key-runtime",
        SESSION_ENGINE="django.contrib.sessions.backends.db",
        USE_TZ=True,
        DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
    )
    django.setup()

import uuid
from contextlib import contextmanager
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@contextmanager
def _patch_allowed_modules(modules):
    """Override LIVEVIEW_ALLOWED_MODULES for the duration of the block."""
    with patch.object(settings, "LIVEVIEW_ALLOWED_MODULES", modules):
        yield


# ------------------------------------------------------------------ #
# Test transport — captures send() calls
# ------------------------------------------------------------------ #


class MockTransport:
    """Minimal Transport implementation that records all outbound frames."""

    def __init__(self, session_id: Optional[str] = None):
        self._session_id = session_id or str(uuid.uuid4())
        self._client_ip: Optional[str] = None
        self.sent: List[Dict[str, Any]] = []
        self.errors: List[Dict[str, Any]] = []
        self.closed_with: Optional[int] = None

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def client_ip(self) -> Optional[str]:
        return self._client_ip

    async def send(self, data: Dict[str, Any]) -> None:
        self.sent.append(data)

    async def send_error(self, error: str, **kwargs: Any) -> None:
        msg = {"type": "error", "error": error, **kwargs}
        self.errors.append(msg)
        self.sent.append(msg)

    async def close(self, code: int = 1000) -> None:
        self.closed_with = code


# ------------------------------------------------------------------ #
# ViewRuntime construction
# ------------------------------------------------------------------ #


class TestViewRuntimeConstruction:
    def test_construct_with_transport(self):
        from djust.runtime import ViewRuntime

        transport = MockTransport()
        runtime = ViewRuntime(transport)

        assert runtime.transport is transport
        assert runtime.view_instance is None
        assert runtime.session_id == transport.session_id

    def test_session_id_proxied_from_transport(self):
        from djust.runtime import ViewRuntime

        sid = "fixed-session-id"
        transport = MockTransport(session_id=sid)
        runtime = ViewRuntime(transport)
        assert runtime.session_id == sid


# ------------------------------------------------------------------ #
# dispatch_message routing
# ------------------------------------------------------------------ #


class TestDispatchMessageRouting:
    @pytest.mark.asyncio
    async def test_unknown_type_emits_error_envelope(self):
        from djust.runtime import ViewRuntime

        transport = MockTransport()
        runtime = ViewRuntime(transport)
        await runtime.dispatch_message({"type": "totally_unknown"})

        assert len(transport.errors) == 1
        assert transport.errors[0]["type"] == "error"
        assert "totally_unknown" in transport.errors[0]["error"]

    @pytest.mark.asyncio
    async def test_routes_mount_to_dispatch_mount(self):
        from djust.runtime import ViewRuntime

        transport = MockTransport()
        runtime = ViewRuntime(transport)

        called = {}

        async def fake_mount(data):
            called["mount"] = data

        runtime.dispatch_mount = fake_mount

        await runtime.dispatch_message({"type": "mount", "view": "x"})
        assert called.get("mount") == {"type": "mount", "view": "x"}

    @pytest.mark.asyncio
    async def test_routes_event_to_dispatch_event(self):
        from djust.runtime import ViewRuntime

        transport = MockTransport()
        runtime = ViewRuntime(transport)

        called = {}

        async def fake_event(data):
            called["event"] = data

        runtime.dispatch_event = fake_event
        await runtime.dispatch_message({"type": "event", "event": "click"})
        assert called.get("event") == {"type": "event", "event": "click"}

    @pytest.mark.asyncio
    async def test_routes_url_change_to_dispatch_url_change(self):
        from djust.runtime import ViewRuntime

        transport = MockTransport()
        runtime = ViewRuntime(transport)

        called = {}

        async def fake_url(data):
            called["url"] = data

        runtime.dispatch_url_change = fake_url
        await runtime.dispatch_message({"type": "url_change", "uri": "/a/"})
        assert called.get("url") == {"type": "url_change", "uri": "/a/"}


# ------------------------------------------------------------------ #
# dispatch_mount URL field plumbing
# ------------------------------------------------------------------ #


class _FakeView:
    """A bare-minimum stand-in for LiveView used by tests where we only
    care about parameter plumbing, not actual rendering."""

    def __init__(self):
        self.kwargs_received: Optional[Dict[str, Any]] = None
        self.handle_params_calls: List = []
        self.assigns: Dict[str, Any] = {}
        self.request = None

    def _initialize_temporary_assigns(self):
        pass

    def _initialize_rust_view(self, request):
        pass

    def _sync_state_to_rust(self):
        pass

    def render_with_diff(self):
        return ("<div></div>", None, 1)

    def _strip_comments_and_whitespace(self, html):
        return html

    def _extract_liveview_content(self, html):
        return html

    def mount(self, request, **kwargs):
        self.kwargs_received = dict(kwargs)

    def handle_params(self, params, uri):
        self.handle_params_calls.append((params, uri))


class TestDispatchMountURLPlumbing:
    @pytest.mark.asyncio
    async def test_mount_uses_url_field_not_endpoint_path(self):
        """Bug 1 reproducer (unit-level).

        ``dispatch_mount`` must read ``data["url"]`` to resolve URL kwargs,
        NOT the SSE endpoint URL. The runtime is intentionally wire-blind:
        it has no concept of "the SSE endpoint path".
        """
        from djust.runtime import ViewRuntime

        transport = MockTransport()
        runtime = ViewRuntime(transport)

        # Patch view-loading + auth + render hooks so the test exercises only
        # the URL-resolution path. The real dispatch_mount imports the view
        # class via dotted path; we short-circuit that by pre-injecting.
        view = _FakeView()
        runtime._instantiate_view = MagicMock(return_value=view)
        runtime._check_auth = AsyncMock(return_value=None)

        # Use a URL that Django's resolver will (probably) not match — that's OK.
        # The contract we're testing: page_url for resolve() is taken from
        # data["url"], not transport.session_id.
        captured_page_url = {}
        original = runtime._resolve_url_kwargs

        def spy_resolve(page_url):
            captured_page_url["page_url"] = page_url
            return original(page_url)

        runtime._resolve_url_kwargs = spy_resolve

        # Bypass the module allowlist (set by demo_project settings) by
        # using a view path inside an explicitly allowed prefix. The
        # _instantiate_view stub fires regardless; we only need the
        # allowlist to admit the path.
        with _patch_allowed_modules(["djust."]):
            await runtime.dispatch_mount(
                {
                    "type": "mount",
                    "view": "djust.tests.test_runtime._FakeView",
                    "params": {},
                    "url": "/items/42/",
                }
            )

        assert captured_page_url.get("page_url") == "/items/42/"

    @pytest.mark.asyncio
    async def test_mount_calls_handle_params_after_mount(self):
        """Bug 3 reproducer (unit-level).

        ``handle_params`` must be invoked AFTER ``mount()`` with both
        ``(params, uri)``, mirroring WebSocket behavior at
        ``websocket.py:2129``.
        """
        from djust.runtime import ViewRuntime

        transport = MockTransport()
        runtime = ViewRuntime(transport)

        view = _FakeView()
        runtime._instantiate_view = MagicMock(return_value=view)
        runtime._check_auth = AsyncMock(return_value=None)
        runtime._resolve_url_kwargs = MagicMock(return_value={})

        with _patch_allowed_modules(["djust."]):
            await runtime.dispatch_mount(
                {
                    "type": "mount",
                    "view": "djust.tests.test_runtime._FakeView",
                    "params": {"category": "books"},
                    "url": "/list/",
                }
            )

        # Mount got called (kwargs_received is populated)
        assert view.kwargs_received is not None
        # handle_params got called with both params and URI
        assert len(view.handle_params_calls) == 1
        called_params, called_uri = view.handle_params_calls[0]
        assert called_params == {"category": "books"}
        # URI should at minimum contain the URL
        assert "/list/" in called_uri


# ------------------------------------------------------------------ #
# Idempotent mount — Risk #1 from the plan
# ------------------------------------------------------------------ #


class TestMountIdempotency:
    @pytest.mark.asyncio
    async def test_double_mount_early_returns(self):
        """Calling dispatch_mount twice does NOT re-mount; the second call
        is a no-op once view_instance is set. Guards against the legacy
        ?view= GET-mount + new POST mount frame double-fire."""
        from djust.runtime import ViewRuntime

        transport = MockTransport()
        runtime = ViewRuntime(transport)

        # Pre-populate view_instance to simulate an already-mounted runtime.
        sentinel = MagicMock()
        runtime.view_instance = sentinel

        await runtime.dispatch_mount(
            {
                "type": "mount",
                "view": "any.path.View",
                "params": {},
                "url": "/x/",
            }
        )

        # No new view_instance was created; the existing one is preserved.
        assert runtime.view_instance is sentinel
        # No frames were emitted.
        assert transport.sent == []


# ------------------------------------------------------------------ #
# use_actors over SSE — ADR-016 §Implementation / #1240
# ------------------------------------------------------------------ #


class _FakeViewWithActors(_FakeView):
    """A view subclass that opts into actor-based state management.

    Used to verify that ``dispatch_mount`` emits a structured error
    envelope rather than letting the mount partially succeed and fail
    downstream when the actor channel-layer code (in ``websocket.py``)
    isn't reachable on the runtime path.
    """

    use_actors = True


class TestDispatchMountUseActorsGuard:
    @pytest.mark.asyncio
    async def test_dispatch_mount_use_actors_emits_error_envelope(self):
        """#1240: when a use_actors=True view tries to mount over a
        runtime whose transport doesn't support actor messages (i.e.,
        SSE), emit a structured error envelope and skip the mount.

        Plan-fidelity gate from ADR-016 §Implementation notes."""
        from djust.runtime import ViewRuntime

        transport = MockTransport()
        runtime = ViewRuntime(transport)

        view = _FakeViewWithActors()
        runtime._instantiate_view = MagicMock(return_value=view)
        runtime._check_auth = AsyncMock(return_value=None)
        runtime._resolve_url_kwargs = MagicMock(return_value={})

        with _patch_allowed_modules(["djust."]):
            await runtime.dispatch_mount(
                {
                    "type": "mount",
                    "view": "djust.tests.test_runtime._FakeViewWithActors",
                    "params": {},
                    "url": "/",
                }
            )

        # Structured error envelope emitted.
        assert len(transport.errors) == 1
        err = transport.errors[0]
        assert err["type"] == "error"
        assert "use_actors" in err["error"]
        # Mount did NOT succeed — runtime stays unmounted.
        assert runtime.view_instance is None
        # The view's mount() was never called (kwargs_received stays None).
        assert view.kwargs_received is None
