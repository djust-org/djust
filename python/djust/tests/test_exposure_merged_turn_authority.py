"""ADR-038 authority on the server-turn paths main 1.2.1 added.

Merging main's #3001 (deferred server_push drain), #2963 (cancelled-task
settle frame) and #2969 (cancel_async / cancel_async_all) into the ADR-038
line added paths that run application code or render outside an event turn.
Each must take fresh authority for a nonlegacy root, like every other
server-originated turn (D-l), and a cancelled explicit task must not run.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from djust.websocket import LiveViewConsumer

pytestmark = pytest.mark.asyncio


def _explicit_view():
    view = MagicMock()
    view.exposure_policy = "explicit"
    # Plain values for framework flags a MagicMock would answer truthily.
    view._djust_child_disposed = False
    view._async_work_generation = 0
    return view


def _consumer(view):
    consumer = LiveViewConsumer()
    consumer.view_instance = view
    consumer.send_json = AsyncMock()
    consumer.send_error = AsyncMock()
    return consumer


async def test_deferred_push_drain_is_authorized_once_and_denied_whole():
    """A revoked session: no queued push reaches the view, nothing renders,
    and the render lock is released."""
    view = _explicit_view()
    consumer = _consumer(view)
    calls = []

    async def deny(v):
        calls.append(v)
        return False

    consumer._authorize_explicit_consumer_turn = deny
    applied = []

    async def apply(v, event):
        applied.append(event)

    consumer._apply_server_push = apply
    consumer._send_update = AsyncMock()

    await consumer._render_lock.acquire()
    consumer._defer_server_push({"n": 1})
    consumer._defer_server_push({"n": 2})
    consumer._render_lock.release()
    await asyncio.wait_for(consumer._push_drain_task, timeout=1)

    assert calls == [view], "one authority check for the whole drained turn"
    assert applied == []
    consumer._send_update.assert_not_awaited()
    assert not consumer._render_lock.locked()


async def test_cancelled_task_settle_frame_needs_current_authority():
    view = _explicit_view()
    consumer = _consumer(view)
    consumer._authorize_explicit_consumer_turn = AsyncMock(return_value=False)
    consumer._send_async_render = AsyncMock()

    await consumer._settle_cancelled_async(view, "go")

    consumer._authorize_explicit_consumer_turn.assert_awaited_once_with(view)
    consumer._send_async_render.assert_not_awaited()
    assert not consumer._render_lock.locked()


async def test_consumer_async_prestart_authority_runs_under_the_render_lock():
    """The pre-start check stashes and pops the view's authorized request, so
    it must not interleave with a turn that holds the lock."""
    view = _explicit_view()
    consumer = _consumer(view)
    seen = []

    async def authorize(v):
        seen.append(consumer._render_lock.locked())
        return False

    consumer._authorize_explicit_consumer_turn = authorize
    ran = []
    await consumer._run_async_work("job", lambda: ran.append(1), (), {}, event_name="go")
    assert seen == [True]
    assert ran == []
    assert not consumer._render_lock.locked()


@pytest.mark.parametrize("when", ["before", "during"])
async def test_explicit_runtime_task_honours_cancel_async(when):
    """#2969 on the explicit runtime path: a task cancelled before it starts
    never runs; one cancelled while running never reaches its result
    handler."""
    from djust.runtime import ViewRuntime

    view = _explicit_view()
    view._async_cancelled = {"job"} if when == "before" else set()
    runtime = ViewRuntime.__new__(ViewRuntime)
    runtime.view_instance = view
    runtime._explicit_event_lock = asyncio.Lock()
    transport = MagicMock()

    class _Ctx:
        async def __aenter__(self):
            return None

        async def __aexit__(self, *exc):
            return False

    transport.event_context = lambda v: _Ctx()
    runtime.transport = transport
    runtime.authorize_explicit_turn = AsyncMock()
    runtime.deny_explicit_turn = AsyncMock()
    ran = []

    def callback():
        ran.append("callback")
        if when == "during":
            view._async_cancelled.add("job")
        return 1

    view.handle_async_result = MagicMock()
    await runtime._execute_explicit_async_task(view, "job", callback, (), {}, "go")
    if when == "before":
        assert ran == []
        runtime.authorize_explicit_turn.assert_not_awaited()
    else:
        assert ran == ["callback"]
        view.handle_async_result.assert_not_called()


async def test_cancel_during_the_prestart_lock_wait_stops_the_callback():
    """cancel_async() that lands while the pre-start authority waits for the
    render lock must still keep the callback from running (#2969)."""
    view = _explicit_view()
    view._async_cancelled = set()
    consumer = _consumer(view)
    consumer._authorize_explicit_consumer_turn = AsyncMock(return_value=True)
    consumer._settle_cancelled_async = AsyncMock()
    ran = []

    await consumer._render_lock.acquire()
    task = asyncio.ensure_future(
        consumer._run_async_work("job", lambda: ran.append(1), (), {}, event_name="go")
    )
    await asyncio.sleep(0.01)  # waiting for the lock
    view._async_cancelled.add("job")  # cancel_async("job")
    consumer._render_lock.release()
    await asyncio.wait_for(task, timeout=1)

    assert ran == []
    consumer._settle_cancelled_async.assert_awaited_once_with(view, "go")
    assert "job" not in view._async_cancelled
