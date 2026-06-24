"""Regression tests for #1788 — consumer-owned monotonic VDOM wire version.

Root cause (verified by local reproduction of djust.org ``/insights/`` ``set_period``):
the wire ``version`` stamped on every client-checked frame was the Rust view's
``self.version`` (``crates/djust_live/src/lib.rs``), which is coupled to the Rust-view
object's *baseline lifetime*, not the connection. When the Rust view loses its
``last_vdom`` baseline mid-session (e.g. the patch-compression path calls
``_rust_view.reset()``, which sets ``version = 0`` and ``last_vdom = None``), the very
next ``render_with_diff`` returns ``(html, patches=None, version=1)`` — a NON-sequential
version that does NOT satisfy the client's
``clientVdomVersion === data.version - 1`` check
(``python/djust/static/djust/src/02-response-handler.js:58``). The client then treats the
``html_update`` as a version mismatch and sends ``request_html`` (recovery round-trip;
pre-#1785 it was a full page reload).

The fix: the *consumer* owns a monotonic per-connection counter (``_last_sent_version``)
and stamps every client-checked outbound frame via ``_next_version()``. The wire version
is then decoupled from the Rust view's lifetime, so a baseline loss produces a correctly
sequenced ``html_update`` (``v_n -> v_{n+1}``) the client accepts directly — no recovery.

CRITICAL FIDELITY NOTE: ``_force_full_html = True`` does NOT lose the Rust baseline (it
keeps ``last_vdom`` and the version increments sequentially), so it does NOT reproduce the
drift and would give a FALSE PASS. The TRUE baseline-loss trigger is
``view_instance._rust_view.reset()`` — that is what these tests use.
"""

from __future__ import annotations

import inspect

import pytest
from asgiref.sync import sync_to_async

from djust import LiveView
from djust.decorators import event_handler


class _WSVersionView(LiveView):
    """Module-level view used to drive real ``WebsocketCommunicator`` round-trips.

    ``bump`` produces a normal text diff (patches present). ``lose_baseline`` first
    resets the Rust view (dropping ``last_vdom`` + setting Rust ``version=0``) and then
    bumps state, so the subsequent ``render_with_diff`` returns ``patches=None`` with a
    non-sequential Rust version — the exact #1788 drift trigger.
    """

    template = (
        '<div dj-view="djust.tests.test_ws_send_version_1788._WSVersionView" '
        'dj-id="0">Count: {{ count }}</div>'
    )

    def mount(self, request, **kwargs):
        self.count = 0

    @event_handler()
    def bump(self, **kwargs):
        self.count += 1

    @event_handler()
    def lose_baseline(self, **kwargs):
        # TRUE baseline-loss trigger: reset the Rust view so its internal
        # version counter resets to 0 and last_vdom is dropped. The next
        # render_with_diff then returns patches=None with a non-sequential
        # Rust version — reproducing the production drift.
        if getattr(self, "_rust_view", None) is not None:
            self._rust_view.reset()
        self.count += 1


# Module-mutable template content for the HVR view, so a hot-reload re-render
# reliably produces a patch (the hotreload handler re-renders and diffs against
# the previous VDOM; identical output yields a 'reload' frame, not a patch).
_HVR_TEMPLATE_BODY = ["Count: {{ count }}"]


class _WSHvrView(LiveView):
    """View used to exercise the channel-layer ``hotreload`` handler (HIDDEN #1)."""

    @property
    def template(self):
        body = _HVR_TEMPLATE_BODY[0]
        return (
            '<div dj-view="djust.tests.test_ws_send_version_1788._WSHvrView" '
            f'dj-id="0">{body}</div>'
        )

    def get_template(self):
        # The hotreload handler calls get_template() to fetch the new content.
        return self.template

    def mount(self, request, **kwargs):
        self.count = 0

    @event_handler()
    def bump(self, **kwargs):
        self.count += 1


async def _receive_until(communicator, wanted_type, *, tries=6, timeout=3):
    """Drain frames until one whose ``type`` == ``wanted_type`` (or return last seen)."""
    last = None
    for _ in range(tries):
        last = await communicator.receive_json_from(timeout=timeout)
        if last.get("type") == wanted_type:
            return last
    return last


async def _connect_and_mount(view_suffix="_WSVersionView", url="/version/"):
    """Lifted harness from test_ws_recovery_html_update_1785.py.

    Returns (communicator, mount_frame).
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

    class _ScopeSession:
        def __init__(self, key):
            self.session_key = key

    communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    communicator.scope["session"] = _ScopeSession(session_key)

    connected, _ = await communicator.connect()
    assert connected, "WebsocketCommunicator must connect"
    await communicator.receive_json_from(timeout=2)  # drain connect frame

    await communicator.send_json_to(
        {
            "type": "mount",
            "view": f"{__name__}.{view_suffix}",
            "url": url,
        }
    )
    mount_frame = await _receive_until(communicator, "mount")
    assert mount_frame.get("type") == "mount", f"expected mount, got {mount_frame!r}"
    return communicator, mount_frame


def _frame_version(frame):
    assert frame is not None
    return frame.get("version")


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_baseline_loss_html_update_stays_monotonic_no_recovery():
    """(a) Round-trip removal — the load-bearing #1788 test.

    mount (v=m) -> normal bump (v=m+1) -> induce baseline loss -> next event emits an
    ``html_update`` whose version is m+2 (consumer stayed monotonic across the baseline
    loss) and satisfies the client's ``prev == emitted - 1`` check, so the client would
    NOT send ``request_html``.

    Gate-off check: if ``_send_update`` stamps the *Rust* version instead of the consumer
    counter, the baseline-loss ``html_update`` carries Rust ``version=1`` (non-sequential)
    and the ``prev == emitted - 1`` assertion fails — proving the test exercises the fix.
    """
    from django.test import override_settings

    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        communicator, mount_frame = await _connect_and_mount()
        m = _frame_version(mount_frame)
        assert m == 1, f"happy-path mount baseline must be 1, got {m!r}"

        # Normal diff event — should be a patch frame at m+1.
        await communicator.send_json_to({"type": "event", "event": "bump", "params": {}, "ref": 1})
        ev1 = await _receive_until(communicator, "patch")
        assert ev1.get("type") == "patch", f"normal bump should patch, got {ev1!r}"
        v1 = _frame_version(ev1)
        assert v1 == m + 1, f"first event must be m+1 ({m + 1}), got {v1!r}"

        # Baseline-loss event — forces patches=None -> html_update fallback.
        await communicator.send_json_to(
            {"type": "event", "event": "lose_baseline", "params": {}, "ref": 2}
        )
        ev2 = await _receive_until(communicator, "html_update")
        assert ev2.get("type") == "html_update", (
            f"baseline-loss event must fall back to html_update; got {ev2!r}"
        )
        assert not ev2.get("patches"), "html_update fallback must carry no patches"
        v2 = _frame_version(ev2)

        # Consumer stayed monotonic across the baseline-loss boundary.
        assert v2 == m + 2, (
            f"baseline-loss html_update must be m+2 ({m + 2}) — consumer-owned version "
            f"must stay monotonic across the Rust baseline reset; got {v2!r}. "
            "If this is 1, the wire version is still the Rust counter (#1788 not fixed)."
        )
        # Client check would PASS: prev (v1) == emitted (v2) - 1 → no request_html.
        assert v1 == v2 - 1, (
            f"client check clientVdomVersion === data.version - 1 must PASS across the "
            f"baseline loss: prev={v1}, emitted={v2}. A failure here is the recovery storm."
        )

        await communicator.disconnect()


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_version_sequence_strictly_monotonic_across_baseline_boundary():
    """(b) DRIFT GUARD (load-bearing).

    mount -> normal event -> baseline-loss html_update -> NORMAL diff event. The full
    version sequence must be strictly m, m+1, m+2, m+3 with no jump/reset at the
    baseline-loss boundary. This catches the case where ONE send path stamps a different
    counter than the others (the entire drift risk of #1788).
    """
    from django.test import override_settings

    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        communicator, mount_frame = await _connect_and_mount()
        m = _frame_version(mount_frame)

        await communicator.send_json_to({"type": "event", "event": "bump", "params": {}, "ref": 1})
        f1 = await _receive_until(communicator, "patch")

        await communicator.send_json_to(
            {"type": "event", "event": "lose_baseline", "params": {}, "ref": 2}
        )
        f2 = await _receive_until(communicator, "html_update")

        # After a reset(), the next diff has a fresh baseline so this normal
        # event produces patches again.
        await communicator.send_json_to({"type": "event", "event": "bump", "params": {}, "ref": 3})
        f3 = await _receive_until(communicator, "patch")

        seq = [m, _frame_version(f1), _frame_version(f2), _frame_version(f3)]
        assert seq == [m, m + 1, m + 2, m + 3], (
            "wire version sequence must be strictly monotonic across the baseline-loss "
            f"boundary (no jump/reset). Expected {[m, m + 1, m + 2, m + 3]}, got {seq}. "
            "A discontinuity means a send path drifted off the consumer counter."
        )

        await communicator.disconnect()


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_hotreload_frame_advances_consumer_counter_then_event_passes():
    """(c) HVR-then-event.

    A hot-reload patch frame (``hotreload=True``) is exempt from the client *version
    check* but it WRITES ``clientVdomVersion = data.version`` (02-response-handler.js:77),
    so it MUST stamp the consumer counter. After an HVR frame, the next normal event's
    version must still satisfy ``prev == emitted - 1`` — proving HIDDEN #1 (the hotreload
    send path) is on the consumer counter.
    """
    from channels.layers import get_channel_layer
    from django.test import override_settings

    # Reset the mutable template body for a clean run.
    _HVR_TEMPLATE_BODY[0] = "Count: {{ count }}"

    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], DEBUG=True):
        communicator, mount_frame = await _connect_and_mount(view_suffix="_WSHvrView", url="/hvr/")
        m = _frame_version(mount_frame)

        # Normal event first to advance the counter to m+1.
        await communicator.send_json_to({"type": "event", "event": "bump", "params": {}, "ref": 1})
        ev1 = await _receive_until(communicator, "patch")
        v1 = _frame_version(ev1)
        assert v1 == m + 1

        # Drive the REAL hotreload handler via the channel-layer group the
        # consumer joins on connect ("djust_hotreload", websocket.py:1478).
        # Change the template body so the re-render produces an actual patch
        # (identical content yields a 'reload' frame, not a patch). The
        # hotreload patch frame must stamp the consumer counter (HIDDEN #1) —
        # it WRITES clientVdomVersion even though it's exempt from the check.
        _HVR_TEMPLATE_BODY[0] = "Counter is now: {{ count }}"
        channel_layer = get_channel_layer()
        await channel_layer.group_send("djust_hotreload", {"type": "hotreload", "file": "x.html"})

        hvr = await _receive_until(communicator, "patch")
        assert hvr.get("type") == "patch" and hvr.get("hotreload") is True, (
            f"channel-layer hotreload must emit a hotreload patch frame; got {hvr!r}"
        )
        vh = _frame_version(hvr)
        assert vh == v1 + 1, (
            f"hotreload patch must stamp the consumer counter (HIDDEN #1, #1788): "
            f"expected {v1 + 1}, got {vh!r}. The hotreload send path WRITES "
            "clientVdomVersion so it must use self._next_version()."
        )

        # Next normal event must chain off the consumer counter (proves the
        # hotreload frame did NOT drift the wire version).
        await communicator.send_json_to({"type": "event", "event": "bump", "params": {}, "ref": 2})
        ev2 = await _receive_until(communicator, "patch")
        v2 = _frame_version(ev2)
        assert v2 == vh + 1, (
            f"event after hotreload must chain off the consumer counter: "
            f"prev={vh}, emitted={v2}. If the hotreload frame stamped a different "
            "counter, this fails — the recovery storm HIDDEN #1 guards against."
        )

        await communicator.disconnect()


# ---------------------------------------------------------------------------
# Source-pin / call-site count tests (#1125 / #1448): pin that every
# client-checked send path uses the consumer counter and pin the call-site
# count so a future new send path that forgets the helper trips the test.
# ---------------------------------------------------------------------------


def test_next_version_helper_exists_and_consumer_init():
    """The consumer must define ``_next_version`` and init ``_last_sent_version = 0``."""
    import djust.websocket as ws_mod

    assert hasattr(ws_mod.LiveViewConsumer, "_next_version"), (
        "LiveViewConsumer must define _next_version() — the single source of truth "
        "for the outbound VDOM wire version (#1788)."
    )
    init_src = inspect.getsource(ws_mod.LiveViewConsumer.__init__)
    assert "_last_sent_version" in init_src, (
        "LiveViewConsumer.__init__ must initialize self._last_sent_version (#1788)."
    )


def test_next_version_is_monotonic():
    """``_next_version`` must return strictly increasing integers starting at 1."""
    import djust.websocket as ws_mod

    class _Probe(ws_mod.LiveViewConsumer):
        def __init__(self):  # bypass Channels consumer __init__ machinery
            self._last_sent_version = 0

    p = _Probe()
    seq = [p._next_version() for _ in range(5)]
    assert seq == [1, 2, 3, 4, 5], f"_next_version must be monotonic from 1, got {seq}"


def test_every_client_checked_send_path_uses_next_version():
    """Source-pin: every ``_send_update(...)`` call that stamps a client-checked frame
    must route through the consumer counter — RENDER-SEND paths via
    ``self._next_version_armed(html)`` (#1817) and the NON-render baseline paths via
    bare ``self._next_version()`` — and the two HIDDEN participants (hotreload via the
    armed helper, streaming) must route through the consumer counter.

    Pins the migrated call-site count so a future send path that forgets the helper
    trips this test (#1125 / #1448). OUT-OF-SCOPE paths are explicitly excluded:
    child_update / sticky_update (per-child Map) and mount_batch per-target object are
    NOT counted.

    #1817: the bare ``_next_version()`` is reserved for NON-render frames (the mount
    baseline) plus the one path deliberately left unarmed pending follow-up (the actor
    event path — its ``result['html']`` shape is not the pre-strip render the recovery
    path expects). The shared helper ``_next_version_armed(html)`` (which internally
    calls ``_next_version()`` once) is the canonical primitive for render-send paths.
    """
    import re

    import djust.streaming as streaming_mod
    import djust.websocket as ws_mod

    ws_src = inspect.getsource(ws_mod)

    # --- RENDER-SEND paths: must route through _next_version_armed(html) (#1817) ---
    # Both the inline kwarg form ``version=self._next_version_armed(html)`` and the
    # assignment form ``X = self._next_version_armed(html)`` (where a local is reused
    # below — e.g. wire_version for telemetry / a separate strip/extract).
    armed_inline = len(re.findall(r"version=self\._next_version_armed\(", ws_src))
    armed_assign = len(re.findall(r"\b[a-z_]+ = self\._next_version_armed\(", ws_src))
    armed_invocations = armed_inline + armed_assign

    # Render-send sites routed through the armed helper (verified at #1817;
    # event sites removed at #1907 THE FLIP — see below):
    #   INLINE (version=self._next_version_armed(html)), 10:
    #     _run_async_work error arms: 2 (patch + html fallback)
    #     deferred-activity render: 2 (patch + html fallback)
    #     handle_hot_reload (HIDDEN #1): 1
    #     handle_time_travel_jump: 1
    #     handle_time_travel_component_jump: 1
    #     handle_forward_replay: 1
    #     db_notify: 1
    #     _run_tick: 1
    #   ASSIGNMENT (X = self._next_version_armed(html)), 3:
    #     _run_async_work success arms: 2
    #     server_push: 1 (wire_version)
    # Total armed invocations = 13.
    #
    # #1907 THE FLIP: the 2 ``handle_event`` ASSIGN sites (the event patch +
    # html_update fallback ``wire_version = self._next_version_armed(html)``) were
    # DELETED with the bespoke ``_handle_event_inner``. Event render-send recovery
    # arming now flows through ``WSConsumerTransport.next_client_version`` (runtime.py)
    # → ``consumer._next_version_armed(html)`` — the SAME helper, called from the
    # runtime render path rather than the consumer. This ws_src grep counts only the
    # consumer-file sites, so the event arming is correctly no longer here; the #1788
    # wire-version + recovery arming on the WS event path is end-to-end pinned by
    # ``test_recovery_version_staleness_1817`` + ``test_ws_send_version_1788``'s
    # WebsocketCommunicator integration cases (which DID stay green across the flip).
    EXPECTED_ARMED_INVOCATIONS = 13
    assert armed_invocations == EXPECTED_ARMED_INVOCATIONS, (
        f"expected {EXPECTED_ARMED_INVOCATIONS} self._next_version_armed() invocations "
        f"across RENDER-SEND paths; found {armed_invocations} "
        f"(inline={armed_inline}, assign={armed_assign}). Every render-send path must "
        "arm recovery via self._next_version_armed(html) so _recovery_version stays "
        "current (#1817). Update this count ONLY if you intentionally added/removed a "
        "render-send path."
    )

    # --- NON-render / pending paths: bare self._next_version() ---
    # The bare form is now reserved for: the helper body's single delegated call, the
    # mount baseline (non-render), and the actor event path (left unarmed, #1817
    # follow-up). Count send-site bare invocations EXCLUDING the helper-internal one.
    bare_inline = len(re.findall(r"version=self\._next_version\(\)", ws_src))
    bare_assign = len(re.findall(r"\b[a-z_]+ = self\._next_version\(\)(?!_armed)", ws_src))
    # The helper body contains one ``version = self._next_version()`` delegation;
    # exclude it so this counts only true send-site baseline allocations.
    helper_src = inspect.getsource(ws_mod.LiveViewConsumer._next_version_armed)
    helper_internal = len(re.findall(r"\b[a-z_]+ = self\._next_version\(\)", helper_src))
    bare_send_sites = bare_inline + bare_assign - helper_internal

    # Bare send-site invocations (verified at #1817; actor site moved at #1907;
    # mount baseline moved at #1919):
    #   (none remain in websocket.py).
    #
    # #1907 THE FLIP: the actor event path's INLINE ``version=self._next_version()``
    # moved to ``WSConsumerTransport.dispatch_actor_event`` (runtime.py) in Phase
    # 2.3a. #1919 THE MOUNT FLIP: the ``handle_mount`` baseline
    # ``version = self._next_version()`` moved to
    # ``WSConsumerTransport.next_mount_version`` (runtime.py, Finding C) — it calls
    # ``consumer._next_version()`` (still the consumer counter, still the NO-ARM
    # mount baseline), but the call site is now in runtime.py, so this consumer-file
    # grep no longer counts it. No bare send-site remains in websocket.py.
    EXPECTED_BARE_SEND_SITES = 0
    assert bare_send_sites == EXPECTED_BARE_SEND_SITES, (
        f"expected {EXPECTED_BARE_SEND_SITES} bare self._next_version() send-site "
        f"invocations in websocket.py (mount baseline moved to the next_mount_version "
        f"runtime hook at #1919); found {bare_send_sites} "
        f"(inline={bare_inline}, assign={bare_assign}, helper_internal={helper_internal}). "
        "A render-send path on the BARE helper is the #1817 drift — route it through "
        "self._next_version_armed(html). Update this count ONLY if you intentionally "
        "added/removed a non-render baseline path."
    )

    # The armed helper must exist and delegate to _next_version + _arm_recovery (#1817).
    assert hasattr(ws_mod.LiveViewConsumer, "_next_version_armed"), (
        "LiveViewConsumer must define _next_version_armed(html) — the canonical "
        "render-send primitive that advances the wire version AND arms recovery (#1817)."
    )
    assert "self._next_version()" in helper_src and "self._arm_recovery(" in helper_src, (
        "_next_version_armed must delegate to _next_version() AND _arm_recovery(html) "
        "so the version allocation and recovery baseline can never drift (#1817)."
    )

    # The mount frame must stamp the consumer counter (covers actor mount too).
    # Post-#1919 (THE MOUNT FLIP) the bespoke handle_mount baseline stamp moved to
    # the ``WSConsumerTransport.next_mount_version`` hook (runtime.py, Finding C),
    # which returns ``self._consumer._next_version()`` — the SAME monotonic counter,
    # NO-ARM (mount has no prior frame to recover to). Pin it at its converged home.
    import djust.runtime as rt_mod

    mount_ver_src = inspect.getsource(rt_mod.WSConsumerTransport.next_mount_version)
    assert "self._consumer._next_version()" in mount_ver_src, (
        "WSConsumerTransport.next_mount_version must stamp the mount frame version via "
        "the consumer counter (consumer._next_version()) so the client baseline = 1 and "
        "the actor mount path does not trust result['version']."
    )

    # handle_request_html must send the consumer-owned recovery version, NOT a fresh
    # Rust version (the client sets clientVdomVersion = data.version directly on
    # html_recovery — 03-websocket.js:727).
    req_html_src = inspect.getsource(ws_mod.LiveViewConsumer.handle_request_html)
    assert "_recovery_version" in req_html_src, (
        "handle_request_html must send self._recovery_version (the consumer version of "
        "the frame being replaced), not a fresh Rust version (#1788)."
    )

    # HIDDEN #2 — streaming push_state must route through the consumer counter.
    stream_src = inspect.getsource(streaming_mod.StreamingMixin.push_state)
    assert stream_src.count("self._ws_consumer._next_version()") >= 2, (
        "StreamingMixin.push_state bypasses _send_update and sends patch + html_update "
        "directly; both MUST stamp self._ws_consumer._next_version() (HIDDEN #2, #1788). "
        f"Found {stream_src.count('self._ws_consumer._next_version()')} of 2."
    )


def test_arm_recovery_stores_consumer_version():
    """``_arm_recovery`` must capture the consumer's last-sent version (``_last_sent_version``)
    rather than rely on a passed Rust version — recovery sets
    ``clientVdomVersion = data.version`` directly client-side, so the recovery version must
    equal the consumer version of the frame it replaces (#1788).
    """
    import djust.websocket as ws_mod

    arm_src = inspect.getsource(ws_mod.LiveViewConsumer._arm_recovery)
    assert "_last_sent_version" in arm_src, (
        "_arm_recovery must store self._recovery_version = self._last_sent_version so the "
        "html_recovery frame carries the consumer version of the frame it replaces (#1788)."
    )
