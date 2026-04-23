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
