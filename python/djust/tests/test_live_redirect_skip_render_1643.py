"""Regression: live_redirect() from a state-unchanging sync handler must still
navigate the client (#1643).

The bug
-------

``live_redirect()`` queues a navigation command on the view
(``_pending_navigation``). The consumer flushes it to the client only via
``_flush_navigation()`` — called inside ``_send_update`` and the embedded-child
branch.

``handle_event`` (and the batched ``_dispatch_single_event``) auto-skip the
render when a handler reassigns no public attr (``pre_assigns == post_assigns``
→ ``skip_render = True``) and return via ``_send_noop`` after flushing
push_events / flash / page_metadata / pending_layout / deferred — but NOT
``_flush_navigation``. ``_send_noop`` sends only ``{"type": "noop"}``.

A handler that mutates an external model (DB row, cache) and calls
``live_redirect`` — without reassigning a public view attr — therefore hits the
skip branch: the queued ``live_redirect`` is never flushed and the browser URL
never changes. ``live_redirect`` is a navigation command, separate from assigns,
so it is invisible to the assign-based skip detection.

``LiveViewTestClient.assert_live_redirect`` checks the *view's* queue (populated
correctly), which is why existing tests missed this — the gap is the consumer
never flushing it. These tests drive the real ``handle_event`` and assert at the
sent-frame level.
"""

from __future__ import annotations

import inspect

import pytest
from asgiref.sync import sync_to_async
from unittest.mock import MagicMock

from djust import LiveView
from djust.decorators import event_handler

# Module-level external side-effect marker (stands in for the reporter's DB
# write). Mutating this — not a view attr — keeps the handler from changing any
# public assign, so handle_event takes the auto-skip-render branch.
_EXTERNAL_STATE = {"done": False}


def _external_side_effect():
    _EXTERNAL_STATE["done"] = True


class _RedirectSkipView(LiveView):
    """Sync handler that changes NO public state, only redirects — hits the
    auto-skip-render branch."""

    template = '<div dj-view="djust.tests.x._RedirectSkipView" dj-id="0">confirm</div>'

    def mount(self, request, **kwargs):
        self.label = "confirm"

    @event_handler()
    def confirm_and_redirect(self, **kwargs):
        # Mirrors the reporter: an external side effect (DB write) + redirect,
        # setting NO view attribute, so handle_event's auto-skip-render fires.
        _external_side_effect()
        self.live_redirect("/done/")


class _RedirectAndChangeView(LiveView):
    """Control: handler changes public state AND redirects — takes the
    render/_send_update path, which already flushes navigation."""

    template = '<div dj-view="djust.tests.x._RedirectAndChangeView" dj-id="0">{{ label }}</div>'

    def mount(self, request, **kwargs):
        self.label = "confirm"

    @event_handler()
    def change_and_redirect(self, **kwargs):
        self.label = "changed"
        self.live_redirect("/done/")


async def _drive_event(view_cls, event_name):
    """Mount ``view_cls`` and dispatch one event through the REAL
    ``LiveViewConsumer.handle_event``; return the list of frames the consumer
    sent. Only ``send_json`` (frame capture) and ``_rate_limiter`` (infra) are
    stubbed — the skip-render branch and ``_flush_navigation`` run for real."""
    from djust.websocket import LiveViewConsumer

    view = view_cls()
    await sync_to_async(view.mount)(request=None)
    await sync_to_async(view.render_with_diff)()  # establish baseline VDOM

    consumer = LiveViewConsumer()
    consumer.scope = {"session": None}
    consumer.session_id = "s1"
    consumer.view_instance = view
    consumer.use_actors = False
    consumer._view_path = f"djust.tests.x.{view_cls.__name__}"
    rl = MagicMock()
    rl.check.return_value = True
    consumer._rate_limiter = rl

    sent = []

    async def _send_json(msg):
        sent.append(msg)

    consumer.send_json = _send_json
    await consumer.handle_event({"type": "event", "event": event_name, "params": {}})
    return view, sent


def _nav_frames(frames):
    return [
        f for f in frames if f.get("type") == "navigation" and f.get("action") == "live_redirect"
    ]


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_live_redirect_from_state_unchanging_handler_emits_navigation_frame():
    """The reporter's case: a sync handler that changes no public state and
    calls live_redirect must still emit a navigation frame to the client."""
    view, frames = await _drive_event(_RedirectSkipView, "confirm_and_redirect")

    # The command is queued on the view (this part always worked)...
    # ...but the consumer must actually FLUSH it to the client.
    navs = _nav_frames(frames)
    assert navs, (
        "live_redirect from a state-unchanging handler emitted NO navigation "
        f"frame — the skip-render branch dropped it. Frames: "
        f"{[f.get('type') for f in frames]}"
    )
    assert navs[0].get("path") == "/done/", navs
    # And the queue must have been drained (not left dangling).
    assert not getattr(view, "_pending_navigation", []), (
        "navigation queue should be drained after flush"
    )


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_state_changing_handler_with_live_redirect_still_navigates():
    """Control: a handler that ALSO changes public state still navigates (the
    _send_update path already flushes navigation). Guards against a fix that
    only patches one path."""
    _view, frames = await _drive_event(_RedirectAndChangeView, "change_and_redirect")
    navs = _nav_frames(frames)
    assert navs, (
        f"state-changing handler must still navigate. Frames: {[f.get('type') for f in frames]}"
    )
    assert navs[0].get("path") == "/done/", navs


def test_turn_end_paths_use_single_flush_helper():
    """Source-pin the DRY consolidation (#1643): every turn-end path flushes via
    the single ``_flush_all_pending`` helper rather than a hand-copied subset, so
    no path can silently drop queued navigation/accessibility/i18n. The original
    bug was that ~9 sites copied the 5-flush prelude and several omitted the
    navigation/accessibility/i18n suffix."""
    import djust.websocket as ws_mod

    for name in (
        # Finding #6: handle_event is a thin tenant-context wrapper; the flush
        # logic lives in _handle_event_inner now.
        "_handle_event_inner",
        "_dispatch_single_event",
        "server_push",
        "db_notify",
        "_run_async_work",
    ):
        src = inspect.getsource(getattr(ws_mod.LiveViewConsumer, name))
        assert "_flush_all_pending" in src, (
            f"{name} must flush via _flush_all_pending so queued navigation/"
            f"i18n/accessibility commands aren't dropped (#1643)."
        )


def test_flush_all_pending_covers_every_side_effect():
    """The helper must flush ALL queued side-effects — push_events, flash,
    page_metadata, pending_layout, deferred, navigation, accessibility, i18n —
    so consolidating onto it can never drop one a hand-copied block included."""
    import djust.websocket as ws_mod

    src = inspect.getsource(ws_mod.LiveViewConsumer._flush_all_pending)
    for flush in (
        "_flush_push_events",
        "_flush_flash",
        "_flush_page_metadata",
        "_flush_pending_layout",
        "_flush_deferred",
        "_flush_navigation",
        "_flush_accessibility",
        "_flush_i18n",
    ):
        assert f"self.{flush}()" in src, f"_flush_all_pending must call {flush} (#1643)"
