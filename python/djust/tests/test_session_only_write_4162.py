"""A handler's own ``request.session`` write is stored before its reply (#4162).

A turn that changed no state of the view saved nothing, so what its handler
wrote to the session (an assignment, a removal, an in-place change to a value
it read) never reached the store over the WebSocket, and an in-place change
was lost over SSE and the HTTP fallback too (Django's ``SessionMiddleware``
saves only a session marked ``modified``). Each case here writes from a
handler that changes nothing else, then a plain Django view reads the session
on the next request, through the real session middleware.

Transports: a real ``WebsocketCommunicator``, the SSE endpoints (each request
finished the way ``SessionMiddleware`` finishes it) and the HTTP fallback POST
through Django's test ``Client``. Also: the merge keeps another request's write
and djust's own stored state; with ``DJUST_STATE_SAVE_INTERVAL`` set the
writing turn is still saved before its reply; a turn that does not touch the
session adds no save; a component handler, background work, and a state store
(an external one and the sealed one).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, Dict, List, Optional

import pytest
from asgiref.sync import sync_to_async
from django.conf import settings
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import JsonResponse
from django.test import Client, RequestFactory
from django.urls import path as _path

from djust import LiveView, _automatic_state, state_store
from djust.components.descriptors.base import LiveComponent
from djust.components.mixins.base import TypedState
from djust.decorators import event_handler
from djust.state_store import StateKey, ViewStateStore
from djust.tests import _per_turn_harness as harness

pytestmark = pytest.mark.django_db(transaction=True)
MODULE = __name__

#: Every ``SessionStore.save`` (djust's merged write and the middleware's alike).
SAVES: List[Optional[str]] = []


class Plain(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root><pre>{{ count }}</pre></div>"

    def mount(self, request, **kwargs):
        self.count = 0

    @event_handler()
    def inc(self, **kwargs):
        self.count += 1

    @event_handler()
    def nothing(self, **kwargs):
        pass

    @event_handler()
    def read_only(self, **kwargs):
        self.request.session.get("cart")

    @event_handler()
    def set_only(self, **kwargs):
        self.request.session["k"] = "v"

    @event_handler()
    def append_only(self, **kwargs):
        self.request.session["cart"].append("x")

    @event_handler()
    def delete_only(self, **kwargs):
        del self.request.session["gone"]

    @event_handler()
    def later(self, **kwargs):
        self.start_async(self._write_later, name="later")

    def _write_later(self):
        self.request.session["late"] = "yes"

    @event_handler()
    def later_after_logout(self, **kwargs):
        self.start_async(self._logout_then_write, name="later")

    def _logout_then_write(self):
        # Another request logs this session out while the work runs.
        SessionStore(self.request.session.session_key).delete()
        self.request.session["late"] = "yes"

    def get_context_data(self, **kwargs):
        return {"count": self.count}


class Cart(LiveComponent):
    template = '<button dj-click="add">{{ n }}</button>'

    class State(TypedState):
        n: int = 0

    @event_handler()
    def add(self, **kwargs):
        self._view.request.session["cart"].append("from-component")


class WithComponent(Plain):
    cart = Cart()

    def get_context_data(self, **kwargs):
        return {"count": self.count, "cart": self.cart}


class StickyCart(LiveView):
    """A sticky child whose handler writes only the session."""

    exposure_policy = "explicit"
    sticky = True
    sticky_id = "cartbox"
    template = "<div>cart</div>"

    @event_handler()
    def add(self, **kwargs):
        self.request.session["cart"].append("from-child")

    @event_handler()
    def mark(self, **kwargs):
        self.request.session["child"] = "set"


class WithSticky(Plain):
    template = (
        "<div dj-root><pre>{{ count }}</pre>{% load live_tags %}"
        '{% live_render "' + MODULE + '.StickyCart" sticky=True %}</div>'
    )


def seed(request):
    request.session["cart"] = ["a"]
    request.session["gone"] = 1
    return JsonResponse({})


def other(request):
    request.session["other"] = "kept"
    return JsonResponse({})


def plain(request):
    return JsonResponse(
        {k: v for k, v in request.session.items() if not k.startswith(("_djust", "liveview_"))}
    )


urlpatterns = [
    _path("seed/", seed),
    _path("other/", other),
    _path("plain/", plain),
    _path("plain-view/", Plain.as_view()),
    _path("with-component/", WithComponent.as_view()),
    _path("with-sticky/", WithSticky.as_view()),
]

#: What a plain Django view reads after each handler, starting from ``seed``.
EXPECTED = {
    "set_only": {"cart": ["a"], "gone": 1, "k": "v"},
    "append_only": {"cart": ["a", "x"], "gone": 1},
    "delete_only": {"cart": ["a"]},
}
SEEDED = {"cart": ["a"], "gone": 1}


@pytest.fixture(autouse=True)
def _tab_and_saves(monkeypatch):
    monkeypatch.setattr(_automatic_state, "new_tab_id", lambda: harness.TAB)
    real = SessionStore.save

    def save(self, *args, **kwargs):
        SAVES.append(self.session_key)
        return real(self, *args, **kwargs)

    monkeypatch.setattr(SessionStore, "save", save)
    SAVES.clear()


def _url(view: type) -> str:
    return {WithComponent: "/with-component/", WithSticky: "/with-sticky/"}.get(
        view, "/plain-view/"
    )


def _page(view: type = Plain) -> Any:
    client = Client()
    assert client.get("/seed/").status_code == 200
    assert client.get(_url(view)).status_code == 200
    return client


def _key(client: Any) -> str:
    return client.cookies[settings.SESSION_COOKIE_NAME].value


def _read(client: Any) -> Dict[str, Any]:
    return json.loads(client.get("/plain/").content)


def _stored_state(key: str) -> Dict[str, Any]:
    return {k: v for k, v in SessionStore(key).load().items() if k.startswith("_djust_auto_")}


# -- the three transports ------------------------------------------------------


async def _ws_mount(view: type, key: str) -> Any:
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    comm = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    comm.scope["session"] = SessionStore(key)
    from django.contrib.auth.models import AnonymousUser

    comm.scope["user"] = AnonymousUser()
    connected, _ = await comm.connect()
    assert connected
    await comm.receive_json_from()
    await comm.send_json_to(
        {
            "type": "mount",
            "view": f"{MODULE}.{view.__name__}",
            "url": _url(view),
            "tab": harness.TAB,
        }
    )
    frame = await comm.receive_json_from()
    assert frame["type"] == "mount", frame
    return comm


async def ws_turns(view: type, names: List[str], params: Any = None) -> Any:
    """Page GET, socket mount, then each event; the client and per-turn saves."""
    client = await sync_to_async(_page)(view)
    comm = await _ws_mount(view, _key(client))
    counts = []
    try:
        for name in names:
            SAVES.clear()
            frame = await harness.event(comm, name, params)
            assert frame["type"] in ("patch", "noop", "html_update", "embedded_update"), frame
            counts.append(len(SAVES))
    finally:
        await comm.disconnect()
    return client, counts


async def sse_turns(view: type, names: List[str], params: Any = None) -> Any:
    from django.contrib.auth import get_user
    from django.utils.functional import SimpleLazyObject

    from djust.sse import DjustSSEMessageView, DjustSSEStreamView, _sse_sessions

    client = await sync_to_async(_page)(view)
    key = _key(client)

    def request(method: str, target: str, body: Any) -> Any:
        factory = RequestFactory()
        if method == "GET":
            made = factory.get(target, data=body)
        else:
            made = factory.post(target, data=json.dumps(body), content_type="application/json")
        made.session = SessionStore(key)
        made.user = SimpleLazyObject(lambda: get_user(made))
        made.tenant = None
        return made

    def finish(made: Any, response: Any) -> Any:
        # What SessionMiddleware does once the view has answered.
        return SessionMiddleware(lambda r: response).process_response(made, response)

    sid = str(uuid.uuid4())
    stream = await sync_to_async(request)(
        "GET",
        f"/djust/sse/{sid}/",
        {"view": f"{MODULE}.{view.__name__}", "_djust_url": _url(view), "_djust_tab": harness.TAB},
    )
    response = await DjustSSEStreamView().get(stream, session_id=sid)
    assert response.status_code == 200
    await sync_to_async(finish)(stream, response)
    counts = []
    try:
        for name in names:
            SAVES.clear()
            body = {"type": "event", "event": name, "params": params or {}}
            posted = await sync_to_async(request)("POST", f"/djust/sse/{sid}/message/", body)
            answer = await DjustSSEMessageView().post(posted, session_id=sid)
            assert answer.status_code in (200, 202, 204), answer.content
            await sync_to_async(finish)(posted, answer)
            counts.append(len(SAVES))
    finally:
        _sse_sessions.pop(sid, None)
    return client, counts


def http_turns(view: type, names: List[str], params: Any = None) -> Any:
    client = _page(view)
    counts = []
    for name in names:
        SAVES.clear()
        response = client.post(
            _url(view),
            data=json.dumps(params or {}),
            content_type="application/json",
            HTTP_X_DJUST_EVENT=name,
            HTTP_X_DJUST_TAB=harness.TAB,
        )
        assert response.status_code == 200, response.content[:300]
        counts.append(len(SAVES))
    return client, counts


async def turns(transport: str, view: type, names: List[str], params: Any = None) -> Any:
    if transport == "ws":
        return await ws_turns(view, names, params)
    if transport == "sse":
        return await sse_turns(view, names, params)
    return await sync_to_async(http_turns)(view, names, params)


TRANSPORTS = ["ws", "sse", "http"]
INTERVALS = [pytest.param(0, id="interval-0"), pytest.param(30, id="interval-30")]


def _settings(interval: int, **extra: Any) -> Any:
    if interval:
        extra["DJUST_STATE_SAVE_INTERVAL"] = interval
    return harness.ws_settings(MODULE, ROOT_URLCONF=MODULE, **extra)


# -- the issue ----------------------------------------------------------------


@pytest.mark.parametrize("interval", INTERVALS)
@pytest.mark.parametrize("transport", TRANSPORTS)
@pytest.mark.parametrize("handler", sorted(EXPECTED))
async def test_a_session_only_write_is_read_by_a_plain_view(handler, transport, interval):
    with _settings(interval):
        client, counts = await turns(transport, Plain, [handler])
        seen = await sync_to_async(_read)(client)
    assert seen == EXPECTED[handler]
    assert counts[0] >= 1  # stored by the turn itself, not by a later save


@pytest.mark.parametrize("interval", INTERVALS)
async def test_the_ws_write_is_stored_before_the_reply(interval):
    """The reply frame is read only after the session row holds the write."""
    with _settings(interval):
        client = await sync_to_async(_page)(Plain)
        key = _key(client)
        comm = await _ws_mount(Plain, key)
        try:
            await harness.event(comm, "set_only")
            stored = await sync_to_async(lambda: SessionStore(key).load().get("k"))()
        finally:
            await comm.disconnect()
    assert stored == "v"


@pytest.mark.parametrize("transport", TRANSPORTS)
async def test_another_requests_write_and_the_stored_state_survive(transport):
    """The write is merged: a plain request's key written after the mount stays,
    and so does djust's own stored state for the tab."""
    with _settings(0):
        client = await sync_to_async(_page)(Plain)
        key = _key(client)
        if transport == "ws":
            comm = await _ws_mount(Plain, key)
            try:
                await harness.event(comm, "inc")
                before = await sync_to_async(_stored_state)(key)
                await sync_to_async(client.get)("/other/")
                await harness.event(comm, "set_only")
            finally:
                await comm.disconnect()
        else:
            # The other transports load the session per request: the other
            # request's write is between two of them.
            await sync_to_async(client.get)("/other/")
            client2, _ = await turns(transport, Plain, ["inc"])
            key = _key(client2)
            before = await sync_to_async(_stored_state)(key)
            await sync_to_async(client2.get)("/other/")
            client = client2
            await _one_more(transport, client2, "set_only")
        seen = await sync_to_async(_read)(client)
        after = await sync_to_async(_stored_state)(key)
    assert seen == {"cart": ["a"], "gone": 1, "k": "v", "other": "kept"}
    assert before and after == before


async def _one_more(transport: str, client: Any, name: str) -> None:
    """One more event on an already-loaded page (SSE/HTTP: a new request)."""
    if transport == "http":

        def post() -> None:
            response = client.post(
                "/plain-view/",
                data="{}",
                content_type="application/json",
                HTTP_X_DJUST_EVENT=name,
                HTTP_X_DJUST_TAB=harness.TAB,
            )
            assert response.status_code == 200

        await sync_to_async(post)()
        return
    from django.contrib.auth import get_user
    from django.utils.functional import SimpleLazyObject

    from djust.sse import DjustSSEMessageView, DjustSSEStreamView, _sse_sessions

    key = _key(client)

    def request(method: str, target: str, body: Any) -> Any:
        factory = RequestFactory()
        made = (
            factory.get(target, data=body)
            if method == "GET"
            else factory.post(target, data=json.dumps(body), content_type="application/json")
        )
        made.session = SessionStore(key)
        made.user = SimpleLazyObject(lambda: get_user(made))
        made.tenant = None
        return made

    sid = str(uuid.uuid4())
    stream = await sync_to_async(request)(
        "GET",
        f"/djust/sse/{sid}/",
        {"view": f"{MODULE}.Plain", "_djust_url": "/plain-view/", "_djust_tab": harness.TAB},
    )
    assert (await DjustSSEStreamView().get(stream, session_id=sid)).status_code == 200
    try:
        posted = await sync_to_async(request)(
            "POST", f"/djust/sse/{sid}/message/", {"type": "event", "event": name, "params": {}}
        )
        answer = await DjustSSEMessageView().post(posted, session_id=sid)
        await sync_to_async(
            lambda: SessionMiddleware(lambda r: answer).process_response(posted, answer)
        )()
    finally:
        _sse_sessions.pop(sid, None)


# -- what it costs --------------------------------------------------------------


@pytest.mark.parametrize("interval", INTERVALS)
@pytest.mark.parametrize("transport", TRANSPORTS)
async def test_a_turn_that_does_not_write_the_session_adds_no_save(transport, interval):
    """``nothing`` changes nothing; ``read_only`` reads a list from the session
    without changing it. Neither adds a session save on any transport. The one
    save of a fallback request with an interval set is the holder claim the
    restore writes (``_save_policy.holder_claims``), which Django's
    ``SessionMiddleware`` stores; it was there before #4162 and is the same
    for both handlers."""
    with _settings(interval):
        _client, counts = await turns(transport, Plain, ["nothing", "read_only", "nothing"])
    claim = 1 if transport == "http" and interval else 0
    assert counts == [claim, claim, claim]


@pytest.mark.parametrize("interval", INTERVALS)
async def test_one_save_per_writing_ws_turn(interval):
    """Over the socket the writing turn is one merged save, and the next turn
    that writes nothing saves nothing again."""
    with _settings(interval):
        _client, counts = await ws_turns(Plain, ["set_only", "nothing", "append_only", "nothing"])
    assert counts == [1, 0, 1, 0]


# -- where else a handler runs ----------------------------------------------------


@pytest.mark.parametrize("transport", TRANSPORTS)
async def test_a_component_handler_writes_the_session(transport):
    with _settings(0):
        client, _ = await turns(transport, WithComponent, ["add"], {"component_id": "cart"})
        seen = await sync_to_async(_read)(client)
    assert seen == {"cart": ["a", "from-component"], "gone": 1}


@pytest.mark.parametrize("transport", ["sse", "http"])
async def test_background_work_writes_the_session_sse_and_http(transport):
    """SSE runs the work after the reply, as the socket does; the fallback runs
    it inside the request, before its answer."""
    with _settings(0):
        client, _ = await turns(transport, Plain, ["later"])
        for _ in range(50):
            seen = await sync_to_async(_read)(client)
            if "late" in seen:
                break
            await asyncio.sleep(0.02)
    assert seen.get("late") == "yes"


@pytest.mark.parametrize("interval", INTERVALS)
async def test_background_work_writes_the_session_after_the_reply(interval):
    """``start_async`` work runs after the turn's reply; its write is stored by
    the commit of its own result, before that result's frame."""
    from djust.tests._ws_frames import receive_until

    with _settings(interval):
        client = await sync_to_async(_page)(Plain)
        comm = await _ws_mount(Plain, _key(client))
        try:
            await harness.event(comm, "later")
            await receive_until(
                comm,
                lambda frames: any(
                    f.get("type") in ("patch", "noop", "html_update") for f in frames
                ),
                what="the background result",
            )
            for _ in range(50):
                seen = await sync_to_async(_read)(client)
                if "late" in seen:
                    break
                await asyncio.sleep(0.02)
        finally:
            await comm.disconnect()
    assert seen.get("late") == "yes"


@pytest.mark.parametrize(
    "handler,expected",
    [
        ("add", {"cart": ["a", "from-child"], "gone": 1}),
        ("mark", {"cart": ["a"], "gone": 1, "child": "set"}),
    ],
)
@pytest.mark.parametrize("transport", ["ws", "sse"])
async def test_a_sticky_child_handler_writes_the_session(transport, handler, expected):
    """A sticky child with no declared state: nothing of its own is saved, its
    handler's session write is. (The HTTP fallback does not route a sticky
    child's event: it answers "Embedded view not found".)"""
    with _settings(0):
        client, _ = await turns(transport, WithSticky, [handler], {"view_id": "cartbox"})
        seen = await sync_to_async(_read)(client)
    assert seen == expected


# -- with a state store -----------------------------------------------------------


class ExternalStore(ViewStateStore):
    DATA: Dict[str, bytes] = {}

    def load(self, key: StateKey) -> Optional[bytes]:
        return type(self).DATA.get(key.id)

    def save(self, key: StateKey, payload: bytes, *, expires: int) -> None:
        type(self).DATA[key.id] = payload

    def delete(self, keys: Any) -> None:
        for key in keys:
            type(self).DATA.pop(key.id, None)


STORES = [
    pytest.param(MODULE + ".ExternalStore", id="external"),
    pytest.param("djust.state_store.SealedSessionViewStateStore", id="sealed"),
]


@pytest.mark.parametrize("store", STORES)
@pytest.mark.parametrize("transport", TRANSPORTS)
async def test_a_session_only_write_with_a_state_store(store, transport):
    ExternalStore.DATA.clear()
    with _settings(0, DJUST_STATE_STORE=store, DJUST_STATE_STORE_KEYS=[state_store.generate_key()]):
        client, _ = await turns(transport, Plain, ["inc", "append_only"])
        seen = await sync_to_async(_read)(client)
    assert seen == {"cart": ["a", "x"], "gone": 1}


async def test_background_work_does_not_revive_a_session_logged_out_meanwhile():
    """The work's write is stored only under the result's own authorization: a
    session deleted while the work ran stays deleted (no row written back under
    the old key), and the page is told to reload."""
    from djust.tests._ws_frames import receive_until

    with _settings(0):
        client = await sync_to_async(_page)(Plain)
        key = _key(client)
        comm = await _ws_mount(Plain, key)
        try:
            await harness.event(comm, "later_after_logout")
            frames = await receive_until(
                comm,
                lambda got: any(f.get("type") == "error" for f in got),
                what="the refusal of the background result",
            )
        finally:
            await comm.disconnect()
        exists = await sync_to_async(SessionStore().exists)(key)
    assert [f.get("code") for f in frames if f.get("type") == "error"] == ["permission_denied"]
    assert exists is False
