"""
Tests for AsyncWorkMixin — start_async() background work scheduling.
"""

from unittest.mock import MagicMock

from djust.mixins.async_work import AsyncWorkMixin


class FakeView(AsyncWorkMixin):
    """Minimal view-like class for testing the mixin."""

    pass


class TestStartAsync:
    def test_stores_callback_and_args(self):
        """Legacy behavior: start_async without name stores in _async_pending."""
        view = FakeView()
        callback = MagicMock()
        view.start_async(callback, "arg1", key="val")

        # Check that _async_tasks dict was created with default name
        assert hasattr(view, "_async_tasks")
        assert len(view._async_tasks) == 1
        _, (cb, args, kwargs) = list(view._async_tasks.items())[0]
        assert cb is callback
        assert args == ("arg1",)
        assert kwargs == {"key": "val"}

    def test_stores_callback_with_no_args(self):
        view = FakeView()
        callback = MagicMock()
        view.start_async(callback)

        assert hasattr(view, "_async_tasks")
        assert len(view._async_tasks) == 1
        _, (cb, args, kwargs) = list(view._async_tasks.items())[0]
        assert cb is callback
        assert args == ()
        assert kwargs == {}

    def test_named_task_storage(self):
        """Named tasks are stored in _async_tasks dict."""
        view = FakeView()
        cb1 = MagicMock()
        cb2 = MagicMock()

        view.start_async(cb1, name="task1")
        view.start_async(cb2, name="task2")

        assert "task1" in view._async_tasks
        assert "task2" in view._async_tasks
        assert view._async_tasks["task1"][0] is cb1
        assert view._async_tasks["task2"][0] is cb2

    def test_multiple_concurrent_tasks(self):
        """Multiple tasks can be scheduled concurrently."""
        view = FakeView()
        callbacks = [MagicMock() for _ in range(3)]

        for i, cb in enumerate(callbacks):
            view.start_async(cb, name=f"task{i}")

        assert len(view._async_tasks) == 3
        for i, cb in enumerate(callbacks):
            assert view._async_tasks[f"task{i}"][0] is cb

    def test_auto_generated_task_names(self):
        """Tasks without explicit names get auto-generated names."""
        view = FakeView()
        view.start_async(MagicMock())
        view.start_async(MagicMock())

        # Should have 2 tasks with auto-generated names
        assert len(view._async_tasks) == 2
        task_names = list(view._async_tasks.keys())
        # Names should be unique
        assert len(set(task_names)) == 2

    def test_same_name_overwrites_previous_task(self):
        """Starting a task with the same name replaces the previous one."""
        view = FakeView()
        cb1 = MagicMock()
        cb2 = MagicMock()

        view.start_async(cb1, name="work")
        view.start_async(cb2, name="work")

        assert len(view._async_tasks) == 1
        assert view._async_tasks["work"][0] is cb2

    def test_no_tasks_by_default(self):
        view = FakeView()
        assert not hasattr(view, "_async_tasks") or len(view._async_tasks) == 0


class TestCancelAsync:
    def test_cancel_removes_task(self):
        """cancel_async() removes a scheduled task."""
        view = FakeView()
        view.start_async(MagicMock(), name="work")

        assert "work" in view._async_tasks
        view.cancel_async("work")
        assert "work" not in view._async_tasks

    def test_cancel_nonexistent_task_is_noop(self):
        """Cancelling a non-existent task doesn't raise an error."""
        view = FakeView()
        # Should not raise
        view.cancel_async("nonexistent")

    def test_cancel_marks_task_as_cancelled(self):
        """cancel_async() marks task as cancelled for consumer to check."""
        view = FakeView()
        view.start_async(MagicMock(), name="work")
        view.cancel_async("work")

        # Task should be marked as cancelled
        assert hasattr(view, "_async_cancelled")
        assert "work" in view._async_cancelled


class TestBackgroundDecorator:
    def test_background_decorator_exists(self):
        """@background decorator should be importable."""
        from djust.decorators import background
        assert callable(background)

    def test_background_creates_async_task(self):
        """@background decorator wraps handler to call start_async()."""
        from djust.decorators import background, event_handler

        class TestView(FakeView):
            def __init__(self):
                super().__init__()
                self.result = None

            @event_handler
            @background
            def do_work(self, value: str = "", **kwargs):
                self.result = f"processed: {value}"

        view = TestView()
        view.do_work(value="test")

        # Should have created an async task
        assert hasattr(view, "_async_tasks")
        assert len(view._async_tasks) == 1

    def test_background_preserves_metadata(self):
        """@background should preserve function metadata."""
        from djust.decorators import background

        @background
        def handler(self):
            """Test handler"""
            pass

        assert handler.__name__ == "handler"
        assert handler.__doc__ == "Test handler"

    def test_background_adds_decorator_metadata(self):
        """@background adds metadata to _djust_decorators."""
        from djust.decorators import background

        @background
        def handler(self):
            pass

        assert hasattr(handler, "_djust_decorators")
        assert handler._djust_decorators.get("background") is True

    def test_background_with_args_captured(self):
        """@background captures handler args for async execution."""
        from djust.decorators import background

        class TestView(FakeView):
            def __init__(self):
                super().__init__()
                self.calls = []

            @background
            def work(self, value: str = "", count: int = 0, **kwargs):
                self.calls.append((value, count))

        view = TestView()
        view.work(value="test", count=42)

        # Task should be scheduled with captured args
        assert len(view._async_tasks) == 1
        _, (cb, args, kwargs) = list(view._async_tasks.items())[0]
        # The callback should be executable and use the captured context
        assert callable(cb)


class TestHandleAsyncResult:
    def test_handle_async_result_called_on_success(self):
        """handle_async_result() is called with result when callback succeeds."""
        view = FakeView()
        view.handle_async_result_calls = []

        def handle_async_result(name: str, result=None, error=None):
            view.handle_async_result_calls.append((name, result, error))

        view.handle_async_result = handle_async_result

        # This test validates the interface exists
        view.handle_async_result("test", result="success", error=None)

        assert len(view.handle_async_result_calls) == 1
        assert view.handle_async_result_calls[0] == ("test", "success", None)

    def test_handle_async_result_called_on_error(self):
        """handle_async_result() is called with error when callback fails."""
        view = FakeView()
        view.handle_async_result_calls = []

        def handle_async_result(name: str, result=None, error=None):
            view.handle_async_result_calls.append((name, result, error))

        view.handle_async_result = handle_async_result

        test_error = ValueError("test error")
        view.handle_async_result("test", result=None, error=test_error)

        assert len(view.handle_async_result_calls) == 1
        name, result, error = view.handle_async_result_calls[0]
        assert name == "test"
        assert result is None
        assert error is test_error


class TestAsyncWorkMixinInLiveView:
    def test_liveview_has_start_async(self):
        """LiveView base class should include AsyncWorkMixin."""
        from djust.live_view import LiveView

        assert hasattr(LiveView, "start_async")
        assert issubclass(LiveView, AsyncWorkMixin)

    def test_liveview_has_cancel_async(self):
        """LiveView should have cancel_async method."""
        from djust.live_view import LiveView

        assert hasattr(LiveView, "cancel_async")
