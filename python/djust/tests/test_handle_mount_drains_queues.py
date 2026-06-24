"""
Regression tests for #1280 and #1283 (pin MOVED to the runtime — ADR-022 Iter 3
Phase 3.0, #1911).

Both issues root at the same site: the mount handler ends with the mount frame
and previously did not drain the ``_async_tasks`` or ``_pending_push_events``
queues. The result was:

- ``start_async()`` / ``assign_async()`` calls inside ``mount()`` queued
  callbacks that never spawned, leaving the view frozen at the initial
  loading-state HTML (#1280).
- ``push_event()`` calls inside ``mount()`` (or ``on_mount`` hooks) queued
  events that never reached the client (#1283).

The fix adds ``_flush_push_events()`` and ``_dispatch_async_work()`` calls after
the mount frame, mirroring the pattern in the event-handler and deferred-activity
paths.

**Pin location (#1391 symbol-migration canon):** ADR-022 Iter 3 Phase 3.0 GROWS
the SAME two drains onto ``ViewRuntime.dispatch_mount`` (the converged mount path
the 3.3b flip will route WS mounts through). Per the symbol-migration grep canon,
the source-grep pin MOVES to the runtime location IN THE SAME PR as the grow, so
it pins the path the flip actually uses. The WS ``handle_mount`` copy stays
UNTOUCHED in 3.0 (the flip is 3.3b), and its drains are still verified here as a
secondary pin so a 3.0-era refactor can't silently drop them from WS either.

CRITICAL parity note: the WS mount drains ONLY these TWO queues — NOT the
8-queue ``_flush_all_pending`` the turn-end event path uses. The runtime grow
preserves that EXACTLY (mount establishes a baseline; it does not run a full
event turn-end flush). The runtime pin below asserts the two drains appear and
that ``_flush_all_pending`` is NOT called inside ``dispatch_mount``.
"""

import inspect

from djust.runtime import ViewRuntime
from djust.websocket import LiveViewConsumer


# ---------------------------------------------------------------------------
# Primary pin (#1911): the runtime mount path (the 3.3b-flip target).
# ---------------------------------------------------------------------------


class TestRuntimeDispatchMountSourceShape:
    """``ViewRuntime.dispatch_mount`` must include the post-frame queue drains
    (the #1391 pin MOVED here in the Phase-3.0 grow)."""

    def _source(self) -> str:
        return inspect.getsource(ViewRuntime.dispatch_mount)

    def test_calls_dispatch_async_work(self):
        """``dispatch_mount`` must call ``_dispatch_async_work``. Closes #1280 on
        the converged path.

        Gate-off (#1468): delete ``self._dispatch_async_work(None)`` from the end
        of ``dispatch_mount`` → this FAILS (and start_async() scheduled in
        mount() never spawns on the runtime/SSE/post-flip-WS path).
        """
        assert "_dispatch_async_work(" in self._source(), (
            "dispatch_mount must drain _async_tasks. Without this, "
            "start_async()/assign_async() called from mount() are queued "
            "but never spawned (see #1280)."
        )

    def test_calls_flush_push_events(self):
        """``dispatch_mount`` must call ``_flush_push_events``. Closes #1283 on
        the converged path.

        Gate-off (#1468): delete ``self._flush_push_events()`` from the end of
        ``dispatch_mount`` → this FAILS (and push_event() called from mount()
        never reaches the client on the runtime/SSE/post-flip-WS path).
        """
        assert "_flush_push_events()" in self._source(), (
            "dispatch_mount must drain _pending_push_events. Without this, "
            "push_event() called from mount() never reaches the client "
            "(see #1283)."
        )

    def test_drains_run_after_final_mount_frame(self):
        """Drain calls must occur AFTER the final ``transport.send(mount_msg)``.

        If the order is reversed, the client may receive a push-event frame
        or an async-driven patch frame before the mount frame establishes
        the view, leading to "patch on missing element" errors.
        """
        src = self._source()
        last_send = src.rfind("self.transport.send(mount_msg)")
        assert last_send != -1, "could not find transport.send(mount_msg) in dispatch_mount source"
        flush_after = src.find("_flush_push_events()", last_send)
        dispatch_after = src.find("_dispatch_async_work(", last_send)
        assert flush_after > last_send, (
            "_flush_push_events() must come AFTER the final transport.send("
            "mount_msg); otherwise push events would arrive before the "
            "mount frame establishes the view."
        )
        assert dispatch_after > last_send, (
            "_dispatch_async_work() must come AFTER the final transport.send("
            "mount_msg); otherwise async-driven patches could race the mount."
        )

    def test_mount_drain_is_two_queues_not_full_flush(self):
        """The mount-time drain is ONLY the two queues, NOT the 8-queue
        ``_flush_all_pending`` that the turn-end event path uses.

        Per ADR-022 Iter 3 Phase 3.0: mount establishes a baseline; it must NOT
        run a full event turn-end flush. A future refactor that "upgrades" the
        mount drain to ``_flush_all_pending`` would change behavior (e.g. drain
        navigation/flash mid-mount) and trips this pin.
        """
        src = self._source()
        # Assert the CALL form (``_flush_all_pending(``) is absent — a bare
        # substring would false-match the explanatory comment naming the method.
        assert "_flush_all_pending(" not in src, (
            "dispatch_mount must NOT call _flush_all_pending — the WS mount path "
            "drains ONLY _flush_push_events + _dispatch_async_work (ADR-022 Iter 3 "
            "Phase 3.0). Upgrading to the 8-queue flush changes mount behavior."
        )


# ---------------------------------------------------------------------------
# Secondary pin: the WS handle_mount copy stays UNTOUCHED in Phase 3.0.
# ---------------------------------------------------------------------------


class TestHandleMountSourceShape:
    """Post-#1919 (THE MOUNT FLIP): ``handle_mount`` is a THIN SHIM that delegates
    to ``ViewRuntime.dispatch_mount`` — the two queue drains MOVED into the runtime
    (the primary pin ``TestRuntimeDispatchMountSourceShape`` above). The drains are
    NO LONGER in the WS shim source; pinning them here would FALSE-FAIL. Instead pin
    that the shim delegates to the runtime (so it inherits the runtime's drains)."""

    def _source(self) -> str:
        return inspect.getsource(LiveViewConsumer.handle_mount)

    def test_shim_delegates_to_runtime_dispatch_mount(self):
        """The WS ``handle_mount`` shim routes through ``runtime.dispatch_mount``,
        which owns the #1280/#1283 drains (pinned by the runtime class above)."""
        assert "dispatch_mount(" in self._source(), (
            "handle_mount must delegate to runtime.dispatch_mount post-#1919 — the "
            "mount-time _async_tasks (#1280) + _pending_push_events (#1283) drains "
            "live in the runtime now, so the shim inherits them via delegation."
        )

    def test_drains_not_duplicated_in_shim(self):
        """The drains must NOT also run inline in the shim (double-drain) — they are
        the runtime's responsibility post-flip."""
        src = self._source()
        assert "_dispatch_async_work" not in src and "_flush_push_events" not in src, (
            "handle_mount is a thin shim post-#1919 — the mount-time drains belong "
            "to runtime.dispatch_mount, not the shim. Re-adding them here would "
            "double-drain. Found a drain call in the shim source."
        )
