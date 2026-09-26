"""LiveView sessions on different event loops of one process (#3128).

``djust serve --loops N`` puts each WebSocket session on the loop that accepted
it. These tests connect real ``LiveViewConsumer`` sessions on two loops running
in two threads, with ``djust.layers.MultiLoopInMemoryChannelLayer``, and check
that sessions in one room get each other's pushes, in order. They also cover
the two pieces of state that outlive one request and so can be reached from
another loop: an SSE session and the db_notify listener.
"""

from __future__ import annotations

import asyncio
import contextvars
import threading
import time

import pytest
from asgiref.sync import sync_to_async
from django.test import override_settings

from djust import LiveView
from djust.config import config as djust_config
from djust import multiloop
from djust.decorators import event_handler
from djust.push import push_to_view

pytest.importorskip("channels")

MOD = __name__
VIEW = f"{MOD}._RoomView"

_SEEN: dict = {}
_SEEN_LOCK = threading.Lock()


class _RoomView(LiveView):
    template = f'<div dj-root dj-view="{MOD}._RoomView">{{{{ tag }}}}:{{{{ count }}}}</div>'

    def mount(self, request, **kwargs):
        self.tag = request.GET.get("tag", "?")
        self.room = request.GET.get("room", "lobby")
        self.push_scope = self.room
        self.count = 0

    def handle_seq(self, src="", n=0, **kwargs):
        with _SEEN_LOCK:
            _SEEN.setdefault(self.tag, []).append((src, n))
        self.count += 1

    @event_handler()
    def shout(self, n: int = 0, **kwargs):
        # A sync handler pushing to its room, as a game does on a key press:
        # async_to_sync returns to THIS session's loop, and the layer carries
        # the push to sessions on the other loop.
        push_to_view(VIEW, handler="handle_seq", payload={"src": self.tag, "n": n}, scope=self.room)


class LoopThread:
    def __init__(self, name: str) -> None:
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, name=name, daemon=True)
        self.thread.start()

    def submit(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def run(self, coro, timeout: float = 20):
        return self.submit(coro).result(timeout)

    def stop(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(5)
        self.loop.close()


@pytest.fixture
def two_loops():
    loops = [LoopThread("djust-loop-0"), LoopThread("djust-loop-1")]
    yield loops
    for lt in loops:
        lt.stop()


@pytest.fixture
def multi_loop_layer(settings):
    from channels.layers import channel_layers

    settings.CHANNEL_LAYERS = {"default": {"BACKEND": "djust.layers.MultiLoopInMemoryChannelLayer"}}
    channel_layers.backends.pop("default", None)
    yield channel_layers["default"]
    channel_layers.backends.pop("default", None)


@pytest.fixture(params=[None, 2], ids=["stock", "pool"])
def pool(request, monkeypatch):
    from djust import worker_pool

    monkeypatch.setitem(djust_config._config, "worker_threads", request.param)
    monkeypatch.setattr(worker_pool, "_pool", [])
    _SEEN.clear()
    yield request.param
    _SEEN.clear()


async def _connect(query: str):
    from channels.testing import WebsocketCommunicator
    from django.contrib.sessions.backends.db import SessionStore

    from djust.websocket import LiveViewConsumer

    def _create_session():
        s = SessionStore()
        s.create()
        return s.session_key

    key = await sync_to_async(_create_session)()

    class _ScopeSession:
        session_key = key

    communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    communicator.scope["session"] = _ScopeSession()
    connected, _ = await communicator.connect()
    assert connected
    await communicator.receive_json_from(timeout=5)  # connect frame
    await communicator.send_json_to({"type": "mount", "view": VIEW, "url": f"/r/?{query}"})
    for _ in range(8):
        frame = await communicator.receive_json_from(timeout=5)
        if frame.get("type") == "mount":
            return communicator
    raise AssertionError(f"no mount frame: {frame!r}")


async def _shout(communicator, count: int):
    for n in range(count):
        await communicator.send_json_to(
            {"type": "event", "event": "shout", "params": {"n": n}, "ref": n + 1}
        )
        await asyncio.sleep(0.02)


async def _drain(communicator, seconds: float):
    """Read frames for ``seconds`` without cancelling the application."""
    deadline = time.monotonic() + seconds
    frames = []
    while time.monotonic() < deadline:
        if await communicator.receive_nothing(timeout=0.05, interval=0.01):
            continue
        frames.append(await communicator.receive_json_from(timeout=2))
    return frames


def _wait_for(pred, timeout=15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.05)
    return pred()


@pytest.mark.django_db(transaction=True)
def test_sessions_on_two_loops_in_one_room_get_each_others_pushes_in_order(
    two_loops, multi_loop_layer, pool
):
    count = 12
    loop_a, loop_b = two_loops
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[MOD]):
        a = loop_a.run(_connect("tag=a&room=r1"))
        b = loop_b.run(_connect("tag=b&room=r1"))
        c = loop_b.run(_connect("tag=c&room=r2"))  # another room, on b's loop
        try:
            # One side at a time, so the receiving loop is idle (parked in its
            # selector) when the pushes arrive: a hand-off that does not wake
            # the loop thread-safely would leave them undelivered.
            loop_a.run(_shout(a, count))
            ok = _wait_for(lambda: len(_SEEN.get("b", ())) >= count)
            loop_b.run(_shout(b, count))
            ok = _wait_for(lambda: len(_SEEN.get("a", ())) >= count) and ok
            # Then both at once.
            sends = [loop_a.submit(_shout(a, count)), loop_b.submit(_shout(b, count))]
            for s in sends:
                s.result(20)
            ok = (
                _wait_for(
                    lambda: (
                        len(_SEEN.get("a", ())) >= 2 * count
                        and len(_SEEN.get("b", ())) >= 2 * count
                    )
                )
                and ok
            )
            with _SEEN_LOCK:
                seen = {k: list(v) for k, v in _SEEN.items()}
            assert ok, f"pushes missing: {seen}"
            # Each session skips its own push (#1677) and gets the other's,
            # in the order it was sent, across the two loops.
            assert [n for src, n in seen["a"] if src == "b"] == list(range(count)) * 2
            assert [n for src, n in seen["b"] if src == "a"] == list(range(count)) * 2
            assert not [p for p in seen["a"] if p[0] == "a"]
            assert "c" not in seen, "a session in another room got r1's pushes"
        finally:
            loop_a.run(a.disconnect())
            loop_b.run(b.disconnect())
            loop_b.run(c.disconnect())


@pytest.mark.django_db(transaction=True)
def test_a_push_from_outside_any_loop_reaches_sessions_on_both_loops(
    two_loops, multi_loop_layer, pool
):
    """``push_to_view`` from a plain thread (a room clock thread, a Celery
    task) runs the group send on a temporary loop of its own."""
    loop_a, loop_b = two_loops
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[MOD]):
        a = loop_a.run(_connect("tag=a&room=r7"))
        b = loop_b.run(_connect("tag=b&room=r7"))
        try:
            for n in range(5):
                push_to_view(
                    VIEW, handler="handle_seq", payload={"src": "clock", "n": n}, scope="r7"
                )
            assert _wait_for(lambda: len(_SEEN.get("a", ())) >= 5 and len(_SEEN.get("b", ())) >= 5)
            frames = [loop_a.run(_drain(a, 0.5)), loop_b.run(_drain(b, 0.5))]
            assert all(any(f.get("type") == "patch" for f in fs) for fs in frames)
            assert [n for _, n in _SEEN["a"]] == [n for _, n in _SEEN["b"]] == list(range(5))
        finally:
            loop_a.run(a.disconnect())
            loop_b.run(b.disconnect())


# ---------------------------------------------------------------------------
# SSE: the stream GET and a later event POST can be on different loops
# ---------------------------------------------------------------------------

_REQ_VAR: contextvars.ContextVar = contextvars.ContextVar("req_var", default=None)


@pytest.fixture
def multi_loop_mode(monkeypatch):
    monkeypatch.setattr(multiloop, "_loop_count", 2)


@pytest.mark.django_db
def test_an_sse_post_on_another_loop_dispatches_on_the_sessions_loop(two_loops, multi_loop_mode):
    from djust import sse

    loop_a, loop_b = two_loops

    async def make():
        return sse.SSESession("sid-3128")

    session = loop_a.run(make())
    assert session._loop is loop_a.loop
    seen = {}

    async def dispatch(request, data):
        seen["loop"] = asyncio.get_running_loop()
        seen["thread"] = threading.current_thread().name
        seen["ctx"] = _REQ_VAR.get()
        seen["data"] = data

    session.dispatch = dispatch

    async def post():
        _REQ_VAR.set("this-request")
        await sse._dispatch_on_session_loop(session, object(), {"type": "event"})

    loop_b.run(post())
    assert seen["loop"] is loop_a.loop and seen["thread"] == "djust-loop-0"
    assert seen["ctx"] == "this-request", "the POST's context did not travel with it"
    assert seen["data"] == {"type": "event"}


@pytest.mark.django_db
def test_an_sse_hop_carries_errors_and_diagnostic_restrictions_back(two_loops, multi_loop_mode):
    from djust import sse
    from djust._exposure_diagnostics import _details_allowed, diagnostic_scope

    loop_a, loop_b = two_loops

    async def make():
        return sse.SSESession("sid-3128b")

    session = loop_a.run(make())

    async def failing(request, data):
        _details_allowed.set(False)  # a nonlegacy owner restricted diagnostics
        raise ValueError("boom")

    session.dispatch = failing

    async def post():
        with diagnostic_scope():
            try:
                await sse._dispatch_on_session_loop(session, object(), {})
            except ValueError as exc:
                return str(exc), _details_allowed.get()
        return None, None

    assert loop_b.run(post()) == ("boom", False)


@pytest.mark.django_db
def test_an_sse_push_from_another_loop_reaches_the_stream(two_loops, multi_loop_mode):
    """The stream waits on the session's queue on its own loop; a push made on
    another loop's thread must wake it."""
    from djust import sse

    loop_a, loop_b = two_loops

    async def make():
        return sse.SSESession("sid-3128d")

    session = loop_a.run(make())
    waiting = loop_a.submit(session.queue.get())  # the stream, parked on loop a
    time.sleep(0.05)

    async def push():
        session.push({"type": "patch", "n": 1})

    loop_b.run(push())
    assert waiting.result(5) == {"type": "patch", "n": 1}


@pytest.mark.django_db
def test_with_one_loop_an_sse_post_dispatches_in_place(two_loops):
    """Not multi-loop (the default): no hop, whatever loop the session was
    created on, exactly as before."""
    from djust import sse

    loop_a, loop_b = two_loops

    async def make():
        return sse.SSESession("sid-3128c")

    session = loop_a.run(make())
    seen = {}

    async def dispatch(request, data):
        seen["loop"] = asyncio.get_running_loop()

    session.dispatch = dispatch
    loop_b.run(sse._dispatch_on_session_loop(session, object(), {}))
    assert seen["loop"] is loop_b.loop


# ---------------------------------------------------------------------------
# db_notify: one listener per process, bound to one loop
# ---------------------------------------------------------------------------


@pytest.fixture
def listener(monkeypatch):
    from djust.db.notifications import PostgresNotifyListener

    started = []

    async def fake_start(self):
        started.append(asyncio.get_running_loop())
        self._loop = asyncio.get_running_loop()

    monkeypatch.setattr(PostgresNotifyListener, "_ensure_task_started", fake_start)
    inst = PostgresNotifyListener()
    inst.started = started
    return inst


def test_db_notify_subscriptions_from_other_loops_hop_to_the_listeners_loop(
    two_loops, multi_loop_mode, listener
):
    loop_a, loop_b = two_loops
    loop_a.run(listener.ensure_listening("orders"))
    loop_b.run(listener.ensure_listening("invoices"))
    assert listener._channels == {"orders", "invoices"}
    assert listener.started == [loop_a.loop, loop_a.loop]
    assert listener._loop is loop_a.loop


def test_two_loops_subscribing_at_once_start_one_listener(two_loops, multi_loop_mode, listener):
    loop_a, loop_b = two_loops
    barrier = threading.Barrier(2)

    async def sub(name):
        await asyncio.get_running_loop().run_in_executor(None, barrier.wait)
        await listener.ensure_listening(name)

    futs = [loop_a.submit(sub("one")), loop_b.submit(sub("two"))]
    for f in futs:
        f.result(10)
    assert listener._channels == {"one", "two"}
    assert len(set(listener.started)) == 1, "two loops each started a listener"


def test_db_notify_without_multi_loop_keeps_the_808_same_loop_check(two_loops, listener):
    loop_a, loop_b = two_loops
    loop_a.run(listener.ensure_listening("orders"))
    with pytest.raises(RuntimeError, match="different event loop"):
        loop_b.run(listener.ensure_listening("invoices"))
