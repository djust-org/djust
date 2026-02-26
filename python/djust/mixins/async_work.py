"""
AsyncWorkMixin — Background work with immediate UI feedback for LiveView.

Allows event handlers to flush state to the client, then run slow work
in a background thread. When the work completes, the view re-renders
and sends updated patches to the client.

    class MyView(LiveView):
        @event_handler
        def generate_spec(self, **kwargs):
            self.generating = True  # spinner shows immediately
            self.start_async(self._do_generate, name="spec_gen")

        def _do_generate(self):
            self.result = call_slow_api()  # runs in background
            self.generating = False
            # view auto-re-renders when this returns

        def handle_async_result(self, name: str, result=None, error=None):
            '''Optional callback for handling async completion or errors.'''
            if error:
                self.error = str(error)
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class AsyncWorkMixin:
    """
    Mixin that provides start_async() for running slow work after
    flushing current state to the client.

    The WebSocket consumer checks _async_tasks after sending patches.
    If set, it spawns the callbacks in background tasks and re-renders
    when done.

    Supports multiple concurrent async tasks with optional naming for
    tracking and cancellation.
    """

    def start_async(self, callback, *args, name: Optional[str] = None, **kwargs):
        """
        Schedule a callback to run in a background thread after the
        current handler's state is flushed to the client.

        The callback receives the view instance as ``self`` (it should
        be a regular method on the view class). When it returns, the
        view is automatically re-rendered and patches are sent.

        Multiple tasks can run concurrently by providing unique names.
        Tasks with the same name will replace previously scheduled tasks
        with that name.

        Args:
            callback: A callable (typically a method on this view).
            *args: Positional arguments passed to the callback.
            name: Optional task name for tracking/cancellation.
                  If not provided, an auto-generated name is used.
            **kwargs: Keyword arguments passed to the callback.

        Example::

            @event_handler
            def start_export(self, **kwargs):
                self.exporting = True
                self.start_async(self._run_export, format="csv", name="export")

            def _run_export(self, format="csv"):
                self.data = expensive_export(format)
                self.exporting = False

            def handle_async_result(self, name: str, result=None, error=None):
                '''Called when async task completes or fails.'''
                if error:
                    self.error_message = f"Export failed: {error}"
                    self.exporting = False
        """
        if not hasattr(self, "_async_tasks"):
            self._async_tasks = {}
            self._async_task_counter = 0

        # Generate name if not provided
        if name is None:
            name = f"_task_{self._async_task_counter}"
            self._async_task_counter += 1

        self._async_tasks[name] = (callback, args, kwargs)

    def cancel_async(self, name: str):
        """
        Cancel a scheduled or running async task.

        If the task is still scheduled (not yet started), it will be removed.
        If the task is already running, it will be marked as cancelled so
        the re-render is skipped when it completes.

        Args:
            name: The name of the task to cancel.

        Example::

            @event_handler
            def cancel_export(self, **kwargs):
                self.cancel_async("export")
                self.exporting = False
                self.status = "Cancelled"
        """
        # Remove from scheduled tasks if present
        if hasattr(self, "_async_tasks") and name in self._async_tasks:
            del self._async_tasks[name]

        # Mark as cancelled for running tasks
        if not hasattr(self, "_async_cancelled"):
            self._async_cancelled = set()
        self._async_cancelled.add(name)
