"""Events routed to a LiveComponent (or an embedded child) run the page view's
per-event object-permission check.

The page view's ``get_object()`` / ``has_object_permission()`` are re-run on
every event (ADR-017 § Decision 7). An event carrying ``component_id`` runs the
component's handler and re-renders the page, so it belongs to the page view
and is authorized as a page event is: once the page's object permission is
withdrawn, a component event is refused with the same "Access denied" error
frame, its handler does not run and nothing is re-rendered. Covered on every
transport: the WebSocket consumer, SSE, the shared runtime, and the HTTP POST
event fallback.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest

pytest.importorskip("channels")

from django.contrib.sessions.middleware import SessionMiddleware  # noqa: E402
from django.test import RequestFactory, override_settings  # noqa: E402

from djust import LiveView  # noqa: E402
from djust.components.descriptors.base import LiveComponent, TypedState  # noqa: E402
from djust.decorators import event_handler  # noqa: E402
from djust.tests._ws_frames import drain_extra, receive_until  # noqa: E402

_MOD = __name__

#: Handlers that ran, in order.
RAN: List[str] = []
#: Whether the page's object permission is currently granted.
GRANT = {"allowed": True}


class _Doc:
    pk = 7


class _User:
    is_authenticated = True
    is_active = True
    is_anonymous = False
    is_staff = False
    is_superuser = False
    pk = 1

    def has_perms(self, perms):
        return False


class Toggle(LiveComponent):
    class State(TypedState):
        on: bool = False

    template = "<span>toggle {{ on }}</span>"

    @event_handler()
    def flip(self, **kwargs):
        RAN.append("component-flip")
        self.state.on = not self.state.on


class ObjectScopedPage(LiveView):
    template = (
        f'<div dj-root dj-view="{_MOD}.ObjectScopedPage" dj-id="0">'
        "PAGE-SECRET {{ n }} {{ toggle }}</div>"
    )

    toggle = Toggle()

    def mount(self, request, **kwargs):
        self.n = 0
        # Touching the component in mount registers it on the view.
        self.toggle.on = self.toggle.on

    def get_object(self):
        return _Doc()

    def has_object_permission(self, request, obj):
        return GRANT["allowed"]

    @event_handler()
    def bump(self, **kwargs):
        RAN.append("page-bump")
        self.n += 1


class EmbeddedChild(LiveView):
    template = "<div>child {{ count }}</div>"

    def mount(self, request, **kwargs):
        self.count = 0

    def get_context_data(self, **kwargs):
        return {"count": self.count, "view": self}

    @event_handler()
    def tick(self, **kwargs):
        RAN.append("child-tick")
        self.count += 1


@pytest.fixture(autouse=True)
def _reset():
    RAN.clear()
    GRANT["allowed"] = True
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[_MOD]):
        yield
    RAN.clear()
    GRANT["allowed"] = True


def _denied(frames) -> bool:
    return any(
        f.get("type") == "error" and "Access denied" in json.dumps(f, default=str) for f in frames
    )


def _rendered(frames) -> bool:
    """A frame that carries a re-render of the page or the component."""
    return any(
        f.get("type") in ("patch", "html_update", "component_event", "embedded_update")
        or "PAGE-SECRET" in json.dumps(f, default=str)
        for f in frames
    )


# --------------------------------------------------------------------------- #
# The shared runtime (WebSocket and SSE both dispatch through it)
# --------------------------------------------------------------------------- #
class _MockTransport:
    def __init__(self):
        self.session_id = str(uuid.uuid4())
        self.client_ip = None
        self._client_ip = None
        self.sent: List[Dict[str, Any]] = []

    async def send(self, data):
        self.sent.append(data)

    async def send_error(self, error, **kwargs):
        self.sent.append({"type": "error", "error": error, **kwargs})

    async def close(self, code=1000):
        self.sent.append({"type": "close", "code": code})

    def next_client_version(self, html: Optional[str], rust_version: int) -> int:
        return rust_version

    def build_request(self):
        return None

    def on_view_mounted(self, view_instance):
        pass

    @contextlib.asynccontextmanager
    async def event_context(self, view):
        yield


def _request():
    request = RequestFactory().get("/x/")
    request.user = _User()
    request.session = MagicMock()
    return request


def _runtime(view):
    from djust.runtime import ViewRuntime

    view.request = _request()
    view.mount(view.request)
    transport = _MockTransport()
    runtime = ViewRuntime(transport)
    runtime.view_instance = view
    return runtime, transport


def _parent_with_child():
    parent = ObjectScopedPage()
    child = EmbeddedChild()
    child.mount(None)
    child.request = _request()
    runtime, transport = _runtime(parent)
    parent._register_child("child-1", child)
    return runtime, transport, child


@pytest.mark.django_db
@pytest.mark.asyncio
class TestRuntimeComponentEvent:
    async def test_component_event_refused_after_revocation(self):
        runtime, transport = _runtime(ObjectScopedPage())
        GRANT["allowed"] = False
        await runtime.dispatch_event(
            {"type": "event", "event": "flip", "params": {"component_id": "toggle"}, "ref": 1}
        )
        assert RAN == []
        assert _denied(transport.sent), transport.sent
        assert not _rendered(transport.sent), transport.sent

    async def test_component_event_runs_while_permitted(self):
        runtime, transport = _runtime(ObjectScopedPage())
        await runtime.dispatch_event(
            {"type": "event", "event": "flip", "params": {"component_id": "toggle"}, "ref": 1}
        )
        assert RAN == ["component-flip"]
        assert not _denied(transport.sent), transport.sent

    async def test_page_event_refused_after_revocation(self):
        # The reference behaviour the component path now matches.
        runtime, transport = _runtime(ObjectScopedPage())
        GRANT["allowed"] = False
        await runtime.dispatch_event({"type": "event", "event": "bump", "params": {}, "ref": 1})
        assert RAN == []
        assert _denied(transport.sent), transport.sent

    async def test_embedded_child_event_refused_after_page_revocation(self):
        runtime, transport, child = _parent_with_child()
        GRANT["allowed"] = False
        await runtime.dispatch_event(
            {"type": "event", "event": "tick", "params": {"view_id": "child-1"}, "ref": 1}
        )
        assert RAN == [] and child.count == 0
        assert _denied(transport.sent), transport.sent
        assert not _rendered(transport.sent), transport.sent

    async def test_embedded_child_event_runs_while_permitted(self):
        runtime, transport, _child = _parent_with_child()
        await runtime.dispatch_event(
            {"type": "event", "event": "tick", "params": {"view_id": "child-1"}, "ref": 1}
        )
        assert RAN == ["child-tick"]
        assert not _denied(transport.sent), transport.sent


# --------------------------------------------------------------------------- #
# The WebSocket consumer
# --------------------------------------------------------------------------- #
def _with_user(app):
    async def mw(scope, receive, send):
        scope = dict(scope)
        scope["user"] = _User()
        scope.setdefault("session", {})
        return await app(scope, receive, send)

    return mw


async def _ws_component_event(revoke: bool):
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    communicator = WebsocketCommunicator(_with_user(LiveViewConsumer.as_asgi()), "/ws/")
    connected, _ = await communicator.connect()
    assert connected
    await communicator.receive_json_from()  # connect ack
    await communicator.send_json_to(
        {"type": "mount", "view": f"{_MOD}.ObjectScopedPage", "params": {}, "url": "/x/"}
    )
    mounted = await receive_until(
        communicator,
        lambda frames: any(f.get("type") in ("mount", "websocket.close") for f in frames),
        allow_close=True,
        what="the mount",
    )
    assert mounted[-1].get("type") == "mount", mounted
    if revoke:
        GRANT["allowed"] = False
    await communicator.send_json_to(
        {"type": "event", "event": "flip", "params": {"component_id": "toggle"}, "ref": 1}
    )
    frames = await receive_until(
        communicator,
        lambda fs: any(
            f.get("type") in ("error", "websocket.close") or f.get("ref") == 1 for f in fs
        ),
        allow_close=True,
        what="the answer to the event",
    )
    if frames[-1].get("type") != "websocket.close":
        frames += await drain_extra(communicator)
    try:
        await communicator.disconnect()
    except (asyncio.TimeoutError, asyncio.CancelledError):
        pass
    return [f for f in frames if f.get("type") != "websocket.close"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
class TestWebSocketComponentEvent:
    async def test_component_event_refused_after_revocation(self):
        frames = await _ws_component_event(revoke=True)
        assert RAN == []
        assert _denied(frames), frames
        assert not _rendered(frames), frames

    async def test_component_event_runs_while_permitted(self):
        frames = await _ws_component_event(revoke=False)
        assert RAN == ["component-flip"]
        assert not _denied(frames), frames


# --------------------------------------------------------------------------- #
# SSE
# --------------------------------------------------------------------------- #
async def _sse_component_event(revoke: bool):
    from djust.sse import SSESession

    request = _request()
    session = SSESession(str(uuid.uuid4()))
    session._owner_user_pk = request.user.pk
    session._request = request
    await session.runtime.dispatch_mount(
        {"type": "mount", "view": f"{_MOD}.ObjectScopedPage", "url": "/x/", "params": {}}
    )
    assert session.runtime.view_instance is not None
    while not session.queue.empty():
        session.queue.get_nowait()
    if revoke:
        GRANT["allowed"] = False
    await session.runtime.dispatch_event({"event": "flip", "params": {"component_id": "toggle"}})
    messages = []
    while not session.queue.empty():
        messages.append(session.queue.get_nowait())
    return messages


@pytest.mark.django_db
@pytest.mark.asyncio
class TestSSEComponentEvent:
    async def test_component_event_refused_after_revocation(self):
        messages = await _sse_component_event(revoke=True)
        assert RAN == []
        assert "Access denied" in repr(messages), messages
        assert "PAGE-SECRET" not in repr(messages)

    async def test_component_event_runs_while_permitted(self):
        messages = await _sse_component_event(revoke=False)
        assert RAN == ["component-flip"]
        assert "Access denied" not in repr(messages), messages


# --------------------------------------------------------------------------- #
# The HTTP POST event fallback
# --------------------------------------------------------------------------- #
def _post(params, revoke: bool):
    """Load the page (GET), optionally withdraw the object permission, then POST
    a component event on the same session, as the HTTP fallback client does."""
    page = RequestFactory().get("/x/")
    page.user = _User()
    SessionMiddleware(lambda r: None).process_request(page)
    page.session.save()
    loaded = ObjectScopedPage.as_view()(page)
    assert loaded.status_code == 200
    if revoke:
        GRANT["allowed"] = False
    request = RequestFactory().post(
        "/x/",
        data=json.dumps({"event": "flip", "params": params}),
        content_type="application/json",
    )
    request.user = page.user
    request.session = page.session
    return ObjectScopedPage.as_view()(request)


@pytest.mark.django_db
class TestHttpFallbackComponentEvent:
    def test_component_event_refused_after_revocation(self):
        response = _post({"component_id": "toggle"}, revoke=True)
        assert response.status_code == 403
        assert b"PAGE-SECRET" not in response.content
        assert RAN == []

    def test_component_event_runs_while_permitted(self):
        response = _post({"component_id": "toggle"}, revoke=False)
        assert response.status_code == 200, response.content
        assert RAN == ["component-flip"]
