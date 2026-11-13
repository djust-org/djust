"""#4098: back-to-back events on ``persist="server"`` state all take effect.

An explicit turn's success frame is withheld until storage has the turn's
state. Until #4098 the turn waited 150 ms for that save and then answered a
transient ``state_error`` instead, with a full-HTML catch-up later: one slow
write (an fsync on a busy disk, a loaded process) lost the event's own frame,
and the catch-up's full HTML reset the unsent value of a field that did not
have focus (a focused field keeps its text and caret: checked in a browser).

Now the turn waits 2 s by default for its own save under the runtime's event
lock, so the next event starts after it. The contract these tests pin, per
transport, for a save that lands within the deadline:

* N events sent without waiting for replies all run, in order;
* each gets its own frame, in order, and none gets an error;
* storage ends with the final state.

HOW THE INTERLEAVING IS FORCED. ``SaveGate`` wraps ``SessionStore.save`` and
holds a chosen save inside storage until the test releases it. The test waits
for the gate to be ENTERED (an event), sends the rest of the burst, checks
that nothing is answered while the save is held (``HOLD_S``, an absence
window: a slow machine can only make it miss a frame, never fail a correct
run), then releases. Nothing is ordered by a sleep.

WHICH DEADLINE. The tests in ``AT_THE_DEFAULT`` run on the shipped default,
so restoring 150 ms fails them. The others set the deadline to the 10 s cap:
they pin the ordering, and must not fail on a runner that stalls for longer
than the default while a save is held. What happens PAST the deadline is in
``test_slow_save_backpressure_4098.py``.

This module deliberately does NOT start with ``test_exposure_`` (the conftest
changes the save bound for those modules, #3130).
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
import uuid
from typing import Any, List, Optional

import pytest
from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory, override_settings

from djust import LiveView, event_handler
from djust import runtime as runtime_module
from djust._exposure_sessions import MAX_EXPLICIT_STATE_SAVE_TIMEOUT_S, server_state_adapter
from djust.components.descriptors.base import LiveComponent, TypedState
from djust.decorators import state
from djust.runtime import ViewRuntime
from djust.tests._ws_frames import drain_extra, has_frame, receive_until
from djust.tests.test_exposure_runtime import make_request
from djust.tests.test_runtime_state_save_tt_1894 import MockTransport
from djust.websocket import LiveViewConsumer

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.pr_tier]

MODULE = __name__

#: The absence window: how long a held save is watched for a premature answer.
#: Longer than the 150 ms the old deadline allowed, so the old behaviour (an
#: error frame while the save is still held) shows up inside it.
HOLD_S = 0.5

#: Tests that run on the shipped default deadline (see the module docstring).
AT_THE_DEFAULT = {
    "test_a_burst_over_a_websocket_while_the_first_save_is_slow",
    "test_a_burst_over_sse_while_the_first_save_is_slow",
    "test_the_default_wait_is_two_seconds",
}

#: Handler runs, in order, across every view here (cleared per test).
RAN: List[Any] = []


class Counter(LiveView):
    exposure_policy = "explicit"
    template = '<div dj-root><span id="count">{{ count }}</span></div>'
    count = state(0, persist="server")

    @event_handler()
    def increment(self, **kwargs: Any) -> None:
        self.count += 1
        RAN.append(self.count)


class ClientCounter(LiveView):
    """A server-persisted counter beside a client-persisted field."""

    exposure_policy = "explicit"
    template = '<div dj-root><span id="count">{{ count }}</span><i>{{ draft }}</i></div>'
    count = state(0, persist="server")
    draft = state("", persist="client", client=True)

    @event_handler()
    def increment(self, **kwargs: Any) -> None:
        self.count += 1
        self.draft = "d%d" % self.count
        RAN.append(self.count)


class Tally(LiveComponent):
    class State(TypedState):
        n: int = 0

    template = '<b class="tally">{{ n }}</b>'

    @event_handler()
    def bump(self, **kwargs: Any) -> None:
        self.n += 1
        RAN.append(self.n)


class WithComponent(LiveView):
    exposure_policy = "explicit"
    template = '<div dj-root><span id="count">{{ count }}</span>{{ tally }}</div>'
    count = state(0, persist="server")
    tally = Tally()


@pytest.fixture(autouse=True)
def staged(request, monkeypatch):
    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)
    RAN.clear()
    extra = {}
    if request.node.originalname not in AT_THE_DEFAULT:
        extra["DJUST_EXPLICIT_STATE_SAVE_TIMEOUT"] = MAX_EXPLICIT_STATE_SAVE_TIMEOUT_S
    with override_settings(
        LIVEVIEW_ALLOWED_MODULES=[MODULE, "djust"], DEBUG=False, DJUST_TENANTS=None, **extra
    ):
        yield


# ------------------------------------------------------------------ #
# The gate
# ------------------------------------------------------------------ #


class SaveGate:
    """Hold, fail and record ``SessionStore.save`` calls, by call number.

    ``hold`` saves block inside storage until :meth:`release`. ``fail`` saves
    raise after they are released (or at once when not held). ``landed``
    lists, in the order the writes finished, the count each one stored.
    """

    def __init__(self, hold=(), fail=()) -> None:
        self.hold, self.fail = set(hold), set(fail)
        self.calls = 0
        self.entered = threading.Event()
        self._release = threading.Event()
        self.settled = threading.Event()
        self.landed: List[Optional[int]] = []
        self._lock = threading.Lock()
        self._original = SessionStore.save

    def __enter__(self) -> "SaveGate":
        gate = self

        def save(store, *args, **kwargs):
            with gate._lock:
                gate.calls += 1
                number = gate.calls
            if number in gate.hold:
                gate.entered.set()
                gate._release.wait(timeout=60)  # a hang guard, not a timing bound
            try:
                if number in gate.fail:
                    raise OSError("STORE_SENTINEL")
                result = gate._original(store, *args, **kwargs)
                with gate._lock:
                    gate.landed.append(_count_in(store))
                return result
            finally:
                if number in gate.hold:
                    gate.settled.set()

        SessionStore.save = save
        return self

    def __exit__(self, *exc: Any) -> None:
        self._release.set()
        SessionStore.save = self._original

    def release(self) -> None:
        self._release.set()

    async def wait_entered(self) -> None:
        assert await asyncio.to_thread(self.entered.wait, 60), "no save reached storage"

    async def wait_settled(self) -> None:
        assert await asyncio.to_thread(self.settled.wait, 60), "the held save never finished"


def _count_in(store) -> Optional[int]:
    """The ``count`` a session store object is about to hold, if any."""
    for key, value in getattr(store, "_session_cache", {}).items():
        if str(key).startswith("_djust_explicit_") and isinstance(value, dict):
            found = re.search(r'"count": (\d+)', json.dumps(value))
            if found:
                return int(found.group(1))
    return None


async def _stored(key: str, view_class=Counter, path: str = "/runtime-explicit/"):
    def read():
        request = RequestFactory().get(path)
        request.user, request.tenant = AnonymousUser(), None
        request.session = SessionStore(key)
        adapter = server_state_adapter(view_class(), request)
        stored = adapter.load()
        return None if stored is None else stored["count"]

    return await sync_to_async(read)()


def _shown(frame) -> Optional[int]:
    """The number a rendered frame puts on the page, or None."""
    if frame.get("html"):
        found = re.search(r">(\d+)<", frame["html"])
        return int(found.group(1)) if found else None
    for patch in frame.get("patches") or ():
        if patch.get("type") == "SetText":
            return int(patch["text"])
    return None


def _answers(frames) -> list:
    """(type, ref, shown) of every frame that answers an event."""
    kinds = {"patch", "html_update", "error", "noop", "embedded_update"}
    return [(f["type"], f.get("ref"), _shown(f)) for f in frames if f.get("type") in kinds]


def _event(ref: int, name: str = "increment", **params: Any) -> dict:
    return {"type": "event", "event": name, "params": params, "ref": ref}


# ------------------------------------------------------------------ #
# The default (a mechanical pin of the fix itself)
# ------------------------------------------------------------------ #


def test_the_default_wait_is_two_seconds():
    assert runtime_module.EVENT_STATE_SAVE_TIMEOUT_S == 2.0
    assert runtime_module.explicit_state_save_timeout() == 2.0
    # Below the stuck cap: past it, the deferral path and its escalation run.
    assert runtime_module.EVENT_STATE_SAVE_TIMEOUT_S < MAX_EXPLICIT_STATE_SAVE_TIMEOUT_S
    with override_settings(DJUST_EXPLICIT_STATE_SAVE_TIMEOUT=0.5):
        assert runtime_module.explicit_state_save_timeout() == 0.5


# ------------------------------------------------------------------ #
# WebSocket
# ------------------------------------------------------------------ #


async def _open(view: str = "Counter", key: Optional[str] = None):
    request = await sync_to_async(make_request)(key)
    socket = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    socket.scope.update(session=request.session, user=request.user, tenant=None)
    assert (await socket.connect())[0]
    await socket.receive_json_from()
    await socket.send_json_to(
        {"type": "mount", "view": f"{MODULE}.{view}", "url": "/runtime-explicit/"}
    )
    mounted = await socket.receive_json_from()
    assert mounted["type"] == "mount", mounted
    return socket, request.session.session_key, mounted


@pytest.mark.parametrize("n", [2, 5])
@pytest.mark.parametrize("view", ["Counter", "ClientCounter"])
async def test_a_burst_over_a_websocket_while_the_first_save_is_slow(n, view):
    """N events, none awaited; the first one's save is held in storage."""
    socket, key, _ = await _open(view)
    try:
        with SaveGate(hold={1}) as gate:
            await socket.send_json_to(_event(1))
            await gate.wait_entered()
            for ref in range(2, n + 1):
                await socket.send_json_to(_event(ref))
            # The save is the turn's commit: nothing is answered before it.
            assert await socket.receive_nothing(timeout=HOLD_S, interval=0.01)
            assert RAN == [1], "the next event must not start before the save settles"
            gate.release()
            frames = await receive_until(socket, has_frame(ref=n), what=f"the frame of event {n}")
            frames += await drain_extra(socket)
    finally:
        await socket.disconnect()

    assert RAN == list(range(1, n + 1))
    assert _answers(frames) == [("patch", ref, ref) for ref in range(1, n + 1)], frames
    assert gate.landed == list(range(1, n + 1)), "one write per turn, oldest first"
    assert await _stored(key, globals()[view]) == n


async def _mount_runtime():
    request = await sync_to_async(make_request)()
    transport = MockTransport()
    transport.build_request = lambda: request

    async def fresh(view):
        return await sync_to_async(make_request)(request.session.session_key)

    transport.explicit_event_request = fresh
    runtime = ViewRuntime(transport)
    await runtime.dispatch_mount(
        {"type": "mount", "view": f"{MODULE}.Counter", "url": request.path}
    )
    assert not transport.errors, transport.errors
    transport.sent.clear()
    return runtime, transport, request


async def test_fifty_events_through_the_runtime_with_a_slow_first_save():
    """The same burst at N=50, below the socket's message rate limiter.

    Fifty frames sent at once trip the consumer's default limiter (burst 20),
    which is its job and not this contract's, so this drives the runtime the
    consumer hands events to.
    """
    runtime, transport, request = await _mount_runtime()

    with SaveGate(hold={1}) as gate:
        turns = [asyncio.ensure_future(runtime.dispatch_event(_event(1)))]
        await gate.wait_entered()
        turns += [asyncio.ensure_future(runtime.dispatch_event(_event(r))) for r in range(2, 51)]
        done, _ = await asyncio.wait(turns, timeout=HOLD_S)
        assert not done, "no turn may finish while the first save is held"
        assert not transport.sent and not transport.errors
        gate.release()
        await asyncio.gather(*turns)

    assert RAN == list(range(1, 51))
    assert not transport.errors, transport.errors
    assert _answers(transport.sent) == [("patch", ref, ref) for ref in range(1, 51)]
    assert gate.landed == list(range(1, 51))
    assert await _stored(request.session.session_key) == 50


async def test_waiting_the_cap_ends_in_the_reload_error_not_a_promise():
    """A turn that waited the stuck cap for storage has no update to promise
    (a deadline configured at 10 s).

    A shorter deadline, the default included, gets the transient answer and
    its catch-up (#3200); the catch-up is cancelled here, it is not under test.
    """
    runtime, transport, _ = await _mount_runtime()
    view = runtime.view_instance

    await runtime._explicit_save_deferred(
        view, {}, child=False, waited=MAX_EXPLICIT_STATE_SAVE_TIMEOUT_S
    )
    [error] = transport.errors
    assert error["code"] == "state_error" and not error.get("transient"), error
    assert "reload" in error["error"].lower()
    assert runtime._explicit_catch_up is None

    transport.errors.clear()
    runtime._explicit_deferrals = 0
    await runtime._explicit_save_deferred(view, {}, child=False, waited=0.05)
    [error] = transport.errors
    assert error.get("transient") is True, error
    runtime._explicit_catch_up.cancel()


async def test_a_held_save_past_a_short_cap_answers_the_reload_error(monkeypatch):
    """End to end, with the cap and the default lowered together."""
    from djust import _exposure_sessions

    monkeypatch.setattr(_exposure_sessions, "MAX_EXPLICIT_STATE_SAVE_TIMEOUT_S", 0.05)
    monkeypatch.setattr(runtime_module, "EVENT_STATE_SAVE_TIMEOUT_S", 0.05)
    runtime, transport, request = await _mount_runtime()

    with SaveGate(hold={1}) as gate:
        await runtime.dispatch_event(_event(1))
        [error] = transport.errors
        assert error["code"] == "state_error" and not error.get("transient"), error
        assert not _answers(transport.sent)[1:], transport.sent
        assert runtime._explicit_catch_up is None
        gate.release()
        await gate.wait_settled()

    # The write landed late; the next turn waits for nothing and is full HTML.
    transport.sent.clear()
    transport.errors.clear()
    await runtime.dispatch_event(_event(2))
    assert not transport.errors, transport.errors
    assert _answers(transport.sent) == [("html_update", 2, 2)], transport.sent
    assert await _stored(request.session.session_key) == 2


async def test_a_burst_of_component_events_while_the_first_save_is_slow():
    socket, key, mounted = await _open("WithComponent")
    component_id = re.search(r'data-component-id="([^"]+)"', mounted["html"]).group(1)
    try:
        with SaveGate(hold={1}) as gate:
            await socket.send_json_to(_event(1, "bump", component_id=component_id))
            await gate.wait_entered()
            for ref in (2, 3):
                await socket.send_json_to(_event(ref, "bump", component_id=component_id))
            assert await socket.receive_nothing(timeout=HOLD_S, interval=0.01)
            gate.release()
            frames = await receive_until(socket, has_frame(ref=3), what="the third frame")
            frames += await drain_extra(socket)
    finally:
        await socket.disconnect()

    assert RAN == [1, 2, 3]
    assert [(kind, ref) for kind, ref, _ in _answers(frames)] == [
        ("patch", 1),
        ("patch", 2),
        ("patch", 3),
    ], frames
    assert [shown for _, _, shown in _answers(frames)] == [1, 2, 3]


async def test_a_burst_of_embedded_child_events_while_the_first_save_is_slow(settings):
    """A sticky child's events save the child tree through the same wait."""
    from djust.tests import test_exposure_child_events as child_events

    settings.DJUST_LIVE_RENDER_ALLOWED_MODULES = [child_events.__name__]
    runtime, transport, request = await child_events.mount()
    child = runtime.view_instance._get_child_view("menu")
    transport.sent.clear()
    event = {"type": "event", "event": "increment", "params": {"view_id": "menu"}}

    with SaveGate(hold={1}) as gate:
        turns = [asyncio.ensure_future(runtime.dispatch_event(dict(event)))]
        await gate.wait_entered()
        turns += [asyncio.ensure_future(runtime.dispatch_event(dict(event))) for _ in range(2)]
        done, _ = await asyncio.wait(turns, timeout=HOLD_S)
        assert not done and not transport.sent and not transport.errors
        gate.release()
        await asyncio.gather(*turns)

    assert not transport.errors, transport.errors
    updates = [f for f in transport.sent if f.get("type") == "embedded_update"]
    assert [re.search(r"Count=(\d+)", json.dumps(f)).group(1) for f in updates] == ["2", "3", "4"]
    assert child.count == 4 and child._handler_calls == 3
    restored, _, _ = await child_events.mount(request.session.session_key)
    assert restored.view_instance._get_child_view("menu").count == 4


async def test_a_failed_save_then_the_next_event():
    """Event 1's save fails after being held; event 2 was sent meanwhile.

    Event 1 is answered with the reload error (its failure is seen by its own
    turn, not reported later as "still being saved"). Event 2's handler runs
    once, on the value event 1 left on the view; its save writes both changes
    and its frame is full HTML. Nothing is lost and nothing runs twice.
    """
    socket, key, _ = await _open()
    try:
        with SaveGate(hold={1}, fail={1}) as gate:
            await socket.send_json_to(_event(1))
            await gate.wait_entered()
            await socket.send_json_to(_event(2))
            assert await socket.receive_nothing(timeout=HOLD_S, interval=0.01)
            gate.release()
            frames = await receive_until(socket, has_frame(ref=2), what="the frame of event 2")
            frames += await drain_extra(socket)
    finally:
        await socket.disconnect()

    assert RAN == [1, 2]
    [error] = [f for f in frames if f.get("type") == "error"]
    assert error["code"] == "state_error" and not error.get("transient"), error
    assert "reload" in error["error"].lower()
    assert "STORE_SENTINEL" not in json.dumps(frames)
    assert _answers(frames)[1:] == [("html_update", 2, 2)], frames
    assert frames.index(error) == 0
    assert gate.landed == [2]
    assert await _stored(key) == 2


async def test_a_disconnect_while_the_save_is_held():
    """The browser goes away mid-save: the write still lands, and a reconnect has it.

    The consumer handles the close after the turn it is in, so the turn is not
    cancelled: it waits for its save and then has nobody to send the frame to.
    """
    socket, key, _ = await _open()
    with SaveGate(hold={1}) as gate:
        await socket.send_json_to(_event(1))
        await gate.wait_entered()
        await socket.send_input({"type": "websocket.disconnect", "code": 1001})
        gate.release()
        await gate.wait_settled()
        await socket.wait()
    assert RAN == [1]
    assert gate.landed == [1]
    assert await _stored(key) == 1

    again, _, mounted = await _open(key=key)
    try:
        assert ">1<" in mounted["html"], mounted
        await again.send_json_to(_event(1))
        frames = await receive_until(again, has_frame(ref=1), what="the next event's frame")
    finally:
        await again.disconnect()
    # The first update after a restoring mount is full HTML (#1977).
    assert _answers(frames) == [("html_update", 1, 2)], frames
    assert await _stored(key) == 2


async def test_two_sockets_of_one_session_on_one_route():
    """Each page's saves stay ordered; storage ends with the last write made.

    A's first save is held while B's event is sent. Two pages of one session
    on one route share one stored state, so the last write wins; what must
    hold is that every socket gets its own frames in order with no error, and
    that neither socket's older write lands after its newer one.
    """
    a, key, _ = await _open()
    b, _, _ = await _open(key=key)
    try:
        with SaveGate(hold={1}) as gate:
            await a.send_json_to(_event(1))
            await gate.wait_entered()
            await a.send_json_to(_event(2))
            await b.send_json_to(_event(1))
            assert await a.receive_nothing(timeout=HOLD_S, interval=0.01)
            gate.release()
            a_frames = await receive_until(a, has_frame(ref=2), what="A's second frame")
            b_frames = await receive_until(b, has_frame(ref=1), what="B's frame")
            a_frames += await drain_extra(a)
            b_frames += await drain_extra(b)
    finally:
        await a.disconnect()
        await b.disconnect()

    assert _answers(a_frames) == [("patch", 1, 1), ("patch", 2, 2)], a_frames
    assert _answers(b_frames) == [("patch", 1, 1)], b_frames
    # A wrote 1 then 2, B wrote 1, in some interleaving with A's 1 first.
    assert sorted(gate.landed) == [1, 1, 2] and gate.landed[0] == 1
    assert gate.landed.index(2) > 0
    assert await _stored(key) == gate.landed[-1]


# ------------------------------------------------------------------ #
# SSE
# ------------------------------------------------------------------ #


def _sse_request(method: str, url: str, body: dict, key: str):
    factory = RequestFactory()
    if method == "GET":
        request = factory.get(url, data=body)
    else:
        request = factory.post(url, data=json.dumps(body), content_type="application/json")
    request.session = SessionStore(key)
    request.user, request.tenant = AnonymousUser(), None
    return request


async def test_a_burst_over_sse_while_the_first_save_is_slow():
    from django.urls import path

    from djust.sse import DjustSSEMessageView, DjustSSEStreamView, _sse_sessions

    class Urls:
        urlpatterns = [path("counter/", Counter.as_view())]

    key = (await sync_to_async(make_request)()).session.session_key
    sid = str(uuid.uuid4())
    n = 5

    async def post(ref: int):
        request = await sync_to_async(_sse_request)(
            "POST", f"/djust/sse/{sid}/message/", _event(ref), key
        )
        return await DjustSSEMessageView().post(request, session_id=sid)

    with override_settings(ROOT_URLCONF=Urls):
        stream = await sync_to_async(_sse_request)(
            "GET",
            f"/djust/sse/{sid}/",
            {"view": f"{MODULE}.Counter", "_djust_url": "/counter/"},
            key,
        )
        assert (await DjustSSEStreamView().get(stream, session_id=sid)).status_code == 200
        session = _sse_sessions[sid]
        try:
            while not session.queue.empty():
                session.queue.get_nowait()  # the mount frame
            with SaveGate(hold={1}) as gate:
                posts = [asyncio.ensure_future(post(1))]
                await gate.wait_entered()
                posts += [asyncio.ensure_future(post(ref)) for ref in range(2, n + 1)]
                done, _ = await asyncio.wait(posts, timeout=HOLD_S)
                assert not done, "no event POST may answer while the first save is held"
                assert session.queue.empty()
                gate.release()
                responses = await asyncio.gather(*posts)
            frames = []
            while not session.queue.empty():
                frame = session.queue.get_nowait()
                if frame is not None:
                    frames.append(frame)
        finally:
            _sse_sessions.pop(sid, None)

    assert [r.status_code for r in responses] == [200] * n
    assert RAN == list(range(1, n + 1))
    answers = _answers(frames)
    assert [(kind, shown) for kind, _, shown in answers] == [
        ("patch", i) for i in range(1, n + 1)
    ], frames
    assert sorted(ref for _, ref, _ in answers) == list(range(1, n + 1))
    assert gate.landed == list(range(1, n + 1))
    assert await _stored(key, path="/counter/") == n


# ------------------------------------------------------------------ #
# The HTTP page-POST fallback (a control: its save was never deferred)
# ------------------------------------------------------------------ #


@pytest.mark.parametrize("n", [2, 5, 50])
def test_get_then_fallback_posts(n):
    """GET, then N event POSTs one after another, as the client sends them.

    The fallback saves inside the request with no deadline, so it never
    produced the transient error; this pins that it keeps counting.
    """
    initial = RequestFactory().get("/counter/")
    initial.user, initial.tenant = AnonymousUser(), None
    initial.session = SessionStore()
    initial.session.create()
    assert Counter().get(initial).status_code == 200

    shown = []
    for _ in range(n):
        request = RequestFactory().post(
            "/counter/", data="{}", content_type="application/json", HTTP_X_DJUST_EVENT="increment"
        )
        request.user, request.tenant = AnonymousUser(), None
        request.session = SessionStore(initial.session.session_key)
        response = Counter().post(request)
        assert response.status_code == 200
        body = json.loads(response.content)
        assert body.get("type") != "error" and "error" not in body, body
        shown.append(_shown(body))

    assert RAN == list(range(1, n + 1))
    assert shown == list(range(1, n + 1))
    assert _stored_sync(initial.session.session_key) == n


def _stored_sync(key: str):
    request = RequestFactory().get("/counter/")
    request.user, request.tenant = AnonymousUser(), None
    request.session = SessionStore(key)
    stored = server_state_adapter(Counter(), request).load()
    return None if stored is None else stored["count"]
