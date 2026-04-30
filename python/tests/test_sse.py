"""
Tests for the SSE (Server-Sent Events) fallback transport.

Covers:
- SSESession state management
- DjustSSEStreamView: session registration, invalid inputs, streaming response
- DjustSSEEventView: event dispatch, missing session, malformed body
- Mount flow: view path validation, queue messages
- Event dispatch: handler security, renders patch/html_update
"""

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import django
from django.conf import settings

# Configure Django if not already done
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
            "django.contrib.messages",
        ],
        SECRET_KEY="test-secret-key-for-sse-tests",
        SESSION_ENGINE="django.contrib.sessions.backends.db",
        USE_TZ=True,
        DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
    )
    django.setup()

from django.test import RequestFactory

from djust.sse import (
    SSESession,
    DjustSSEStreamView,
    DjustSSEEventView,
    _get_session,
    _sse_sessions,
    _sse_mount_view,
    _sse_handle_event,
)


# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #


def make_session(session_id=None) -> SSESession:
    """Create an SSESession and register it."""
    sid = session_id or str(uuid.uuid4())
    session = SSESession(sid)
    _sse_sessions[sid] = session
    return session


def drain_queue(session: SSESession) -> list:
    """Collect all messages currently in a session queue without blocking."""
    messages = []
    while not session.queue.empty():
        messages.append(session.queue.get_nowait())
    return messages


# ------------------------------------------------------------------ #
# SSESession unit tests
# ------------------------------------------------------------------ #


class TestSSESession:
    def test_push_enqueues_message(self):
        session = SSESession("test-id")
        session.push({"type": "test"})
        assert not session.queue.empty()
        msg = session.queue.get_nowait()
        assert msg == {"type": "test"}

    def test_shutdown_sets_inactive_and_sends_sentinel(self):
        session = SSESession("test-id")
        session.shutdown()
        assert session.active is False
        sentinel = session.queue.get_nowait()
        assert sentinel is None

    @pytest.mark.asyncio
    async def test_send_error_pushes_error_message(self):
        session = SSESession("test-id")
        await session.send_error("Something went wrong", code=42)
        msg = session.queue.get_nowait()
        assert msg["type"] == "error"
        assert msg["error"] == "Something went wrong"
        assert msg["code"] == 42

    @pytest.mark.asyncio
    async def test_close_shuts_down_session(self):
        session = SSESession("test-id")
        await session.close(code=4429)
        assert session.active is False


# ------------------------------------------------------------------ #
# _get_session
# ------------------------------------------------------------------ #


class TestGetSession:
    def setup_method(self):
        _sse_sessions.clear()

    def test_returns_session_when_registered(self):
        session = make_session("abc-123")
        result = _get_session("abc-123")
        assert result is session

    def test_returns_none_for_unknown_id(self):
        result = _get_session("does-not-exist")
        assert result is None

    def teardown_method(self):
        _sse_sessions.clear()


# ------------------------------------------------------------------ #
# DjustSSEStreamView.get — HTTP-level checks
# ------------------------------------------------------------------ #


class TestDjustSSEStreamViewGet:
    def setup_method(self):
        _sse_sessions.clear()
        self.factory = RequestFactory()

    def teardown_method(self):
        _sse_sessions.clear()

    @pytest.mark.asyncio
    async def test_returns_400_for_invalid_session_id(self):
        request = self.factory.get("/djust/sse/not-a-uuid/", {"view": "myapp.views.MyView"})
        view = DjustSSEStreamView()
        response = await view.get(request, session_id="not-a-uuid")
        assert response.status_code == 400
        data = json.loads(response.content)
        assert "error" in data

    @pytest.mark.asyncio
    async def test_returns_400_when_view_param_missing(self):
        sid = str(uuid.uuid4())
        request = self.factory.get(f"/djust/sse/{sid}/")
        view = DjustSSEStreamView()
        response = await view.get(request, session_id=sid)
        assert response.status_code == 400
        data = json.loads(response.content)
        assert "view" in data["error"]

    @pytest.mark.asyncio
    async def test_registers_session_and_returns_streaming_response(self):
        from django.http import StreamingHttpResponse

        sid = str(uuid.uuid4())
        request = self.factory.get(f"/djust/sse/{sid}/", {"view": "nonexistent.View"})

        view = DjustSSEStreamView()
        # _sse_mount_view will push an error because the view doesn't exist,
        # but the SSE response itself should still be created.
        with patch("djust.sse._sse_mount_view", new_callable=AsyncMock) as mock_mount:
            mock_mount.return_value = None
            response = await view.get(request, session_id=sid)

        assert isinstance(response, StreamingHttpResponse)
        assert "text/event-stream" in response["Content-Type"]
        assert response["Cache-Control"] == "no-cache"
        assert response["X-Accel-Buffering"] == "no"
        assert sid in _sse_sessions


# ------------------------------------------------------------------ #
# DjustSSEEventView.post — HTTP-level checks
# ------------------------------------------------------------------ #


class TestDjustSSEEventViewPost:
    def setup_method(self):
        _sse_sessions.clear()
        self.factory = RequestFactory()

    def teardown_method(self):
        _sse_sessions.clear()

    @pytest.mark.asyncio
    async def test_returns_404_for_unknown_session(self):
        sid = str(uuid.uuid4())
        request = self.factory.post(
            f"/djust/sse/{sid}/event/",
            data=json.dumps({"event": "increment", "params": {}}),
            content_type="application/json",
        )
        view = DjustSSEEventView()
        response = await view.post(request, session_id=sid)
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_returns_503_when_view_not_mounted(self):
        session = make_session()
        # session.view_instance is None (not mounted)
        request = self.factory.post(
            f"/djust/sse/{session.session_id}/event/",
            data=json.dumps({"event": "increment", "params": {}}),
            content_type="application/json",
        )
        view = DjustSSEEventView()
        response = await view.post(request, session_id=session.session_id)
        assert response.status_code == 503

    @pytest.mark.asyncio
    async def test_returns_400_for_non_json_body(self):
        session = make_session()
        session.view_instance = MagicMock()  # Mark as mounted
        request = self.factory.post(
            f"/djust/sse/{session.session_id}/event/",
            data="not json",
            content_type="application/json",
        )
        view = DjustSSEEventView()
        response = await view.post(request, session_id=session.session_id)
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_returns_400_when_event_name_missing(self):
        session = make_session()
        session.view_instance = MagicMock()
        request = self.factory.post(
            f"/djust/sse/{session.session_id}/event/",
            data=json.dumps({"params": {}}),
            content_type="application/json",
        )
        view = DjustSSEEventView()
        response = await view.post(request, session_id=session.session_id)
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_dispatches_event_and_returns_ok(self):
        session = make_session()
        session.view_instance = MagicMock()

        request = self.factory.post(
            f"/djust/sse/{session.session_id}/event/",
            data=json.dumps({"event": "increment", "params": {}}),
            content_type="application/json",
        )
        view = DjustSSEEventView()

        with patch("djust.sse._sse_handle_event", new_callable=AsyncMock) as mock_dispatch:
            mock_dispatch.return_value = None
            response = await view.post(request, session_id=session.session_id)

        assert response.status_code == 200
        data = json.loads(response.content)
        assert data == {"ok": True}
        mock_dispatch.assert_called_once_with(session, "increment", {})


# ------------------------------------------------------------------ #
# Mount flow
# ------------------------------------------------------------------ #


class TestSSEMountView:
    def setup_method(self):
        _sse_sessions.clear()
        self.factory = RequestFactory()

    def teardown_method(self):
        _sse_sessions.clear()

    @pytest.mark.asyncio
    async def test_pushes_error_for_invalid_view_path(self):
        session = make_session()
        request = self.factory.get("/", {"view": "nonexistent.Module.View"})
        request.session = MagicMock()
        request.user = MagicMock()

        await _sse_mount_view(session, request, "nonexistent.Module.View")

        messages = drain_queue(session)
        assert any(m.get("type") == "error" for m in messages)

    @pytest.mark.asyncio
    async def test_pushes_error_when_not_liveview_subclass(self):
        """Attempting to mount a non-LiveView class is rejected."""
        session = make_session()
        request = self.factory.get("/")
        request.session = MagicMock()
        request.user = MagicMock()

        # django.views.View is not a LiveView subclass
        await _sse_mount_view(session, request, "django.views.View")

        messages = drain_queue(session)
        assert any(m.get("type") == "error" for m in messages)

    @pytest.mark.asyncio
    async def test_pushes_error_when_disallowed_module(self):
        session = make_session()
        request = self.factory.get("/")

        with patch.object(settings, "LIVEVIEW_ALLOWED_MODULES", ["myapp."]):
            await _sse_mount_view(session, request, "otherapp.views.MyView")

        messages = drain_queue(session)
        assert any(m.get("type") == "error" for m in messages)


# ------------------------------------------------------------------ #
# Event dispatch
# ------------------------------------------------------------------ #


class TestSSEHandleEvent:
    def setup_method(self):
        _sse_sessions.clear()

    def teardown_method(self):
        _sse_sessions.clear()

    @pytest.mark.asyncio
    async def test_pushes_error_when_view_not_mounted(self):
        session = make_session()
        session.view_instance = None  # Not mounted

        await _sse_handle_event(session, "increment", {})

        messages = drain_queue(session)
        assert any(m.get("type") == "error" for m in messages)

    @pytest.mark.asyncio
    async def test_security_blocks_unsafe_event_name(self):
        """Event names with '__' are blocked by is_safe_event_name."""
        session = make_session()

        # Create a minimal mock view
        view = MagicMock()
        view.__class__.__name__ = "FakeView"
        session.view_instance = view

        await _sse_handle_event(session, "__class__", {})

        messages = drain_queue(session)
        assert any(m.get("type") == "error" for m in messages)


# ------------------------------------------------------------------ #
# sse_urlpatterns
# ------------------------------------------------------------------ #


class TestSSEUrlPatterns:
    def test_urlpatterns_exported(self):
        from djust.sse import sse_urlpatterns

        # 3 routes after #1237: stream, legacy event, new message endpoint.
        assert len(sse_urlpatterns) == 3
        names = {p.name for p in sse_urlpatterns}
        assert "djust-sse-stream" in names
        assert "djust-sse-event" in names
        assert "djust-sse-message" in names


# ------------------------------------------------------------------ #
# #1237 Bugfix: URL kwargs resolved from mount-frame URL, not endpoint path
# ------------------------------------------------------------------ #


class _DummyLiveView:
    """In-test LiveView replacement registered as a class so dispatch_mount
    can resolve it via `__import__` on this module's path."""


# Lazy import + subclass to avoid circular import at module load.
def _make_dummy_view_class():
    from djust.live_view import LiveView

    captured = {"mount_kwargs": None, "handle_params": []}

    class _DummyLV(LiveView):
        # Skip Rust VDOM init for these unit tests — render_with_diff is
        # short-circuited.
        def _initialize_rust_view(self, request):
            pass

        def _sync_state_to_rust(self):
            pass

        def render_with_diff(self):
            return ("<div>" + str(getattr(self, "count", 0)) + "</div>", None, 1)

        def _strip_comments_and_whitespace(self, html):
            return html

        def _extract_liveview_content(self, html):
            return html

        def mount(self, request, **kwargs):
            captured["mount_kwargs"] = dict(kwargs)
            self.count = 0

        def handle_params(self, params, uri):
            captured["handle_params"].append((params, uri))

    return _DummyLV, captured


class TestSSEMountFrameURLKwargs:
    def setup_method(self):
        _sse_sessions.clear()

    def teardown_method(self):
        _sse_sessions.clear()

    @pytest.mark.asyncio
    async def test_mount_resolves_url_kwargs_from_mount_frame(self):
        """Bug 1 reproducer (end-to-end via runtime).

        Posting a mount frame with ``url: /items/42/`` to the new
        ``/message/`` endpoint must resolve URL kwargs against THAT URL,
        not the SSE endpoint URL. Pre-fix, kwargs were resolved against
        ``request.path`` (the SSE endpoint), so ``pk`` was never set.
        """
        from djust.runtime import ViewRuntime, SSESessionTransport

        DummyLV, captured = _make_dummy_view_class()

        # Register on the test module so __import__ can find it.
        import sys

        mod = sys.modules[__name__]
        mod._DummyLV = DummyLV  # type: ignore[attr-defined]

        session = make_session()
        session.runtime = ViewRuntime(SSESessionTransport(session))

        # Patch the URL resolver to simulate Django matching pk=42.
        with patch("djust.runtime.resolve" if False else "django.urls.resolve") as mock_resolve:
            mock_match = MagicMock()
            mock_match.kwargs = {"pk": "42"}
            mock_resolve.return_value = mock_match

            with patch.object(settings, "LIVEVIEW_ALLOWED_MODULES", ["tests.", "test_sse."]):
                # demo_project's settings allowlist doesn't include this test
                # module — patch it to a permissive prefix that DOES match.
                with patch.object(
                    settings, "LIVEVIEW_ALLOWED_MODULES", [mod.__name__.split(".")[0]]
                ):
                    await session.runtime.dispatch_mount(
                        {
                            "type": "mount",
                            "view": f"{mod.__name__}._DummyLV",
                            "params": {},
                            "url": "/items/42/",
                        }
                    )

        # The mount kwargs include the resolved pk, NOT just the empty
        # mount-frame params dict.
        assert captured["mount_kwargs"] is not None
        assert captured["mount_kwargs"].get("pk") == "42"

    @pytest.mark.asyncio
    async def test_mount_calls_handle_params_after_mount(self):
        """Bug 3 reproducer.

        ``handle_params(params, uri)`` must fire ONCE after mount() over SSE,
        matching Phoenix-parity / WebSocket behavior at websocket.py:2129.
        """
        from djust.runtime import ViewRuntime, SSESessionTransport

        DummyLV, captured = _make_dummy_view_class()
        import sys

        mod = sys.modules[__name__]
        mod._DummyLV = DummyLV  # type: ignore[attr-defined]

        session = make_session()
        session.runtime = ViewRuntime(SSESessionTransport(session))

        with patch.object(settings, "LIVEVIEW_ALLOWED_MODULES", [mod.__name__.split(".")[0]]):
            await session.runtime.dispatch_mount(
                {
                    "type": "mount",
                    "view": f"{mod.__name__}._DummyLV",
                    "params": {"category": "books"},
                    "url": "/list/",
                }
            )

        assert len(captured["handle_params"]) == 1
        called_params, called_uri = captured["handle_params"][0]
        assert called_params == {"category": "books"}
        assert "/list/" in called_uri


# ------------------------------------------------------------------ #
# #1237 Bugfix: url_change message dispatches handle_params
# ------------------------------------------------------------------ #


class TestSSEUrlChangeFlow:
    def setup_method(self):
        _sse_sessions.clear()

    def teardown_method(self):
        _sse_sessions.clear()

    @pytest.mark.asyncio
    async def test_url_change_message_dispatches_handle_params(self):
        """Bug 2 reproducer (server side).

        POST to ``/message/`` with ``{"type": "url_change", ...}`` must
        run handle_params() and emit a patch / html_update frame to the
        SSE queue.
        """
        from djust.runtime import ViewRuntime, SSESessionTransport

        DummyLV, captured = _make_dummy_view_class()
        import sys

        mod = sys.modules[__name__]
        mod._DummyLV = DummyLV  # type: ignore[attr-defined]

        session = make_session()
        session.runtime = ViewRuntime(SSESessionTransport(session))

        # Pre-mount a view by injecting an instance directly.
        view = DummyLV()
        view.count = 5
        session.view_instance = view
        session.runtime.view_instance = view

        await session.runtime.dispatch_url_change(
            {"params": {"sort": "name"}, "uri": "/list/?sort=name"}
        )

        assert len(captured["handle_params"]) == 1
        called_params, called_uri = captured["handle_params"][0]
        assert called_params == {"sort": "name"}

        # Frame was queued for the SSE stream.
        messages = drain_queue(session)
        types = [m.get("type") for m in messages]
        assert "patch" in types or "html_update" in types


# ------------------------------------------------------------------ #
# #1237: New /message/ endpoint forward-compat error envelope
# ------------------------------------------------------------------ #


class TestSSEMessageEndpoint:
    def setup_method(self):
        _sse_sessions.clear()
        self.factory = RequestFactory()

    def teardown_method(self):
        _sse_sessions.clear()

    @pytest.mark.asyncio
    async def test_message_endpoint_unknown_type_returns_error(self):
        """Posting an unknown message type returns 200 with a queued error
        envelope on the SSE stream."""
        from djust.sse import DjustSSEMessageView
        from djust.runtime import ViewRuntime, SSESessionTransport

        session = make_session()
        session.view_instance = MagicMock()  # marked as mounted
        session.runtime = ViewRuntime(SSESessionTransport(session))
        session.runtime.view_instance = session.view_instance

        request = self.factory.post(
            f"/djust/sse/{session.session_id}/message/",
            data=json.dumps({"type": "totally_unknown"}),
            content_type="application/json",
        )

        view = DjustSSEMessageView()
        response = await view.post(request, session_id=session.session_id)
        assert response.status_code == 200

        messages = drain_queue(session)
        types = [m.get("type") for m in messages]
        assert "error" in types

    @pytest.mark.asyncio
    async def test_legacy_event_endpoint_still_works(self):
        """The pre-#1237 ``/event/`` endpoint must keep working as a
        deprecated alias so existing clients aren't broken."""
        session = make_session()
        session.view_instance = MagicMock()

        request = self.factory.post(
            f"/djust/sse/{session.session_id}/event/",
            data=json.dumps({"event": "increment", "params": {}}),
            content_type="application/json",
        )

        view = DjustSSEEventView()
        # Legacy view delegates to either _sse_handle_event or runtime —
        # both are acceptable, we only assert the endpoint stays reachable.
        with patch("djust.sse._sse_handle_event", new_callable=AsyncMock) as mock_dispatch:
            mock_dispatch.return_value = None
            response = await view.post(request, session_id=session.session_id)

        assert response.status_code == 200
        data = json.loads(response.content)
        assert data == {"ok": True}


# ------------------------------------------------------------------ #
# #1237: Double-mount idempotency
# ------------------------------------------------------------------ #


class TestSSEDoubleMountGuard:
    def setup_method(self):
        _sse_sessions.clear()

    def teardown_method(self):
        _sse_sessions.clear()

    @pytest.mark.asyncio
    async def test_double_mount_guard(self):
        """Calling dispatch_mount twice on the same runtime is a no-op
        on the second call. Guards the legacy `?view=` GET-mount + new
        POST mount-frame double-fire (Risk #1)."""
        from djust.runtime import ViewRuntime, SSESessionTransport

        DummyLV, captured = _make_dummy_view_class()
        import sys

        mod = sys.modules[__name__]
        mod._DummyLV = DummyLV  # type: ignore[attr-defined]

        session = make_session()
        session.runtime = ViewRuntime(SSESessionTransport(session))

        with patch.object(settings, "LIVEVIEW_ALLOWED_MODULES", [mod.__name__.split(".")[0]]):
            await session.runtime.dispatch_mount(
                {
                    "type": "mount",
                    "view": f"{mod.__name__}._DummyLV",
                    "params": {},
                    "url": "/list/",
                }
            )
            first_view = session.runtime.view_instance
            assert first_view is not None
            assert captured["mount_kwargs"] is not None

            # Second call with a different URL — should be a no-op.
            captured["mount_kwargs"] = None
            await session.runtime.dispatch_mount(
                {
                    "type": "mount",
                    "view": f"{mod.__name__}._DummyLV",
                    "params": {"reset": "1"},
                    "url": "/other/",
                }
            )
            assert session.runtime.view_instance is first_view
            # mount() was NOT re-invoked.
            assert captured["mount_kwargs"] is None


# ------------------------------------------------------------------ #
# #1237: Runtime.dispatch_url_change shared with WS
# ------------------------------------------------------------------ #


class TestSSERuntimeUrlChangeSharedWithWs:
    def setup_method(self):
        _sse_sessions.clear()

    def teardown_method(self):
        _sse_sessions.clear()

    @pytest.mark.asyncio
    async def test_runtime_dispatch_url_change_shared_with_ws(self):
        """Sanity check that dispatch_url_change is wire-blind:
        the same view + frame produce the same on-wire shape regardless
        of which transport the runtime is wired to. Detailed coverage in
        python/tests/test_sse_ws_symmetry.py."""
        from djust.runtime import ViewRuntime, SSESessionTransport

        DummyLV, captured = _make_dummy_view_class()

        session = make_session()
        session.runtime = ViewRuntime(SSESessionTransport(session))
        view = DummyLV()
        view.count = 0
        session.view_instance = view
        session.runtime.view_instance = view

        await session.runtime.dispatch_url_change({"params": {"page": "1"}, "uri": "/?page=1"})

        # handle_params was invoked once; queue holds an html_update or patch.
        assert len(captured["handle_params"]) == 1
        types = [m.get("type") for m in drain_queue(session)]
        assert types[0] in ("patch", "html_update")
