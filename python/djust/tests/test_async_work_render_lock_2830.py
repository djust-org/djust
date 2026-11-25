"""#2830 — the WS async-work render path must serialise on ``_render_lock``.

``_run_async_work`` renders the view without holding the consumer's
``_render_lock``, unlike ``server_push``, ``db_notify`` and ``_tick_once``,
which all acquire it. The render helper it calls documents that "the caller MUST
already hold ``self._render_lock``", so the async path violates a stated
precondition — on a PyO3 view whose VDOM baseline is not thread-safe.

The test holds the lock itself rather than racing a timer. Be precise about what
that buys:

- The FIXED direction is unconditional: a path that respects the lock cannot
  render through a lock the test holds, so it cannot flake green-to-red.
- The DETECTION direction (unfixed code) is settle-window based: the assertion
  is correct only if the buggy path reaches its render inside the 0.1s window.
  Measured on base under full CPU saturation, task-start to `render_with_diff`
  was 2.7 / 17.7 / 8.0 ms — roughly 6-37x headroom — and 0 of 44 adversarial
  runs (including 12 concurrent pytest processes) false-passed. A false-pass
  would need the hop chain ~6x slower than the worst observed.

Both arms are covered: the success arm and the ERROR arm, which re-renders to
display the error state and is the documented `handle_async_result` pattern.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from djust import LiveView


class _AsyncView(LiveView):
    exposure_policy = "explicit"
    template = "<div>{{ n }}</div>"

    def mount(self, request, **kwargs):
        self.n = 0

    def get_context_data(self, **kwargs):
        return {"n": self.n}


async def _make_consumer(view_class):
    """A real consumer with only frame capture stubbed, mirroring the harness in
    ``test_live_redirect_skip_render_1643.py``. The view is mounted through the
    real ``_mount_page`` (whose render establishes the VDOM baseline): an
    explicit view's background work is authorized by the runtime that mounted
    it."""
    from djust.tests._live_session import amount_on_consumer
    from djust.websocket import LiveViewConsumer

    consumer = LiveViewConsumer()
    sent = []

    async def _send_json(msg):
        sent.append(msg)

    consumer.send_json = _send_json
    await amount_on_consumer(consumer, f"{__name__}.{view_class.__name__}")
    sent.clear()
    rate_limiter = MagicMock()
    rate_limiter.check.return_value = True
    consumer._rate_limiter = rate_limiter
    return consumer, sent


async def _run_with_lock_held_from_callback_start(consumer, callback):
    """Start ``_run_async_work`` and take ``_render_lock`` once the callback runs.

    An explicit view's async turn is authorized under ``_render_lock`` BEFORE
    the callback starts, so a lock taken before the task starts would stall
    that authorization and never reach the render under test. Holding it from
    the moment the callback runs leaves exactly the result render to contend
    for it, as an in-flight event-path render would. Returns the task and the
    event that lets the callback finish.
    """
    import threading

    started = threading.Event()
    proceed = threading.Event()

    def gated():
        started.set()
        proceed.wait(5)
        return callback()

    task = asyncio.ensure_future(consumer._run_async_work("t", gated, (), {}))
    for _ in range(1000):
        if started.is_set():
            break
        await asyncio.sleep(0.005)
    assert started.is_set(), "the async callback never started"
    return task, proceed


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_async_render_waits_for_the_render_lock():
    consumer, _sent = await _make_consumer(_AsyncView)
    view = consumer.view_instance

    # Count the renders this path performs. The baseline render above is already
    # done, so anything counted here comes from _run_async_work.
    renders: list[int] = []
    real_render = view.render_with_diff

    def counting_render(*args, **kwargs):
        renders.append(1)
        return real_render(*args, **kwargs)

    view.render_with_diff = counting_render

    task, proceed = await _run_with_lock_held_from_callback_start(consumer, lambda: None)
    # Hold the lock, exactly as an in-flight event-path render would.
    await consumer._render_lock.acquire()
    try:
        proceed.set()
        # Let the task run as far as it can. 0.1s is a SETTLE window, not a
        # threshold: if the path ignores the lock it will have rendered within
        # milliseconds, and if it respects the lock it can never render here.
        await asyncio.sleep(0.1)
        rendered_while_locked = len(renders)
    finally:
        consumer._render_lock.release()

    assert rendered_while_locked == 0, (
        "the async-work render ran while _render_lock was held by another "
        "render — it must serialise on the lock like server_push / db_notify / "
        "_tick_once (#2830)"
    )

    await asyncio.wait_for(task, timeout=5)
    assert len(renders) >= 1, "the async render must still happen once the lock is free"


class _ErrorAsyncView(LiveView):
    """The documented error-state pattern: a callback that raises, plus
    ``handle_async_result`` to render the error."""

    exposure_policy = "explicit"
    template = "<div>{{ n }}</div>"

    def mount(self, request, **kwargs):
        self.n = 0

    def handle_async_result(self, task_name, result=None, error=None):
        self.n = -1

    def get_context_data(self, **kwargs):
        return {"n": self.n}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_ERROR_arm_render_also_waits_for_the_render_lock():
    """The error arm re-renders through the same helper, so it must hold the
    same lock — it was the arm the first version of this fix missed."""
    consumer, _sent = await _make_consumer(_ErrorAsyncView)
    view = consumer.view_instance

    renders: list[int] = []
    real_render = view.render_with_diff

    def counting_render(*args, **kwargs):
        renders.append(1)
        return real_render(*args, **kwargs)

    view.render_with_diff = counting_render

    def _boom():
        raise RuntimeError("callback failed")

    task, proceed = await _run_with_lock_held_from_callback_start(consumer, _boom)
    await consumer._render_lock.acquire()
    try:
        proceed.set()
        await asyncio.sleep(0.1)
        rendered_while_locked = len(renders)
    finally:
        consumer._render_lock.release()

    assert rendered_while_locked == 0, (
        "the error-arm re-render ran while _render_lock was held — the error arm "
        "renders through the same helper and must serialise too (#2830)"
    )

    await asyncio.wait_for(task, timeout=5)
    assert len(renders) >= 1, "the error re-render must still happen once the lock is free"
