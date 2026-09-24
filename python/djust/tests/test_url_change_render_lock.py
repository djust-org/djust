"""URL rendering shares the actual WS/SSE mutation lock and rechecks its owner."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from djust.runtime import SSESessionTransport, ViewRuntime, WSConsumerTransport


def setup(kind):
    if kind == "ws":
        from djust.websocket import LiveViewConsumer

        host = LiveViewConsumer()
        transport = WSConsumerTransport(host)
    else:
        from djust.sse import SSESession

        host = SSESession("url-lock-probe")
        transport = SSESessionTransport(host)
    host.send_error = AsyncMock()
    runtime = ViewRuntime(transport)
    runtime.view_instance = SimpleNamespace(_tenant=None)
    return runtime, host


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["ws", "sse"])
async def test_url_body_waits_for_real_transport_lock_and_runs_once(kind):
    runtime, host = setup(kind)
    held = []

    async def body(data):
        held.append(host._render_lock.locked())

    runtime._dispatch_url_change_inner = body
    async with host._render_lock:
        task = asyncio.create_task(runtime.dispatch_url_change({}))
        await asyncio.sleep(0)  # Let the dispatched task reach the held lock.
        ran_while_locked = bool(held)
    await task
    assert not ran_while_locked
    assert held == [True]
    assert not host._render_lock.locked()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["ws", "sse"])
async def test_url_waiter_cannot_operate_on_a_replacement_owner(kind):
    runtime, host = setup(kind)
    body = AsyncMock()
    runtime._dispatch_url_change_inner = body
    async with host._render_lock:
        task = asyncio.create_task(runtime.dispatch_url_change({}))
        await asyncio.sleep(0)
        runtime.view_instance = SimpleNamespace(_tenant=None)
    await task
    body.assert_not_awaited()
    host.send_error.assert_awaited_once_with("View changed. Please reload the page.")


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["ws", "sse"])
async def test_cancelled_url_body_releases_transport_context(kind):
    runtime, host = setup(kind)
    held = []

    async def body(data):
        held.append(host._render_lock.locked())
        raise asyncio.CancelledError()

    runtime._dispatch_url_change_inner = body
    with pytest.raises(asyncio.CancelledError):
        await runtime.dispatch_url_change({})
    assert held == [True]
    assert not host._render_lock.locked()
    if kind == "ws":
        assert host._processing_user_event is False
