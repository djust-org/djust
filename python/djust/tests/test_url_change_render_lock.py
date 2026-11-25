"""URL rendering shares the actual WS/SSE mutation lock and rechecks its owner."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from djust import LiveView
from djust.runtime import SSESessionTransport, ViewRuntime
from djust.tests._live_session import amount_on_consumer, amount_on_runtime

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def _allowed_modules(settings):
    settings.LIVEVIEW_ALLOWED_MODULES = [__name__]


class UrlLockView(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root>url lock</div>"


async def setup(kind):
    if kind == "ws":
        from djust.websocket import LiveViewConsumer

        host = LiveViewConsumer()
        host.send_json = AsyncMock()
        # An explicit view's turns need a mounted owner on a saved session.
        await amount_on_consumer(host, f"{__name__}.UrlLockView", view=UrlLockView())
        runtime = host._get_runtime()
    else:
        from djust.sse import SSESession

        host = SSESession("url-lock-probe")
        runtime = ViewRuntime(SSESessionTransport(host))
        await amount_on_runtime(runtime, UrlLockView())
    host.send_error = AsyncMock()
    return runtime, host


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["ws", "sse"])
async def test_url_body_waits_for_real_transport_lock_and_runs_once(kind):
    runtime, host = await setup(kind)
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
    runtime, host = await setup(kind)
    body = AsyncMock()
    runtime._dispatch_url_change_inner = body
    async with host._render_lock:
        task = asyncio.create_task(runtime.dispatch_url_change({}))
        await asyncio.sleep(0)
        runtime.view_instance = SimpleNamespace(_tenant=None, exposure_policy="explicit")
    await task
    body.assert_not_awaited()
    host.send_error.assert_awaited_once_with("View changed. Please reload the page.")


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["ws", "sse"])
async def test_cancelled_url_body_releases_transport_context(kind):
    runtime, host = await setup(kind)
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
