"""Tests for @background with async def handlers (#692, #697).

Verifies that @background correctly handles both sync and async def
handlers.  #692 added a workaround in ``_run_async_work`` to detect
unawaited coroutine returns.  #697 upgraded the ``@background``
decorator itself to detect ``asyncio.iscoroutinefunction`` and create
a native async closure, eliminating the workaround for new code.
"""

from __future__ import annotations

import asyncio
import inspect

import pytest

from djust.decorators import background, event_handler


# ---------------------------------------------------------------------------
# Minimal stubs
# ---------------------------------------------------------------------------


class _FakeView:
    """Minimal view that captures start_async calls."""

    def __init__(self):
        self._scheduled: list[tuple[str, object]] = []
        self._results: list[object] = []

    def start_async(self, callback, *args, name: str = None, **kwargs):
        self._scheduled.append((name, callback))


class _AsyncRunner:
    """Simulates the consumer's _run_async_work coroutine detection."""

    @staticmethod
    async def run_callback(callback, *args, **kwargs):
        """Mirror the real _run_async_work coroutine detection logic."""
        result = callback(*args, **kwargs)
        if inspect.iscoroutine(result):
            result = await result
        return result


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestBackgroundSyncHandler:
    """@background with a regular (sync) def handler."""

    def test_sync_handler_schedules_via_start_async(self):
        """The wrapper calls start_async with a closure."""
        view = _FakeView()

        @event_handler
        @background
        def do_work(self, **kwargs):
            self._results.append("sync_done")

        do_work(view)
        assert len(view._scheduled) == 1
        assert view._scheduled[0][0] == "do_work"

    @pytest.mark.asyncio
    async def test_sync_handler_body_executes(self):
        """The sync handler body actually runs when the callback is awaited."""
        view = _FakeView()

        @event_handler
        @background
        def do_work(self, **kwargs):
            self._results.append("sync_done")

        do_work(view)
        callback = view._scheduled[0][1]
        await _AsyncRunner.run_callback(callback)
        assert "sync_done" in view._results

    @pytest.mark.asyncio
    async def test_sync_handler_return_is_not_coroutine(self):
        """A sync handler's callback does not return a coroutine."""
        view = _FakeView()

        @event_handler
        @background
        def do_work(self, **kwargs):
            return 42

        do_work(view)
        callback = view._scheduled[0][1]
        result = callback()
        assert not inspect.iscoroutine(result)
        assert result == 42


class TestBackgroundAsyncHandler:
    """@background with an async def handler (#692)."""

    def test_async_handler_schedules_via_start_async(self):
        """The wrapper calls start_async even for async def handlers."""
        view = _FakeView()

        @event_handler
        @background
        async def do_work(self, **kwargs):
            self._results.append("async_done")

        do_work(view)
        assert len(view._scheduled) == 1
        assert view._scheduled[0][0] == "do_work"

    @pytest.mark.asyncio
    async def test_async_handler_returns_coroutine(self):
        """Calling the callback of an async handler returns a coroutine."""
        view = _FakeView()

        @event_handler
        @background
        async def do_work(self, **kwargs):
            self._results.append("async_done")

        do_work(view)
        callback = view._scheduled[0][1]
        result = callback()
        assert inspect.iscoroutine(result)
        # Clean up — await the coroutine so it doesn't warn
        await result

    @pytest.mark.asyncio
    async def test_async_handler_body_executes_via_runner(self):
        """The async handler body actually executes when the coroutine is awaited.

        This is the core regression test for #692: without the coroutine
        detection in _run_async_work, the handler body never runs.
        """
        view = _FakeView()

        @event_handler
        @background
        async def do_work(self, **kwargs):
            self._results.append("async_done")

        do_work(view)
        callback = view._scheduled[0][1]
        await _AsyncRunner.run_callback(callback)
        assert "async_done" in view._results

    @pytest.mark.asyncio
    async def test_async_handler_with_await(self):
        """An async handler that uses await internally works correctly."""
        view = _FakeView()

        @event_handler
        @background
        async def do_work(self, **kwargs):
            await asyncio.sleep(0)
            self._results.append("awaited_done")

        do_work(view)
        callback = view._scheduled[0][1]
        await _AsyncRunner.run_callback(callback)
        assert "awaited_done" in view._results


class TestCoroutineDetection:
    """Tests for the coroutine detection pattern used in _run_async_work."""

    @pytest.mark.asyncio
    async def test_sync_result_passed_through(self):
        def sync_fn():
            return "hello"

        result = await _AsyncRunner.run_callback(sync_fn)
        assert result == "hello"

    @pytest.mark.asyncio
    async def test_coroutine_result_awaited(self):
        async def async_fn():
            return "world"

        result = await _AsyncRunner.run_callback(async_fn)
        assert result == "world"

    @pytest.mark.asyncio
    async def test_none_result_handled(self):
        def none_fn():
            return None

        result = await _AsyncRunner.run_callback(none_fn)
        assert result is None

    @pytest.mark.asyncio
    async def test_async_exception_propagates(self):
        async def bad_fn():
            raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            await _AsyncRunner.run_callback(bad_fn)


class TestBackgroundNativeAsyncDetection:
    """#697: @background should natively detect async def handlers.

    The decorator should use asyncio.iscoroutinefunction to create an
    async closure, so _run_async_work can call it directly on the event
    loop instead of routing through sync_to_async first.
    """

    def test_async_handler_closure_is_coroutinefunction(self):
        """The closure for an async handler is itself a coroutine function."""
        view = _FakeView()

        @event_handler
        @background
        async def do_work(self, **kwargs):
            self._results.append("async_native")

        do_work(view)
        callback = view._scheduled[0][1]
        assert asyncio.iscoroutinefunction(callback)

    def test_sync_handler_closure_is_not_coroutinefunction(self):
        """The closure for a sync handler is NOT a coroutine function."""
        view = _FakeView()

        @event_handler
        @background
        def do_work(self, **kwargs):
            self._results.append("sync")

        do_work(view)
        callback = view._scheduled[0][1]
        assert not asyncio.iscoroutinefunction(callback)

    @pytest.mark.asyncio
    async def test_native_async_closure_executes_correctly(self):
        """Awaiting the async closure runs the handler body."""
        view = _FakeView()

        @event_handler
        @background
        async def do_work(self, **kwargs):
            await asyncio.sleep(0)
            self._results.append("native_async_done")

        do_work(view)
        callback = view._scheduled[0][1]
        await callback()
        assert "native_async_done" in view._results

    @pytest.mark.asyncio
    async def test_native_async_closure_propagates_exceptions(self):
        """Exceptions from async handlers propagate through the closure."""
        view = _FakeView()

        @event_handler
        @background
        async def do_work(self, **kwargs):
            raise ValueError("async boom")

        do_work(view)
        callback = view._scheduled[0][1]
        with pytest.raises(ValueError, match="async boom"):
            await callback()

    @pytest.mark.asyncio
    async def test_native_async_closure_return_value(self):
        """The async closure preserves the handler's return value."""
        view = _FakeView()

        @event_handler
        @background
        async def do_work(self, **kwargs):
            return 42

        do_work(view)
        callback = view._scheduled[0][1]
        result = await callback()
        assert result == 42
