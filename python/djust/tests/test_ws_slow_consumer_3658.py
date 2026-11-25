"""#3658 slice 1: a bounded per-connection WebSocket outbound buffer.

A client that reads slowly makes the ASGI server's ``websocket.send`` wait
(back-pressure). While one send of a connection waits, every other frame of
that connection queues in the consumer's outbound buffer, bounded in bytes by
``LIVEVIEW_CONFIG["max_outbound_buffer_bytes"]``. Past the bound the socket is
closed with 1013 (decision D12) and the client reconnects and resyncs through
the ordinary recovery path.

The slow reader is a gate: an ASGI ``send`` that holds every
``websocket.send`` until the test opens it. Nothing here waits on the clock to
synchronise; each step waits on an event or a queued frame.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List
from unittest.mock import AsyncMock

import pytest
from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.test import override_settings

import djust.websocket as ws_module
from djust import LiveView, event_handler
from djust import state as djust_state
from djust._outbound_buffer import OutboundBuffer, outbound_buffer_limit
from djust.config import DEFAULT_MAX_OUTBOUND_BUFFER_BYTES, config as djust_config
from djust.websocket import WS_CLOSE_SLOW_CONSUMER, LiveViewConsumer


class SlowReader:
    """An ASGI ``send`` whose ``websocket.send`` waits for the gate to open."""

    def __init__(self) -> None:
        self.gate = asyncio.Event()
        self.gate.set()
        self.sent: List[Dict[str, Any]] = []
        self.blocked = asyncio.Event()

    def stall(self) -> None:
        self.gate.clear()
        self.blocked.clear()

    async def __call__(self, message: Dict[str, Any]) -> None:
        if message["type"] == "websocket.send" and not self.gate.is_set():
            self.blocked.set()
            await self.gate.wait()
        self.sent.append(message)

    def texts(self) -> List[Any]:
        return [json.loads(m["text"])["n"] for m in self.sent if m["type"] == "websocket.send"]

    def closes(self) -> List[Any]:
        return [m.get("code") for m in self.sent if m["type"] == "websocket.close"]


def _consumer(reader: SlowReader, limit: int = 1000) -> LiveViewConsumer:
    consumer = LiveViewConsumer()
    consumer.base_send = reader
    consumer._outbound_buffer().limit = limit
    return consumer


def _frame(n: int, pad: int = 0) -> Dict[str, Any]:
    return {"n": n, "pad": "x" * pad}


async def _start_leader(consumer: LiveViewConsumer, reader: SlowReader, n: int, pad: int = 0):
    """A send that is in flight on a stalled socket: it holds the connection."""
    reader.stall()
    task = asyncio.ensure_future(consumer.send_json(_frame(n, pad)))
    await reader.blocked.wait()
    return task


# -- configuration ---------------------------------------------------------


def test_default_limit_is_four_mib():
    assert DEFAULT_MAX_OUTBOUND_BUFFER_BYTES == 4 * 1024 * 1024
    with override_settings(LIVEVIEW_CONFIG={}):
        assert outbound_buffer_limit() == DEFAULT_MAX_OUTBOUND_BUFFER_BYTES


def test_limit_follows_liveview_config():
    with override_settings(LIVEVIEW_CONFIG={"max_outbound_buffer_bytes": 1234}):
        assert outbound_buffer_limit() == 1234
    with override_settings(LIVEVIEW_CONFIG={"max_outbound_buffer_bytes": 0}):
        assert outbound_buffer_limit() == 0


@pytest.mark.parametrize("bad", [-1, "abc", 2.5, True])
def test_a_bad_limit_is_the_default_never_no_limit(bad):
    with override_settings(LIVEVIEW_CONFIG={"max_outbound_buffer_bytes": bad}):
        assert djust_config.get("max_outbound_buffer_bytes") == DEFAULT_MAX_OUTBOUND_BUFFER_BYTES
        assert outbound_buffer_limit() == DEFAULT_MAX_OUTBOUND_BUFFER_BYTES


# -- the send path ---------------------------------------------------------


@pytest.mark.asyncio
async def test_an_uncontended_send_is_on_the_wire_when_it_returns():
    reader = SlowReader()
    consumer = _consumer(reader)
    await consumer.send_json(_frame(1))
    assert reader.texts() == [1]
    assert consumer._outbound.queued_frames == 0


@pytest.mark.asyncio
async def test_slow_reader_frames_queue_and_keep_their_order():
    reader = SlowReader()
    consumer = _consumer(reader)
    leader = await _start_leader(consumer, reader, 1)

    # Other senders do not wait for the stalled socket: their frames queue.
    for n in (2, 3, 4):
        await consumer.send_json(_frame(n))
    buffer = consumer._outbound
    assert buffer.queued_frames == 3
    assert buffer.queued_bytes == sum(len(json.dumps(_frame(n)).encode()) for n in (2, 3, 4))
    assert reader.texts() == []

    reader.gate.set()
    await leader
    assert reader.texts() == [1, 2, 3, 4]
    assert buffer.queued_frames == 0 and buffer.queued_bytes == 0


@pytest.mark.asyncio
async def test_the_backlog_never_passes_the_bound_and_overflow_closes_1013():
    reader = SlowReader()
    consumer = _consumer(reader, limit=1000)
    leader = await _start_leader(consumer, reader, 0)

    n = 0
    while not getattr(consumer, "_ws_close_sent", False):
        n += 1
        await consumer.send_json(_frame(n, pad=180))
        assert consumer._outbound.queued_bytes <= 1000
    buffer = consumer._outbound
    assert 800 < buffer.peak_bytes <= 1000, "memory bound: the backlog stays under the limit"
    assert buffer.dead and buffer.queued_frames == 0, "the backlog is released at once"

    await consumer._slow_consumer_close
    assert reader.closes() == [WS_CLOSE_SLOW_CONSUMER]

    # Later frames are dropped, not queued.
    await consumer.send_json(_frame(99))
    assert buffer.queued_frames == 0

    reader.gate.set()
    await leader
    # Only the frame already in flight reached the wire; the backlog did not.
    assert reader.texts() == [0]


@pytest.mark.asyncio
async def test_one_frame_larger_than_the_limit_is_still_sent():
    reader = SlowReader()
    consumer = _consumer(reader, limit=100)
    await consumer.send_json(_frame(1, pad=5000))
    leader = await _start_leader(consumer, reader, 2)
    # Nothing waits yet, so one oversized frame is accepted into the queue.
    await consumer.send_json(_frame(3, pad=5000))
    assert not getattr(consumer, "_ws_close_sent", False)
    reader.gate.set()
    await leader
    assert reader.texts() == [1, 2, 3]


@pytest.mark.asyncio
async def test_no_limit_when_configured_zero():
    reader = SlowReader()
    consumer = _consumer(reader, limit=0)
    leader = await _start_leader(consumer, reader, 0)
    for n in range(1, 50):
        await consumer.send_json(_frame(n, pad=1000))
    assert not getattr(consumer, "_ws_close_sent", False)
    reader.gate.set()
    await leader
    assert reader.texts() == list(range(50))


@pytest.mark.asyncio
async def test_close_sends_the_queued_frames_first():
    reader = SlowReader()
    consumer = _consumer(reader)
    leader = await _start_leader(consumer, reader, 1)
    await consumer.send_json(_frame(2))

    closing = asyncio.ensure_future(consumer.close(code=4403))
    # The close waits for the queued frame; open the socket to let it through.
    reader.gate.set()
    await closing
    await leader
    kinds = [(m["type"], m.get("code")) for m in reader.sent]
    assert kinds == [
        ("websocket.send", None),
        ("websocket.send", None),
        ("websocket.close", 4403),
    ]


@pytest.mark.asyncio
async def test_close_gives_up_on_a_reader_that_never_drains(monkeypatch):
    monkeypatch.setattr(ws_module, "_CLOSE_FLUSH_TIMEOUT_S", 0.01)
    reader = SlowReader()
    consumer = _consumer(reader)
    leader = await _start_leader(consumer, reader, 1)
    await consumer.send_json(_frame(2))

    await consumer.close(code=1000)
    assert reader.closes() == [1000]
    assert consumer._outbound.dead and consumer._outbound.queued_frames == 0

    reader.gate.set()
    await leader
    assert reader.texts() == [1]


# -- cancellation ----------------------------------------------------------


@pytest.mark.asyncio
async def test_disconnect_drops_the_queued_frames():
    reader = SlowReader()
    consumer = _consumer(reader)
    consumer.channel_layer = AsyncMock()
    consumer.channel_name = "test.channel"
    consumer._view_group = None
    consumer._presence_group = None
    consumer.view_instance = None
    leader = await _start_leader(consumer, reader, 1)
    await consumer.send_json(_frame(2))
    await consumer.send_json(_frame(3))

    await consumer.disconnect(1006)
    assert consumer._outbound.dead and consumer._outbound.queued_bytes == 0

    reader.gate.set()
    await leader
    assert reader.texts() == [1], "frames for a gone client are dropped"


@pytest.mark.asyncio
async def test_a_cancelled_sender_leaves_its_followers_to_the_next_send():
    reader = SlowReader()
    consumer = _consumer(reader)
    leader = await _start_leader(consumer, reader, 1)
    await consumer.send_json(_frame(2))
    await consumer.send_json(_frame(3))

    leader.cancel()
    with pytest.raises(asyncio.CancelledError):
        await leader
    assert consumer._outbound.queued_frames == 2

    reader.gate.set()
    await consumer.send_json(_frame(4))
    assert reader.texts() == [2, 3, 4], "the stranded frames go first, in order"


@pytest.mark.asyncio
async def test_a_transport_error_still_reaches_the_uncontended_sender():
    async def boom(message):
        raise RuntimeError("boom")

    consumer = LiveViewConsumer()
    consumer.base_send = boom
    with pytest.raises(RuntimeError, match="boom"):
        await consumer.send_json({"type": "pong"})


@pytest.mark.asyncio
async def test_buffer_flush_drains_frames_a_failed_leader_left():
    sent: List[int] = []
    fail = [True]

    async def transmit(frame: int) -> bool:
        if fail[0]:
            fail[0] = False
            raise RuntimeError("transport")
        sent.append(frame)
        return True

    buffer: OutboundBuffer[int] = OutboundBuffer(
        transmit, lambda f: 1, lambda pending, limit: None, 10
    )
    buffer._enqueue(2)  # a frame queued behind the leader that is about to fail
    buffer._queue.appendleft((1, 1))
    buffer.queued_bytes += 1
    buffer._sending = False
    with pytest.raises(RuntimeError):
        await buffer.send(3)
    assert await buffer.flush(1.0)
    assert sent == [2, 3]


# -- end to end: overflow, then reconnect ----------------------------------

CONSUMERS: List["RecordingConsumer"] = []


class RecordingConsumer(LiveViewConsumer):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        CONSUMERS.append(self)


class CounterView(LiveView):
    exposure_policy = "explicit"
    n = djust_state(0)
    template = "<div dj-root>{{ n }}</div>"

    def mount(self, request, **kwargs):
        self.n = 0

    @event_handler()
    def bump(self, **kwargs):
        self.n += 1


def _app(reader_holder: Dict[str, SlowReader]):
    inner = RecordingConsumer.as_asgi()

    async def app(scope, receive, send):
        reader = SlowReader()
        reader_holder["reader"] = reader

        async def gated(message):
            await reader(message)
            await send(message)

        return await inner(scope, receive, gated)

    return app


def _session() -> SessionStore:
    session = SessionStore()
    session.save()
    return session


async def _mount(app) -> WebsocketCommunicator:
    session = await sync_to_async(_session)()
    socket = WebsocketCommunicator(app, "/ws/")
    socket.scope.update(session=session, user=AnonymousUser(), tenant=None)
    assert (await socket.connect())[0]
    await socket.receive_json_from()
    await socket.send_json_to({"type": "mount", "view": __name__ + ".CounterView", "url": "/c/"})
    assert (await socket.receive_json_from())["type"] == "mount"
    return socket


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_overflow_closes_1013_and_the_client_can_reconnect_and_remount():
    CONSUMERS.clear()
    holder: Dict[str, SlowReader] = {}
    settings = dict(
        DEBUG=False,
        DJUST_TENANTS=None,
        LIVEVIEW_ALLOWED_MODULES=[__name__],
        LIVEVIEW_CONFIG={"max_outbound_buffer_bytes": 1000},
    )
    with override_settings(**settings):
        app = _app(holder)
        socket = await _mount(app)
        consumer = CONSUMERS[-1]
        reader = holder["reader"]

        leader = await _start_leader(consumer, reader, 0)
        while not getattr(consumer, "_ws_close_sent", False):
            await consumer.send_json(_frame(1, pad=400))

        closed = await socket.receive_output()
        assert closed == {"type": "websocket.close", "code": WS_CLOSE_SLOW_CONSUMER}

        reader.gate.set()
        await leader
        await socket.send_input({"type": "websocket.disconnect", "code": WS_CLOSE_SLOW_CONSUMER})
        await socket.wait()

        # The client's reconnect: a new socket mounts the view afresh (resync).
        again = await _mount(app)
        await again.send_json_to({"type": "event", "event": "bump", "params": {}})
        frame = await again.receive_json_from()
        assert frame["type"] in ("patch", "html_update")
        await again.disconnect()
