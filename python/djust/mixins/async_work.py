"""
AsyncWorkMixin — Background work with immediate UI feedback for LiveView.

Allows event handlers to flush state to the client, then run slow work
in a background thread. When the work completes, the view re-renders
and sends updated patches to the client.

    class MyView(LiveView):
        @event_handler
        def generate_spec(self, **kwargs):
            self.generating = True  # spinner shows immediately
            self.start_async(self._do_generate)

        def _do_generate(self):
            self.result = call_slow_api()  # runs in background
            self.generating = False
            # view auto-re-renders when this returns
"""

import logging

logger = logging.getLogger(__name__)


class AsyncWorkMixin:
    """
    Mixin that provides start_async() for running slow work after
    flushing current state to the client.

    The WebSocket consumer checks _async_pending after sending patches.
    If set, it spawns the callback in a background task and re-renders
    when done.
    """

    def start_async(self, callback, *args, **kwargs):
        """
        Schedule a callback to run in a background thread after the
        current handler's state is flushed to the client.

        The callback receives the view instance as ``self`` (it should
        be a regular method on the view class). When it returns, the
        view is automatically re-rendered and patches are sent.

        Args:
            callback: A callable (typically a method on this view).
            *args: Positional arguments passed to the callback.
            **kwargs: Keyword arguments passed to the callback.

        Example::

            @event_handler
            def start_export(self, **kwargs):
                self.exporting = True
                self.start_async(self._run_export, format="csv")

            def _run_export(self, format="csv"):
                self.data = expensive_export(format)
                self.exporting = False
        """
        self._async_pending = (callback, args, kwargs)
