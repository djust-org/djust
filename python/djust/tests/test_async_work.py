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
        view = FakeView()
        callback = MagicMock()
        view.start_async(callback, "arg1", key="val")

        assert view._async_pending is not None
        cb, args, kwargs = view._async_pending
        assert cb is callback
        assert args == ("arg1",)
        assert kwargs == {"key": "val"}

    def test_stores_callback_with_no_args(self):
        view = FakeView()
        callback = MagicMock()
        view.start_async(callback)

        cb, args, kwargs = view._async_pending
        assert cb is callback
        assert args == ()
        assert kwargs == {}

    def test_overwrites_previous_pending(self):
        view = FakeView()
        cb1 = MagicMock()
        cb2 = MagicMock()

        view.start_async(cb1)
        view.start_async(cb2)

        cb, _, _ = view._async_pending
        assert cb is cb2

    def test_no_pending_by_default(self):
        view = FakeView()
        assert not hasattr(view, "_async_pending") or view._async_pending is None


class TestAsyncWorkMixinInLiveView:
    def test_liveview_has_start_async(self):
        """LiveView base class should include AsyncWorkMixin."""
        from djust.live_view import LiveView

        assert hasattr(LiveView, "start_async")
        assert issubclass(LiveView, AsyncWorkMixin)
