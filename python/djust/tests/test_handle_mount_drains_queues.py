"""
Regression tests for #1280 and #1283.

Both issues root at the same site: ``LiveViewConsumer.handle_mount`` ends
with ``await self.send_json(response)`` and previously did not drain the
``_async_tasks`` or ``_pending_push_events`` queues. The result was:

- ``start_async()`` / ``assign_async()`` calls inside ``mount()`` queued
  callbacks that never spawned, leaving the view frozen at the initial
  loading-state HTML (#1280).
- ``push_event()`` calls inside ``mount()`` (or ``on_mount`` hooks) queued
  events that never reached the client (#1283).

The fix adds ``_flush_push_events()`` and ``_dispatch_async_work()`` calls
after the mount frame, mirroring the pattern in the event-handler and
deferred-activity paths.

Test strategy: source-shape assertions over ``inspect.getsource(handle_mount)``.
The structural tests assert (a) both calls appear, (b) both come after the
final ``send_json(response)``. They fail loudly if a refactor accidentally
removes the calls or reverses the order. A behavioral test driving the full
``handle_mount`` would require ~700 lines of fixture setup (Rust state,
sticky views, sessions, hooks); the source-shape assertions are sufficient
for the regression class this PR closes.
"""

import inspect

from djust.websocket import LiveViewConsumer


# ---------------------------------------------------------------------------
# Structural assertions — lock in the fix at source-line level.
# ---------------------------------------------------------------------------


class TestHandleMountSourceShape:
    """``handle_mount`` source must include the post-frame queue drains."""

    def _source(self) -> str:
        return inspect.getsource(LiveViewConsumer.handle_mount)

    def test_calls_dispatch_async_work(self):
        """``handle_mount`` must call ``_dispatch_async_work``. Closes #1280."""
        assert "_dispatch_async_work()" in self._source(), (
            "handle_mount must drain _async_tasks. Without this, "
            "start_async()/assign_async() called from mount() are queued "
            "but never spawned (see #1280)."
        )

    def test_calls_flush_push_events(self):
        """``handle_mount`` must call ``_flush_push_events``. Closes #1283."""
        assert "_flush_push_events()" in self._source(), (
            "handle_mount must drain _pending_push_events. Without this, "
            "push_event() called from mount() never reaches the client "
            "(see #1283)."
        )

    def test_drains_run_after_final_mount_frame(self):
        """Drain calls must occur AFTER the final ``send_json(response)``.

        If the order is reversed, the client may receive a push-event frame
        or an async-driven patch frame before the mount frame establishes
        the view, leading to "patch on missing element" errors.
        """
        src = self._source()
        last_send = src.rfind("send_json(response)")
        assert last_send != -1, "could not find send_json(response) in handle_mount source"
        flush_after = src.find("_flush_push_events()", last_send)
        dispatch_after = src.find("_dispatch_async_work()", last_send)
        assert flush_after > last_send, (
            "_flush_push_events() must come AFTER the final send_json("
            "response); otherwise push events would arrive before the "
            "mount frame establishes the view."
        )
        assert dispatch_after > last_send, (
            "_dispatch_async_work() must come AFTER the final send_json("
            "response); otherwise async-driven patches could race the mount."
        )
