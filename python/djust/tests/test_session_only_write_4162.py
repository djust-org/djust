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
import contextlib
import json
import uuid
from typing import Any, Dict, Iterator, List, Optional
from unittest import mock

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
from djust.tests._frames import legacy_type

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


#: The #4211 hold: ``reached`` once the held step is entered, ``gate`` to let it go on.
HOLD: Dict[str, asyncio.Event] = {}


async def _absorbing_wait(gate: asyncio.Event) -> None:
    """Wait for ``gate``, swallowing a cancellation meanwhile: the step goes on
    after a teardown cancelled it, as one that does not reach an await that the
    cancellation stops it at (asgiref's ``SyncToAsync`` keeps awaiting a thread
    that already started)."""
    try:
        await asyncio.shield(gate.wait())
    except asyncio.CancelledError:
        await gate.wait()


#: Switches the background-work views below flip while their work runs.
FLAGS: Dict[str, Any] = {"deny": False}


class Revocable(Plain):
    """Its permission check reads ``FLAGS``: the work revokes it."""

    def check_permissions(self, request):
        return not FLAGS["deny"]

    @event_handler()
    def revoke_bg(self, **kwargs):
        self.start_async(self._revoke_bg, name="w")

    def _revoke_bg(self):
        self.request.session["late"] = "yes"
        self.request.session["cart"].append("bg")
        FLAGS["deny"] = True

    @event_handler()
    def del_bg(self, **kwargs):
        self.start_async(self._del_bg, name="w")

    def _del_bg(self):
        del self.request.session["cart"]

    @event_handler()
    def fail_bg(self, **kwargs):
        self.start_async(self._fail_bg, name="w")

    def _fail_bg(self):
        self.request.session["late"] = "yes"
        raise RuntimeError("the work failed after writing")

    @event_handler()
    def ok_bg(self, **kwargs):
        self.start_async(self._ok_bg, name="w")

    def _ok_bg(self):
        self.request.session["late"] = "yes"
        return "done"

    @event_handler()
    def held_ok_bg(self, **kwargs):
        self.start_async(self._held_bg, False, name="w")

    @event_handler()
    def held_fail_bg(self, **kwargs):
        self.start_async(self._held_bg, True, name="w")

    async def _held_bg(self, fail):
        # A DB session must not load on the loop.
        await sync_to_async(self.request.session.__setitem__)("late", "yes")
        HOLD["reached"].set()
        await _absorbing_wait(HOLD["gate"])
        if fail:
            raise RuntimeError("the work failed after writing")
        return "done"

    @event_handler()
    def armed_ok_bg(self, **kwargs):
        self.start_async(self._armed_ok_bg, name="w")

    def _armed_ok_bg(self):
        # Arms the #4211 save hold (:func:`_held_save_start`) for the result's commit.
        HOLD["armed"] = True
        self.request.session["late"] = "yes"
        return "done"

    @event_handler()
    def armed_inc_bg(self, **kwargs):
        self.start_async(self._armed_inc_bg, name="w")

    def _armed_inc_bg(self):
        # Changes the view only, so its result's save is deferred (interval set).
        HOLD["armed"] = True
        HOLD["view"] = self
        self.count += 1
        return "done"


class RevocableHandled(Revocable):
    def handle_async_result(self, name, result=None, error=None):
        pass


class ResultHandlerRaises(Revocable):
    def handle_async_result(self, name, result=None, error=None):
        raise RuntimeError("the result handler failed too")


class SignsInElsewhere(Plain):
    """Its work signs another user into the same session (as a login in
    another tab would): the principal the result resolves is not the mount's."""

    @event_handler()
    def sign_in_bg(self, **kwargs):
        self.start_async(self._sign_in_bg, name="w")

    def _sign_in_bg(self):
        from django.contrib.auth import BACKEND_SESSION_KEY, HASH_SESSION_KEY, SESSION_KEY
        from django.contrib.auth.models import User

        user = User.objects.create_user("other-4162")
        elsewhere = SessionStore(self.request.session.session_key)
        elsewhere[SESSION_KEY] = str(user.pk)
        elsewhere[BACKEND_SESSION_KEY] = "django.contrib.auth.backends.ModelBackend"
        elsewhere[HASH_SESSION_KEY] = user.get_session_auth_hash()
        elsewhere.save()
        self.request.session["late"] = "yes"


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
    _path("revocable/", Revocable.as_view()),
    _path("revocable-handled/", RevocableHandled.as_view()),
    _path("result-handler-raises/", ResultHandlerRaises.as_view()),
    _path("signs-in-elsewhere/", SignsInElsewhere.as_view()),
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
    FLAGS.update(deny=False)


def _url(view: type) -> str:
    # A view of another module's test (#4168) names its own page.
    own = getattr(view, "page_url", None)
    if own:
        return own
    return {
        WithComponent: "/with-component/",
        WithSticky: "/with-sticky/",
        Revocable: "/revocable/",
        RevocableHandled: "/revocable-handled/",
        ResultHandlerRaises: "/result-handler-raises/",
        SignsInElsewhere: "/signs-in-elsewhere/",
    }.get(view, "/plain-view/")


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

#: Only a guard against a hang: every wait below is for an event, and its outcome is an ordering.
HANG_S = 60


@contextlib.contextmanager
def _background_settled() -> Iterator[asyncio.Event]:
    """An event set once the runtime has finished with a background task's result, whether it
    stored it, rendered it or discarded it.

    A socket closed before then tears the owner down while the work may still run, and such work
    keeps nothing (#4211). So a test of what finished work stores waits for this before it closes
    the connection.
    """
    from djust.runtime import ViewRuntime

    settled = asyncio.Event()
    original = ViewRuntime._execute_explicit_async_task

    async def wrapped(self: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return await original(self, *args, **kwargs)
        finally:
            settled.set()

    with mock.patch.object(ViewRuntime, "_execute_explicit_async_task", wrapped):
        yield settled


async def _settle(settled: Optional[asyncio.Event]) -> None:
    if settled is not None:
        await asyncio.wait_for(settled.wait(), HANG_S)
        settled.clear()


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
            "view": f"{view.__module__}.{view.__qualname__}",
            "url": _url(view),
            "tab": harness.TAB,
        }
    )
    frame = await comm.receive_json_from()
    assert frame["type"] == "mount", frame
    return comm


async def ws_turns(
    view: type, names: List[str], params: Any = None, settled: Optional[asyncio.Event] = None
) -> Any:
    """Page GET, socket mount, then each event; the client and per-turn saves. With
    ``settled`` (:func:`_background_settled`), each event's background work is finished with
    before the next event, and before the socket closes."""
    client = await sync_to_async(_page)(view)
    comm = await _ws_mount(view, _key(client))
    counts = []
    try:
        for name in names:
            SAVES.clear()
            frame = await harness.event(comm, name, params)
            assert legacy_type(frame) in ("patch", "noop", "html_update", "embedded_update"), frame
            await _settle(settled)
            counts.append(len(SAVES))
    finally:
        await comm.disconnect()
    return client, counts


async def sse_turns(
    view: type, names: List[str], params: Any = None, settled: Optional[asyncio.Event] = None
) -> Any:
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
        {
            "view": f"{view.__module__}.{view.__qualname__}",
            "_djust_url": _url(view),
            "_djust_tab": harness.TAB,
        },
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
            await _settle(settled)
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


async def turns(
    transport: str,
    view: type,
    names: List[str],
    params: Any = None,
    settled: Optional[asyncio.Event] = None,
) -> Any:
    if transport == "ws":
        return await ws_turns(view, names, params, settled)
    if transport == "sse":
        return await sse_turns(view, names, params, settled)
    # The fallback runs the work inside its request, before its answer: nothing to wait for.
    return await sync_to_async(http_turns)(view, names, params)


TRANSPORTS = ["ws", "sse", "http"]
INTERVALS = [pytest.param(0, id="interval-0"), pytest.param(30, id="interval-30")]


def _settings(interval: int, **extra: Any) -> Any:
    # 0 is stated: unset, the interval is 2 seconds (#4164).
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
    without changing it. Neither adds a session save on any transport. A
    fallback request with an interval set claims its tab only with a save that
    changes the stored state (#4164 item 2), so it adds none either."""
    with _settings(interval):
        _client, counts = await turns(transport, Plain, ["nothing", "read_only", "nothing"])
    assert counts == [0, 0, 0]


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
    with _settings(0), _background_settled() as settled:
        client, _ = await turns(transport, Plain, ["later"], settled=settled)
        seen = await sync_to_async(_read)(client)
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
                # No render frame when the result changed nothing the template
                # reads (#4123): the batch's ``async_complete`` ends it then.
                lambda frames: any(
                    legacy_type(f) in ("patch", "noop", "html_update", "async_complete")
                    for f in frames
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


# -- background work: refusals, removals, failures ------------------------------


def _stored_or_none(key: str) -> Optional[Dict[str, Any]]:
    store = SessionStore(key)
    if not store.exists(key):
        return None
    return {k: v for k, v in store.load().items() if not k.startswith(("_djust", "liveview_"))}


async def _ws_background(view: type, name: str) -> Any:
    """Page, socket, one event whose work runs after the reply; the frames up
    to the work's own outcome (its result, or a refusal), and the session key."""
    from djust.tests._ws_frames import receive_until

    client = await sync_to_async(_page)(view)
    key = _key(client)
    comm = await _ws_mount(view, key)
    try:
        await harness.event(comm, name)
        # A result that changed nothing the template reads sends no render
        # frame (#4123); the batch's ``async_complete`` is then its outcome.
        frames = await receive_until(
            comm,
            lambda got: any(
                f.get("source") == "async" or f.get("type") in ("error", "async_complete")
                for f in got
            ),
            what="the background outcome",
            allow_close=True,
        )
        await asyncio.sleep(0.2)
    finally:
        await comm.disconnect()
    return frames, key


@pytest.mark.parametrize("view", [Revocable, RevocableHandled], ids=["plain", "handled"])
async def test_revoked_writes_nothing(view):
    """Permission revoked while the work runs: the result's authorization fails,
    the page is told, and nothing the work wrote is stored (an assignment and an
    in-place change alike), with or without ``handle_async_result``. The
    session row is still there, so it is the refusal that withholds the write."""
    with _settings(0):
        frames, key = await _ws_background(view, "revoke_bg")
        stored = await sync_to_async(_stored_or_none)(key)
    assert [f.get("code") for f in frames if f.get("type") == "error"] == ["permission_denied"]
    assert stored == SEEDED


async def test_a_changed_principal_refuses_and_writes_nothing():
    """Another user signed into the session while the work ran: the result's
    request no longer matches the mount's identity, so it is refused. The
    session row still exists (only the principal changed), so it is the
    refusal, not a missing row, that keeps the work's write out of it."""
    with _settings(0):
        frames, key = await _ws_background(SignsInElsewhere, "sign_in_bg")
        stored = await sync_to_async(_stored_or_none)(key)
    assert [f.get("code") for f in frames if f.get("type") == "error"] == ["permission_denied"]
    assert stored is not None
    assert {k: v for k, v in stored.items() if not k.startswith("_auth")} == SEEDED


async def test_background_removal_is_stored():
    with _settings(0):
        _frames, key = await _ws_background(Revocable, "del_bg")
        stored = await sync_to_async(_stored_or_none)(key)
    assert stored == {"gone": 1}


@pytest.mark.parametrize(
    "view",
    [Revocable, RevocableHandled, ResultHandlerRaises],
    ids=["plain", "handled", "handler-raises"],
)
@pytest.mark.parametrize("transport", TRANSPORTS)
async def test_failed_work_keeps_what_it_wrote(transport, view):
    """One rule for work that writes the session and then raises: the write is
    kept, with or without ``handle_async_result`` and on every transport (the
    fallback runs the work inside its request and always kept it). The work's
    other effects stand as well; only the view's state is not saved."""
    # The work is finished with before the connection closes: work the teardown cancels keeps
    # nothing (decided by #4211, pinned below).
    with _settings(0), _background_settled() as settled:
        client, _ = await turns(transport, view, ["fail_bg"], settled=settled)
        seen = await sync_to_async(_read)(client)
    assert seen == {"cart": ["a"], "gone": 1, "late": "yes"}


# -- the fallback's merged save ------------------------------------------------------


class ChangesState(Plain):
    @event_handler()
    def inc_while_another_request_writes(self, **kwargs):
        # Another request stores a key while this one is being handled.
        other = SessionStore(self.request.session.session_key)
        other["other"] = "kept"
        other.save()
        self.count += 1


urlpatterns.append(_path("changes-state/", ChangesState.as_view()))


def test_fallback_keeps_a_write_made_during_the_request():
    """The fallback's state save merges into the session as stored when it
    writes, so a key another request stored after this request loaded the
    session survives both that save and Django's whole-session save after it
    (which writes the reconciled dict). This is what the extra session read
    of a tracked fallback request buys."""
    with _settings(0):
        client = Client()
        assert client.get("/seed/").status_code == 200
        assert client.get("/changes-state/").status_code == 200
        response = client.post(
            "/changes-state/",
            data="{}",
            content_type="application/json",
            HTTP_X_DJUST_EVENT="inc_while_another_request_writes",
            HTTP_X_DJUST_TAB=harness.TAB,
        )
        assert response.status_code == 200, response.content[:300]
        seen = _read(client)
    assert seen == {"cart": ["a"], "gone": 1, "other": "kept"}


# -- #4211: work cancelled by a disconnect keeps nothing -------------------------------------


@contextlib.contextmanager
def _held_result_authorization() -> Iterator[None]:
    """Hold the result's authorization (the call from the result step,
    ``complete``) until ``HOLD["gate"]``, absorbing the teardown's
    cancellation. It stands in for any await after the result's authorization
    that does not stop at the cancellation."""
    import sys

    from djust.runtime import ViewRuntime

    real = ViewRuntime.authorize_explicit_turn

    async def held(self: Any, view: Any, *args: Any, **kwargs: Any) -> Any:
        from_result = sys._getframe(1).f_code.co_name == "complete"
        outcome = await real(self, view, *args, **kwargs)
        if from_result:
            HOLD["reached"].set()
            await _absorbing_wait(HOLD["gate"])
        return outcome

    with mock.patch.object(ViewRuntime, "authorize_explicit_turn", held):
        yield


async def _teardown_while_held(transport: str, view: type, event_name: str) -> Any:
    """Send ``event_name``, wait for the hold, tear the connection down, release
    the hold and wait until the runtime is done with the result. Returns the
    client and the saves made after the teardown."""
    from djust.sse import DjustSSEMessageView, DjustSSEStreamView, _sse_sessions

    HOLD.update(reached=asyncio.Event(), gate=asyncio.Event())
    with _background_settled() as settled:
        client = await sync_to_async(_page)(view)
        key = _key(client)
        if transport == "ws":
            comm = await _ws_mount(view, key)
            try:
                await harness.event(comm, event_name)
                await asyncio.wait_for(HOLD["reached"].wait(), HANG_S)
            finally:
                await comm.disconnect()
        else:
            from django.contrib.auth import get_user
            from django.utils.functional import SimpleLazyObject

            def request(method: str, target: str, body: Any) -> Any:
                factory = RequestFactory()
                if method == "GET":
                    made = factory.get(target, data=body)
                else:
                    made = factory.post(
                        target, data=json.dumps(body), content_type="application/json"
                    )
                made.session = SessionStore(key)
                made.user = SimpleLazyObject(lambda: get_user(made))
                made.tenant = None
                return made

            sid = str(uuid.uuid4())
            stream = await sync_to_async(request)(
                "GET",
                f"/djust/sse/{sid}/",
                {
                    "view": f"{view.__module__}.{view.__qualname__}",
                    "_djust_url": _url(view),
                    "_djust_tab": harness.TAB,
                },
            )
            response = await DjustSSEStreamView().get(stream, session_id=sid)
            assert response.status_code == 200
            body = {"type": "event", "event": event_name, "params": {}}
            posted = await sync_to_async(request)("POST", f"/djust/sse/{sid}/message/", body)
            answer = await DjustSSEMessageView().post(posted, session_id=sid)
            assert answer.status_code in (200, 202, 204), answer.content
            await asyncio.wait_for(HOLD["reached"].wait(), HANG_S)
            session = _sse_sessions.pop(sid)
            session.shutdown()
        saves = len(SAVES)
        HOLD["gate"].set()
        await _settle(settled)
        held = HOLD.get("save")
        if held is not None:
            # A save whose start was held runs on after the turn stopped waiting.
            await asyncio.wait({held}, timeout=HANG_S)
        return client, len(SAVES) - saves


@pytest.mark.parametrize(
    "view",
    [Revocable, RevocableHandled, ResultHandlerRaises],
    ids=["plain", "handled", "handler-raises"],
)
@pytest.mark.parametrize("arm", ["fail_bg", "ok_bg"])
@pytest.mark.parametrize("transport", ["ws", "sse"])
async def test_cancelled_result_keeps_nothing(transport, arm, view):
    """#4211: the disconnect's teardown cancelled the work after its result was
    authorized; the result step goes on, but stores no session write (and no
    state, no frame): the work no longer belongs to a live view."""
    with _settings(0), _held_result_authorization():
        client, saves_after = await _teardown_while_held(transport, view, arm)
        seen = await sync_to_async(_read)(client)
    assert "late" not in seen, seen
    assert saves_after == 0


@pytest.mark.parametrize("interval", INTERVALS)
async def test_cancelled_result_keeps_nothing_with_deferred_saves(interval):
    """#4211: nor does the disconnect's flush of deferred state store it."""
    with _settings(interval), _held_result_authorization():
        client, saves_after = await _teardown_while_held("ws", Revocable, "fail_bg")
        seen = await sync_to_async(_read)(client)
    assert "late" not in seen, seen
    assert saves_after == 0


@pytest.mark.parametrize("arm", ["held_ok_bg", "held_fail_bg"])
@pytest.mark.parametrize("transport", ["ws", "sse"])
async def test_work_finishing_after_teardown_keeps_nothing(transport, arm):
    """#4211, the issue's ordering: the work itself is held until the teardown
    has happened, then finishes (or fails). What it wrote is not stored."""
    with _settings(0):
        client, saves_after = await _teardown_while_held(transport, Revocable, arm)
        seen = await sync_to_async(_read)(client)
    assert "late" not in seen, seen
    assert saves_after == 0


@contextlib.contextmanager
def _held_save_start() -> Iterator[None]:
    """Hold the start of the next save the work arms (``HOLD["armed"]``) on the
    loop until ``HOLD["gate"]``: the root save's thread step queued behind a
    busy sync thread while the disconnect's teardown lands (#4211 review S1)."""
    from djust import runtime

    real = runtime._spawn_save

    def spawn(run: Any) -> Any:
        if not HOLD.pop("armed", None):
            return real(run)

        async def held() -> None:
            HOLD["reached"].set()
            await HOLD["gate"].wait()
            await real(run)

        HOLD["save"] = asyncio.ensure_future(held())
        return HOLD["save"]

    with mock.patch.object(runtime, "_spawn_save", spawn):
        yield


@contextlib.contextmanager
def _held_deferred_check() -> Iterator[None]:
    """Hold the deferred path's binding check (its one thread step) the work
    arms, absorbing the teardown's cancellation (#4211 review S1)."""
    from djust import runtime

    real = runtime.sync_to_async

    def s2a(func: Any, *args: Any, **kwargs: Any) -> Any:
        wrapped = real(func, *args, **kwargs)
        if getattr(func, "__name__", "") != "check" or not HOLD.pop("armed", None):
            return wrapped

        async def held(*a: Any, **kw: Any) -> Any:
            HOLD["reached"].set()
            await _absorbing_wait(HOLD["gate"])
            return await wrapped(*a, **kw)

        return held

    with mock.patch.object(runtime, "sync_to_async", s2a):
        yield


@pytest.mark.parametrize("transport", ["ws", "sse"])
async def test_success_save_started_after_teardown_keeps_nothing(transport):
    """#4211 review S1: the result's commit passed its checks, but its save's
    thread step started only after the disconnect's teardown. The save checks
    the work still belongs to the live view as its first step: nothing is
    stored."""
    HOLD.pop("save", None)
    with _settings(0), _held_save_start():
        client, saves_after = await _teardown_while_held(transport, Revocable, "armed_ok_bg")
        seen = await sync_to_async(_read)(client)
    HOLD.pop("save", None)
    assert "late" not in seen, seen
    assert saves_after == 0


async def test_deferred_success_after_teardown_keeps_nothing():
    """#4211 review S1, the deferred-save path: the work is cancelled
    (``cancel_async_all``: its generation moves on, the view stays live) while
    the result's binding check waits for its thread. The result leaves no
    change owed, so the close's flush stores nothing of it."""
    HOLD.update(reached=asyncio.Event(), gate=asyncio.Event())
    with _settings(30), _held_deferred_check(), _background_settled() as settled:
        client = await sync_to_async(_page)(Revocable)
        comm = await _ws_mount(Revocable, _key(client))
        try:
            # The starting turn changes nothing; let any flush it owes run first.
            await harness.event(comm, "armed_inc_bg")
            await asyncio.wait_for(HOLD["reached"].wait(), HANG_S)
            HOLD.pop("view").cancel_async_all()
            HOLD["gate"].set()
            await _settle(settled)
            SAVES.clear()
        finally:
            await comm.disconnect()
    assert SAVES == []
