"""#3658 slice 2: the SSE stream's outbound limit.

An SSE session queues every frame for its stream generator. When the client
reads slowly the queue grows; its bytes are now bounded by
``LIVEVIEW_CONFIG["max_outbound_buffer_bytes"]``. Past the bound the waiting
frames are dropped and the stream ends, so ``EventSource`` reconnects with the
same id and the reconnect mount resynchronises the page.

The slow reader is a stream the test does not iterate: nothing waits on the
clock.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from unittest.mock import patch

import pytest
from django.contrib.sessions.backends.cache import SessionStore
from django.test import RequestFactory, override_settings

from djust import sse
from djust._outbound_buffer import ByteCountingQueue
from djust.sse import DjustSSEStreamView, SSESession, _sse_sessions

from .test_sse_session_binding_f24_f25 import ALLOWED_ORIGIN, _auth_user, _fake_mount_ok


def _frame(n: int, pad: int = 0) -> dict:
    return {"type": "push_event", "n": n, "pad": "x" * pad}


def _size(frame: dict) -> int:
    return len(json.dumps(frame).encode())


def _session(limit: int) -> SSESession:
    with override_settings(LIVEVIEW_CONFIG={"max_outbound_buffer_bytes": limit}):
        return SSESession(str(uuid.uuid4()))


def _drain(session: SSESession) -> list:
    out = []
    while not session.queue.empty():
        out.append(session.queue.get_nowait())
    return out


# -- the queue -------------------------------------------------------------


@pytest.mark.asyncio
async def test_queue_counts_bytes_until_the_stream_takes_the_frame():
    queue = ByteCountingQueue()
    queue.put_nowait("head")
    queue.put_sized("a", 10)
    queue.put_sized("b", 5)
    assert queue.queued_bytes == 15 and queue.peak_bytes == 15
    assert queue.get_nowait() == "head" and queue.queued_bytes == 15
    assert queue.get_nowait() == "a" and queue.queued_bytes == 5
    assert await queue.get() == "b" and queue.queued_bytes == 0
    queue.put_sized("c", 7)
    queue.clear()
    assert queue.empty() and queue.queued_bytes == 0


# -- the session -----------------------------------------------------------


@pytest.mark.asyncio
async def test_frames_waiting_for_a_slow_reader_keep_their_order_under_the_limit():
    session = _session(10_000)
    for n in range(5):
        session.push(_frame(n, pad=100))
    # The head frame is the stream's next write; the rest wait and are counted.
    assert session.queue.queued_bytes == 4 * _size(_frame(0, pad=100))
    assert [m["n"] for m in _drain(session)] == [0, 1, 2, 3, 4]
    assert session.queue.queued_bytes == 0


@pytest.mark.asyncio
async def test_over_the_limit_the_backlog_is_dropped_and_the_stream_ends(caplog):
    session = _session(1000)
    n = 0
    with caplog.at_level(logging.WARNING, logger="djust.sse"):
        while not session._outbound_overflowed:
            session.push(_frame(n, pad=150))
            n += 1
            assert session.queue.queued_bytes <= 1000, "memory bound"
    assert 800 < session.queue.peak_bytes <= 1000
    assert "not reading fast enough" in caplog.text
    # Only the close sentinel is left: the stream ends at its next read.
    assert _drain(session) == [None]

    session.push(_frame(99))
    assert session.queue.empty(), "frames after the overflow are dropped"


@pytest.mark.asyncio
async def test_one_large_frame_alone_is_still_sent():
    session = _session(100)
    session.push(_frame(1, pad=5000))
    assert not session._outbound_overflowed
    assert [m["n"] for m in _drain(session)] == [1]


@pytest.mark.asyncio
async def test_no_limit_when_configured_zero():
    session = _session(0)
    for n in range(100):
        session.push(_frame(n, pad=1000))
    assert not session._outbound_overflowed
    assert len(_drain(session)) == 100


# -- end to end: the stream ends, the client reconnects --------------------


@pytest.fixture
def _registry(monkeypatch):
    _sse_sessions.clear()
    monkeypatch.setattr(sse, "_SESSION_LINGER_S", 0)
    yield
    _sse_sessions.clear()


def _get(sid: str):
    request = RequestFactory().get(
        f"/djust/sse/{sid}/",
        {"view": "demo.View"},
        HTTP_ORIGIN=ALLOWED_ORIGIN,
        HTTP_HOST="example.com",
        secure=True,
    )
    request.user = _auth_user(7)
    request.session = SessionStore()
    return request


@override_settings(
    ALLOWED_HOSTS=["example.com"], LIVEVIEW_CONFIG={"max_outbound_buffer_bytes": 1000}
)
@pytest.mark.asyncio
async def test_overflow_ends_the_stream_and_the_same_id_reconnects(_registry):
    sid = str(uuid.uuid4())
    view = DjustSSEStreamView()
    with patch("djust.runtime.ViewRuntime.dispatch_mount", new=_fake_mount_ok):
        response = await view.get(_get(sid), session_id=sid)
    session = _sse_sessions[sid]
    stream = response.streaming_content
    ack = await stream.__anext__()
    assert b"sse_connect" in ack

    # The client stops reading while the server keeps pushing.
    while not session._outbound_overflowed:
        session.push(_frame(1, pad=300))

    # What the client still gets: the stream ends without the dropped backlog.
    rest = [chunk async for chunk in stream]
    assert all(b"push_event" not in chunk for chunk in rest)
    assert sid not in _sse_sessions, "the ended stream released its session"

    # EventSource reconnects with the same id: a new session mounts (resync).
    with patch("djust.runtime.ViewRuntime.dispatch_mount", new=_fake_mount_ok):
        await view.get(_get(sid), session_id=sid)
    fresh = _sse_sessions[sid]
    assert fresh is not session and not fresh._outbound_overflowed
    fresh.shutdown()


@override_settings(
    ALLOWED_HOSTS=["example.com"], LIVEVIEW_CONFIG={"max_outbound_buffer_bytes": 1000}
)
@pytest.mark.asyncio
async def test_overflow_ends_a_stream_that_is_mid_write(_registry):
    """The usual shape: the stream is parked writing a frame to a slow client."""
    sid = str(uuid.uuid4())
    view = DjustSSEStreamView()
    with patch("djust.runtime.ViewRuntime.dispatch_mount", new=_fake_mount_ok):
        response = await view.get(_get(sid), session_id=sid)
    session = _sse_sessions[sid]
    stream = response.streaming_content
    await stream.__anext__()  # the ack

    # The stream waits on the queue; the first frame wakes it, and it is then
    # parked at that frame's write while the client does not read.
    reading = asyncio.ensure_future(stream.__anext__())
    while not session.queue._getters:
        await asyncio.sleep(0)
    session.push(_frame(0))
    assert b'"n": 0' in await reading

    while not session._outbound_overflowed:
        session.push(_frame(1, pad=300))
    rest = [chunk async for chunk in stream]
    assert all(b"push_event" not in chunk for chunk in rest)
    assert sid not in _sse_sessions
