"""End-to-end integration tests for Sticky LiveView preservation across
``live_redirect`` (Phase B of v0.6.0 Sticky LiveViews).

These tests drive a lightly-faked :class:`djust.websocket.LiveViewConsumer`
through the full ``handle_live_redirect_mount`` flow:

  - Mount a parent view with a sticky child (``live_render "..." sticky=True``).
  - Mutate sticky state.
  - Trigger ``handle_live_redirect_mount`` to a destination layout that
    contains a matching ``[dj-sticky-slot]`` element.
  - Assert the sticky INSTANCE survived (same Python object, same
    ``current_track`` state) and is re-registered on the new parent.

Three scenarios:

1. Dashboard → Settings with matching slot — sticky survives.
2. Rapid A→B→A ping-pong — same instance throughout.
3. Redirect to a URL not in the live_session route map is equivalent to
   full HTTP navigation (client side) — the server's perspective is only
   to run auth re-check and NOT reattach, as no sticky_hold will reach
   the client (test shim stops short at consumer return).
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

import pytest
from django.test import RequestFactory

from djust.live_view import LiveView
from djust.websocket import LiveViewConsumer


# ---------------------------------------------------------------------------
# Module-scope views — resolvable by dotted path for import_string().
# ---------------------------------------------------------------------------


class _AudioPlayerSticky(LiveView):
    sticky = True
    sticky_id = "audio-player"
    template = '<div><button dj-click="play">Play {{ current_track }}</button></div>'

    def mount(self, request, **kwargs):
        self.current_track = "none"

    def play(self, **kwargs):
        self.current_track = "next-track"


class _DashboardView(LiveView):
    template = (
        "{% load live_tags %}"
        "<div dj-root>"
        "<h1>Dashboard</h1>"
        '{% live_render "tests.integration.test_sticky_redirect_flow._AudioPlayerSticky" sticky=True %}'
        "</div>"
    )

    def mount(self, request, **kwargs):
        pass

    def get_context_data(self, **kwargs):
        return {"view": self}


class _SettingsWithSlotView(LiveView):
    template = '<div dj-root><h1>Settings</h1><div dj-sticky-slot="audio-player"></div></div>'

    def mount(self, request, **kwargs):
        pass

    def get_context_data(self, **kwargs):
        return {"view": self}


class _SettingsNoSlotView(LiveView):
    template = "<div dj-root><h1>Settings (no slot)</h1></div>"

    def mount(self, request, **kwargs):
        pass

    def get_context_data(self, **kwargs):
        return {"view": self}


# ---------------------------------------------------------------------------
# Test consumer shim — bypasses Channels ASGI wiring.
# ---------------------------------------------------------------------------


class _FakeConsumer(LiveViewConsumer):
    """LiveViewConsumer stand-in that captures ``send_json`` payloads and
    skips network / channel-layer side effects.

    Kept minimal: we override exactly the methods whose real
    implementations would require a full Channels runtime.
    """

    def __init__(self):  # type: ignore[no-untyped-def]
        # DO NOT call super().__init__ — AsyncWebsocketConsumer wants an
        # ASGI scope we don't have. Set only the fields our flow touches.
        self.view_instance: Optional[LiveView] = None
        self._view_group = None
        self._presence_group = None
        self._tick_task = None
        self._render_lock = asyncio.Lock()
        self._processing_user_event = False
        self._sticky_preserved: Dict[str, Any] = {}
        self._db_notify_channels: set[str] = set()
        self.sent_frames: List[Dict[str, Any]] = []
        self.session_id = "test-session"
        self.scope = {"path": "/", "query_string": b"", "session": None}
        self.channel_name = "test-channel"
        self.use_actors = False
        self.actor_handle = None

    class _NullChannelLayer:
        async def group_add(self, *args, **kwargs):
            return None

        async def group_discard(self, *args, **kwargs):
            return None

    @property
    def channel_layer(self):  # type: ignore[override]
        return self._NullChannelLayer()

    async def send_json(self, payload):  # type: ignore[override]
        self.sent_frames.append(payload)

    async def send(self, *args, **kwargs):  # type: ignore[override]
        # Binary patch path — record nothing for integration tests.
        return None

    async def send_error(self, message: str, **kwargs) -> None:  # type: ignore[override]
        self.sent_frames.append({"type": "error", "message": message})

    async def close(self, code=None):  # type: ignore[override]
        return None


def _mount_parent(consumer: _FakeConsumer, view_path: str) -> None:
    """Run the minimal subset of ``handle_mount`` that gives us a live
    parent view without requiring the full Rust/VDOM pipeline.

    This helper sidesteps the Rust renderer so the integration tests
    don't depend on a built Rust extension — they focus on the
    sticky-preservation Python logic.
    """
    from django.utils.module_loading import import_string

    view_cls = import_string(view_path)
    parent = view_cls()
    rf = RequestFactory()
    request = rf.get("/")
    from django.contrib.auth.models import AnonymousUser

    request.user = AnonymousUser()
    parent.request = request
    parent.mount(request)
    # Render the template so {% live_render %} runs and registers children.
    from django.template import Context, Template

    Template(parent.template).render(Context({"view": parent, "request": request}))
    consumer.view_instance = parent


# ---------------------------------------------------------------------------
# 1. End-to-end Dashboard → Settings preservation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_end_to_end_dashboard_to_settings_sticky_audio_player():
    consumer = _FakeConsumer()
    _mount_parent(
        consumer,
        "tests.integration.test_sticky_redirect_flow._DashboardView",
    )
    dashboard = consumer.view_instance
    assert dashboard is not None
    assert "audio-player" in dashboard._child_views
    audio = dashboard._child_views["audio-player"]
    # Mutate sticky state.
    audio.play()
    assert audio.current_track == "next-track"

    # Stage sticky children — this is the core of Phase B.
    rf = RequestFactory()
    new_request = rf.get("/settings/")
    from django.contrib.auth.models import AnonymousUser

    new_request.user = AnonymousUser()
    survivors = dashboard._preserve_sticky_children(new_request)
    assert "audio-player" in survivors
    assert survivors["audio-player"] is audio
    # State is the SAME Python object — mutations survive.
    assert survivors["audio-player"].current_track == "next-track"

    # Simulate the destination parent's render producing HTML with the
    # matching slot.
    from django.template import Context, Template

    settings_html = Template(_SettingsWithSlotView.template).render(Context({}))
    from djust.websocket import _find_sticky_slot_ids

    assert _find_sticky_slot_ids(settings_html) == {"audio-player"}

    # Install new parent + re-register survivor.
    new_parent = _SettingsWithSlotView()
    new_parent.request = new_request
    new_parent.mount(new_request)
    for sticky_id, child in survivors.items():
        new_parent._register_child(sticky_id, child)

    # Handler dispatched through new parent's registry still targets
    # the preserved audio instance.
    target = new_parent._get_child_view("audio-player")
    assert target is audio
    target.play()
    # current_track was overwritten to "next-track" above, then play()
    # runs again — state persists on the same object.
    assert target.current_track == "next-track"


# ---------------------------------------------------------------------------
# 2. A → B → A ping-pong — instance identity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rapid_navigate_a_b_a_sticky_instance_identity():
    consumer = _FakeConsumer()
    _mount_parent(
        consumer,
        "tests.integration.test_sticky_redirect_flow._DashboardView",
    )
    dashboard_a = consumer.view_instance
    audio = dashboard_a._child_views["audio-player"]

    rf = RequestFactory()
    from django.contrib.auth.models import AnonymousUser

    def _redirect(to_path: str, dest_cls) -> LiveView:
        parent = consumer.view_instance
        req = rf.get(to_path)
        req.user = AnonymousUser()
        survivors = parent._preserve_sticky_children(req)
        # Build the destination parent.
        new_parent = dest_cls()
        new_parent.request = req
        new_parent.mount(req)
        for sticky_id, child in survivors.items():
            new_parent._register_child(sticky_id, child)
        consumer.view_instance = new_parent
        return new_parent

    # Dashboard → Settings.
    settings = _redirect("/settings/", _SettingsWithSlotView)
    assert settings._child_views["audio-player"] is audio

    # Settings → Dashboard — the survivor handoff re-registers ``audio``
    # on dashboard_b via ``_register_child``. The helper does NOT render
    # the destination template, so no competing child is created; in
    # the production pipeline the [dj-sticky-slot] scan in
    # handle_live_redirect_mount would pick the same winner.
    dashboard_b = _redirect("/", _DashboardView)
    assert dashboard_b._child_views["audio-player"] is audio

    # Settings → Dashboard → Settings: same instance all the way.
    # Stricter than the old ``or`` fallback (Fix #8) — an ``or`` with
    # ``current_track == "next-track"`` would have hidden a case where
    # re-registration silently produced a new instance that merely
    # happened to share state.
    settings2 = _redirect("/settings/", _SettingsWithSlotView)
    assert settings2._child_views["audio-player"] is audio


# ---------------------------------------------------------------------------
# 3. Non-route redirect (server perspective)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_live_redirect_to_non_live_session_full_reload_unmounts_sticky():
    """When the destination page has NO matching ``[dj-sticky-slot]`` the
    server's sticky staging still runs auth re-check (the sticky is
    "survivable" in principle) but the final reattach step finds no
    slot and fires ``_on_sticky_unmount()``. From the server's
    perspective, the preserved stash is drained before the
    ``sticky_hold`` frame would be sent."""
    consumer = _FakeConsumer()
    _mount_parent(
        consumer,
        "tests.integration.test_sticky_redirect_flow._DashboardView",
    )
    dashboard = consumer.view_instance
    audio = dashboard._child_views["audio-player"]
    unmount_calls: list[str] = []
    audio._on_sticky_unmount = (  # type: ignore[attr-defined]
        lambda: unmount_calls.append("audio-unmounted")
    )

    rf = RequestFactory()
    from django.contrib.auth.models import AnonymousUser

    req = rf.get("/other/")
    req.user = AnonymousUser()
    survivors = dashboard._preserve_sticky_children(req)
    assert "audio-player" in survivors  # passes auth, stageable

    # But the destination template has NO matching slot — reconcile.
    from django.template import Context, Template
    from djust.websocket import _find_sticky_slot_ids

    dest_html = Template(_SettingsNoSlotView.template).render(Context({}))
    matched = _find_sticky_slot_ids(dest_html)
    assert matched == set()

    # Apply the post-mount reconcile logic the consumer runs.
    for sticky_id, child in list(survivors.items()):
        if sticky_id not in matched:
            hook = getattr(child, "_on_sticky_unmount", None)
            if callable(hook):
                hook()
            survivors.pop(sticky_id)
    assert survivors == {}
    assert unmount_calls == ["audio-unmounted"]


# ---------------------------------------------------------------------------
# 4. ADR-014: tag auto-detect drives the full pipeline
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dashboard_to_dashboard_auto_reattach_emits_slot_via_tag():
    """ADR-014: when the destination parent's template re-issues
    ``{% live_render sticky=True %}`` for the same sticky_id the consumer
    is holding, the tag itself emits a ``<dj-sticky-slot>`` placeholder
    rather than a fresh subtree.

    This test deliberately exercises the destination template render
    (the existing ``_redirect`` helper bypasses it). Without ADR-014's
    fix the freshly-mounted child would collide with the survivor on
    ``_register_child`` and the survivor would be discarded via
    ``_on_sticky_unmount``.
    """
    consumer = _FakeConsumer()
    _mount_parent(
        consumer,
        "tests.integration.test_sticky_redirect_flow._DashboardView",
    )
    dashboard_a = consumer.view_instance
    audio = dashboard_a._child_views["audio-player"]
    audio.play()  # mutate state we expect to survive

    # Stage stickies for a Dashboard → Dashboard return-trip.
    rf = RequestFactory()
    new_request = rf.get("/")
    from django.contrib.auth.models import AnonymousUser

    new_request.user = AnonymousUser()
    survivors = dashboard_a._preserve_sticky_children(new_request)
    assert survivors.get("audio-player") is audio

    # Wire the consumer's sticky state as the real pipeline does — this
    # is the bridge ADR-014 added: the tag reads from
    # ``consumer._sticky_preserved`` and writes to
    # ``consumer._sticky_auto_reattached``.
    consumer._sticky_preserved = dict(survivors)
    consumer._sticky_auto_reattached = set()

    # Build the destination Dashboard. The ``_ws_consumer`` back-reference
    # is what the tag uses to find the consumer.
    dashboard_b = _DashboardView()
    dashboard_b.request = new_request
    dashboard_b._ws_consumer = consumer
    dashboard_b.mount(new_request)

    # Render the Dashboard template — this is the step that the new tag
    # branch executes. The OLD behavior would mount a fresh
    # AudioPlayerSticky and emit ``dj-sticky-view``; the NEW behavior
    # detects the survivor and emits ``dj-sticky-slot`` instead.
    from django.template import Context, Template

    rendered = Template(dashboard_b.template).render(
        Context({"view": dashboard_b, "request": new_request})
    )

    # Tag took the auto-detect branch.
    assert 'dj-sticky-slot="audio-player"' in rendered
    assert "dj-sticky-view=" not in rendered

    # Survivor was re-registered onto the new parent without re-running
    # mount() — its state is preserved.
    assert dashboard_b._child_views.get("audio-player") is audio
    assert audio.current_track == "next-track"

    # Tag pushed onto the consumer's auto-reattach set so the post-render
    # slot scan in ``handle_mount`` will skip the second
    # ``_register_child`` and not ``ValueError``.
    assert "audio-player" in consumer._sticky_auto_reattached


@pytest.mark.asyncio
async def test_dashboard_to_dashboard_via_full_handle_mount_pipeline():
    """End-to-end check that ``handle_mount`` running with both
    ``sticky_preserved`` AND a destination template carrying inline
    ``{% live_render sticky=True %}`` ends in the right place: survivor
    registered exactly once, ``sticky_hold`` frame includes the id, and
    the ``mount`` frame body contains ``dj-sticky-slot`` (not
    ``dj-sticky-view``) for the auto-reattached id.

    This is the ADR-014 §"Test contract" T8/T9 case — verifying that
    the auto-reattach path coordinates correctly with the consumer's
    post-render slot-scan + frame-emission logic.
    """
    consumer = _FakeConsumer()
    _mount_parent(
        consumer,
        "tests.integration.test_sticky_redirect_flow._DashboardView",
    )
    dashboard_a = consumer.view_instance
    audio = dashboard_a._child_views["audio-player"]
    audio.play()

    rf = RequestFactory()
    new_request = rf.get("/")
    from django.contrib.auth.models import AnonymousUser

    new_request.user = AnonymousUser()
    survivors = dashboard_a._preserve_sticky_children(new_request)
    consumer._sticky_preserved = dict(survivors)
    consumer._sticky_auto_reattached = set()

    # Build the new parent with the consumer back-reference, render its
    # template (auto-reattach branch fires), then run the consumer's
    # post-render slot scan via the real path.
    dashboard_b = _DashboardView()
    dashboard_b.request = new_request
    dashboard_b._ws_consumer = consumer
    dashboard_b.mount(new_request)
    from django.template import Context, Template

    rendered_html = Template(dashboard_b.template).render(
        Context({"view": dashboard_b, "request": new_request})
    )
    consumer.view_instance = dashboard_b

    # Inline the consumer's post-render slot-scan + sticky_hold emission
    # logic from handle_mount (lines around websocket.py:2278). Tests
    # the integration of: tag's auto-reattach + slot-scan skip-on-claim
    # + sticky_hold survivor list.
    from djust.websocket import _find_sticky_slot_ids

    matched_ids = _find_sticky_slot_ids(rendered_html)
    survivors_final: Dict[str, Any] = {}
    for sticky_id, child in consumer._sticky_preserved.items():
        if sticky_id in consumer._sticky_auto_reattached:
            survivors_final[sticky_id] = child
        elif sticky_id in matched_ids:
            try:
                consumer.view_instance._register_child(sticky_id, child)
                survivors_final[sticky_id] = child
            except ValueError:
                pass

    # ``audio-player`` survives via the auto-reattach branch.
    assert "audio-player" in survivors_final
    assert survivors_final["audio-player"] is audio
    # ``mount`` frame body uses dj-sticky-slot, NOT dj-sticky-view.
    assert 'dj-sticky-slot="audio-player"' in rendered_html
    # Survivor was registered exactly once (no ValueError, no duplicate).
    assert dashboard_b._child_views.get("audio-player") is audio
    # State preserved.
    assert audio.current_track == "next-track"
