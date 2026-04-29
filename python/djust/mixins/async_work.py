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

import inspect
import logging
from typing import Any, Callable, Optional

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

    def defer(self, callback: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        """
        Schedule a callback to run **once, after the current render+patch
        cycle** completes. Phoenix-style ``send(self(), :foo)`` semantics —
        useful for telemetry, post-render cleanup, or follow-up side
        effects that should fire after the user sees the change.

        Unlike :meth:`start_async`, ``defer``:

        * Runs synchronously in the same WebSocket message cycle (not in a
          background thread).
        * Does NOT trigger a re-render after the callback returns.
        * Is fire-once — every call appends to a queue that is drained and
          cleared after each ``_send_update`` returns.

        If the callback raises, the exception is logged at WARN with full
        traceback and execution continues to the next deferred callback —
        a deferred callback's failure must not break the WebSocket
        connection or the user's interactive flow.

        Async callbacks (``async def`` or coroutine-returning) are
        awaited inline, mirroring the existing
        :meth:`~djust.mixins.async_work.AsyncWorkMixin.start_async`
        async-detection pattern.

        Reentry semantics (calling ``defer()`` from a deferred callback):

            A callback that itself calls ``self.defer(other_cb)`` queues
            ``other_cb`` for the **next** render+patch cycle, NOT the
            current drain. This matches Phoenix ``send(self(), :foo)``
            semantics — the message is processed after the current handler
            returns. Implementation: :meth:`_drain_deferred` clears
            ``self._deferred_callbacks`` BEFORE iterating the snapshot, so
            re-entry into ``defer()`` writes to a fresh empty queue. This
            avoids unbounded loops (a callback that re-defers itself does
            NOT spin within a single drain) and gives users a predictable
            "next tick" mental model.

        Args:
            callback: Callable (typically a method on this view).
            *args: Positional arguments passed to the callback.
            **kwargs: Keyword arguments passed to the callback.

        Example::

            @event_handler
            def increment(self, **kwargs):
                self.count += 1
                self.defer(self._record_metric, action="increment")

            def _record_metric(self, action: str):
                # Fires after the patch reaches the client.
                metrics.increment(f"liveview.{action}", count=self.count)
        """
        if not hasattr(self, "_deferred_callbacks"):
            self._deferred_callbacks = []
        self._deferred_callbacks.append((callback, args, kwargs))

    def _drain_deferred(self) -> list:
        """Pop and return the queued deferred callbacks; reset the queue.

        Called by :class:`~djust.websocket.LiveViewConsumer` immediately
        after each ``_send_update``. The drain is exception-isolated per
        callback — see :meth:`defer` for semantics.

        Returns the list of ``(callback, args, kwargs)`` tuples; consumers
        invoke each one. Returning the list (rather than invoking inline)
        keeps the mixin transport-agnostic — the HTTP path could call
        :meth:`_drain_deferred` after :meth:`render` if we ever want
        deferred-callback support there too.
        """
        callbacks = getattr(self, "_deferred_callbacks", None)
        if not callbacks:
            return []
        self._deferred_callbacks = []
        return callbacks

    def assign_async(
        self,
        name: str,
        loader: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> None:
        """High-level async data loading with built-in loading / ok / failed state.

        Sets ``self.<name>`` to an :class:`~djust.async_result.AsyncResult` in
        the ``loading`` state immediately, then schedules ``loader`` via
        :meth:`start_async`. When the loader completes, ``self.<name>`` is
        replaced with ``AsyncResult.succeeded(result)`` (or
        ``AsyncResult.errored(exc)`` on failure) and the view re-renders.

        Templates read the state via the three mutually-exclusive flags::

            {% if metrics.loading %}<div class="skeleton"></div>{% endif %}
            {% if metrics.ok %}<div>{{ metrics.result.total_users }}</div>{% endif %}
            {% if metrics.failed %}<div class="error">{{ metrics.error }}</div>{% endif %}

        Multiple ``assign_async`` calls in the same handler load concurrently
        (each is an independent :meth:`start_async` task). Cancel via
        :meth:`cancel_async` using ``"assign_async:<name>"`` as the task name.

        Both synchronous and ``async def`` loaders are supported — coroutine
        functions are awaited on the consumer's event loop; sync functions run
        in a worker thread via ``sync_to_async``.

        Args:
            name: Attribute name to bind on ``self``. The loader's result is
                wrapped in an :class:`AsyncResult` and set at ``self.<name>``.
            loader: Callable (or async callable) that returns the payload.
            *args: Positional args forwarded to ``loader``.
            **kwargs: Keyword args forwarded to ``loader``.

        Example::

            class DashboardView(LiveView):
                def mount(self, request, **kwargs):
                    self.assign_async("metrics", self._load_metrics)
                    self.assign_async("notifications", self._load_notifications)

                def _load_metrics(self):
                    return expensive_query()
        """
        # Deferred import avoids a circular dependency at package-init time.
        from ..async_result import AsyncResult

        # Issue #793: concurrent same-name cancellation.
        # Each assign_async() call bumps a per-attribute generation
        # counter. The runner captures the counter at creation time and
        # only writes the AsyncResult back if the captured generation
        # still matches — older in-flight loaders become no-ops and
        # can't overwrite a fresher pending state with stale data.
        if not hasattr(self, "_assign_async_gens"):
            self._assign_async_gens: dict = {}
        self._assign_async_gens[name] = self._assign_async_gens.get(name, 0) + 1
        gen = self._assign_async_gens[name]

        setattr(self, name, AsyncResult.pending())

        def _superseded() -> bool:
            return self._assign_async_gens.get(name) != gen

        if inspect.iscoroutinefunction(loader):

            async def _async_runner() -> None:
                try:
                    result = await loader(*args, **kwargs)
                    if _superseded():
                        logger.debug("assign_async(%s) succeeded but superseded — discarding", name)
                        return
                    setattr(self, name, AsyncResult.succeeded(result))
                except BaseException as exc:  # noqa: BLE001 — surface all failures in AsyncResult
                    if _superseded():
                        logger.debug(
                            "assign_async(%s) raised but superseded — discarding: %s",
                            name,
                            exc,
                        )
                        return
                    setattr(self, name, AsyncResult.errored(exc))
                    logger.debug("assign_async loader for %s raised: %s", name, exc)

            self.start_async(_async_runner, name=f"assign_async:{name}")
        else:

            def _sync_runner() -> None:
                try:
                    result = loader(*args, **kwargs)
                    if _superseded():
                        logger.debug("assign_async(%s) succeeded but superseded — discarding", name)
                        return
                    setattr(self, name, AsyncResult.succeeded(result))
                except BaseException as exc:  # noqa: BLE001 — surface all failures in AsyncResult
                    if _superseded():
                        logger.debug(
                            "assign_async(%s) raised but superseded — discarding: %s",
                            name,
                            exc,
                        )
                        return
                    setattr(self, name, AsyncResult.errored(exc))
                    logger.debug("assign_async loader for %s raised: %s", name, exc)

            self.start_async(_sync_runner, name=f"assign_async:{name}")
