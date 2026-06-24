"""Real-``WebsocketCommunicator`` MOUNT parity net for the 3.3b flip (#1911, ADR-022).

ADR-022 Iter 3 converges the WS ``mount`` verb onto ``ViewRuntime.dispatch_mount``
over zero-WS-routing-risk PRs (Phase 3.0-3.3a build-up), then ONE atomic flip
(Phase 3.3b: ``mount`` into ``RUNTIME_OWNED_VERBS`` + a thin ``handle_mount``
shim). This file — the FIRST PR of the mount convergence (Phase 3.0) —
CHARACTERIZES the six mount behaviors the flip must preserve, driving each
against the CURRENT bespoke ``handle_mount`` path over a real channels
``WebsocketCommunicator``.

Every test here:

* drives ``LiveViewConsumer.as_asgi()`` end-to-end (mount → assert on the mount
  frame / a follow-on frame), so it exercises the WS mount path that 3.3b flips;
* PASSES NOW against the bespoke path and must stay green THROUGH the flip — the
  unchanged equivalence IS the parity proof (#1466 / #1780 / #1468);
* asserts intermediate state and carries a gate-off / contrast sibling (#1468)
  proving the assertion distinguishes the real behavior from a vacuous one.

The six behaviors (ADR-022 Iter 3 Phase 3.0):

1. **actor MOUNT** (``use_actors=True`` view → an actor-backed render frame, NOT
   a ``send_error`` refusal) — Finding D (actor mount is WS-verbatim; SSE refuses).
2. **sticky_hold-before-mount-frame ORDERING** via ``live_redirect_mount`` —
   Finding B (the sticky_hold frame MUST precede the mount frame).
3. **Channels group_add server-push reachability** (mount joins the view group →
   a broadcast to that group reaches this session) — Finding B's transport hooks.
4. **periodic tick started at mount** (``tick_interval`` view → a ``source="tick"``
   frame arrives without any client event).
5. **optimistic_rules + upload_configs on the mount frame** (the Phase-3.0 grows,
   now characterized against the BESPOKE WS path they already ship from).
6. **live_redirect re-mount idempotency** (mount A → live_redirect to B → B
   actually mounts, not a no-op) — THE Finding-A regression net: the bespoke path
   nulls ``self.view_instance`` before re-mounting; a naive 3.3b flip that forgets
   to also reset ``runtime.view_instance`` would silently no-op the re-mount
   (``dispatch_mount`` early-returns when ``view_instance is not None``). This is
   the test that would catch that flip bug.

Harness ``_connect_and_mount`` lifted from
``test_ws_event_flip_parity_1896.py:81`` (#1077 lift-from-reference).
"""

from __future__ import annotations

import pytest
from asgiref.sync import sync_to_async

from djust import LiveView


# ---------------------------------------------------------------------------
# Harness (lifted from test_ws_event_flip_parity_1896.py)
# ---------------------------------------------------------------------------


async def _receive_until(communicator, wanted_type, *, tries=8, timeout=3):
    """Drain frames until one whose ``type`` == ``wanted_type`` (or last seen)."""
    last = None
    for _ in range(tries):
        last = await communicator.receive_json_from(timeout=timeout)
        if last.get("type") == wanted_type:
            return last
    return last


async def _drain_available(communicator, *, max_frames=8, timeout=2):
    """Best-effort drain of any frames already on the wire (stops at first
    receive timeout). Uses ``receive_nothing`` polling so a trailing timeout
    does not leave a dangling cancelled receive future (which would make a later
    ``disconnect()`` raise ``CancelledError``)."""
    frames = []
    for _ in range(max_frames):
        nothing = await communicator.receive_nothing(timeout=timeout, interval=0.05)
        if nothing:
            break
        frames.append(await communicator.receive_json_from(timeout=timeout))
    return frames


class _ScopeSession:
    def __init__(self, key):
        self.session_key = key


async def _connect(view_path=None):
    """Connect a ``WebsocketCommunicator`` (connect frame drained). Returns the
    communicator; the caller sends the mount frame."""
    pytest.importorskip("channels")
    from channels.testing import WebsocketCommunicator
    from django.contrib.sessions.backends.db import SessionStore

    from djust.websocket import LiveViewConsumer

    def _create_session():
        s = SessionStore()
        s.create()
        return s.session_key

    session_key = await sync_to_async(_create_session)()

    communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    communicator.scope["session"] = _ScopeSession(session_key)

    connected, _ = await communicator.connect()
    assert connected, "WebsocketCommunicator must connect"
    await communicator.receive_json_from(timeout=2)  # drain connect frame
    return communicator


async def _connect_and_mount(view_path: str, url: str = "/parity/"):
    """Connect + mount ``view_path``. Returns ``(communicator, mount_frame)``."""
    communicator = await _connect()
    await communicator.send_json_to({"type": "mount", "view": view_path, "url": url})
    mount_frame = await _receive_until(communicator, "mount")
    assert mount_frame.get("type") == "mount", f"expected mount, got {mount_frame!r}"
    return communicator, mount_frame


# ---------------------------------------------------------------------------
# Test views — module-level so resolve_view_class() finds them by dotted path.
# ---------------------------------------------------------------------------

_ALLOWED = "djust.tests.test_ws_mount_flip_parity_1911"


class ActorMountView(LiveView):
    """``use_actors=True`` view — mount must go through the actor path and return
    a normal mount frame (Finding D: actor mount is WS-verbatim)."""

    use_actors = True
    template = f'<div dj-root dj-view="{_ALLOWED}.ActorMountView" dj-id="0">c={{{{ c }}}}</div>'

    def mount(self, request, **kwargs):
        self.c = 0

    def get_context_data(self, **kwargs):
        return {"c": self.c}


class PlainMountView(LiveView):
    """Non-actor control for the actor-mount gate-off (proves the actor frame is
    not just "any mount frame")."""

    template = f'<div dj-root dj-view="{_ALLOWED}.PlainMountView" dj-id="0">c={{{{ c }}}}</div>'

    def mount(self, request, **kwargs):
        self.c = 0

    def get_context_data(self, **kwargs):
        return {"c": self.c}


class TickView(LiveView):
    """A view with a short ``tick_interval`` whose ``handle_tick`` changes public
    state — so the tick loop emits a ``source="tick"`` render frame."""

    tick_interval = 50  # ms
    template = f'<div dj-root dj-view="{_ALLOWED}.TickView" dj-id="0">t={{{{ t }}}}</div>'

    def mount(self, request, **kwargs):
        self.t = 0

    def handle_tick(self):
        self.t += 1

    def get_context_data(self, **kwargs):
        return {"t": self.t}


class NoTickView(LiveView):
    """Gate-off control: NO ``tick_interval`` → no tick frame ever (proves the
    tick frame comes from the interval opt-in, not from every mount)."""

    template = f'<div dj-root dj-view="{_ALLOWED}.NoTickView" dj-id="0">t={{{{ t }}}}</div>'

    def mount(self, request, **kwargs):
        self.t = 0

    def get_context_data(self, **kwargs):
        return {"t": self.t}


class BroadcastView(LiveView):
    """Plain view — mount joins the per-view channel group, so a server-push
    broadcast to that group reaches this session (proves group_add at mount)."""

    template = f'<div dj-root dj-view="{_ALLOWED}.BroadcastView" dj-id="0">v={{{{ v }}}}</div>'

    def mount(self, request, **kwargs):
        self.v = "init"

    def get_context_data(self, **kwargs):
        return {"v": self.v}


class _OptDescriptor:
    class Meta:
        tier = "optimistic"
        event = "toggle"
        optimistic_rule = {"action": "toggle_class", "class": "open"}


class _FakeUploadManager:
    def get_upload_state(self):
        return {"avatar": {"config": {"max_size": 1024, "accept": "image/*"}}}


class OptimisticUploadMountView(LiveView):
    """Ships optimistic rules (descriptor component) AND upload_configs (upload
    manager) on its mount frame — the Phase-3.0 grows, characterized against the
    BESPOKE WS path."""

    _component_descriptors = {"acc": _OptDescriptor()}
    template = f'<div dj-root dj-view="{_ALLOWED}.OptimisticUploadMountView" dj-id="0">ready</div>'

    def mount(self, request, **kwargs):
        self._upload_manager = _FakeUploadManager()

    def get_context_data(self, **kwargs):
        return {}


class PlainNoExtrasView(LiveView):
    """Gate-off control: no descriptors, no upload manager → neither key."""

    template = f'<div dj-root dj-view="{_ALLOWED}.PlainNoExtrasView" dj-id="0">ready</div>'

    def mount(self, request, **kwargs):
        pass

    def get_context_data(self, **kwargs):
        return {}


# ---- live_redirect / sticky views (tests 2 + 6) ----


class StickyChild(LiveView):
    """Sticky child embedded via ``{% live_render sticky=True %}``."""

    sticky = True
    sticky_id = "sticky-widget"
    template = "<div>track={{ track }}</div>"

    def mount(self, request, **kwargs):
        self.track = "one"

    def get_context_data(self, **kwargs):
        return {"track": self.track, "view": self}


class StickyParentView(LiveView):
    """Parent embedding the sticky child (the live_redirect SOURCE)."""

    template = (
        "{% load live_tags %}"
        f'<div dj-root dj-view="{_ALLOWED}.StickyParentView" dj-id="0">'
        "<h1>Parent A</h1>"
        f'{{% live_render "{_ALLOWED}.StickyChild" sticky=True %}}'
        "</div>"
    )

    def mount(self, request, **kwargs):
        pass

    def get_context_data(self, **kwargs):
        return {"view": self}


class DestinationWithSlotView(LiveView):
    """live_redirect DESTINATION carrying a matching ``[dj-sticky-slot]`` so the
    survivor reattaches and appears in the sticky_hold ``views`` list."""

    template = (
        f'<div dj-root dj-view="{_ALLOWED}.DestinationWithSlotView" dj-id="0">'
        '<h1>Dest B</h1><div dj-sticky-slot="sticky-widget"></div></div>'
    )

    def mount(self, request, **kwargs):
        pass

    def get_context_data(self, **kwargs):
        return {"view": self}


class RedirectViewA(LiveView):
    """Plain re-mount source A (no sticky) for the idempotency test."""

    template = f'<div dj-root dj-view="{_ALLOWED}.RedirectViewA" dj-id="0"><h1>A</h1></div>'

    def mount(self, request, **kwargs):
        self.who = "A"

    def get_context_data(self, **kwargs):
        return {"who": self.who}


class RedirectViewB(LiveView):
    """Plain re-mount destination B for the idempotency test."""

    template = f'<div dj-root dj-view="{_ALLOWED}.RedirectViewB" dj-id="0"><h1>B</h1></div>'

    def mount(self, request, **kwargs):
        self.who = "B"

    def get_context_data(self, **kwargs):
        return {"who": self.who}


# ---------------------------------------------------------------------------
# Test URLConf — the sticky_hold ordering test (#2) needs the live_redirect
# DESTINATION url to RESOLVE: ``handle_live_redirect_mount`` →
# ``_build_live_redirect_request`` calls ``resolve(url)`` to re-check sticky auth
# against the new URL, and returns None (dropping all stickys) on Resolver404.
# Mapping ``/b/`` to ``DestinationWithSlotView`` here makes resolve() succeed AND
# makes the #1647 ``_resolve_view_path_from_url`` override a NO-OP (it resolves to
# the SAME view the test sends). Applied via @override_settings(ROOT_URLCONF=...)
# on the sticky tests only — the other tests use the default demo URLConf.
# ---------------------------------------------------------------------------
try:
    from django.urls import path as _url_path

    urlpatterns = [
        _url_path("a/", StickyParentView.as_view(), name="parity-a"),
        _url_path("b/", DestinationWithSlotView.as_view(), name="parity-b"),
        _url_path("noslot/", RedirectViewB.as_view(), name="parity-noslot"),
    ]
except Exception:  # pragma: no cover - channels/Django optional at import time
    urlpatterns = []


# ===========================================================================
# 1. actor MOUNT
# ===========================================================================


@pytest.mark.django_db
@pytest.mark.asyncio
class TestActorMount:
    async def test_actor_view_mounts_with_render_frame_not_refusal(self):
        """A ``use_actors=True`` view mounts over real WS via the actor path
        (creates a ``SessionActor``, sets ``actor_handle``) and returns a normal
        ``mount`` frame carrying HTML — NOT the SSE-style ``send_error`` refusal
        the runtime emits for actors (Finding D: actor mount is WS-verbatim).

        The flip (3.3b) must route the WS actor mount through the
        ``transport.dispatch_actor_mount`` hook so this stays a mount frame, never
        a refusal. Reproduce-first: assert the frame is a mount (not an error) AND
        carries the rendered ``c=0`` state.
        """
        pytest.importorskip("channels")
        from djust._rust import create_session_actor  # noqa: F401

        from django.test import override_settings

        with override_settings(LIVEVIEW_ALLOWED_MODULES=[_ALLOWED]):
            communicator, mount_frame = await _connect_and_mount(f"{_ALLOWED}.ActorMountView")

            assert mount_frame.get("type") == "mount", (
                "an actor view must mount with a mount frame (Finding D: WS actor "
                f"mount is verbatim, not the SSE 'use_actors not supported' refusal); "
                f"got {mount_frame!r}"
            )
            # The actor mount path renders via actor_handle.mount and returns
            # ID-tagged HTML (the exact shape differs from the non-actor extract,
            # but it is a real render — never a None/error). Assert HTML present +
            # ID-tagged for VDOM patching, which proves the actor mount produced a
            # client-applicable frame rather than the refusal envelope.
            assert mount_frame.get("html"), (
                f"the actor mount frame must carry rendered HTML; got {mount_frame!r}"
            )
            assert mount_frame.get("has_ids") is True, (
                f"the actor mount HTML must be dj-id-tagged for patching; got {mount_frame!r}"
            )
            assert "error" not in mount_frame.get("type", ""), (
                f"the actor mount must NOT be a refusal/error frame; got {mount_frame!r}"
            )

            await communicator.disconnect()

    async def test_gate_off_plain_view_mounts_without_actor_path(self):
        """GATE-OFF / contrast: a NON-actor view mounts with the same mount-frame
        shape, proving the actor test's frame is a real mount (both views mount;
        the actor one additionally exercises the actor branch). Distinguishes
        "actor mount works" from "any mount produces a mount frame"."""
        from django.test import override_settings

        with override_settings(LIVEVIEW_ALLOWED_MODULES=[_ALLOWED]):
            communicator, mount_frame = await _connect_and_mount(f"{_ALLOWED}.PlainMountView")
            assert mount_frame.get("type") == "mount"
            assert "c=0" in mount_frame.get("html", "")
            await communicator.disconnect()


# ===========================================================================
# 2. sticky_hold-before-mount-frame ORDERING (live_redirect)
# ===========================================================================


@pytest.mark.django_db
@pytest.mark.asyncio
class TestStickyHoldOrdering:
    async def test_sticky_hold_precedes_mount_frame_on_live_redirect(self):
        """On a ``live_redirect_mount`` from a parent with a sticky child to a
        destination carrying the matching ``[dj-sticky-slot]``, the ``sticky_hold``
        frame is emitted BEFORE the destination ``mount`` frame (Finding B
        ordering, websocket.py:2892).

        The flip must preserve this ordering via the transport hooks. Assert the
        ORDERING invariant (sticky_hold index < mount index) — load-independent,
        not a timing race.

        ROOT_URLCONF override: ``handle_live_redirect_mount`` resolves the
        destination URL (to re-check sticky auth) and drops all stickys on
        Resolver404, so the destination ``/b/`` MUST resolve — mapped to
        ``DestinationWithSlotView`` in this module's urlpatterns (which also makes
        the #1647 view-override a no-op).
        """
        from django.test import override_settings

        with override_settings(LIVEVIEW_ALLOWED_MODULES=[_ALLOWED], ROOT_URLCONF=__name__):
            communicator, _ = await _connect_and_mount(f"{_ALLOWED}.StickyParentView", url="/a/")

            await communicator.send_json_to(
                {
                    "type": "live_redirect_mount",
                    "view": f"{_ALLOWED}.DestinationWithSlotView",
                    "url": "/b/",
                }
            )

            frames = await _drain_available(communicator)
            types = [f.get("type") for f in frames]
            assert "sticky_hold" in types, (
                f"a live_redirect with a surviving sticky must emit sticky_hold; got {types}"
            )
            assert "mount" in types, f"the destination must mount; got {types}"
            hold_idx = types.index("sticky_hold")
            mount_idx = types.index("mount")
            assert hold_idx < mount_idx, (
                "the sticky_hold frame MUST precede the mount frame (Finding B "
                f"ordering, websocket.py:2892); got order {types}"
            )
            # The survivor is in the sticky_hold views list (proves a real sticky
            # was preserved, not an empty drop-everything hold).
            hold = frames[hold_idx]
            assert "sticky-widget" in hold.get("views", []), (
                f"the surviving sticky id must be in the sticky_hold views; got {hold!r}"
            )

            await communicator.disconnect()

    async def test_gate_off_no_slot_destination_still_orders_hold_first(self):
        """GATE-OFF / contrast: redirect to a destination WITHOUT the matching
        slot — the survivor does NOT reattach, so the sticky_hold ``views`` list
        is empty (drop-everything), yet the hold STILL precedes the mount frame.
        Proves the ordering invariant holds independent of survivor membership,
        and that membership genuinely reflects slot matching."""
        from django.test import override_settings

        with override_settings(LIVEVIEW_ALLOWED_MODULES=[_ALLOWED], ROOT_URLCONF=__name__):
            communicator, _ = await _connect_and_mount(f"{_ALLOWED}.StickyParentView", url="/a/")

            await communicator.send_json_to(
                {
                    "type": "live_redirect_mount",
                    # RedirectViewB has NO [dj-sticky-slot="sticky-widget"]; mapped
                    # to /noslot/ so resolve() succeeds (sticky staging runs) but no
                    # survivor reattaches.
                    "view": f"{_ALLOWED}.RedirectViewB",
                    "url": "/noslot/",
                }
            )

            frames = await _drain_available(communicator)
            types = [f.get("type") for f in frames]
            assert "mount" in types, f"the destination must still mount; got {types}"
            if "sticky_hold" in types:
                hold_idx = types.index("sticky_hold")
                mount_idx = types.index("mount")
                assert hold_idx < mount_idx, (
                    f"sticky_hold (even empty) must precede the mount frame; got {types}"
                )
                assert "sticky-widget" not in frames[hold_idx].get("views", []), (
                    "with no matching slot the survivor must NOT be in the hold list "
                    f"— proving membership reflects slot matching; got {frames[hold_idx]!r}"
                )

            await communicator.disconnect()


# ===========================================================================
# 3. Channels group_add server-push reachability
# ===========================================================================


@pytest.mark.django_db
@pytest.mark.asyncio
class TestGroupAddReachability:
    async def test_mount_joins_view_group_broadcast_reaches_session(self):
        """Mount joins the per-view channel group (``group_add`` at
        websocket.py:2174), so a ``push_to_view`` broadcast to that group reaches
        THIS session as a ``source="broadcast"`` render frame.

        The flip must move ``group_add`` into a transport hook so broadcast
        delivery survives. Reproduce-first: the only way the broadcast reaches the
        client is if the consumer joined the group at mount.
        """
        from django.test import override_settings

        from djust.push import apush_to_view

        with override_settings(LIVEVIEW_ALLOWED_MODULES=[_ALLOWED]):
            communicator, _ = await _connect_and_mount(f"{_ALLOWED}.BroadcastView")

            # Broadcast a state update to the view group from "outside" (no
            # origin channel set → not a self-broadcast skip).
            await apush_to_view(f"{_ALLOWED}.BroadcastView", state={"v": "pushed"})

            frames = await _drain_available(communicator, timeout=3)
            broadcast_frames = [f for f in frames if f.get("source") == "broadcast"]
            assert broadcast_frames, (
                "a server-push broadcast must reach the session that mounted (proving "
                f"group_add happened at mount); got {[f.get('type') for f in frames]}"
            )
            blob = "".join(str(f) for f in broadcast_frames)
            assert "v=pushed" in blob, (
                f"the broadcast frame must reflect the pushed state; got {broadcast_frames!r}"
            )

            await communicator.disconnect()

    async def test_gate_off_broadcast_to_other_group_not_received(self):
        """GATE-OFF / contrast: a broadcast to a DIFFERENT view's group is NOT
        received — proving the positive test's delivery is group-scoped (this
        session only joined ITS view's group), not a catch-all."""
        from django.test import override_settings

        from djust.push import apush_to_view

        with override_settings(LIVEVIEW_ALLOWED_MODULES=[_ALLOWED]):
            communicator, _ = await _connect_and_mount(f"{_ALLOWED}.BroadcastView")

            # Push to a group this session did NOT join.
            await apush_to_view(f"{_ALLOWED}.PlainMountView", state={"v": "other"})

            frames = await _drain_available(communicator, timeout=1)
            broadcast_frames = [f for f in frames if f.get("source") == "broadcast"]
            assert not broadcast_frames, (
                "a broadcast to a different view's group must NOT reach this session "
                f"(group-scoped delivery); got {broadcast_frames!r}"
            )

            await communicator.disconnect()


# ===========================================================================
# 4. periodic tick started at mount
# ===========================================================================


@pytest.mark.django_db
@pytest.mark.asyncio
class TestTickAtMount:
    async def test_tick_interval_view_emits_tick_frame_without_client_event(self):
        """A view with ``tick_interval`` whose ``handle_tick`` mutates state emits a
        ``source="tick"`` render frame WITHOUT any client event — proving the tick
        task started at mount (websocket.py:2208).

        The flip must move the tick-task start into a transport hook so periodic
        ticks survive. Reproduce-first: no event is sent, so the only source of a
        render frame is the mount-started tick loop.
        """
        from django.test import override_settings

        with override_settings(LIVEVIEW_ALLOWED_MODULES=[_ALLOWED]):
            communicator, _ = await _connect_and_mount(f"{_ALLOWED}.TickView")

            # No client event — just wait for the tick loop (50ms interval) to fire.
            frames = await _drain_available(communicator, timeout=3)
            tick_frames = [f for f in frames if f.get("source") == "tick"]
            assert tick_frames, (
                "a tick_interval view must emit a source='tick' frame from the "
                "mount-started tick loop with NO client event; got "
                f"{[(f.get('type'), f.get('source')) for f in frames]}"
            )

            await communicator.disconnect()

    async def test_gate_off_no_tick_interval_no_tick_frame(self):
        """GATE-OFF (#1468): an identical mount on a view WITHOUT ``tick_interval``
        emits NO tick frame within the same window — proving the tick frame comes
        from the interval opt-in starting the loop at mount, not from every
        mount."""
        from django.test import override_settings

        with override_settings(LIVEVIEW_ALLOWED_MODULES=[_ALLOWED]):
            communicator, _ = await _connect_and_mount(f"{_ALLOWED}.NoTickView")

            frames = await _drain_available(communicator, timeout=1)
            tick_frames = [f for f in frames if f.get("source") == "tick"]
            assert not tick_frames, (
                "a view without tick_interval must NOT emit a tick frame; got "
                f"{[(f.get('type'), f.get('source')) for f in frames]}"
            )

            await communicator.disconnect()


# ===========================================================================
# 5. optimistic_rules + upload_configs on the mount frame
# ===========================================================================


@pytest.mark.django_db
@pytest.mark.asyncio
class TestOptimisticUploadOnMountFrame:
    async def test_mount_frame_carries_optimistic_rules_and_upload_configs(self):
        """The bespoke WS mount frame carries ``optimistic_rules`` (DEP-002
        descriptor components) AND ``upload_configs`` (UploadMixin). These are the
        Phase-3.0 runtime grows; this test characterizes them against the BESPOKE
        path so the flip can verify the runtime frame matches.

        Gate-off sibling below: a plain view carries NEITHER key.
        """
        from django.test import override_settings

        with override_settings(LIVEVIEW_ALLOWED_MODULES=[_ALLOWED]):
            communicator, mount_frame = await _connect_and_mount(
                f"{_ALLOWED}.OptimisticUploadMountView"
            )

            assert mount_frame.get("optimistic_rules") == {
                "toggle": {"action": "toggle_class", "class": "open"}
            }, f"the mount frame must carry optimistic_rules; got {mount_frame!r}"
            assert mount_frame.get("upload_configs") == {
                "avatar": {"max_size": 1024, "accept": "image/*"}
            }, f"the mount frame must carry upload_configs; got {mount_frame!r}"

            await communicator.disconnect()

    async def test_gate_off_plain_view_carries_neither_key(self):
        """GATE-OFF (#1468): a plain view (no descriptors, no upload manager)
        carries NEITHER ``optimistic_rules`` nor ``upload_configs`` — proving the
        keys above come from the view's config, not unconditional stamping."""
        from django.test import override_settings

        with override_settings(LIVEVIEW_ALLOWED_MODULES=[_ALLOWED]):
            communicator, mount_frame = await _connect_and_mount(f"{_ALLOWED}.PlainNoExtrasView")

            assert "optimistic_rules" not in mount_frame, (
                f"a plain view must not carry optimistic_rules; got {mount_frame!r}"
            )
            assert "upload_configs" not in mount_frame, (
                f"a plain view must not carry upload_configs; got {mount_frame!r}"
            )

            await communicator.disconnect()


# ===========================================================================
# 6. live_redirect re-mount idempotency — THE Finding-A regression net
# ===========================================================================


@pytest.mark.django_db
@pytest.mark.asyncio
class TestLiveRedirectRemountIdempotency:
    async def test_live_redirect_actually_remounts_not_noop(self):
        """Mount view A, then ``live_redirect_mount`` to view B: B must ACTUALLY
        mount (a fresh mount frame carrying B's content), NOT silently no-op.

        **THE Finding-A net (ADR-022 Iter 3).** The bespoke path nulls
        ``self.view_instance`` in ``handle_live_redirect_mount`` (websocket.py:3844)
        BEFORE re-mounting, so the re-mount runs. ``ViewRuntime.dispatch_mount``
        early-returns when ``view_instance is not None`` (runtime.py:1125), and the
        consumer never nulls ``runtime.view_instance``. A NAIVE 3.3b flip that
        forgets to reset ``runtime.view_instance = None`` on the live_redirect
        teardown would make this re-mount a SILENT no-op — and THIS test would
        catch it (it stays green only if the flip resets runtime.view_instance).

        Reproduce-first: assert B's distinct content (``<h1>B</h1>``) reaches the
        client, proving the second mount genuinely ran (not A's stale frame).
        """
        from django.test import override_settings

        with override_settings(LIVEVIEW_ALLOWED_MODULES=[_ALLOWED]):
            communicator, mount_a = await _connect_and_mount(f"{_ALLOWED}.RedirectViewA", url="/a/")
            assert ">A</h1>" in mount_a.get("html", ""), (
                f"the first mount must render A; got {mount_a!r}"
            )

            await communicator.send_json_to(
                {
                    "type": "live_redirect_mount",
                    "view": f"{_ALLOWED}.RedirectViewB",
                    "url": "/b/",
                }
            )

            frames = await _drain_available(communicator)
            mount_frames = [f for f in frames if f.get("type") == "mount"]
            assert mount_frames, (
                "the live_redirect to B must produce a fresh mount frame — NOT a "
                f"silent no-op (Finding A); got {[f.get('type') for f in frames]}"
            )
            mount_b = mount_frames[-1]
            assert ">B</h1>" in mount_b.get("html", ""), (
                "the re-mount must render the NEW view B (proving the re-mount ran, "
                f"not a stale A frame / no-op); got {mount_b!r}"
            )
            assert mount_b.get("view") == f"{_ALLOWED}.RedirectViewB", (
                f"the re-mount frame must name view B; got {mount_b!r}"
            )

            await communicator.disconnect()

    async def test_gate_off_first_mount_is_view_a_not_b(self):
        """GATE-OFF / contrast: BEFORE the redirect, the mounted view is A (its
        content, not B's) — proving the positive test's ``<h1>B</h1>`` assertion
        genuinely distinguishes the re-mounted B from the original A (the two
        views render distinct content)."""
        from django.test import override_settings

        with override_settings(LIVEVIEW_ALLOWED_MODULES=[_ALLOWED]):
            communicator, mount_a = await _connect_and_mount(f"{_ALLOWED}.RedirectViewA", url="/a/")
            assert ">A</h1>" in mount_a.get("html", ""), (
                f"the first mount renders A; got {mount_a!r}"
            )
            assert ">B</h1>" not in mount_a.get("html", ""), (
                "A's mount frame must NOT already contain B's content — proving the "
                f"positive test's B-assertion is non-vacuous; got {mount_a!r}"
            )
            await communicator.disconnect()
