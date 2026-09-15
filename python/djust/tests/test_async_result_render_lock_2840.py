"""#2840 — ``handle_async_result`` must run under the render lock.

Pre-fix, BOTH arms of ``LiveViewConsumer._run_async_work`` awaited
``handle_async_result`` OUTSIDE ``self._render_lock`` (the lock region started
after the handler await), so a handler that mutates view state — the documented
pattern (set ``self.result`` / ``self.error`` so the re-render displays it) —
could interleave with a concurrent lock-holding render of the same view
(server_push / db_notify / ``_tick_once``, or another async result).

The #1646 parallel-path sweep found the same defect — worse — on the runtime
twin ``ViewRuntime._execute_async_task``: its ``handle_async_result`` AND its
render (``_render_async_result``) both ran with NO lock at all, and after the
ADR-022 flip a WS *event's* ``start_async`` work dispatches through that twin.
Its fix borrows the consumer's lock via ``transport.event_context(view)`` —
the ADR-022 mechanism (runtime.py:362 / the WS override at runtime.py:1027);
on WS that acquires the consumer's ``_render_lock``; on SSE it is a no-op
(SSE has no concurrent tick/push loop).

Harness provenance (#1077): ``_SpyView`` / ``_make_consumer`` lifted from
``test_run_async_work_teardown_1940`` (the reference direct driver of this
method); the probe records ``asyncio.Lock.locked()`` at the moment the handler
runs. For the runtime twin the consumer is a real ``LiveViewConsumer`` so
``WSConsumerTransport`` borrows the REAL lock object.

Non-tautology (#1468): every probe test asserts BOTH that the handler ran
exactly once (and its render frame was emitted) AND that the lock was held when
the handler ran — a "fix" that suppressed the handler or skipped the render
fails the once-only / frame assertions; the pre-fix code fails the
lock-held assertions. The ordering test drives the exact #2840 interleave: a
foreign task holding the lock while the async result lands.
"""

import asyncio

import pytest


class _SpyView:
    """Minimal view stand-in recording post-callback writes + the lock probe.

    Lifted from ``test_run_async_work_teardown_1940._SpyView``; adds a
    ``_lock_probe`` list that ``handle_async_result`` appends
    ``consumer_lock.locked()`` to, so the test can assert the lock state at
    handler time.
    """

    def __init__(self, name, lock_probe=None):
        self.name = name
        self.writes = []
        self.handler_calls = 0
        self._lock_probe = lock_probe if lock_probe is not None else []
        self._probe_lock = None  # set by the harness to the consumer's lock

    def handle_async_result(self, task_name, result=None, error=None):
        self.handler_calls += 1
        self.writes.append(("handle_async_result", task_name, result, error))
        if self._probe_lock is not None:
            self._lock_probe.append(self._probe_lock.locked())

    def _sync_state_to_rust(self):
        self.writes.append(("_sync_state_to_rust",))

    def render_with_diff(self):
        self.writes.append(("render_with_diff",))
        # (html, patches, version) — patches=None forces the HTML fallback path,
        # which also writes against the view via the strip/extract helpers.
        return ("<div>x</div>", None, 1)

    def _strip_comments_and_whitespace(self, h):
        self.writes.append(("_strip_comments_and_whitespace",))
        return h

    def _extract_liveview_content(self, h):
        self.writes.append(("_extract_liveview_content",))
        return h


def _make_consumer(view):
    """A real ``LiveViewConsumer`` with sends captured (lifted from #1940)."""
    from djust.websocket import LiveViewConsumer

    consumer = LiveViewConsumer()
    consumer.view_instance = view
    consumer.sent_frames = []

    async def _capture(data):
        consumer.sent_frames.append(data)

    # ``_send_update`` calls ``send_json``; capture every outbound frame.
    consumer.send_json = _capture  # type: ignore[assignment]

    async def _noop():
        return None

    # ``_run_async_work`` flushes pending queues after a successful re-render;
    # stub it so the test doesn't depend on those subsystems.
    consumer._flush_all_pending = _noop  # type: ignore[assignment]
    consumer._flush_push_events = _noop  # type: ignore[assignment]

    # Hand the probe the consumer's REAL render lock.
    view._probe_lock = consumer._render_lock
    return consumer


def _make_runtime_with_consumer(view):
    """A ``ViewRuntime`` over a REAL consumer via ``WSConsumerTransport``.

    The transport's ``event_context`` borrows the consumer's ``_render_lock``
    (runtime.py:1055), so the runtime-twin probe observes the same lock the
    consumer-path probe does. Sends are captured on the consumer.
    """
    from djust.runtime import ViewRuntime, WSConsumerTransport

    consumer = _make_consumer(view)
    runtime = ViewRuntime(WSConsumerTransport(consumer))
    runtime.view_instance = view
    return runtime, consumer


@pytest.mark.asyncio
class TestConsumerAsyncWorkHandlerUnderLock:
    """``LiveViewConsumer._run_async_work`` (#2840, the cited path)."""

    async def test_success_arm_handler_runs_under_render_lock(self):
        probe = []
        view = _SpyView("live", lock_probe=probe)
        consumer = _make_consumer(view)

        async def cb():
            return "done"

        await consumer._run_async_work("work", cb, (), {})

        assert view.handler_calls == 1, "the success handler must run exactly once"
        assert probe == [True], (
            "handle_async_result must run INSIDE _render_lock on the success "
            "arm (#2840): its state mutation must not interleave with a "
            "concurrent lock-holding render"
        )
        assert len(consumer.sent_frames) == 1, "the success re-render frame must still be sent"
        assert consumer.sent_frames[0].get("source") == "async"

    async def test_error_arm_handler_runs_under_render_lock(self):
        probe = []
        view = _SpyView("live", lock_probe=probe)
        consumer = _make_consumer(view)

        async def failing_cb():
            raise ValueError("boom")

        await consumer._run_async_work("work", failing_cb, (), {})

        assert view.handler_calls == 1, "the error handler must run exactly once"
        assert probe == [True], (
            "handle_async_result must run INSIDE _render_lock on the error arm "
            "(#2840) — widening the region must not weaken the error path"
        )
        assert len(consumer.sent_frames) == 1, "the error-state re-render frame must still be sent"
        assert consumer.sent_frames[0].get("source") == "async"

    async def test_handler_waits_for_a_foreign_lock_holder(self):
        """The exact #2840 interleave: a foreign task (the concurrent render)
        holds ``_render_lock`` when the async result lands — the handler must
        WAIT for the lock instead of mutating state mid-render."""
        probe = []
        view = _SpyView("live", lock_probe=probe)
        consumer = _make_consumer(view)

        started = asyncio.Event()
        release = asyncio.Event()

        async def slow_cb():
            started.set()
            await release.wait()  # the await window where a render can be mid-flight
            return "done"

        # Foreign holder emulates the concurrent lock-holding render of #2840.
        await consumer._render_lock.acquire()
        task = asyncio.ensure_future(consumer._run_async_work("work", slow_cb, (), {}))
        await asyncio.wait_for(started.wait(), timeout=1.0)

        release.set()
        # Give the task ample loop ticks to (wrongly) run its handler out of
        # order. Post-fix it stays parked at the lock acquire.
        for _ in range(200):
            await asyncio.sleep(0)
            if task.done():
                break

        assert view.handler_calls == 0, (
            "handle_async_result ran while ANOTHER task held _render_lock — "
            "its state mutation interleaved with a lock-holding render (#2840)"
        )

        consumer._render_lock.release()
        await asyncio.wait_for(task, timeout=1.0)

        # Non-tautology guard: the handler is delayed, never dropped.
        assert view.handler_calls == 1
        assert probe == [True]
        assert len(consumer.sent_frames) == 1


@pytest.mark.asyncio
class TestRuntimeAsyncTaskHandlerUnderLock:
    """``ViewRuntime._execute_async_task`` (#1646 twin, found by the sweep).

    Post ADR-022 2.3b a WS *event's* ``start_async`` work dispatches through
    this runtime helper, so its handler + render must serialize on the
    consumer's lock via ``transport.event_context`` — same object the tick /
    server_push / db_notify loops hold."""

    async def test_success_arm_handler_runs_under_render_lock(self):
        probe = []
        view = _SpyView("live", lock_probe=probe)
        runtime, consumer = _make_runtime_with_consumer(view)

        async def cb():
            return "done"

        await asyncio.wait_for(
            runtime._execute_async_task("work", cb, (), {}, "probe"), timeout=5.0
        )

        assert view.handler_calls == 1, "the success handler must run exactly once"
        assert probe == [True], (
            "the runtime twin's handle_async_result must run under the "
            "consumer's _render_lock (borrowed via transport.event_context, "
            "the ADR-022 mechanism) — pre-fix it ran with NO lock at all"
        )
        assert len(consumer.sent_frames) == 1, "the success re-render frame must still be sent"
        assert consumer.sent_frames[0].get("source") == "async"

    async def test_error_arm_handler_runs_under_render_lock(self):
        probe = []
        view = _SpyView("live", lock_probe=probe)
        runtime, consumer = _make_runtime_with_consumer(view)

        async def failing_cb():
            raise ValueError("boom")

        await asyncio.wait_for(
            runtime._execute_async_task("work", failing_cb, (), {}, "probe"), timeout=5.0
        )

        assert view.handler_calls == 1, "the error handler must run exactly once"
        assert probe == [True], (
            "the runtime twin's error-arm handle_async_result must run under "
            "the consumer's _render_lock (#2840 twin)"
        )
        assert len(consumer.sent_frames) == 1, "the error-state re-render frame must still be sent"

    async def test_stale_view_after_teardown_gets_no_handler_and_no_frame(self):
        """The runtime twin must drop work for a torn-down view — the #1940
        identity-guard the WS twin already has (pre-fix the twin had NO
        post-await guard at all: the handler wrote the stale view and the
        render re-read ``view_instance`` live, contaminating a replacement)."""
        old_view = _SpyView("old")
        new_view = _SpyView("new")
        runtime, consumer = _make_runtime_with_consumer(old_view)

        started = asyncio.Event()
        release = asyncio.Event()

        async def slow_cb():
            started.set()
            await release.wait()
            return "done"

        task = asyncio.ensure_future(runtime._execute_async_task("work", slow_cb, (), {}, "probe"))
        await asyncio.wait_for(started.wait(), timeout=1.0)

        # Re-mount mid-await: the live view is now a NEW object.
        runtime.view_instance = new_view
        consumer.view_instance = new_view

        release.set()
        await asyncio.wait_for(task, timeout=1.0)

        assert old_view.handler_calls == 0, "stale OLD view's handler ran after re-mount"
        assert old_view.writes == [], "stale OLD view written after re-mount"
        assert new_view.handler_calls == 0, "NEW view contaminated by the stale async task"
        assert new_view.writes == [], "NEW view rendered by the stale async task"
        assert consumer.sent_frames == [], "a frame was sent for a torn-down view"
