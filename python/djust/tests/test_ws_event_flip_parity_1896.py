"""Real-``WebsocketCommunicator`` event parity net for the 2.3 flip (#1896, ADR-022).

Phase 2.3b (#1907, THE FLIP) flipped ALL WS events from the (now deleted) bespoke
``_handle_event_inner`` (``websocket.py``) onto ``ViewRuntime.dispatch_event`` by
adding ``"event"`` to ``RUNTIME_OWNED_VERBS``. This file CHARACTERIZES the six
behaviors the flip had to preserve — behaviors the Phase-2.1/2.2 runtime tests
cover only against a ``MockTransport`` (``test_runtime_child_routing_1892.py``),
NOT a live consumer.

Every test here drives a real channels ``WebsocketCommunicator`` against
``LiveViewConsumer.as_asgi()`` end-to-end (mount → event frame → assert on the
response frame), so it exercises the WS event path — which is now the RUNTIME
path. Five behaviors are pure-equivalent and stay green unchanged across the flip
(time-travel, dj_activity, actor, ref echo, force_full_html) — that equivalence IS
the parity proof (#1466 / #1780 / #1468). The SIXTH (``component_id`` — see
``TestComponentIdRouting``) is the ONE intended behavioral change: the deleted
bespoke path returned an ``error`` frame (it never re-rendered the parent — #1898),
the runtime path renders the parent and emits ``html_update``. That test was
UPDATED at the flip (error → html_update); the update is itself the signal that
the flip fixed #1898 on that axis.

Each behavior carries a gate-off / contrast sibling (#1468) proving the
assertion distinguishes the real behavior from a vacuous one (a handler that
does NOT set ``_force_full_html`` patches instead of html_update; an event with
no ``ref`` echoes none; a non-deferred event renders immediately).

Harness lifted from ``test_ws_send_version_1788.py`` /
``test_sticky_child_event_noop_1802.py``.
"""

from __future__ import annotations

import pytest
from asgiref.sync import sync_to_async

from djust import LiveView
from djust.components.base import LiveComponent
from djust.decorators import event_handler


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


async def _receive_until(communicator, wanted_type, *, tries=8, timeout=3):
    """Drain frames until one whose ``type`` == ``wanted_type`` (or last seen)."""
    last = None
    for _ in range(tries):
        last = await communicator.receive_json_from(timeout=timeout)
        if last.get("type") == wanted_type:
            return last
    return last


async def _drain_available(communicator, *, max_frames=6, timeout=2):
    """Best-effort drain of any frames already on the wire.

    Stops at the first receive timeout (no more frames). Crucially uses
    ``communicator.receive_nothing``-style polling via a bounded receive so a
    trailing timeout does NOT leave the application's receive future in a
    cancelled state (which would make a subsequent ``disconnect()`` raise
    ``CancelledError``). Returns the list of frames collected.
    """
    frames = []
    for _ in range(max_frames):
        # receive_nothing returns True if no frame arrives within ``timeout``;
        # it polls without leaving a dangling cancelled receive future.
        nothing = await communicator.receive_nothing(timeout=timeout, interval=0.05)
        if nothing:
            break
        frames.append(await communicator.receive_json_from(timeout=timeout))
    return frames


class _ScopeSession:
    def __init__(self, key):
        self.session_key = key


async def _connect_and_mount(view_path: str, url: str = "/parity/"):
    """Connect a ``WebsocketCommunicator`` and mount ``view_path``.

    Returns ``(communicator, mount_frame)``.
    """
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

    await communicator.send_json_to({"type": "mount", "view": view_path, "url": url})
    mount_frame = await _receive_until(communicator, "mount")
    assert mount_frame.get("type") == "mount", f"expected mount, got {mount_frame!r}"
    return communicator, mount_frame


# ---------------------------------------------------------------------------
# Test views — module-level so import_string() resolves them by dotted path.
# ---------------------------------------------------------------------------


class _Widget(LiveComponent):
    """LiveComponent whose ``relabel`` handler mutates component state and
    notifies the parent (events-up, the documented pattern)."""

    template = "<span>label={{ label }}</span>"

    def __init__(self, component_id: str) -> None:
        super().__init__(component_id=component_id)
        self.label = "start"

    @event_handler()
    def relabel(self, label: str = "", **kwargs) -> None:
        self.label = label
        self.send_parent("relabelled", {"label": label})

    def get_context_data(self, **kwargs):
        return {"label": self.label}


class ComponentParentView(LiveView):
    """Parent hosting a LiveComponent (``component_id`` routing target)."""

    template = (
        '<div dj-root dj-view="djust.tests.test_ws_event_flip_parity_1896.ComponentParentView" '
        'dj-id="0"><h1>parent {{ parent_n }}</h1><p>last={{ last_label }}</p></div>'
    )

    def mount(self, request, **kwargs):
        self.parent_n = 0
        self.last_label = "none"
        # Instance-attr LiveComponent: the context mixin's get_context_data
        # walks self.__dict__, finds it, and registers it into _components
        # under its component_id ("w1") — the registry component_id routing
        # resolves against.
        self.widget = _Widget("w1")

    def handle_component_event(self, component_id, event, data):
        if event == "relabelled":
            self.last_label = data.get("label", "")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["parent_n"] = self.parent_n
        ctx["last_label"] = self.last_label
        return ctx


class TimeTravelView(LiveView):
    """View with time-travel recording enabled (DEBUG + class opt-in)."""

    time_travel_enabled = True
    template = (
        '<div dj-root dj-view="djust.tests.test_ws_event_flip_parity_1896.TimeTravelView" '
        'dj-id="0">n={{ n }}</div>'
    )

    def mount(self, request, **kwargs):
        self.n = 0

    @event_handler()
    def bump(self, **kwargs):
        self.n += 1


class PlainView(LiveView):
    """No time-travel buffer — the gate-off control for the tt-event test."""

    template = (
        '<div dj-root dj-view="djust.tests.test_ws_event_flip_parity_1896.PlainView" '
        'dj-id="0">n={{ n }}</div>'
    )

    def mount(self, request, **kwargs):
        self.n = 0

    @event_handler()
    def bump(self, **kwargs):
        self.n += 1


class ActivityView(LiveView):
    """View with a ``dj_activity`` region registered HIDDEN.

    The ``{% dj_activity %}`` template tag would register the region during
    render, but the inline Rust template engine doesn't support the tag, so we
    register it directly in mount via ``set_activity_visible`` — equivalent for
    the server-side deferral gate (``is_activity_visible`` reads the same
    ``_djust_activities`` map the tag populates).
    """

    template = (
        '<div dj-root dj-view="djust.tests.test_ws_event_flip_parity_1896.ActivityView" '
        'dj-id="0">n={{ n }} opens={{ opens }}</div>'
    )

    def mount(self, request, **kwargs):
        self.n = 0
        self.opens = 0
        self.set_activity_visible("panel", False)

    @event_handler()
    def bump(self, **kwargs):
        self.n += 1

    @event_handler()
    def show_panel(self, **kwargs):
        # Flip the activity visible AND change a public assign. The auto-skip
        # early-return path bypasses the deferred-activity flush, so a
        # panel-open handler must produce a render for queued events to drain
        # in the same round-trip.
        self.set_activity_visible("panel", True)
        self.opens += 1

    def get_context_data(self, **kwargs):
        return {"n": self.n, "opens": self.opens, "view": self}


class ActorEventView(LiveView):
    """View driven by the Rust actor system (``use_actors = True``)."""

    use_actors = True
    template = (
        '<div dj-root dj-view="djust.tests.test_ws_event_flip_parity_1896.ActorEventView" '
        'dj-id="0">c={{ c }}</div>'
    )

    def mount(self, request, **kwargs):
        self.c = 0

    @event_handler()
    def inc(self, **kwargs):
        self.c += 1

    def get_context_data(self, **kwargs):
        return {"c": self.c}


class RefView(LiveView):
    """Drives all three event-response handlers for the ref-echo test:
    a noop (no state change), a patch (text change), an html_update
    (``_force_full_html``)."""

    template = (
        '<div dj-root dj-view="djust.tests.test_ws_event_flip_parity_1896.RefView" '
        'dj-id="0"><p>n={{ n }}</p></div>'
    )

    def mount(self, request, **kwargs):
        self.n = 0

    @event_handler()
    def noop_event(self, **kwargs):
        pass  # no public assign change → auto-skip → noop frame

    @event_handler()
    def patch_change(self, **kwargs):
        self.n += 1  # text change → patch frame

    @event_handler()
    def force(self, **kwargs):
        self.n += 1
        self._force_full_html = True  # discard patches → html_update frame

    def get_context_data(self, **kwargs):
        return {"n": self.n}


class ForceView(LiveView):
    """Force-full-html on the FIRST event, normal patch on the SECOND —
    proves the flag is consumed (not sticky)."""

    template = (
        '<div dj-root dj-view="djust.tests.test_ws_event_flip_parity_1896.ForceView" '
        'dj-id="0"><p>n={{ n }}</p></div>'
    )

    def mount(self, request, **kwargs):
        self.n = 0

    @event_handler()
    def force_once(self, **kwargs):
        self.n += 1
        self._force_full_html = True

    @event_handler()
    def plain_bump(self, **kwargs):
        self.n += 1  # no _force_full_html → normal patch

    def get_context_data(self, **kwargs):
        return {"n": self.n}


class _DebugProbeView(LiveView):
    """A view with a public assign so ``get_debug_update()`` yields a non-empty
    variables dump for the #1908 ``_debug``-payload unit tests."""

    template = (
        '<div dj-root dj-view="djust.tests.test_ws_event_flip_parity_1896._DebugProbeView" '
        'dj-id="0"><p>n={{ n }}</p></div>'
    )

    def mount(self, request, **kwargs):
        self.n = 0

    @event_handler()
    def bump(self, **kwargs):
        self.n += 1

    def get_context_data(self, **kwargs):
        return {"n": self.n}


_ALLOWED = "djust.tests.test_ws_event_flip_parity_1896"


# ===========================================================================
# 1. component_id routing
# ===========================================================================


@pytest.mark.django_db
@pytest.mark.asyncio
class TestComponentIdRouting:
    """``component_id`` event routing — UPDATED at THE FLIP (#1907, closes #1898).

    **The load-bearing divergence the flip CLOSES.** Before Phase 2.3b, the
    bespoke ``_handle_event_inner`` ``component_id`` branch resolved + ran the
    component handler but NEVER re-rendered the parent: ``html`` stayed ``None``
    (initialized at the top of the non-actor block, never reassigned in the
    component branch), so the html_update fallback stripped ``None`` and the path
    raised ``TypeError: expected string or bytes-like object, got 'NoneType'``,
    which ``handle_exception`` turned into an ``error`` frame (#1898 — the bespoke
    component_id bug). This test originally PINNED that broken behavior as the 2.3a
    finding and flagged that the flip would change it from ``error`` to
    ``html_update``.

    Phase 2.3b (#1907) flipped WS events onto ``ViewRuntime.dispatch_event``,
    whose ``_dispatch_component_event`` (the Phase-2.1 port,
    ``test_runtime_child_routing_1892.py`` ``TestRuntimeComponentRouting``)
    renders the parent correctly and emits a parent-scoped ``html_update``. This
    test is now UPDATED to assert that correct behavior — the update IS the parity
    signal that the flip fixed #1898 (the change from ``error`` → ``html_update``
    on this axis is the SINGLE intended behavioral change of THE FLIP).
    """

    async def test_component_id_event_resolves_component_handler_then_renders_parent(self):
        """The component handler runs (component state mutates, parent notified via
        ``send_parent``), and the runtime then re-renders the PARENT and emits a
        parent-scoped ``html_update`` carrying the parent's updated state — NOT the
        ``error`` frame the deleted bespoke path produced (#1898 fixed by #1907).

        Reproduce-first: a ``component_id``-routed event must reach the COMPONENT's
        ``relabel`` (not the parent, which has no such handler) — proven by the
        parent's ``last_label`` reflecting the value the component handler pushed up
        via ``send_parent("relabelled", {"label": "DONE"})``, and by the absence of
        a 'Component not found' / permission error.
        """
        from django.test import override_settings

        with override_settings(LIVEVIEW_ALLOWED_MODULES=[_ALLOWED]):
            communicator, _ = await _connect_and_mount(f"{_ALLOWED}.ComponentParentView")

            await communicator.send_json_to(
                {
                    "type": "event",
                    "event": "relabel",
                    "params": {"component_id": "w1", "label": "DONE"},
                    "ref": 5,
                }
            )
            resp = await communicator.receive_json_from(timeout=3)

            # #1898 FIX (#1907 THE FLIP): the runtime component_id path re-renders
            # the parent and emits a parent-scoped html_update — NOT the bespoke
            # error frame.
            assert resp.get("type") == "html_update", (
                "#1907 THE FLIP fixed #1898: the runtime component_id path now "
                "re-renders the parent and emits a parent-scoped html_update "
                f"(the deleted bespoke path returned an error frame). Got {resp!r}"
            )
            # The component handler genuinely ran: it called send_parent, which the
            # parent's handle_component_event consumed into last_label="DONE", and
            # the parent re-render reflects it. (Proves the event resolved the
            # COMPONENT's relabel, not a parent handler / not-found rejection.)
            assert "last=DONE" in resp.get("html", ""), (
                "the component_id event must resolve the COMPONENT's relabel "
                "handler, which pushes 'DONE' to the parent via send_parent; the "
                f"parent re-render must reflect last=DONE. Got {resp!r}"
            )
            # And it echoes the client ref (#560) on the parent render frame.
            assert resp.get("ref") == 5, f"the component render frame must echo ref=5; got {resp!r}"

            await communicator.disconnect()

    async def test_unknown_component_id_errors_component_not_found(self):
        """GATE-OFF / contrast: a bogus ``component_id`` is rejected at
        RESOLUTION with a 'Component not found' error — distinct from the
        None-html crash of the resolved-but-unrendered case above. Proves the
        first test's event genuinely resolved a real component (the two errors
        have different causes)."""
        from django.test import override_settings

        with override_settings(LIVEVIEW_ALLOWED_MODULES=[_ALLOWED]):
            communicator, _ = await _connect_and_mount(f"{_ALLOWED}.ComponentParentView")

            await communicator.send_json_to(
                {
                    "type": "event",
                    "event": "relabel",
                    "params": {"component_id": "ghost", "label": "X"},
                    "ref": 6,
                }
            )
            resp = await communicator.receive_json_from(timeout=3)

            assert resp.get("type") == "error", f"unknown component_id must error; got {resp!r}"
            assert "not found" in resp.get("error", "").lower(), (
                f"unknown component_id must be a 'Component not found' rejection; got {resp!r}"
            )

            await communicator.disconnect()


# ===========================================================================
# 2. time-travel-on-event
# ===========================================================================


@pytest.mark.django_db
@pytest.mark.asyncio
class TestTimeTravelOnEvent:
    async def test_event_emits_time_travel_event_frame_with_update(self):
        """An event on a time-travel-enabled view (DEBUG + class opt-in) emits a
        ``time_travel_event`` frame in addition to the render (``patch``) frame.
        ``_maybe_push_tt_event`` runs in the handler's ``finally`` (before the
        render send), so both frames reach the client in the same round-trip.
        """
        from django.test import override_settings

        with override_settings(LIVEVIEW_ALLOWED_MODULES=[_ALLOWED], DEBUG=True):
            communicator, _ = await _connect_and_mount(f"{_ALLOWED}.TimeTravelView")

            await communicator.send_json_to(
                {"type": "event", "event": "bump", "params": {}, "ref": 1}
            )

            frames = await _drain_available(communicator)

            types = [f.get("type") for f in frames]
            assert "time_travel_event" in types, (
                f"a time-travel-enabled view must emit a time_travel_event frame; got {types}"
            )
            # The render frame for the state change is also present.
            assert "patch" in types or "html_update" in types, (
                f"the event's render frame must accompany the tt frame; got {types}"
            )
            tt = next(f for f in frames if f.get("type") == "time_travel_event")
            assert "entry" in tt and tt["entry"].get("event_name") == "bump", (
                f"time_travel_event must carry the recorded entry for 'bump'; got {tt!r}"
            )

            await communicator.disconnect()

    async def test_gate_off_no_tt_frame_without_buffer(self):
        """GATE-OFF (#1468): an identical event on a view WITHOUT
        ``time_travel_enabled`` emits NO ``time_travel_event`` frame — proving
        the frame in the positive test comes from the buffer opt-in, not from
        every event unconditionally."""
        from django.test import override_settings

        with override_settings(LIVEVIEW_ALLOWED_MODULES=[_ALLOWED], DEBUG=True):
            communicator, _ = await _connect_and_mount(f"{_ALLOWED}.PlainView")

            await communicator.send_json_to(
                {"type": "event", "event": "bump", "params": {}, "ref": 1}
            )

            frames = await _drain_available(communicator)

            types = [f.get("type") for f in frames]
            assert "time_travel_event" not in types, (
                f"a view without time_travel_enabled must NOT emit a tt frame; got {types}"
            )
            assert "patch" in types or "html_update" in types, (
                f"the event still renders normally; got {types}"
            )

            await communicator.disconnect()


# ===========================================================================
# 3. dj_activity deferral
# ===========================================================================


@pytest.mark.django_db
@pytest.mark.asyncio
class TestActivityDeferral:
    async def test_hidden_activity_event_defers_then_drains_on_show(self):
        """An event targeting a HIDDEN ``dj_activity`` region is deferred: the
        server returns a bare ``noop`` (no render). When a later handler flips
        the activity visible AND changes public state (so it doesn't auto-skip),
        the deferred event drains in the SAME round-trip — a second render frame
        carrying the deferred handler's ``event_name``.
        """
        from django.test import override_settings

        with override_settings(LIVEVIEW_ALLOWED_MODULES=[_ALLOWED]):
            communicator, _ = await _connect_and_mount(f"{_ALLOWED}.ActivityView")

            # 1. Fire bump targeting the hidden 'panel' activity → deferred noop.
            await communicator.send_json_to(
                {
                    "type": "event",
                    "event": "bump",
                    "params": {"_activity": "panel"},
                    "ref": 1,
                }
            )
            deferred = await communicator.receive_json_from(timeout=3)
            assert deferred.get("type") == "noop", (
                "an event on a hidden non-eager activity must be deferred with a "
                f"noop (no render); got {deferred!r}"
            )
            assert deferred.get("ref") == 1, f"the deferred noop echoes the ref; got {deferred!r}"

            # 2. Show the panel (also changes a public assign so it renders) →
            #    the queued bump drains in the same round-trip.
            await communicator.send_json_to(
                {"type": "event", "event": "show_panel", "params": {}, "ref": 2}
            )
            frames = await _drain_available(communicator)

            event_names = [f.get("event_name") for f in frames]
            assert "show_panel" in event_names, (
                f"the show_panel render frame must be present; got {frames!r}"
            )
            assert "bump" in event_names, (
                "the deferred 'bump' must drain in the same round-trip once the "
                f"activity is shown; got frames {frames!r}"
            )
            # The drained bump actually applied (n incremented to 1) — visible in
            # the second frame's patch text or full html.
            drained = next(f for f in frames if f.get("event_name") == "bump")
            blob = str(drained)
            assert "n=1" in blob, (
                f"the drained bump must reflect n=1 in its render; got {drained!r}"
            )

            await communicator.disconnect()

    async def test_gate_off_visible_activity_event_renders_immediately(self):
        """GATE-OFF (#1468): the SAME ``bump`` event with NO ``_activity`` marker
        (i.e. not gated behind a hidden region) renders IMMEDIATELY — a render
        frame, never a deferred noop. Proves the deferral in the positive test
        is caused by the hidden-activity gate, not by the event being a noop."""
        from django.test import override_settings

        with override_settings(LIVEVIEW_ALLOWED_MODULES=[_ALLOWED]):
            communicator, _ = await _connect_and_mount(f"{_ALLOWED}.ActivityView")

            await communicator.send_json_to(
                {"type": "event", "event": "bump", "params": {}, "ref": 9}
            )
            resp = await _receive_until(communicator, "patch")

            assert resp.get("type") in ("patch", "html_update"), (
                f"an ungated bump must render immediately (not defer); got {resp!r}"
            )
            assert resp.get("event_name") == "bump"
            assert "n=1" in str(resp), f"the ungated bump applies immediately; got {resp!r}"

            await communicator.disconnect()


# ===========================================================================
# 4. actor-path events
# ===========================================================================


@pytest.mark.django_db
@pytest.mark.asyncio
class TestActorPathEvents:
    async def test_actor_event_returns_render_frame(self):
        """A view with ``use_actors = True`` mounts a Rust SessionActor over real
        WS (sets ``actor_handle``); an event then routes through the actor block
        (``actor_handle.event``) and returns a client-applicable render frame
        (``patch`` or ``html_update``) stamped with the consumer-owned version
        and the event name.

        This is the highest-value test: the actor event path is currently
        UNTESTED via the live consumer dispatch (the Phase-5 actor tests in
        ``python/tests/test_actor_integration.py`` call ``handle.event``
        directly, never the WS consumer). The flip must preserve it.
        """
        pytest.importorskip("channels")
        # The Rust actor factory must be available in this build.
        from djust._rust import create_session_actor  # noqa: F401

        from django.test import override_settings

        with override_settings(LIVEVIEW_ALLOWED_MODULES=[_ALLOWED]):
            communicator, mount_frame = await _connect_and_mount(f"{_ALLOWED}.ActorEventView")
            # Mount went through the actor path (use_actors=True) — the consumer
            # created a SessionActor; the mount frame is a normal mount frame.
            assert mount_frame.get("type") == "mount"

            await communicator.send_json_to(
                {"type": "event", "event": "inc", "params": {}, "ref": 1}
            )

            # The actor event path emits a single render frame (a patch for this
            # text-only diff). Drain to the first patch/html_update.
            frames = await _drain_available(communicator)
            render_frames = [f for f in frames if f.get("type") in ("patch", "html_update")]
            assert render_frames, (
                f"actor event must return a patch/html_update render frame; got {frames!r}"
            )
            frame = render_frames[0]
            assert frame.get("type") in ("patch", "html_update"), (
                f"actor event path must emit a render frame; got {frame!r}"
            )
            assert frame.get("event_name") == "inc", (
                f"the actor render frame must carry the event_name; got {frame!r}"
            )
            # Consumer-owned wire version (#1788): mount baseline 1 → first event 2.
            assert frame.get("version") == mount_frame.get("version") + 1, (
                "actor event frame must advance the consumer wire version by 1; "
                f"mount={mount_frame.get('version')!r}, event={frame.get('version')!r}"
            )

            await communicator.disconnect()


# ===========================================================================
# 5. ref echo (three handlers)
# ===========================================================================


@pytest.mark.django_db
@pytest.mark.asyncio
class TestRefEcho:
    async def test_ref_echoed_on_noop_patch_and_html_update(self):
        """The client-sent ``ref`` (#560) is echoed back on all three event
        response frame shapes: ``noop`` (auto-skip), ``patch`` (text diff), and
        ``html_update`` (``_force_full_html``). Each is a distinct handler /
        send path, so all three echo sites must be characterized.
        """
        from django.test import override_settings

        with override_settings(LIVEVIEW_ALLOWED_MODULES=[_ALLOWED]):
            communicator, _ = await _connect_and_mount(f"{_ALLOWED}.RefView")

            # (a) noop frame
            await communicator.send_json_to(
                {"type": "event", "event": "noop_event", "params": {}, "ref": 11}
            )
            noop = await communicator.receive_json_from(timeout=3)
            assert noop.get("type") == "noop", f"no-state-change event must noop; got {noop!r}"
            assert noop.get("ref") == 11, f"noop must echo ref=11; got {noop!r}"

            # (b) patch frame
            await communicator.send_json_to(
                {"type": "event", "event": "patch_change", "params": {}, "ref": 22}
            )
            patch = await _receive_until(communicator, "patch")
            assert patch.get("type") == "patch", f"text change must patch; got {patch!r}"
            assert patch.get("ref") == 22, f"patch must echo ref=22; got {patch!r}"

            # (c) html_update frame
            await communicator.send_json_to(
                {"type": "event", "event": "force", "params": {}, "ref": 33}
            )
            html_update = await _receive_until(communicator, "html_update")
            assert html_update.get("type") == "html_update", (
                f"_force_full_html must html_update; got {html_update!r}"
            )
            assert html_update.get("ref") == 33, (
                f"html_update must echo ref=33; got {html_update!r}"
            )

            await communicator.disconnect()

    async def test_gate_off_no_ref_no_echo(self):
        """GATE-OFF (#1468): an event sent WITHOUT a ``ref`` produces a frame
        with NO ``ref`` key — proving the echo is genuinely sourced from the
        client's ref, not a constant the consumer always stamps."""
        from django.test import override_settings

        with override_settings(LIVEVIEW_ALLOWED_MODULES=[_ALLOWED]):
            communicator, _ = await _connect_and_mount(f"{_ALLOWED}.RefView")

            await communicator.send_json_to(
                {"type": "event", "event": "patch_change", "params": {}}
            )
            patch = await _receive_until(communicator, "patch")
            assert patch.get("type") == "patch", f"text change must patch; got {patch!r}"
            assert "ref" not in patch, (
                f"a ref-less event must not carry a ref in its response; got {patch!r}"
            )

            await communicator.disconnect()


# ===========================================================================
# 6. _force_full_html
# ===========================================================================


@pytest.mark.django_db
@pytest.mark.asyncio
class TestForceFullHtml:
    async def test_force_full_html_then_flag_consumed(self):
        """A handler that sets ``_force_full_html = True`` makes the response an
        ``html_update`` (patches discarded, full HTML on the wire). The flag is
        single-shot: the NEXT event renders a normal ``patch`` — proving the
        consumer reset it after the forced frame.
        """
        from django.test import override_settings

        with override_settings(LIVEVIEW_ALLOWED_MODULES=[_ALLOWED]):
            communicator, _ = await _connect_and_mount(f"{_ALLOWED}.ForceView")

            # Forced event → html_update with full HTML, no patches.
            await communicator.send_json_to(
                {"type": "event", "event": "force_once", "params": {}, "ref": 1}
            )
            forced = await _receive_until(communicator, "html_update")
            assert forced.get("type") == "html_update", (
                f"_force_full_html must produce an html_update; got {forced!r}"
            )
            assert forced.get("html"), "the html_update must carry full HTML"
            assert not forced.get("patches"), (
                f"the forced frame must NOT carry patches; got {forced!r}"
            )
            assert "n=1" in forced["html"], f"the forced html reflects n=1; got {forced!r}"

            # Next event → normal patch (flag was consumed, not sticky).
            await communicator.send_json_to(
                {"type": "event", "event": "plain_bump", "params": {}, "ref": 2}
            )
            nxt = await _receive_until(communicator, "patch")
            assert nxt.get("type") == "patch", (
                "the event after a forced frame must patch normally — the "
                f"_force_full_html flag must be consumed; got {nxt!r}"
            )

            await communicator.disconnect()

    async def test_gate_off_no_force_flag_patches_not_html_update(self):
        """GATE-OFF (#1468): the SAME kind of text change WITHOUT setting
        ``_force_full_html`` produces a ``patch``, not an ``html_update`` —
        proving the html_update in the positive test is caused by the flag, not
        by the change shape."""
        from django.test import override_settings

        with override_settings(LIVEVIEW_ALLOWED_MODULES=[_ALLOWED]):
            communicator, _ = await _connect_and_mount(f"{_ALLOWED}.ForceView")

            await communicator.send_json_to(
                {"type": "event", "event": "plain_bump", "params": {}, "ref": 1}
            )
            resp = await _receive_until(communicator, "patch")
            assert resp.get("type") == "patch", (
                f"a text change with no _force_full_html must patch (not html_update); got {resp!r}"
            )

            await communicator.disconnect()


# ===========================================================================
# 7. Residual-fold observability (#1907): DJE-053 warning + record_handler_timing
#
# THE FLIP deletes the bespoke _handle_event_inner, which owned the DJE-053
# warning (websocket.py:4215 — production-visible, MUST SURVIVE per #1079) and
# the record_handler_timing telemetry (websocket.py:3645). The runtime re-emits
# both via the on_render_emitted / on_handler_timing transport hooks. These tests
# pin that the production-visible residual survives the flip + the gate-off
# discipline (the warning is reason/version-gated, not unconditional).
# ===========================================================================


class TestResidualFoldObservability:
    """Unit tests for the WSConsumerTransport hooks the flip introduced (#1907)."""

    def _make_transport(self):
        from djust.runtime import WSConsumerTransport

        class _FakeConsumer:
            _last_sent_version = 7

            def _next_version_armed(self, html):
                return 8

            def _next_version(self):
                return 8

            def _arm_recovery(self, html):
                pass

        return WSConsumerTransport(_FakeConsumer())

    class _V:
        template_name = "tmpl.html"

    def test_dje053_warning_fires_on_no_patches_with_baseline(self, caplog):
        """The DJE-053 developer warning (production-visible, #1079) MUST fire when
        a runtime event render falls back to full HTML with an established VDOM
        baseline (reason='no_patches', version>1). This is the ONE residual the
        flip had to preserve that is NOT DEBUG-gated."""
        t = self._make_transport()
        with caplog.at_level("WARNING", logger="djust.runtime"):
            t.on_render_emitted(
                self._V(), reason="no_patches", version=3, event_name="bump", html="<p>x</p>"
            )
        assert any("DJE-053" in r.message for r in caplog.records), (
            "the DJE-053 warning must survive THE FLIP — it fires on a no-patches "
            "html_update fallback with version>1 (#1079 / #1907)."
        )

    def test_dje053_gate_off_first_render_logs_debug_not_warning(self, caplog):
        """GATE-OFF (#1468): a genuine first render (version<=1) must NOT emit the
        DJE-053 WARNING (it's a benign first paint, not a diff failure) — proving
        the warning is reason/version-gated, not emitted on every full-HTML frame."""
        t = self._make_transport()
        with caplog.at_level("WARNING", logger="djust.runtime"):
            t.on_render_emitted(
                self._V(), reason="first_render", version=1, event_name="bump", html="<p>x</p>"
            )
        assert not any("DJE-053" in r.message for r in caplog.records), (
            "DJE-053 must NOT fire for a first render (version<=1) — that would be "
            "a false-positive on a benign first paint (#1907)."
        )

    def test_dje053_gate_off_patch_compression_no_warning(self, caplog):
        """GATE-OFF (#1468): patch_compression chose HTML over patches that DID
        exist — it is not a diff failure, so DJE-053 must NOT fire (matches the WS
        bespoke gate, where compression emitted only its own signal)."""
        t = self._make_transport()
        with caplog.at_level("WARNING", logger="djust.runtime"):
            t.on_render_emitted(
                self._V(),
                reason="patch_compression",
                version=9,
                event_name="bump",
                html="<p>x</p>",
                patch_count=200,
            )
        assert not any("DJE-053" in r.message for r in caplog.records), (
            "DJE-053 must NOT fire on patch_compression (patches existed; "
            "compression chose HTML for size) (#1907)."
        )

    def test_record_handler_timing_forwarded(self):
        """record_handler_timing telemetry MUST survive the flip (#1907): the
        on_handler_timing hook forwards (view_class, event_name, duration_ms) to
        the global percentile registry the bespoke view-path populated."""
        from unittest.mock import patch as _patch

        t = self._make_transport()
        with _patch("djust.observability.timings.record_handler_timing") as mock_rht:
            t.on_handler_timing(self._V(), "bump", 12.5)
        assert mock_rht.called, "on_handler_timing must call record_handler_timing (#1907)."
        assert mock_rht.call_args.args == ("_V", "bump", 12.5), (
            f"record_handler_timing must receive (view_class, event_name, ms); "
            f"got {mock_rht.call_args!r}"
        )

    def test_record_handler_timing_swallows_errors(self):
        """The telemetry hook is best-effort: a record_handler_timing failure must
        never propagate (a telemetry bug can't break the event turn)."""
        from unittest.mock import patch as _patch

        t = self._make_transport()
        with _patch(
            "djust.observability.timings.record_handler_timing", side_effect=RuntimeError("boom")
        ):
            # Must not raise.
            t.on_handler_timing(self._V(), "bump", 1.0)

    # -- #1908 item 2: no_patches context_snapshot threaded through the signal -----

    def test_no_patches_context_snapshot_threaded_to_signal(self):
        """#1908 item 2: on the ``no_patches`` reason, a ``context`` dict passed to
        ``on_render_emitted`` is built into the ``_emit_full_html_update`` signal's
        ``context_snapshot`` — restoring the DEBUG-tooling metadata the bespoke path
        carried (the runtime previously hard-coded ``context_snapshot=None``)."""
        from unittest.mock import patch as _patch

        t = self._make_transport()
        with _patch("djust.websocket._emit_full_html_update") as mock_emit:
            t.on_render_emitted(
                self._V(),
                reason="no_patches",
                version=3,
                event_name="bump",
                html="<p>x</p>",
                context={"count": 5, "name": "x"},
            )
        assert mock_emit.called, "the full-HTML-update signal must be emitted"
        snap = mock_emit.call_args.kwargs.get("context_snapshot")
        assert snap == {"count": 5, "name": "x"}, (
            "the no_patches signal must carry the context snapshot built from the "
            f"threaded context dict (#1908 item 2); got {snap!r}"
        )

    def test_no_patches_context_snapshot_none_when_no_context(self):
        """GATE-OFF (#1468): with NO ``context`` threaded (the old behavior), the
        ``no_patches`` snapshot stays ``None`` — proving the snapshot comes from the
        threaded dict, not some incidental source."""
        from unittest.mock import patch as _patch

        t = self._make_transport()
        with _patch("djust.websocket._emit_full_html_update") as mock_emit:
            t.on_render_emitted(
                self._V(), reason="no_patches", version=3, event_name="bump", html="<p>x</p>"
            )
        assert mock_emit.call_args.kwargs.get("context_snapshot") is None, (
            "without a threaded context, the no_patches snapshot must be None (#1908)."
        )

    def test_context_snapshot_only_on_no_patches_reason(self):
        """The context snapshot is scoped to the ``no_patches`` reason (matching the
        bespoke path, which only snapshotted there) — a ``first_render`` with a
        context dict must NOT carry a snapshot."""
        from unittest.mock import patch as _patch

        t = self._make_transport()
        with _patch("djust.websocket._emit_full_html_update") as mock_emit:
            t.on_render_emitted(
                self._V(),
                reason="first_render",
                version=1,
                event_name="bump",
                html="<p>x</p>",
                context={"count": 5},
            )
        assert mock_emit.call_args.kwargs.get("context_snapshot") is None, (
            "context_snapshot must be None for first_render even when a context is "
            "threaded — it is a no_patches-only diagnostic (#1908)."
        )

    # -- #1908 item 1 + 3: on_event_frame DEBUG residual fold (unit) --------------

    def _make_attaching_transport(self, *, debug_panel_active=True, populate_tracker=False):
        """A transport whose fake consumer carries the REAL ``_attach_debug_payload``
        + a view with ``get_debug_update`` so ``on_event_frame`` exercises the true
        attach path. Returns ``(transport, consumer)``."""
        from djust.runtime import WSConsumerTransport
        from djust.websocket import LiveViewConsumer

        view = _DebugProbeView()
        view.mount(None)

        class _FakeConsumer:
            view_instance = view
            _debug_panel_active = debug_panel_active
            # Borrow the real consumer method (unbound) so the production attach logic
            # is what runs, not a stub (#1037 greenwashing-catcher).
            _attach_debug_payload = LiveViewConsumer._attach_debug_payload

        consumer = _FakeConsumer()
        t = WSConsumerTransport(consumer)
        if populate_tracker:
            from djust.performance import PerformanceTracker

            tracker = PerformanceTracker()
            with tracker.track("Event Processing"):
                pass
            PerformanceTracker.set_current(tracker)
        else:
            from djust.performance import PerformanceTracker

            PerformanceTracker.set_current(None)
        return t, consumer

    def test_on_event_frame_attaches_debug_in_debug_mode(self):
        """#1908 item 1b: in DEBUG with the panel active, ``on_event_frame`` attaches
        the per-event ``_debug`` panel payload to a runtime-emitted event frame —
        the residual the bespoke ``_send_update`` carried and the runtime dropped."""
        from django.test import override_settings

        t, _ = self._make_attaching_transport()
        frame = {"type": "patch", "patches": [], "version": 8, "event_name": "bump"}
        with override_settings(DEBUG=True):
            t.on_event_frame(t._consumer.view_instance, frame, event_name="bump", event_ref=1)
        assert "_debug" in frame, (
            "in DEBUG, a runtime event frame must carry the _debug panel payload (#1908 item 1)."
        )
        assert frame["_debug"].get("_eventName") == "bump"

    def test_on_event_frame_no_debug_in_production(self):
        """PARITY/GATE-OFF (#1468): in PRODUCTION (DEBUG=False) the same frame must
        NOT carry ``_debug`` / ``timing`` / ``performance`` — the production frame is
        byte-identical to the pre-fix runtime frame (no behavior change)."""
        from django.test import override_settings

        t, _ = self._make_attaching_transport()
        frame = {"type": "patch", "patches": [], "version": 8, "event_name": "bump"}
        with override_settings(DEBUG=False, DJUST_EXPOSE_TIMING=False):
            t.on_event_frame(t._consumer.view_instance, frame, event_name="bump", event_ref=1)
        assert "_debug" not in frame, "DEBUG payload must be absent in production (#1908)."
        assert "timing" not in frame, "top-level timing must be absent in production (#654/#1908)."
        assert "performance" not in frame, "top-level performance must be absent in production."
        assert "_timing_render_ms" not in frame, (
            "the internal render-timing marker must never leak onto the wire frame."
        )

    def test_on_event_frame_no_debug_when_panel_closed(self):
        """GATE-OFF (#1468): even in DEBUG, a CLOSED debug panel
        (``_debug_panel_active = False``) must suppress the _debug payload — matching
        the bespoke ``_attach_debug_payload`` panel gate (websocket.py:1649)."""
        from django.test import override_settings

        t, _ = self._make_attaching_transport(debug_panel_active=False)
        frame = {"type": "patch", "patches": [], "version": 8, "event_name": "bump"}
        with override_settings(DEBUG=True):
            t.on_event_frame(t._consumer.view_instance, frame, event_name="bump", event_ref=1)
        assert "_debug" not in frame, (
            "a closed debug panel must suppress the _debug payload even in DEBUG (#1908)."
        )

    def test_on_event_frame_timing_under_expose_timing(self):
        """#1908 item 1: with ``_should_expose_timing()`` true (DEBUG or
        DJUST_EXPOSE_TIMING) the top-level ``timing`` carries the render duration the
        runtime stamped as ``_timing_render_ms``; the internal marker is consumed."""
        from django.test import override_settings

        t, _ = self._make_attaching_transport()
        frame = {"type": "patch", "patches": [], "version": 8, "_timing_render_ms": 4.2}
        with override_settings(DEBUG=False, DJUST_EXPOSE_TIMING=True):
            t.on_event_frame(t._consumer.view_instance, frame, event_name="bump")
        assert frame.get("timing") == {"render": 4.2}, (
            f"timing must carry the render ms under expose_timing; got {frame.get('timing')!r}"
        )
        assert "_timing_render_ms" not in frame, "the internal marker must be popped."

    def test_on_event_frame_performance_attached_when_tracker_populated(self):
        """#1908 item 1: when the borrowed PerformanceTracker is populated,
        ``on_event_frame`` attaches the top-level ``performance`` summary under
        expose-timing (the bespoke ``tracker.get_summary()`` feed)."""
        from django.test import override_settings

        t, _ = self._make_attaching_transport(populate_tracker=True)
        frame = {"type": "patch", "patches": [], "version": 8, "_timing_render_ms": 1.0}
        with override_settings(DEBUG=False, DJUST_EXPOSE_TIMING=True):
            t.on_event_frame(t._consumer.view_instance, frame, event_name="bump")
        from djust.performance import PerformanceTracker

        PerformanceTracker.set_current(None)  # cleanup contextvar
        assert "performance" in frame and frame["performance"], (
            "a populated tracker summary must ride the frame as top-level performance "
            f"under expose-timing (#1908); got {frame.get('performance')!r}"
        )

    def test_on_event_frame_stamps_consumer_event_attrs(self):
        """#1908 item 3: ``on_event_frame`` stamps ``_current_event_name`` /
        ``_current_event_ref`` on the consumer (cosmetic parity for out-of-band
        readers; the bespoke handler set them for its no-arg _dispatch_async_work)."""
        from django.test import override_settings

        t, consumer = self._make_attaching_transport()
        frame = {"type": "patch", "patches": [], "version": 8}
        with override_settings(DEBUG=False):
            t.on_event_frame(consumer.view_instance, frame, event_name="bump", event_ref=42)
        assert consumer._current_event_name == "bump"
        assert consumer._current_event_ref == 42

    def test_on_event_frame_swallows_attach_errors(self):
        """Best-effort: an attach failure must never propagate out of on_event_frame
        (a debug-decoration bug can't break the event turn)."""
        from django.test import override_settings

        from djust.runtime import WSConsumerTransport

        class _BoomConsumer:
            view_instance = object()

            def _attach_debug_payload(self, *a, **k):
                raise RuntimeError("boom")

        t = WSConsumerTransport(_BoomConsumer())
        frame = {"type": "patch", "version": 8}
        with override_settings(DEBUG=True):
            # Must not raise; the consumer attrs are still stamped before the attach.
            t.on_event_frame(_BoomConsumer.view_instance, frame, event_name="bump")

    # -- #1921: dead _extract_* removal from LiveViewConsumer ----------------------

    def test_dead_extract_methods_removed_from_consumer(self):
        """#1921: the bespoke ``LiveViewConsumer._extract_cache_config`` /
        ``_extract_optimistic_rules`` were dead post-mount-flip (#1920 deleted the
        handle_mount body that called them; runtime.py owns the live copies). Pin
        that they are GONE from the consumer so they cannot silently return."""
        from djust.websocket import LiveViewConsumer

        assert not hasattr(LiveViewConsumer, "_extract_cache_config"), (
            "LiveViewConsumer._extract_cache_config was dead code removed in #1921 — "
            "the runtime owns the live copy (runtime.py)."
        )
        assert not hasattr(LiveViewConsumer, "_extract_optimistic_rules"), (
            "LiveViewConsumer._extract_optimistic_rules was dead code removed in #1921 "
            "— the runtime owns the live copy (runtime.py)."
        )

    def test_runtime_extract_methods_still_live(self):
        """The runtime ``ViewRuntime`` copies of the extract helpers — the live ones
        the mount frame uses — MUST remain (the #1921 deletion was consumer-only)."""
        from djust.runtime import ViewRuntime

        assert hasattr(ViewRuntime, "_extract_cache_config")
        assert hasattr(ViewRuntime, "_extract_optimistic_rules")


# ===========================================================================
# 8. start_async / @background on a WS event (post-flip path, #1907)
#
# Before THE FLIP, a WS event's start_async/@background work dispatched through
# the CONSUMER's _dispatch_async_work / _run_async_work. Post-flip it routes
# through ViewRuntime._dispatch_async_work → _execute_async_task → the
# source="async" result frame. No prior test drove this end-to-end over a real
# WebsocketCommunicator, so this pins that background work still streams its
# re-rendered result frame after a WS event (the runtime async path is otherwise
# only soaked via SSE + url_change).
# ===========================================================================


class _BackgroundView(LiveView):
    """A handler that flushes a loading state immediately, then completes
    background work that updates state + clears the flag (the @background
    pattern)."""

    template = (
        '<div dj-root dj-view="djust.tests.test_ws_event_flip_parity_1896._BackgroundView" '
        'dj-id="0"><p>val={{ val }} loading={{ loading }}</p></div>'
    )

    def mount(self, request, **kwargs):
        self.val = 0
        self.loading = False

    @event_handler()
    def go(self, **kwargs):
        self.loading = True

        def _work():
            self.val = 99
            self.loading = False

        self.start_async(_work)

    def get_context_data(self, **kwargs):
        return {"val": self.val, "loading": self.loading}


@pytest.mark.django_db
@pytest.mark.asyncio
class TestBackgroundWorkOverRuntime:
    async def test_start_async_streams_result_frame_after_ws_event(self):
        """A WS event whose handler schedules ``start_async`` work must (1) return
        the immediate event frame reflecting the loading flag, then (2) stream a
        second ``source="async"`` frame once the background callback completes —
        proving the runtime async dispatcher (now the WS event async path post-flip)
        re-renders + streams the result."""
        from django.test import override_settings

        with override_settings(LIVEVIEW_ALLOWED_MODULES=[_ALLOWED]):
            communicator, _ = await _connect_and_mount(f"{_ALLOWED}._BackgroundView")

            await communicator.send_json_to(
                {"type": "event", "event": "go", "params": {}, "ref": 1}
            )

            frames = await _drain_available(communicator, max_frames=8, timeout=3)
            # The background completion frame is tagged source="async" and carries
            # the post-work state (val=99, loading cleared).
            async_frames = [f for f in frames if f.get("source") == "async"]
            assert async_frames, (
                "start_async background work must stream a source='async' result "
                f"frame after the WS event (runtime async dispatcher, #1907); got "
                f"{[(f.get('type'), f.get('source')) for f in frames]}"
            )
            blob = "".join(str(f) for f in async_frames)
            assert "val=99" in blob, (
                f"the streamed background result must reflect the post-work state "
                f"(val=99); got {async_frames!r}"
            )

            await communicator.disconnect()


# ===========================================================================
# 9. DEBUG event-render residuals over a real WebsocketCommunicator (#1908)
#
# THE FLIP (#1907) routed WS events through ViewRuntime, whose render path sends
# frames via ``transport.send`` directly — dropping the DEBUG ``_debug`` panel
# payload + ``timing`` / ``performance`` fields the bespoke ``_send_update``
# attached. #1908 threads them back through ``WSConsumerTransport.on_event_frame``.
# These tests drive a REAL ``WebsocketCommunicator`` (#1468 reproduction fidelity:
# the actual receive() → runtime dispatch → _render_and_send → on_event_frame
# path, not a hand-built frame) and assert the DEBUG-vs-PRODUCTION parity.
# ===========================================================================


@pytest.mark.django_db
@pytest.mark.asyncio
class TestDebugResidualOnEventFrame:
    async def test_event_frame_carries_debug_payload_in_debug(self):
        """In DEBUG (panel active by default), a runtime-routed WS event's render
        frame carries the per-event ``_debug`` panel payload (#1908 item 1)."""
        from django.test import override_settings

        with override_settings(LIVEVIEW_ALLOWED_MODULES=[_ALLOWED], DEBUG=True):
            communicator, _ = await _connect_and_mount(f"{_ALLOWED}._DebugProbeView")
            await communicator.send_json_to(
                {"type": "event", "event": "bump", "params": {}, "ref": 1}
            )
            frames = await _drain_available(communicator, max_frames=6, timeout=3)
            render_frames = [f for f in frames if f.get("type") in ("patch", "html_update")]
            assert render_frames, f"expected a render frame; got {frames!r}"
            assert any("_debug" in f for f in render_frames), (
                "a DEBUG runtime-routed WS event frame must carry the _debug panel "
                f"payload (#1908); got {[list(f.keys()) for f in render_frames]}"
            )
            await communicator.disconnect()

    async def test_event_frame_carries_timing_under_expose_timing(self):
        """With ``DJUST_EXPOSE_TIMING`` (and DEBUG), the event render frame carries
        the top-level ``timing`` field (the render duration) — #1908 item 1."""
        from django.test import override_settings

        with override_settings(
            LIVEVIEW_ALLOWED_MODULES=[_ALLOWED], DEBUG=True, DJUST_EXPOSE_TIMING=True
        ):
            communicator, _ = await _connect_and_mount(f"{_ALLOWED}._DebugProbeView")
            await communicator.send_json_to(
                {"type": "event", "event": "bump", "params": {}, "ref": 1}
            )
            frames = await _drain_available(communicator, max_frames=6, timeout=3)
            render_frames = [f for f in frames if f.get("type") in ("patch", "html_update")]
            assert render_frames, f"expected a render frame; got {frames!r}"
            assert any("timing" in f and "render" in f["timing"] for f in render_frames), (
                "under DJUST_EXPOSE_TIMING the event frame must carry timing.render "
                f"(#1908); got {render_frames!r}"
            )
            await communicator.disconnect()

    async def test_event_frame_no_residuals_in_production(self):
        """PARITY/GATE-OFF (#1468): in PRODUCTION (DEBUG=False, no expose-timing) the
        runtime event frame carries NEITHER ``_debug`` NOR ``timing`` / ``performance``
        NOR the internal ``_timing_render_ms`` marker — byte-identical to the pre-fix
        production frame (the residuals are DEBUG/timing-gated, no behavior change)."""
        from django.test import override_settings

        with override_settings(
            LIVEVIEW_ALLOWED_MODULES=[_ALLOWED], DEBUG=False, DJUST_EXPOSE_TIMING=False
        ):
            communicator, _ = await _connect_and_mount(f"{_ALLOWED}._DebugProbeView")
            await communicator.send_json_to(
                {"type": "event", "event": "bump", "params": {}, "ref": 1}
            )
            frames = await _drain_available(communicator, max_frames=6, timeout=3)
            render_frames = [f for f in frames if f.get("type") in ("patch", "html_update")]
            assert render_frames, f"expected a render frame; got {frames!r}"
            for f in render_frames:
                assert "_debug" not in f, f"prod frame must not carry _debug; got {f.keys()}"
                assert "timing" not in f, f"prod frame must not carry timing; got {f.keys()}"
                assert "performance" not in f, "prod frame must not carry performance"
                assert "_timing_render_ms" not in f, (
                    "the internal render-timing marker must never leak onto the wire"
                )
            await communicator.disconnect()
