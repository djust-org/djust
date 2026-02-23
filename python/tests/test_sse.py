"""
Tests for the SSE (Server-Sent Events) fallback transport.

Covers:
- SSESession state management
- DjustSSEStreamView: session registration, invalid inputs, streaming response
- DjustSSEEventView: event dispatch, missing session, malformed body
- Mount flow: view path validation, queue messages
- Event dispatch: handler security, renders patch/html_update
"""

import asyncio
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

        assert len(sse_urlpatterns) == 2
        names = {p.name for p in sse_urlpatterns}
        assert "djust-sse-stream" in names
        assert "djust-sse-event" in names
