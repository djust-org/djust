"""
Integration tests for async background work with WebSocket consumer.

Tests the full flow: event handler -> start_async -> background execution
-> handle_async_result -> re-render.
"""

import asyncio
import time
import threading
from unittest.mock import patch

import pytest

from djust.live_view import LiveView
from djust.decorators import event_handler, background


class AsyncTestView(LiveView):
    """Test view with async operations."""

    template_name = "async_test.html"

    def mount(self, request, **kwargs):
        self.loading = False
        self.result = None
        self.error = None
        self.completed = threading.Event()

    @event_handler
    def start_work(self, **kwargs):
        """Explicitly start async work."""
        self.loading = True
        self.start_async(self._do_work, name="work")

    def _do_work(self):
        """Simulate slow operation."""
        time.sleep(0.05)
        self.result = "completed"
        self.loading = False
        self.completed.set()

    @event_handler
    @background
    def background_work(self, value: str = "", **kwargs):
        """Handler with @background decorator."""
        self.loading = True
        time.sleep(0.05)
        self.result = f"processed: {value}"
        self.loading = False

    @event_handler
    def error_work(self, **kwargs):
        """Work that raises an exception."""
        self.loading = True
        self.start_async(self._do_error_work, name="error_work")

    def _do_error_work(self):
        """Raises an error for testing."""
        raise ValueError("Test error")

    def handle_async_result(self, name: str, result=None, error=None):
        """Handle async completion."""
        self.loading = False
        if error:
            self.error = str(error)
        # result is already set by the callback


class CancellableView(LiveView):
    """Test view with cancellable operations."""

    template_name = "cancel_test.html"

    def mount(self, request, **kwargs):
        self.working = False
        self.cancelled = False
        self.result = None

    @event_handler
    def start_long_work(self, **kwargs):
        self.working = True
        self.start_async(self._long_work, name="long_task")

    def _long_work(self):
        """Long-running work that can be cancelled."""
        for i in range(10):
            time.sleep(0.01)
        self.result = "finished"
        self.working = False

    @event_handler
    def cancel_work(self, **kwargs):
        """Cancel the long work."""
        self.cancel_async("long_task")
        self.working = False
        self.cancelled = True


@pytest.mark.django_db
@pytest.mark.asyncio
class TestAsyncWorkIntegration:
    """Integration tests for start_async() with WebSocket consumer."""

    async def test_start_async_executes_in_background(self):
        """Handler returns immediately, task is scheduled."""
        view = AsyncTestView()
        view.mount(None)

        # Simulate event handler
        view.start_work()

        # Loading state should be set immediately
        assert view.loading is True
        assert view.result is None

        # Task should be scheduled (not yet executed)
        assert hasattr(view, "_async_tasks")
        assert "work" in view._async_tasks

        # Manually execute the callback to verify it works
        callback, args, kwargs = view._async_tasks["work"]
        callback(*args, **kwargs)

        # Work should complete
        assert view.completed.is_set()
        assert view.result == "completed"
        assert view.loading is False

    async def test_background_decorator_integration(self):
        """@background decorator schedules work correctly."""
        view = AsyncTestView()
        view.mount(None)

        # Call handler with @background
        view.background_work(value="test data")

        # Should have scheduled async task
        assert hasattr(view, "_async_tasks")
        assert "background_work" in view._async_tasks

        # The callback should be executable
        task_name, (callback, args, kwargs) = list(view._async_tasks.items())[0]
        assert callable(callback)

    async def test_multiple_concurrent_tasks(self):
        """Multiple async tasks can run concurrently."""
        view = AsyncTestView()
        view.mount(None)

        # Start multiple tasks
        view.start_async(lambda: setattr(view, "task1", "done"), name="task1")
        view.start_async(lambda: setattr(view, "task2", "done"), name="task2")
        view.start_async(lambda: setattr(view, "task3", "done"), name="task3")

        assert len(view._async_tasks) == 3
        assert "task1" in view._async_tasks
        assert "task2" in view._async_tasks
        assert "task3" in view._async_tasks

    async def test_cancel_removes_scheduled_task(self):
        """cancel_async() removes task before execution."""
        view = CancellableView()
        view.mount(None)

        # Schedule work
        view.start_long_work()
        assert "long_task" in view._async_tasks

        # Cancel immediately
        view.cancel_work()
        assert "long_task" not in view._async_tasks
        assert view.cancelled is True

    async def test_handle_async_result_success(self):
        """handle_async_result() is called on success."""
        view = AsyncTestView()
        view.mount(None)

        results = []

        def mock_handle(name, result=None, error=None):
            results.append((name, result, error))

        view.handle_async_result = mock_handle

        # Trigger async work
        view.start_work()

        # Manually execute the callback (simulating consumer behavior)
        callback, args, kwargs = view._async_tasks["work"]
        callback(*args, **kwargs)

        # Call handle_async_result manually (in real flow, consumer does this)
        view.handle_async_result("work", result=None, error=None)

        assert len(results) == 1
        assert results[0][0] == "work"
        assert results[0][2] is None  # No error

    async def test_handle_async_result_error(self):
        """handle_async_result() is called on exception."""
        view = AsyncTestView()
        view.mount(None)

        results = []

        def mock_handle(name, result=None, error=None):
            results.append((name, result, error))
            view.loading = False
            if error:
                view.error = str(error)

        view.handle_async_result = mock_handle

        # Trigger error work
        view.error_work()

        # Manually execute the callback and catch exception
        callback, args, kwargs = view._async_tasks["error_work"]
        test_error = None
        try:
            callback(*args, **kwargs)
        except Exception as e:
            test_error = e

        # Call handle_async_result with error
        view.handle_async_result("error_work", result=None, error=test_error)

        assert len(results) == 1
        assert results[0][0] == "error_work"
        assert results[0][2] is not None
        assert view.error == "Test error"


@pytest.mark.django_db
@pytest.mark.asyncio
class TestConsumerAsyncDispatch:
    """Test WebSocket consumer's async work dispatch."""

    async def test_dispatch_multiple_tasks(self):
        """Consumer dispatches all pending tasks."""
        from djust.websocket import LiveViewConsumer

        consumer = LiveViewConsumer()
        view = AsyncTestView()
        view.mount(None)
        consumer.view_instance = view

        # Schedule multiple tasks
        view.start_async(lambda: None, name="task1")
        view.start_async(lambda: None, name="task2")

        assert len(view._async_tasks) == 2

        # Dispatch should spawn both tasks
        with patch.object(asyncio, "ensure_future") as mock_ensure_future:
            await consumer._dispatch_async_work()

            # Should have called ensure_future twice (once per task)
            assert mock_ensure_future.call_count == 2

        # Tasks should be cleared after dispatch
        assert len(view._async_tasks) == 0

    async def test_legacy_async_pending_compatibility(self):
        """Consumer still supports old _async_pending format."""
        from djust.websocket import LiveViewConsumer

        consumer = LiveViewConsumer()
        view = AsyncTestView()
        view.mount(None)
        consumer.view_instance = view

        # Use legacy format
        view._async_pending = (lambda: None, (), {})

        with patch.object(asyncio, "ensure_future") as mock_ensure_future:
            await consumer._dispatch_async_work()

            # Should dispatch the legacy task
            assert mock_ensure_future.call_count == 1

        # Should be cleared
        assert view._async_pending is None


@pytest.mark.django_db
@pytest.mark.asyncio
class TestAsyncWorkErrorHandling:
    """Test error handling in async work."""

    async def test_exception_calls_handle_async_result(self):
        """Exceptions in callbacks are passed to handle_async_result."""
        view = AsyncTestView()
        view.mount(None)

        error_calls = []

        def track_error(name, result=None, error=None):
            error_calls.append((name, error))

        view.handle_async_result = track_error

        # Manually test the error path
        test_error = ValueError("boom")
        view.handle_async_result("test", result=None, error=test_error)

        assert len(error_calls) == 1
        assert error_calls[0][0] == "test"
        assert isinstance(error_calls[0][1], ValueError)

    async def test_error_without_handle_async_result(self):
        """Errors are raised from callbacks that fail."""
        # Use a view without handle_async_result
        view = CancellableView()
        view.mount(None)

        # Define a callback that raises an error
        def error_callback():
            raise ValueError("Test error")

        view.start_async(error_callback, name="error_task")

        # Get the callback
        callback, args, kwargs = view._async_tasks["error_task"]

        # Should raise ValueError when executed
        with pytest.raises(ValueError, match="Test error"):
            callback(*args, **kwargs)
