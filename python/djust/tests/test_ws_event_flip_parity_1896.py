"""Real-``WebsocketCommunicator`` event parity net for the 2.3 flip (#1896, ADR-022).

Phase 2.3b will flip ALL WS events from the bespoke ``_handle_event_inner``
(``websocket.py``) onto ``ViewRuntime.dispatch_event`` by adding ``"event"`` to
``RUNTIME_OWNED_VERBS``. Before that flip lands, this file CHARACTERIZES what
the flip must preserve: the observable response frames the *current* bespoke
WS event path produces for six behaviors that the existing Phase-2.1/2.2 runtime
tests cover only against a ``MockTransport``
(``test_runtime_child_routing_1892.py``), NOT a live consumer.

Every test here drives a real channels ``WebsocketCommunicator`` against
``LiveViewConsumer.as_asgi()`` end-to-end (mount → event frame → assert on the
response frame), so it exercises the WS path the flip will replace. These pass
NOW; 2.3b requires them to stay green after the flip — that equivalence IS the
parity proof (#1466 / #1780 / #1468). Where the bespoke path diverges from the
runtime path (component_id — see ``TestComponentIdRouting``), the test pins the
bespoke behavior verbatim and the docstring flags the divergence as a 2.3a
finding: that test will need to be UPDATED at the flip, and the update is itself
the signal that the flip changed behavior on that axis.

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


_ALLOWED = "djust.tests.test_ws_event_flip_parity_1896"


# ===========================================================================
# 1. component_id routing
# ===========================================================================


@pytest.mark.django_db
@pytest.mark.asyncio
class TestComponentIdRouting:
    """``component_id`` event routing on the bespoke WS path.

    **2.3a FINDING (the load-bearing divergence).** The bespoke
    ``_handle_event_inner`` ``component_id`` branch resolves + runs the
    component handler, but NEVER re-renders the parent: ``html`` stays ``None``
    (initialized at the top of the non-actor block, never reassigned in the
    component branch), so the html_update fallback strips ``None`` and the path
    raises ``TypeError: expected string or bytes-like object, got 'NoneType'``,
    which ``handle_exception`` turns into an ``error`` frame. The
    Phase-2.1 runtime path (``test_runtime_child_routing_1892.py``
    ``TestRuntimeComponentRouting``) renders correctly and emits a parent-scoped
    ``html_update`` — so the flip will CHANGE this axis from ``error`` to
    ``html_update``. This test therefore pins the *current* bespoke behavior;
    it MUST be updated at 2.3b (error → html_update), and that update is the
    parity signal that the flip fixed the component path.
    """

    async def test_component_id_event_resolves_component_handler_then_errors(self):
        """The component handler runs (component state mutates, parent notified),
        but the bespoke path then crashes on the None parent-html render and
        returns an ``error`` frame — NOT a parent ``patch`` and NOT a component
        ``html_update``.

        Reproduce-first: a ``component_id``-routed event must reach the
        COMPONENT's ``relabel`` (not the parent, which has no such handler) —
        proven by the absence of a 'Component not found' / permission error and
        by the bespoke crash being inside ``CompParentView.relabel`` (the
        component-event arm), not a handler-not-found rejection.
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

            # Bespoke component path does not render the parent → error frame.
            assert resp.get("type") == "error", (
                "2.3a finding: the bespoke component_id path does not re-render "
                "the parent (html stays None) and returns an error frame. The "
                "Phase-2.1 runtime path renders a parent-scoped html_update — "
                f"the flip will change this axis. Got {resp!r}"
            )
            # It is NOT a component-not-found / handler-not-found rejection: the
            # routing reached the COMPONENT handler (the crash is downstream of
            # resolution).
            assert "not found" not in resp.get("error", "").lower(), (
                f"component_id must resolve the component (not a not-found error); got {resp!r}"
            )
            # And it is NOT a successful parent patch / component html_update —
            # that is exactly the divergence the flip closes.
            assert resp.get("type") not in ("patch", "html_update"), (
                f"the bespoke component path produces no render frame today; got {resp!r}"
            )

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
                "an ungated bump must render immediately (not defer); got "
                f"{resp!r}"
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
            assert html_update.get("ref") == 33, f"html_update must echo ref=33; got {html_update!r}"

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
                "a text change with no _force_full_html must patch (not html_update); "
                f"got {resp!r}"
            )

            await communicator.disconnect()
