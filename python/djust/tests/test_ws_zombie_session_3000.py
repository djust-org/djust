"""#3000: a socket closed while an event is in flight must still reach ``disconnect()``.

Reproduced against the real snake-arena app under uvicorn ``--ws websockets``:
the client closes while its event is in flight, so the event's reply is sent
on a socket the peer has already closed. uvicorn raises
``ClientDisconnected`` (an ``OSError`` subclass; ASGI 2.4 specifies an
``OSError`` for a send on a closed connection). ``_send_frame`` only knew the
``RuntimeError`` shape, so the error escaped; ``receive()``'s catch-all tried
to send an error frame on the same dead socket, and that second failure
escaped Channels' dispatch loop. The loop was gone, so the queued
``websocket.disconnect`` was never dispatched: no ``disconnect()``, no presence
cleanup, and the tick task (nobody cancelled it) ran forever. uvicorn catches
``ClientDisconnected`` silently, so nothing was logged.

The tests drive a real ``WebsocketCommunicator`` whose ``send`` starts raising
an ``OSError`` subclass after mount, the same as uvicorn after the peer's
close frame arrives.
"""

import asyncio

import pytest
from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.test import override_settings

from djust import LiveView, event_handler
from djust.websocket import LiveViewConsumer

SETTINGS = dict(DEBUG=False, DJUST_TENANTS=None, DJUST_CONFIG={})
CONSUMERS = []
TICKS = []


class ClientDisconnected(OSError):
    """Stands in for ``uvicorn.protocols.utils.ClientDisconnected``."""


class RecordingConsumer(LiveViewConsumer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.disconnect_calls = 0
        CONSUMERS.append(self)

    async def disconnect(self, close_code):
        self.disconnect_calls += 1
        await super().disconnect(close_code)


class TickingView(LiveView):
    template = "<div dj-root>{{ n }}</div>"
    tick_interval = 30

    def mount(self, request, **kwargs):
        self.n = 0

    def get_context_data(self, **kwargs):
        return {"n": self.n}

    def handle_tick(self):
        # Like snake-arena's heartbeat tick: no render, so the tick itself
        # never sends a frame that could notice the dead socket.
        TICKS.append(1)
        self._skip_render = True

    @event_handler()
    def key(self, **kwargs):
        self._skip_render = True


def _dead_after_mount_app(state):
    """ASGI wrapper: once ``state['dead']``, every ``websocket.send`` raises."""
    inner = RecordingConsumer.as_asgi()

    async def app(scope, receive, send):
        async def guarded_send(message):
            if state["dead"] and message["type"] == "websocket.send":
                raise ClientDisconnected()
            await send(message)

        return await inner(scope, receive, guarded_send)

    return app


def _session():
    session = SessionStore()
    session.save()
    return session


async def _mounted(app):
    session = await sync_to_async(_session)()
    socket = WebsocketCommunicator(app, "/ws/")
    socket.scope.update(session=session, user=AnonymousUser(), tenant=None)
    assert (await socket.connect())[0]
    await socket.receive_json_from(timeout=3)
    await socket.send_json_to({"type": "mount", "view": __name__ + ".TickingView", "url": "/t/"})
    assert (await socket.receive_json_from(timeout=3))["type"] == "mount"
    return socket


async def _ticks_after(seconds):
    before = len(TICKS)
    await asyncio.sleep(seconds)
    return len(TICKS) - before


@pytest.fixture(autouse=True)
def _reset():
    CONSUMERS.clear()
    TICKS.clear()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_close_while_event_in_flight_still_reaches_disconnect():
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], **SETTINGS):
        state = {"dead": False}
        socket = await _mounted(_dead_after_mount_app(state))
        consumer = CONSUMERS[-1]
        assert await _ticks_after(0.2) > 0, "precondition: the tick loop runs"

        # The peer closes; the in-flight event's reply hits the dead socket.
        state["dead"] = True
        await socket.send_json_to({"type": "event", "event": "key", "params": {}})
        await asyncio.sleep(0.2)
        # uvicorn then delivers the disconnect on the receive side.
        await socket.send_input({"type": "websocket.disconnect", "code": 1006})
        await socket.wait(timeout=3)

        assert consumer.disconnect_calls == 1, "disconnect() never ran: zombie session"
        assert consumer.view_instance is None
        assert await _ticks_after(0.2) == 0, "the tick task outlived the socket"


@pytest.mark.asyncio
async def test_send_frame_drops_the_asgi_oserror_shape():
    sends = []

    async def base_send(message):
        sends.append(message)
        raise ClientDisconnected()

    consumer = LiveViewConsumer()
    consumer.base_send = base_send

    await consumer.send_json({"type": "noop"})  # must not raise
    assert consumer._ws_close_sent is True
    await consumer.send_json({"type": "error"})
    assert len(sends) == 1, "a known-closed socket must not be sent to again"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_exception_escaping_dispatch_still_runs_disconnect(monkeypatch):
    """Backstop: whatever escapes Channels' dispatch loop, cleanup still runs."""

    async def exploding_receive(self, text_data=None, bytes_data=None):
        raise RuntimeError("handler blew up outside the catch-all")

    monkeypatch.setattr(RecordingConsumer, "receive", exploding_receive)
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], **SETTINGS):
        socket = WebsocketCommunicator(RecordingConsumer.as_asgi(), "/ws/")
        socket.scope.update(session=await sync_to_async(_session)(), user=AnonymousUser())
        assert (await socket.connect())[0]
        await socket.receive_json_from(timeout=3)
        consumer = CONSUMERS[-1]
        await socket.send_json_to({"type": "ping"})
        with pytest.raises(RuntimeError, match="blew up"):
            await socket.wait(timeout=3)
        assert consumer.disconnect_calls == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_disconnect_dispatch_then_raise_is_not_cleaned_up_twice():
    """A ``websocket.disconnect`` whose ``disconnect()`` raises is not cleaned up twice.

    The backstop only covers a loop that died BEFORE the disconnect was
    dispatched; re-running a cleanup that already failed would repeat its
    side effects (presence leave, group discards)."""

    class Boom(RecordingConsumer):
        async def disconnect(self, close_code):
            self.disconnect_calls += 1
            raise RuntimeError("cleanup failed")

    consumer = Boom()
    consumer.scope = {"type": "websocket"}

    async def receive():
        return {"type": "websocket.disconnect", "code": 1000}

    async def send(message):
        pass

    consumer.channel_layer_alias = None  # no channel layer: receive() only
    with pytest.raises(RuntimeError, match="cleanup failed"):
        await consumer(consumer.scope, receive, send)
    assert consumer.disconnect_calls == 1


@pytest.mark.asyncio
async def test_tick_loop_stops_once_the_socket_is_gone():
    """Backstop: a closed socket stops the tick even while a view is attached."""
    consumer = LiveViewConsumer()
    consumer.view_instance = object()
    calls = []

    async def tick_once():
        calls.append(1)
        return False

    consumer._tick_once = tick_once
    consumer._ws_close_sent = True
    await asyncio.wait_for(consumer._run_tick(5), timeout=1)
    assert calls == []


@pytest.mark.asyncio
async def test_close_on_a_peer_closed_socket_does_not_raise():
    """``close()`` (e.g. a re-auth 4403) after the peer left must not escape."""

    async def base_send(message):
        raise ClientDisconnected()

    consumer = LiveViewConsumer()
    consumer.base_send = base_send
    await consumer.close(code=4403)
    assert consumer._ws_close_sent is True
