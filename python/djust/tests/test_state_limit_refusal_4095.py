"""#4095: persisted state over its size limit refuses that one change.

Persisted server state is bounded by ``StateLimits`` (64 KB encoded). Going
over answered 500 for that event, and on a live socket for every later event
too: the oversized value stayed on the view, so every later save failed again.

Now an over-limit change is refused before anything is written: the user gets
an error naming the field and the limit (``code="state_too_large"``), the store
keeps the last state that fit, and later events work. On a live connection the
view's declared server fields are put back to that stored state, so the view,
the store and the page agree. Driven past the limit and back on the WebSocket,
SSE and HTTP fallback transports.
"""

from __future__ import annotations

import json
import uuid

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth import get_user
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory, override_settings
from django.utils.functional import SimpleLazyObject

from djust import LiveView, state
from djust._exposure import StateLimits
from djust._exposure_sessions import StateLimitExceeded, load_server_state, save_server_state
from djust.decorators import event_handler

pytest.importorskip("channels")

pytestmark = pytest.mark.django_db(transaction=True)

MOD = __name__
LIMIT = StateLimits().max_bytes
BIG = "x" * (LIMIT * 2 // 5)  # two fit, three do not


class Notes(LiveView):
    exposure_policy = "explicit"
    template = (
        '<div dj-root dj-view="' + MOD + '.Notes"><b>count={{ count }}</b><i>{{ sizes }}</i></div>'
    )
    notes = state([], persist="server")
    count = state(0, persist="server")

    @event_handler
    def add(self, size: str = "small", **kwargs):
        self.notes = self.notes + [BIG if size == "big" else "s"]
        self.count += 1

    def get_context_data(self, **kwargs):
        return {"count": self.count, "sizes": ",".join(str(len(n)) for n in self.notes)}


@pytest.fixture(autouse=True)
def staged(monkeypatch):
    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[MOD], DEBUG=False, DJUST_CONFIG={}):
        yield


def _fresh_key():
    store = SessionStore()
    store.create()
    return store.session_key


def _request(method, key, body=None, url="/notes/", event=None):
    factory = RequestFactory()
    if method == "GET":
        request = factory.get(url, data=body or {})
    else:
        extra = {"HTTP_X_DJUST_EVENT": event} if event else {}
        request = factory.post(
            url, data=json.dumps(body or {}), content_type="application/json", **extra
        )
    request.session = SessionStore(key)
    request.user = SimpleLazyObject(lambda: get_user(request))
    request.tenant = None
    return request


def _stored(key):
    view = Notes()
    request = _request("GET", key)
    values = load_server_state(view, request)
    return None if values is None else (values["count"], [len(n) for n in values["notes"]])


# --------------------------------------------------------------------------- #
# The save itself
# --------------------------------------------------------------------------- #


def test_an_over_limit_save_is_refused_before_writing_and_names_the_field():
    key = _fresh_key()
    view = Notes()
    view.notes, view.count = [BIG, BIG], 2
    save_server_state(view, _request("GET", key))
    view.notes, view.count = [BIG, BIG, BIG], 3
    with pytest.raises(StateLimitExceeded) as refused:
        save_server_state(view, _request("GET", key))
    assert refused.value.field == "notes"
    assert refused.value.limit == "bytes"
    assert "'notes'" in str(refused.value) and str(LIMIT) in str(refused.value)
    assert _stored(key) == (2, [len(BIG), len(BIG)])  # the last state that fit


def test_a_field_over_the_structural_limit_is_named_with_that_limit():
    key = _fresh_key()
    view = Notes()
    deep = []
    for _ in range(StateLimits().max_depth + 2):
        deep = [deep]
    view.notes, view.count = deep, 1
    with pytest.raises(StateLimitExceeded) as refused:
        save_server_state(view, _request("GET", key))
    assert (refused.value.field, refused.value.limit) == ("notes", "structure")
    assert "depth" in str(refused.value)


# --------------------------------------------------------------------------- #
# HTTP fallback
# --------------------------------------------------------------------------- #


def _post(key, size):
    return Notes.as_view()(_request("POST", key, {"size": size}, event="add"))


def test_http_fallback_past_the_limit_and_back():
    key = _fresh_key()
    assert Notes.as_view()(_request("GET", key)).status_code == 200
    assert [_post(key, "big").status_code for _ in range(2)] == [200, 200]

    refused = _post(key, "big")
    assert refused.status_code == 413
    body = json.loads(refused.content)
    assert body["code"] == "state_too_large"
    assert "'notes'" in body["error"] and str(LIMIT) in body["error"]
    assert _stored(key) == (2, [len(BIG), len(BIG)])

    # Later events work, from the last saved state.
    again = _post(key, "small")
    assert again.status_code == 200
    assert _stored(key) == (3, [len(BIG), len(BIG), 1])


# --------------------------------------------------------------------------- #
# WebSocket
# --------------------------------------------------------------------------- #


async def _connect(key):
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    communicator.scope["session"] = SessionStore(key)
    communicator.scope["user"] = AnonymousUser()
    connected, _ = await communicator.connect()
    assert connected
    await communicator.receive_json_from()  # connect ack
    return communicator


async def _until(communicator, *types):
    frames = []
    while True:
        frames.append(await communicator.receive_json_from(timeout=15))
        if frames[-1].get("type") in types:
            return frames[-1]


async def _event(communicator, size):
    await communicator.send_json_to({"type": "event", "event": "add", "params": {"size": size}})
    return await _until(communicator, "patch", "html_update", "error", "noop")


async def test_websocket_past_the_limit_and_back():
    key = await sync_to_async(_fresh_key)()
    socket = await _connect(key)
    try:
        await socket.send_json_to({"type": "mount", "view": MOD + ".Notes", "url": "/notes/"})
        mounted = await _until(socket, "mount", "error")
        assert mounted["type"] == "mount", mounted
        for _ in range(2):
            ok = await _event(socket, "big")
            assert ok["type"] != "error", ok

        refused = await _event(socket, "big")
        assert refused["type"] == "error", refused
        assert refused["code"] == "state_too_large"
        assert "'notes'" in refused["error"] and str(LIMIT) in refused["error"]
        assert await sync_to_async(_stored)(key) == (2, [len(BIG), len(BIG)])

        # The view went back to the saved state: the next event starts there,
        # and its full render shows what the store holds.
        again = await _event(socket, "small")
        assert again["type"] == "html_update", again
        assert "count=3" in again["html"]
        assert "%d,%d,1" % (len(BIG), len(BIG)) in again["html"]
        assert await sync_to_async(_stored)(key) == (3, [len(BIG), len(BIG), 1])
    finally:
        await socket.disconnect()


# --------------------------------------------------------------------------- #
# SSE
# --------------------------------------------------------------------------- #


async def test_sse_past_the_limit_and_back():
    from djust.sse import DjustSSEMessageView, DjustSSEStreamView, _sse_sessions

    key = await sync_to_async(_fresh_key)()
    sid = str(uuid.uuid4())
    stream = await sync_to_async(_request)(
        "GET", key, {"view": MOD + ".Notes", "_djust_url": "/notes/"}, url="/djust/sse/%s/" % sid
    )
    response = await DjustSSEStreamView().get(stream, session_id=sid)
    assert response.status_code == 200
    session = _sse_sessions[sid]

    def drain():
        frames = []
        while not session.queue.empty():
            frame = session.queue.get_nowait()
            if frame is not None:
                frames.append(frame)
        return frames

    async def event(size):
        drain()
        request = await sync_to_async(_request)(
            "POST",
            key,
            {"type": "event", "event": "add", "params": {"size": size}},
            url="/djust/sse/%s/message/" % sid,
        )
        posted = await DjustSSEMessageView().post(request, session_id=sid)
        assert posted.status_code == 200
        return [f for f in drain() if f.get("type") in ("patch", "html_update", "error")]

    try:
        for _ in range(2):
            frames = await event("big")
            assert frames and not [f for f in frames if f["type"] == "error"], frames

        (refused,) = await event("big")
        assert refused["type"] == "error" and refused["code"] == "state_too_large", refused
        assert "'notes'" in refused["error"]
        assert await sync_to_async(_stored)(key) == (2, [len(BIG), len(BIG)])

        frames = await event("small")
        assert frames and frames[-1]["type"] == "html_update", frames
        assert "count=3" in frames[-1]["html"]
        assert await sync_to_async(_stored)(key) == (3, [len(BIG), len(BIG), 1])
    finally:
        _sse_sessions.pop(sid, None)
