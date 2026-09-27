"""#3227: a terminal stream op must not overtake the stream's queued content.

``stream_to``/``stream_text`` rate-limit to ``MIN_STREAM_INTERVAL_S``: an op
inside the window is queued and flushed later by ``_flush_stream_batch``.
``stream_done`` and ``stream_error`` used to send directly, so a queued final
update reached the client AFTER ``done`` (``start, replace, done, replace``) and
a client that finalises on ``done`` saw content land on a finished stream.

The first test drives the real path (``WebsocketCommunicator`` against
``LiveViewConsumer.as_asgi()``, a ``start_async`` callback, the real
``stream_to``/``stream_done``). The others pin the edge cases of the fix on a
real ``LiveView`` instance with a recording consumer.

Determinism: "inside the rate window" is forced as state (``_last_stream_time``
a minute ahead, as ``python/tests/test_streaming.py`` does), never as a race
against the 16 ms timer. The WebSocket view waits for any flush task to finish
before its callback returns, so every stream frame precedes the callback's
``source="async"`` render and no quiet window decides the outcome.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from asgiref.sync import sync_to_async

from djust import LiveView
from djust.decorators import event_handler
from djust.streaming import StreamingMixin

_MODULE = __name__
_TARGET = "[dj-stream='reply']"


class _SettleView(LiveView):
    """The tutorial's shape: stream, settle once with ``stream_to``, then ``done``."""

    template = (
        f'<div dj-view="{_MODULE}._SettleView" dj-id="0">'
        '<article dj-stream="reply" dj-update="ignore" dj-id="1"></article>'
        "</div>"
    )

    def mount(self, request, **kwargs):
        self.streaming = False

    @event_handler()
    def go(self, **kwargs):
        self.streaming = True
        self.start_async(self._stream, name="reply")

    async def _stream(self):
        await self.stream_start("reply")
        await self.stream_to("reply", html="<p>1</p>")  # sent at once
        _inside_window(self)
        await self.stream_to("reply", html="<p>12</p>")  # queued
        await self.stream_to("reply", html="<p>123</p>")  # queued, replaces 12
        await self.stream_done("reply")
        # Test sync point: let any flush task finish, so a stray replace (the
        # bug) is sent before this callback's end-of-work render.
        task = self._stream_flush_task
        if task is not None:
            await asyncio.wait({task})
        self.streaming = False


def _inside_window(view):
    """Make the NEXT stream op queue, and its flush run without the sleep.

    A last send a minute in the future keeps ``elapsed < MIN_STREAM_INTERVAL_S``
    under any load. The flush keeps the real ``_flush_stream_batch`` body but
    drops its (now minute-long) delay, so it runs on the next loop turn.
    """
    view._last_stream_time = time.monotonic() + 60.0
    view._flush_stream_batch = lambda delay: StreamingMixin._flush_stream_batch(view, 0)


async def _connect_and_mount(view_suffix):
    pytest.importorskip("channels")
    from channels.testing import WebsocketCommunicator
    from django.contrib.sessions.backends.db import SessionStore

    from djust.websocket import LiveViewConsumer

    def _create_session():
        s = SessionStore()
        s.create()
        return s.session_key

    session_key = await sync_to_async(_create_session)()

    class _ScopeSession:
        def __init__(self, key):
            self.session_key = key

    communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    communicator.scope["session"] = _ScopeSession(session_key)
    connected, _ = await communicator.connect()
    assert connected
    await communicator.receive_json_from(timeout=2)  # connect frame
    await communicator.send_json_to(
        {"type": "mount", "view": f"{_MODULE}.{view_suffix}", "url": "/settle/"}
    )
    for _ in range(6):
        frame = await communicator.receive_json_from(timeout=3)
        if frame.get("type") == "mount":
            return communicator
    raise AssertionError("never received a mount frame")


def _stream_ops(frames):
    return [op for f in frames if f.get("type") == "stream" for op in f["ops"]]


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_done_arrives_after_the_queued_settle_over_websocket():
    from django.test import override_settings

    from ._ws_frames import receive_until

    with override_settings(LIVEVIEW_ALLOWED_MODULES=[_MODULE]):
        communicator = await _connect_and_mount("_SettleView")
        await communicator.send_json_to({"type": "event", "event": "go", "params": {}, "ref": 1})
        frames = await receive_until(
            communicator,
            lambda fs: any(
                f.get("type") in ("patch", "html_update") and f.get("source") == "async" for f in fs
            ),
            what="the end-of-callback render",
        )
        await communicator.disconnect()

    ops = _stream_ops(frames)
    assert [op["op"] for op in ops] == ["start", "replace", "replace", "done"], ops
    assert ops[2]["html"] == "<p>123</p>", "the latest queued update is the one flushed"


# ── Edge cases on a real LiveView with a recording consumer ──


class _Consumer:
    def __init__(self):
        self.frames = []
        self.gate = None  # an asyncio.Event that holds the NEXT send only

    async def send_json(self, data):
        gate, self.gate = self.gate, None
        if gate is not None:
            await gate.wait()
        self.frames.append(data)

    def ops(self):
        return [
            (f["stream"], op["op"], op.get("html") or op.get("text"))
            for f in self.frames
            for op in f["ops"]
        ]


class _View(LiveView):
    template = (
        '<div dj-root><article dj-stream="a"></article><article dj-stream="b"></article></div>'
    )


def _view():
    view = _View()
    view._ws_consumer = _Consumer()
    return view


@pytest.mark.asyncio
async def test_stream_error_sends_the_queued_content_first():
    view = _view()
    await view.stream_to("a", html="first")
    _inside_window(view)
    await view.stream_to("a", html="partial")
    await view.stream_error("a", "provider failed")

    assert view._ws_consumer.ops() == [
        ("a", "replace", "first"),
        ("a", "replace", "partial"),
        ("a", "error", None),
    ]
    assert view._stream_flush_task is None, "nothing else was queued: the flush task is cancelled"
    assert view._stream_batch == {}


@pytest.mark.asyncio
async def test_done_on_one_stream_leaves_another_streams_queued_op_to_the_flush():
    view = _view()
    _inside_window(view)
    await view.stream_text("a", "a-queued")
    await view.stream_text("b", "b-queued")
    task = view._stream_flush_task
    assert task is not None and not task.done()

    await view.stream_done("a")
    # a's queued op went out before a's done; b's is still queued for the flush.
    assert view._ws_consumer.ops() == [("a", "text", "a-queued"), ("a", "done", None)]
    assert view._stream_flush_task is task and not task.done()
    assert list(view._stream_batch) == ["b"]

    await task
    assert view._ws_consumer.ops()[-1] == ("b", "text", "b-queued")


@pytest.mark.asyncio
async def test_done_waits_for_a_flush_that_is_already_sending():
    """The flush took the batch (holding a's op) and is mid-send: done waits for it."""
    view = _view()
    consumer = view._ws_consumer
    _inside_window(view)
    await view.stream_to("a", html="queued")
    task = view._stream_flush_task

    gate = consumer.gate = asyncio.Event()
    # Let the flush wake and block inside its send.
    while not view._stream_flush_sending:
        await asyncio.sleep(0)
    assert view._stream_batch == {}, "the flush owns the batch now"

    done = asyncio.ensure_future(view.stream_done("a"))
    for _ in range(5):
        await asyncio.sleep(0)
    assert not done.done(), "done must not be sent while the flush holds a's op"

    gate.set()
    await done
    assert task.done()
    assert [op[1] for op in consumer.ops()] == ["replace", "done"]


@pytest.mark.asyncio
async def test_an_op_queued_while_the_flush_sends_is_flushed_by_a_successor():
    """An op queued during the send phase lands in a fresh batch and is not lost."""
    view = _view()
    consumer = view._ws_consumer
    _inside_window(view)
    await view.stream_text("a", "one")
    first = view._stream_flush_task

    gate = consumer.gate = asyncio.Event()
    while not view._stream_flush_sending:
        await asyncio.sleep(0)
    _inside_window(view)
    await view.stream_text("a", "two")  # the pending task is still `first`
    assert view._stream_flush_task is first

    gate.set()
    await first
    successor = view._stream_flush_task
    assert successor is not first and successor is not None
    await successor
    assert [op[2] for op in consumer.ops()] == ["one", "two"]


@pytest.mark.asyncio
async def test_stream_done_with_nothing_queued_is_unchanged():
    view = _view()
    await view.stream_start("a")
    await view.stream_done("a")
    assert [op[1] for op in view._ws_consumer.ops()] == ["start", "done"]
    assert view._stream_flush_task is None


def test_the_new_flag_is_framework_state():
    """``_stream_flush_sending`` is set in ``__init__`` before the snapshot (#1393)."""
    view = _View()
    assert "_stream_flush_sending" in view._framework_attrs
