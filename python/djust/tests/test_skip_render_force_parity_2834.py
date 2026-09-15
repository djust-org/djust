"""#2834 — ``_skip_render`` + ``_force_full_html`` must resolve identically on
every render path: the forced render wins.

Before the fix, the three sibling paths the tick path cites as its model
(``runtime.dispatch_event``, ``websocket.server_push``,
``websocket.db_notify``) resolved the collision the opposite way from
``websocket._tick_once``: with BOTH flags set by the same handler, the tick
rendered (``_force_full_html`` won — the #1646/#1981 "hatch silently dropped"
class the tick's own reasoning argues against) while the other three skipped,
silently dropping the forced render AND leaking ``_force_full_html`` into some
later unrelated turn (a surprise full-HTML update).

The documented contract (``RustBridgeMixin.set_changed_keys`` in
``mixins/rust_bridge.py``) is that ``_force_full_html`` is "the sanctioned skip
bypass: honored on every skip path ... and consumed (reset) after the render it
forces". The tick path is that contract's reference implementation, so the fix
lifts the tick's resolution into a shared ``_resolve_skip_render`` helper and
points the three sibling paths at it. These tests pin the parity: each path,
same two flags, one answer.

Harness provenance (#1077 lift-from-reference):
- ``_event_runtime_with_view`` imported from
  ``test_transport_behavioral_parity`` (the harness ``test_runtime_child_routing_1892``
  and ``test_set_changed_keys_zero_arg_1992`` already share).
- The consumer harness is lifted from ``test_tick_skip_render_2822``
  (itself lifted from ``test_ws_mount_flip_parity_1911``): a real, un-mocked
  ``LiveViewConsumer`` + real ``LiveView`` subclass; only the socket/timer are
  skipped — ``server_push`` / ``db_notify`` are driven directly as methods,
  exactly as ``_tick_once`` is driven there.

Non-tautology (#1468/#1200): each collision test is paired with a skip-only
gate-off sibling — same path, ``_skip_render`` only — which must still skip.
The collision test differs from its sibling by exactly the
``set_changed_keys()`` call, so the delta is the forced render.
"""

from __future__ import annotations

import pytest

from djust import LiveView
from djust.decorators import event_handler
from djust.tests.test_transport_behavioral_parity import _event_runtime_with_view

_ALLOWED = "djust.tests.test_skip_render_force_parity_2834"


def _consumer_with_view(view_class):
    """A ``LiveViewConsumer`` with ``view_class`` mounted and its sends captured.

    Lifted from ``test_tick_skip_render_2822::_consumer_with_view`` (itself
    lifted from ``test_ws_mount_flip_parity_1911``).
    """
    from djust.websocket import LiveViewConsumer

    consumer = LiveViewConsumer()
    consumer.scope = {"session": None, "user": None}
    consumer.channel_name = "test.channel"
    consumer.sent = []

    async def _capture(payload):
        consumer.sent.append(payload)

    consumer.send_json = _capture

    view = view_class()
    view.mount(None)
    # Establish the diff baseline the real mount render establishes. Without
    # it the first render_with_diff() returns full HTML with patches=None and
    # a forced render would legitimately send nothing.
    view.render_with_diff()
    consumer.view_instance = view
    return consumer


def _updates(sent):
    return [f for f in sent if f.get("type") in ("patch", "html_update")]


def _noops(sent):
    return [f for f in sent if f.get("type") == "noop"]


# ---------------------------------------------------------------------------
# Test views — one pair (collision / skip-only control) per path
# ---------------------------------------------------------------------------


class _CollisionHandler:
    """Mixin body: the #2834 contradictory-intent handler shape."""

    def _set_both_flags(self):
        self._skip_render = True
        self.set_changed_keys()  # forces _force_full_html = True (#1981)


class CollisionEventView(_CollisionHandler, LiveView):
    """Event path (runtime.dispatch_event): handler sets BOTH flags."""

    template = f'<div dj-root dj-view="{_ALLOWED}.CollisionEventView" dj-id="0">c={{{{ c }}}}</div>'

    def mount(self, request, **kwargs):
        self.c = 0

    def get_context_data(self, **kwargs):
        return {"c": self.c}

    @event_handler()
    def collide(self, **kwargs):
        self._set_both_flags()


class SkipOnlyEventView(LiveView):
    """Gate-off sibling: ``_skip_render`` only — must still skip (noop)."""

    template = f'<div dj-root dj-view="{_ALLOWED}.SkipOnlyEventView" dj-id="0">c={{{{ c }}}}</div>'

    def mount(self, request, **kwargs):
        self.c = 0

    def get_context_data(self, **kwargs):
        return {"c": self.c}

    @event_handler()
    def skip_only(self, **kwargs):
        self._skip_render = True


class CollisionPushView(_CollisionHandler, LiveView):
    """server_push path: the channel-layer handler sets BOTH flags."""

    template = f'<div dj-root dj-view="{_ALLOWED}.CollisionPushView" dj-id="0">c={{{{ c }}}}</div>'

    def mount(self, request, **kwargs):
        self.c = 0

    def get_context_data(self, **kwargs):
        return {"c": self.c}

    def handle_push(self, **kwargs):
        self._set_both_flags()

    def handle_skip_only(self, **kwargs):
        self._skip_render = True


class CollisionNotifyView(_CollisionHandler, LiveView):
    """db_notify path: ``handle_info`` sets BOTH flags (or skip-only, per the
    payload's ``mode`` — one view covers both arms of this path)."""

    template = (
        f'<div dj-root dj-view="{_ALLOWED}.CollisionNotifyView" dj-id="0">c={{{{ c }}}}</div>'
    )

    def mount(self, request, **kwargs):
        self.c = 0

    def get_context_data(self, **kwargs):
        return {"c": self.c}

    def handle_info(self, message):
        if message.get("payload", {}).get("mode") == "skip_only":
            self._skip_render = True
        else:
            self._set_both_flags()


# ---------------------------------------------------------------------------
# Tests — one collision + one gate-off sibling per path
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.asyncio
class TestSkipRenderForceParity2834:
    # ---- runtime.dispatch_event (the live event spine) --------------------

    async def test_dispatch_event_force_wins_over_skip_render(self):
        runtime, transport = _event_runtime_with_view(CollisionEventView())
        runtime.view_instance.mount(None)

        await runtime.dispatch_event({"type": "event", "event": "collide", "params": {}})

        assert _updates(transport.sent), (
            "_force_full_html (set_changed_keys()) must win over _skip_render "
            f"on the runtime event path, got {transport.sent!r}"
        )
        assert runtime.view_instance._force_full_html is False, (
            "the forced render must consume the flag on the turn that served "
            "it — a leaked _force_full_html forces a surprise full render on a "
            "later unrelated turn"
        )

    async def test_dispatch_event_skip_only_still_skips(self):
        """GATE-OFF sibling (#1468): without the force flag, the explicit skip
        must still suppress the render — the collision fix may not weaken the
        plain skip."""
        runtime, transport = _event_runtime_with_view(SkipOnlyEventView())
        runtime.view_instance.mount(None)

        await runtime.dispatch_event({"type": "event", "event": "skip_only", "params": {}})

        assert not _updates(transport.sent), (
            "a handler that sets only _skip_render must still suppress the "
            f"render, got {transport.sent!r}"
        )
        assert _noops(transport.sent), f"expected a noop ack, got {transport.sent!r}"
        assert runtime.view_instance._skip_render is False, "the skip flag must be consumed"

    # ---- websocket.server_push ---------------------------------------------

    async def test_server_push_force_wins_over_skip_render(self):
        consumer = _consumer_with_view(CollisionPushView)

        await consumer.server_push({"handler": "handle_push", "payload": {}})

        assert _updates(consumer.sent), (
            "_force_full_html (set_changed_keys()) must win over _skip_render "
            f"on the server_push path, got {consumer.sent!r}"
        )
        assert consumer.view_instance._force_full_html is False, (
            "the forced render must consume the flag on the turn that served it"
        )

    async def test_server_push_skip_only_still_skips(self):
        """GATE-OFF sibling (#1468): skip-only must still suppress the render."""
        consumer = _consumer_with_view(CollisionPushView)

        await consumer.server_push({"handler": "handle_skip_only", "payload": {}})

        assert not _updates(consumer.sent), (
            "a handler that sets only _skip_render must still suppress the "
            f"server_push render, got {consumer.sent!r}"
        )
        assert _noops(consumer.sent), f"expected a noop ack, got {consumer.sent!r}"
        assert consumer.view_instance._skip_render is False, "the skip flag must be consumed"

    # ---- websocket.db_notify ------------------------------------------------

    async def test_db_notify_force_wins_over_skip_render(self):
        consumer = _consumer_with_view(CollisionNotifyView)

        await consumer.db_notify({"channel": "test", "payload": {}})

        assert _updates(consumer.sent), (
            "_force_full_html (set_changed_keys()) must win over _skip_render "
            f"on the db_notify path, got {consumer.sent!r}"
        )
        assert consumer.view_instance._force_full_html is False, (
            "the forced render must consume the flag on the turn that served it"
        )

    async def test_db_notify_skip_only_still_skips(self):
        """GATE-OFF sibling (#1468): skip-only must still suppress the render."""
        consumer = _consumer_with_view(CollisionNotifyView)

        await consumer.db_notify({"channel": "test", "payload": {"mode": "skip_only"}})

        assert not _updates(consumer.sent), (
            "a handler that sets only _skip_render must still suppress the "
            f"db_notify render, got {consumer.sent!r}"
        )
        assert _noops(consumer.sent), f"expected a noop ack, got {consumer.sent!r}"
        assert consumer.view_instance._skip_render is False, "the skip flag must be consumed"
