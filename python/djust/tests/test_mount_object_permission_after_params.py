"""The live mount checks object permission after ``handle_params``.

A view may resolve its object from the URL in ``handle_params`` (Phoenix
parity: ``?id=`` selects the record). The HTTP GET render
(``RequestMixin.get``) and ``dispatch_url_change`` both run ``handle_params``
and then ``enforce_object_permission``, so the check sees the object the page
will actually render. ``ViewRuntime.dispatch_mount`` (the WebSocket mount and
the SSE mount both route through it) now uses the same order: mount(), then
handle_params(), then the object check, then the first render.

These tests drive the real ``LiveViewConsumer`` over a ``WebsocketCommunicator``
and the SSE session runtime with a view whose ``has_object_permission`` allows
only document 1:

* a mount whose params select document 2 is refused (error frame + close 4403
  on WS; no mounted view on SSE) and document 2's content never leaves the
  server, both when ``mount()`` leaves the id unset and when it defaults to the
  caller's own document;
* a mount whose params select document 1, or that carries no params and keeps
  ``mount()``'s own-document default, renders normally.

Gate-off: moving the ``enforce_object_permission`` block in ``dispatch_mount``
back above the ``handle_params`` call makes the refusal tests fail (the mount
frame carries document 2).
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from unittest.mock import MagicMock

import pytest

pytest.importorskip("channels")

from django.test import RequestFactory, override_settings  # noqa: E402

from djust import LiveView  # noqa: E402

DOCS = {1: "OWN-DOC-CONTENT", 2: "OTHER-USER-DOC-CONTENT"}


class _Doc:
    def __init__(self, pk):
        self.pk = pk


class _AuthedUser:
    is_authenticated = True
    is_active = True
    is_anonymous = False
    is_staff = False
    pk = 1

    def has_perms(self, perms):  # pragma: no cover - no permission_required here
        return True


class _ParamsDocView(LiveView):
    """Resolves its document from ``?id=`` in handle_params; mount() sets none."""

    login_required = False
    template = (
        '<div dj-root dj-view="djust.tests.test_mount_object_permission_after_params.'
        '_ParamsDocView" dj-id="0">doc:{{ body }}</div>'
    )

    def mount(self, request, **kwargs):
        self.doc_id = None

    def handle_params(self, params, uri):
        raw = params.get("id")
        if raw:
            self.doc_id = int(raw)

    def get_object(self):
        return _Doc(self.doc_id) if self.doc_id in DOCS else None

    def has_object_permission(self, request, obj):
        return obj.pk == 1

    def get_context_data(self, **kwargs):
        return {"body": DOCS.get(self.doc_id, "none")}


class _DefaultOwnDocView(_ParamsDocView):
    """mount() defaults to the caller's own document; params may switch it."""

    template = (
        '<div dj-root dj-view="djust.tests.test_mount_object_permission_after_params.'
        '_DefaultOwnDocView" dj-id="0">doc:{{ body }}</div>'
    )

    def mount(self, request, **kwargs):
        self.doc_id = 1


for _name in ("_ParamsDocView", "_DefaultOwnDocView"):
    setattr(sys.modules[__name__], _name, globals()[_name])

_MOD = __name__
_VIEWS = [f"{_MOD}._ParamsDocView", f"{_MOD}._DefaultOwnDocView"]


@pytest.fixture
def _allow_this_module():
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[_MOD]):
        yield


def _authed_middleware(app):
    async def mw(scope, receive, send):
        scope = dict(scope)
        scope["user"] = _AuthedUser()
        scope.setdefault("session", {})
        return await app(scope, receive, send)

    return mw


async def _ws_mount(frame):
    """Mount over the real consumer; return every output until close/timeout."""
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    communicator = WebsocketCommunicator(_authed_middleware(LiveViewConsumer.as_asgi()), "/ws/")
    connected, _ = await communicator.connect()
    assert connected
    try:
        await communicator.receive_json_from(timeout=2)  # connect ack
    except (asyncio.TimeoutError, asyncio.CancelledError):
        pass
    await communicator.send_json_to(frame)
    outputs = []
    try:
        while True:
            out = await communicator.receive_output(timeout=2)
            outputs.append(out)
            if out.get("type") == "websocket.close":
                break
            if '"type": "mount"' in (out.get("text") or ""):
                break
    except (asyncio.TimeoutError, asyncio.CancelledError):
        pass
    try:
        await communicator.disconnect()
    except (asyncio.TimeoutError, asyncio.CancelledError):
        pass
    return outputs


def _mount_frame(view_path, doc_id=None):
    params = {"id": doc_id} if doc_id is not None else {}
    url = f"/doc/?id={doc_id}" if doc_id is not None else "/doc/"
    return {"type": "mount", "view": view_path, "params": params, "url": url}


@pytest.mark.django_db
@pytest.mark.asyncio
@pytest.mark.usefixtures("_allow_this_module")
class TestWebSocketMount:
    @pytest.mark.parametrize("view_path", _VIEWS)
    async def test_params_selecting_a_denied_object_are_refused(self, view_path):
        outputs = await _ws_mount(_mount_frame(view_path, "2"))
        text = repr(outputs)

        assert DOCS[2] not in text, f"denied document rendered on mount: {outputs!r}"
        assert "permission_denied" in text, f"no permission_denied frame: {outputs!r}"
        assert not any('"type": "mount"' in (o.get("text") or "") for o in outputs), outputs
        closes = [o for o in outputs if o.get("type") == "websocket.close"]
        assert closes and closes[-1].get("code") == 4403, f"socket not closed 4403: {outputs!r}"

    @pytest.mark.parametrize("view_path", _VIEWS)
    async def test_params_selecting_an_allowed_object_mount(self, view_path):
        outputs = await _ws_mount(_mount_frame(view_path, "1"))
        text = repr(outputs)

        assert f"doc:{DOCS[1]}" in text, f"own document not rendered: {outputs!r}"
        assert "permission_denied" not in text, outputs
        assert not any(o.get("type") == "websocket.close" for o in outputs), outputs

    async def test_mount_default_object_without_params_mounts(self):
        outputs = await _ws_mount(_mount_frame(f"{_MOD}._DefaultOwnDocView"))
        text = repr(outputs)

        assert f"doc:{DOCS[1]}" in text, f"mount() default not rendered: {outputs!r}"
        assert "permission_denied" not in text, outputs


def _sse_request(path):
    request = RequestFactory().get(path)
    request.user = _AuthedUser()
    request.session = MagicMock()
    return request


async def _sse_mount(view_path, doc_id=None):
    from djust.sse import SSESession

    frame = _mount_frame(view_path, doc_id)
    session = SSESession(str(uuid.uuid4()))
    session._owner_user_pk = 1
    session._request = _sse_request(frame["url"])
    await session.runtime.dispatch_mount(frame)
    messages = []
    while not session.queue.empty():
        messages.append(session.queue.get_nowait())
    return session.runtime.view_instance is not None, messages


@pytest.mark.django_db
@pytest.mark.asyncio
@pytest.mark.usefixtures("_allow_this_module")
class TestSSEMount:
    @pytest.mark.parametrize("view_path", _VIEWS)
    async def test_params_selecting_a_denied_object_are_refused(self, view_path):
        mounted, messages = await _sse_mount(view_path, "2")

        assert mounted is False, "a denied object stayed mounted over SSE"
        assert DOCS[2] not in repr(messages), f"denied document sent over SSE: {messages!r}"
        assert any(m and m.get("type") == "error" for m in messages), messages
        assert not any(m and m.get("type") == "mount" for m in messages), messages

    @pytest.mark.parametrize("view_path", _VIEWS)
    async def test_params_selecting_an_allowed_object_mount(self, view_path):
        mounted, messages = await _sse_mount(view_path, "1")

        assert mounted is True
        assert any(m and m.get("type") == "mount" for m in messages), messages
        assert DOCS[1] in repr(messages)


def test_http_get_order_matches():
    """The HTTP GET render already refuses the same request (parity anchor)."""
    request = RequestFactory().get("/doc/?id=2")
    request.user = _AuthedUser()
    request.session = MagicMock()
    request.session.session_key = "k"
    response = _ParamsDocView.as_view()(request)
    assert response.status_code == 403
