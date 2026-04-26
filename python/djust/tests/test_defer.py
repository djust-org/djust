"""Tests for ``LiveView.defer(callback, *args, **kwargs)`` — Phoenix-style
post-render callback scheduling.

Mixin-level behavior (queue + drain + exception isolation) is tested
directly against ``AsyncWorkMixin``. Integration with the WebSocket
flush pipeline (``LiveViewConsumer._flush_deferred``) is verified by
calling the consumer method against a fake view in async tests.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from djust.mixins.async_work import AsyncWorkMixin


class FakeView(AsyncWorkMixin):
    """Minimal view-like class for direct mixin testing."""

    pass


# ---------------------------------------------------------------------------
# defer() queue mechanics
# ---------------------------------------------------------------------------


class TestDeferQueue:
    def test_defer_appends_to_queue(self):
        view = FakeView()
        cb = MagicMock()
        view.defer(cb)

        assert hasattr(view, "_deferred_callbacks")
        assert len(view._deferred_callbacks) == 1
        stored_cb, args, kwargs = view._deferred_callbacks[0]
        assert stored_cb is cb
        assert args == ()
        assert kwargs == {}

    def test_defer_preserves_args_and_kwargs(self):
        view = FakeView()
        cb = MagicMock()
        view.defer(cb, "a", "b", key="value", flag=True)

        stored_cb, args, kwargs = view._deferred_callbacks[0]
        assert stored_cb is cb
        assert args == ("a", "b")
        assert kwargs == {"key": "value", "flag": True}

    def test_defer_multiple_times_appends_all(self):
        """Order of registration is the order of execution."""
        view = FakeView()
        cb1, cb2, cb3 = MagicMock(), MagicMock(), MagicMock()
        view.defer(cb1)
        view.defer(cb2, "x")
        view.defer(cb3, key="y")

        assert len(view._deferred_callbacks) == 3
        assert view._deferred_callbacks[0][0] is cb1
        assert view._deferred_callbacks[1][0] is cb2
        assert view._deferred_callbacks[1][1] == ("x",)
        assert view._deferred_callbacks[2][0] is cb3
        assert view._deferred_callbacks[2][2] == {"key": "y"}


class TestDrainDeferred:
    def test_drain_empty_queue_returns_empty_list(self):
        view = FakeView()
        # Queue never created → returns []
        assert view._drain_deferred() == []
        # Queue created-but-empty also returns []
        view._deferred_callbacks = []
        assert view._drain_deferred() == []

    def test_drain_returns_callbacks_then_clears(self):
        view = FakeView()
        cb = MagicMock()
        view.defer(cb, "arg1", key="val")

        callbacks = view._drain_deferred()
        assert len(callbacks) == 1
        assert callbacks[0][0] is cb
        assert callbacks[0][1] == ("arg1",)
        assert callbacks[0][2] == {"key": "val"}

        # Queue is now empty — second drain returns empty.
        assert view._drain_deferred() == []

    def test_drain_isolates_subsequent_renders(self):
        """A render+drain cycle must not affect callbacks queued during
        the next handler. Previously-drained callbacks must NOT re-fire."""
        view = FakeView()
        cb1, cb2 = MagicMock(), MagicMock()

        view.defer(cb1)
        first_drain = view._drain_deferred()
        assert len(first_drain) == 1
        assert first_drain[0][0] is cb1

        # Simulate next handler queuing a different callback
        view.defer(cb2)
        second_drain = view._drain_deferred()
        assert len(second_drain) == 1
        assert second_drain[0][0] is cb2  # NOT cb1 — first drain already consumed

    def test_defer_returns_none_not_self(self):
        """``defer()`` is a queue-append, not a builder. Don't accidentally
        chain it like ``view.defer(cb).defer(cb2)`` and silently drop the
        second registration."""
        view = FakeView()
        result = view.defer(MagicMock())
        assert result is None


# ---------------------------------------------------------------------------
# WebSocket flush integration — async exception isolation + async-callback
# await semantics. Tests the consumer's _flush_deferred directly against a
# fake view to avoid spinning a real Channels consumer.
# ---------------------------------------------------------------------------


class _FakeConsumer:
    """Minimal stand-in for LiveViewConsumer that exposes _flush_deferred."""

    def __init__(self, view_instance):
        self.view_instance = view_instance

    # Pull the implementation off the real consumer class to exercise
    # the actual flush logic, not a re-implementation. Importing at
    # method definition time keeps test discovery fast.
    async def _flush_deferred(self):
        from djust.websocket import LiveViewConsumer

        return await LiveViewConsumer._flush_deferred(self)


@pytest.mark.asyncio
async def test_flush_deferred_runs_sync_callback_in_order():
    view = FakeView()
    order: list[int] = []
    view.defer(lambda: order.append(1))
    view.defer(lambda: order.append(2))
    view.defer(lambda: order.append(3))

    await _FakeConsumer(view)._flush_deferred()
    assert order == [1, 2, 3]
    # Queue cleared
    assert view._drain_deferred() == []


@pytest.mark.asyncio
async def test_flush_deferred_awaits_async_callback():
    view = FakeView()
    order: list[str] = []

    async def async_cb():
        order.append("async")

    def sync_cb():
        order.append("sync")

    view.defer(async_cb)
    view.defer(sync_cb)

    await _FakeConsumer(view)._flush_deferred()
    assert order == ["async", "sync"]


@pytest.mark.asyncio
async def test_flush_deferred_passes_args_and_kwargs():
    view = FakeView()
    captured = {}

    def cb(arg1, arg2, key=None, flag=False):
        captured["args"] = (arg1, arg2)
        captured["key"] = key
        captured["flag"] = flag

    view.defer(cb, "x", "y", key="z", flag=True)
    await _FakeConsumer(view)._flush_deferred()

    assert captured == {"args": ("x", "y"), "key": "z", "flag": True}


@pytest.mark.asyncio
async def test_flush_deferred_logs_and_continues_on_exception(caplog):
    """A failing deferred callback must not break the connection or stop
    later callbacks from running. Failure is logged at WARN."""
    import logging

    view = FakeView()
    later_ran = []

    def failing_cb():
        raise RuntimeError("boom")

    def later_cb():
        later_ran.append(True)

    view.defer(failing_cb)
    view.defer(later_cb)

    with caplog.at_level(logging.WARNING, logger="djust.websocket"):
        await _FakeConsumer(view)._flush_deferred()

    # Later callback ran despite the earlier one failing.
    assert later_ran == [True]

    # WARN log produced with traceback context.
    matches = [r for r in caplog.records if "Deferred callback" in r.message]
    assert len(matches) == 1
    assert "raised; continuing" in matches[0].message


@pytest.mark.asyncio
async def test_flush_deferred_handles_no_view_instance():
    """Edge case: consumer with view_instance=None must not raise."""

    class _NullConsumer:
        view_instance = None

        async def _flush_deferred(self):
            from djust.websocket import LiveViewConsumer

            return await LiveViewConsumer._flush_deferred(self)

    # Should be a no-op, not raise
    result = await _NullConsumer()._flush_deferred()
    assert result is None


@pytest.mark.asyncio
async def test_flush_deferred_handles_view_without_drain_method():
    """Edge case: view that doesn't subclass AsyncWorkMixin (no
    ``_drain_deferred``) is silently skipped — defensive against legacy
    or third-party view classes."""

    class _LegacyView:
        pass  # No _drain_deferred attribute

    # Should be a no-op, not raise
    await _FakeConsumer(_LegacyView())._flush_deferred()


@pytest.mark.asyncio
async def test_flush_deferred_async_exception_isolated(caplog):
    """Async-callback failure must isolate the same way sync-callback
    failure does."""
    import logging

    view = FakeView()
    later_ran = []

    async def failing_async_cb():
        raise ValueError("async boom")

    def later_cb():
        later_ran.append(True)

    view.defer(failing_async_cb)
    view.defer(later_cb)

    with caplog.at_level(logging.WARNING, logger="djust.websocket"):
        await _FakeConsumer(view)._flush_deferred()

    assert later_ran == [True]
    matches = [r for r in caplog.records if "Deferred callback" in r.message]
    assert len(matches) == 1


# ---------------------------------------------------------------------------
# Drain-reentry semantics — Stage 11 must-fix from PR #1091.
#
# A callback that calls ``self.defer(other_cb)`` queues ``other_cb`` for the
# NEXT drain, not the current one. This matches Phoenix ``send(self(), :foo)``
# semantics and avoids unbounded loops (a callback that re-defers itself does
# NOT spin within a single drain). Implementation: ``_drain_deferred`` clears
# the queue BEFORE iterating its snapshot, so re-entry into ``defer()`` writes
# to a fresh empty queue.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_flush_deferred_callback_that_calls_defer_enqueues_for_next_drain():
    """A callback that calls ``view.defer(other_cb)`` queues ``other_cb``
    for the NEXT drain, NOT the current one."""
    view = FakeView()
    order: list[str] = []

    def second_cb():
        order.append("second")

    def first_cb():
        order.append("first")
        # Re-enter defer — this should NOT fire in the current drain.
        view.defer(second_cb)

    view.defer(first_cb)

    # First drain: only first_cb fires.
    await _FakeConsumer(view)._flush_deferred()
    assert order == ["first"]
    # second_cb is now queued — confirm by inspecting the queue state.
    assert len(view._deferred_callbacks) == 1
    assert view._deferred_callbacks[0][0] is second_cb

    # Second drain: second_cb fires, queue empty.
    await _FakeConsumer(view)._flush_deferred()
    assert order == ["first", "second"]
    assert view._drain_deferred() == []


@pytest.mark.asyncio
async def test_flush_deferred_self_redefer_does_not_loop():
    """A callback that re-defers itself must NOT spin in the current drain.

    Locks the contract that drain-reentry is always 'next tick' — even when
    a callback queues itself, the current drain completes after one
    invocation and the self-re-deferred copy waits for the next drain.
    """
    view = FakeView()
    fire_count = []

    def self_redeferring_cb():
        fire_count.append(True)
        view.defer(self_redeferring_cb)

    view.defer(self_redeferring_cb)

    # First drain: callback fires exactly once, even though it re-defers itself.
    await _FakeConsumer(view)._flush_deferred()
    assert len(fire_count) == 1
    assert len(view._deferred_callbacks) == 1  # the self-re-defer

    # Second drain: callback fires once more, re-defers itself again.
    await _FakeConsumer(view)._flush_deferred()
    assert len(fire_count) == 2
    assert len(view._deferred_callbacks) == 1


# ---------------------------------------------------------------------------
# SSE transport integration — Stage 11 must-fix from PR #1091.
#
# Without this wire-in, SSE-served views that call ``self.defer(...)`` would
# accumulate callbacks indefinitely (slow leak + Phoenix-parity contract
# violation: 'after each render+patch').
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sse_flush_deferred_drains_and_runs_callbacks():
    """``_flush_deferred_to_sse`` mirrors the WS flush — drains queue,
    runs each callback (sync or async), exception-isolates per callback."""
    from djust.sse import _flush_deferred_to_sse

    view = FakeView()
    order: list[str] = []

    def sync_cb():
        order.append("sync")

    async def async_cb():
        order.append("async")

    view.defer(sync_cb)
    view.defer(async_cb)

    await _flush_deferred_to_sse(view)
    assert order == ["sync", "async"]
    assert view._drain_deferred() == []  # queue cleared


@pytest.mark.asyncio
async def test_sse_flush_deferred_handles_no_view_instance():
    """``_flush_deferred_to_sse(None)`` is a no-op, mirroring the WS guard."""
    from djust.sse import _flush_deferred_to_sse

    # Should not raise
    await _flush_deferred_to_sse(None)


@pytest.mark.asyncio
async def test_sse_flush_deferred_isolates_callback_exceptions(caplog):
    """SSE-side exception isolation must match WS-side: failed callback
    logs at WARN and execution continues."""
    import logging

    from djust.sse import _flush_deferred_to_sse

    view = FakeView()
    later_ran = []

    def failing_cb():
        raise RuntimeError("sse boom")

    def later_cb():
        later_ran.append(True)

    view.defer(failing_cb)
    view.defer(later_cb)

    with caplog.at_level(logging.WARNING, logger="djust.sse"):
        await _flush_deferred_to_sse(view)

    assert later_ran == [True]
    matches = [r for r in caplog.records if "Deferred callback" in r.message]
    assert len(matches) == 1
