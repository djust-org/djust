"""Regression tests for #1817 — ``_recovery_version`` staleness across render-send paths.

Root cause
----------
After #1816 (#1788) every client-checked outbound WS frame stamps the
consumer-owned ``_last_sent_version`` via ``_next_version()``
(``websocket.py``). ``_arm_recovery(html)`` captures
``_recovery_version = _last_sent_version`` at arm time, and
``handle_request_html`` sends an ``html_recovery`` frame stamped with that
``_recovery_version``. The client sets ``clientVdomVersion = data.version``
directly on ``html_recovery`` (``static/djust/src/03-websocket.js:727``).

The bug: several RENDER-SEND paths (the async-result error arms, the
deferred-activity render, the hotreload frame, the time-travel jumps, and the
tick / db_notify broadcasts) advanced ``_next_version()`` WITHOUT arming
recovery. So after such a frame the client's applied version was AHEAD of
``_recovery_version``. A later ``request_html`` then returned an
``html_recovery`` stamped with the STALE ``_recovery_version`` → the client
reset ``clientVdomVersion`` backwards → the next successful diff's
``data.version - 1`` no longer matched → an extra recovery round-trip.

The fix: route every render-send path through the shared
``_next_version_armed(html)`` helper, which advances the wire version AND arms
recovery in one step, so ``_recovery_version == _last_sent_version`` after each
applied frame. Recovery then always resets the client to the version it is
actually on.

Fidelity
--------
``test_time_travel_jump_recovery_version_is_current`` drives the REAL
``LiveViewConsumer`` through a real ``WebsocketCommunicator`` end-to-end:
mount → arming event → time-travel jump (a render-send drift path) →
``request_html``, and asserts the ``html_recovery`` frame carries the version
the client is CURRENTLY on (post-jump), not the stale pre-jump version. The
GATE-OFF evidence (reverting the arm on the jump path) is recorded in the PR.
"""

from __future__ import annotations

import asyncio

import pytest
from asgiref.sync import sync_to_async

from djust import LiveView
from djust.decorators import event_handler


class _TTRecoveryView(LiveView):
    """Time-travel-enabled view used to drive the jump render-send drift path.

    ``time_travel_enabled = True`` makes each ``@event_handler`` dispatch record
    a ``state_before`` / ``state_after`` snapshot, so a ``time_travel_jump`` to
    a past snapshot can restore ``count`` and re-render. The re-render is a
    render-send (the client applies it as new display state + writes
    ``clientVdomVersion``), so it MUST arm recovery (#1817).
    """

    time_travel_enabled = True

    template = (
        '<div dj-view="djust.tests.test_recovery_version_staleness_1817._TTRecoveryView" '
        'dj-id="0">Count: {{ count }}</div>'
    )

    def mount(self, request, **kwargs):
        self.count = 0

    @event_handler()
    def bump(self, **kwargs):
        self.count += 1


# Every frame this module receives, in order, for diagnostics only (#2154).
#
# This test failed twice during the v1.1.0 drain under `-n auto` and could not
# be reproduced afterwards in 23 consecutive runs, including at 2x CPU
# saturation. Both observed failures coincided with concurrent worktree agents,
# i.e. heavy I/O and database contention rather than CPU alone.
#
# Rather than guess at a fix for a cause that has not been pinned — a phantom
# fix is worse than none — this records what actually arrived so the NEXT
# occurrence is diagnosable instead of merely repeatable. It changes no
# behaviour and asserts nothing.
_FRAME_LOG: list = []

#: Frame types an `event` is *supposed* to produce on this view. Anything else
#: arriving in the mount -> event window is unsolicited (#2215).
_EVENT_RESPONSE_TYPES = {"patch", "time_travel_event"}


def _log_frames() -> str:
    if not _FRAME_LOG:
        return "  (no frames were received at all)"
    return "\n".join(
        f"  [{i}] type={f.get('type')!r} version={f.get('version')!r}"
        for i, f in enumerate(_FRAME_LOG)
    )


async def _recv(communicator, timeout):
    """Receive one frame, recording it, and say what preceded a timeout."""
    try:
        frame = await communicator.receive_json_from(timeout=timeout)
    except Exception as exc:  # noqa: BLE001 - re-raised with context below
        raise AssertionError(
            f"no frame arrived within {timeout}s ({type(exc).__name__}). "
            f"Frames received before this point:\n{_log_frames()}\n"
            "If this is the #2154 flake, that sequence is the evidence needed "
            "to tell a slow round-trip from a missing frame."
        ) from exc
    _FRAME_LOG.append(frame)
    return frame


@pytest.fixture(autouse=True)
def _reset_frame_log():
    _FRAME_LOG.clear()
    yield
    _FRAME_LOG.clear()


async def _receive_until(communicator, wanted_type, *, tries=8, timeout=3):
    """Drain frames until one whose ``type`` == ``wanted_type`` (or return last seen)."""
    last = None
    for _ in range(tries):
        last = await _recv(communicator, timeout)
        if last.get("type") == wanted_type:
            return last
    return last


async def _connect_and_mount(view_suffix, url):
    """WS harness lifted from test_ws_send_version_1788.py. Returns (communicator, mount)."""
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
    await _recv(communicator, 2)  # drain connect frame

    await communicator.send_json_to(
        {"type": "mount", "view": f"{__name__}.{view_suffix}", "url": url}
    )
    mount_frame = await _receive_until(communicator, "mount")
    assert mount_frame.get("type") == "mount", f"expected mount, got {mount_frame!r}"
    return communicator, mount_frame


def _frame_version(frame):
    assert frame is not None
    return frame.get("version")


# ---------------------------------------------------------------------------
# (1) End-to-end real-WebsocketCommunicator reproduction (load-bearing, #1210).
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_time_travel_jump_recovery_version_is_current():
    """mount → bump → time_travel_jump → request_html.

    The ``time_travel_jump`` is a render-send DRIFT path: it advances the wire
    version and the client applies it (clientVdomVersion = jump version). BEFORE
    #1817 the jump did NOT arm recovery, so a subsequent ``request_html``
    returned an ``html_recovery`` stamped with the STALE pre-jump version. AFTER
    the fix the ``html_recovery`` version equals the post-jump version the
    client is currently on.

    Gate-off check: revert the arm on the jump path (use bare ``_next_version()``
    in ``handle_time_travel_jump``) and the final assertion fails with the stale
    pre-jump version — proving this test exercises the fix.
    """
    from django.test import override_settings

    # DEBUG=True is required for time-travel handlers (dev-only feature).
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], DEBUG=True):
        communicator, mount_frame = await _connect_and_mount(
            view_suffix="_TTRecoveryView", url="/tt/"
        )
        v_mount = _frame_version(mount_frame)
        assert v_mount == 1, (
            f"fresh mount baseline must be 1, got {v_mount!r}. A value other "
            f"than 1 means this was not a fresh mount — session or consumer "
            f"state leaked from an earlier test (see #2154). Frames so far:\n"
            f"{_log_frames()}"
        )

        # (2) Arming event: a normal diff. Records time-travel snapshot[0]
        # (state_before={count:0}, state_after={count:1}) AND arms recovery at
        # this version (the canonical handle_event armed path).
        await communicator.send_json_to({"type": "event", "event": "bump", "params": {}, "ref": 1})
        ev1 = await _receive_until(communicator, "patch")
        assert ev1.get("type") == "patch", f"bump should patch, got {ev1!r}"
        v_arm = _frame_version(ev1)
        # MONOTONIC, not adjacent (#2215). This used to assert
        # `v_arm == v_mount + 1`, and that is what the flake failed on: an
        # extra `_next_version()` bump landed between the mount and this frame,
        # so it read 3 where 2 was expected.
        #
        # The exact integer was never what this test guards. Gating off the
        # #1817 fix shows the LAST assertion — `v_recovery == v_jump` — catches
        # the regression entirely on its own; these two only assert that the
        # counter is a counter. So they now assert exactly that, and the
        # "did something else bump it" question moved to
        # `test_an_idle_connection_receives_no_unsolicited_frames` below, which
        # can NAME the intruder instead of reporting an off-by-N.
        assert v_arm > v_mount, (
            f"the arming event must ADVANCE the wire version past the mount "
            f"baseline ({v_mount}); got {v_arm!r}. Frames so far:\n{_log_frames()}"
        )

        # (3) DRIFT path: jump back to snapshot[0]'s state_before (count → 0).
        # This re-renders (count 1 → 0 produces a patch) and advances the wire
        # version; the client applies it (clientVdomVersion = jump version).
        await communicator.send_json_to({"type": "time_travel_jump", "index": 0, "which": "before"})
        # The jump emits a patch/html_update render frame first, then a
        # time_travel_state frame. Capture the render frame's version.
        jump_render = None
        for _ in range(8):
            frame = await _recv(communicator, 3)
            if frame.get("type") in ("patch", "html_update"):
                jump_render = frame
                break
            if frame.get("type") == "error":
                pytest.fail(f"time_travel_jump errored: {frame!r}")
        assert jump_render is not None, "time_travel_jump must emit a render frame"
        v_jump = _frame_version(jump_render)
        # Monotonic for the same reason as above (#2215): what matters is that
        # the jump ADVANCES the version — the client writes
        # `clientVdomVersion = data.version` from this frame — not that it
        # advances by exactly one.
        assert v_jump > v_arm, (
            f"the jump render must advance the wire version past the arming "
            f"version ({v_arm}); got {v_jump!r}. Frames so far:\n{_log_frames()}"
        )

        # (4) request_html → html_recovery. Its version MUST be the post-jump
        # version the client is currently on (v_jump), NOT the stale pre-jump
        # arming version (v_arm). BEFORE #1817 this was v_arm.
        await communicator.send_json_to({"type": "request_html"})
        recovery = await _receive_until(communicator, "html_recovery")
        assert recovery.get("type") == "html_recovery", (
            f"request_html must return html_recovery; got {recovery!r}"
        )
        v_recovery = _frame_version(recovery)
        assert v_recovery == v_jump, (
            f"html_recovery version must equal the client's CURRENT version "
            f"after the time-travel jump ({v_jump}); got {v_recovery!r}. A stale "
            f"value (the pre-jump arming version {v_arm}) is the #1817 drift: the "
            "client would reset backwards and the next diff would mismatch, "
            "forcing an extra recovery round-trip. The jump render-send path must "
            "arm recovery via self._next_version_armed(html)."
        )

        await communicator.disconnect()


# ---------------------------------------------------------------------------
# (2) Direct unit tests on the helper + per-path arm invariant.
# ---------------------------------------------------------------------------


class _ArmProbe:
    """Minimal probe exercising the consumer's version/recovery helpers directly.

    Bypasses Channels consumer machinery; only the version + recovery state
    are needed to assert the ``_next_version_armed`` contract.
    """

    def __init__(self):
        from djust.websocket import LiveViewConsumer

        self._last_sent_version = 0
        self._recovery_html = None
        self._recovery_version = 0
        # Bind the real implementations so the probe exercises production code.
        self._next_version = LiveViewConsumer._next_version.__get__(self)
        self._arm_recovery = LiveViewConsumer._arm_recovery.__get__(self)
        self._next_version_armed = LiveViewConsumer._next_version_armed.__get__(self)


def test_next_version_armed_advances_and_arms_together():
    """``_next_version_armed`` must advance the wire version AND set
    ``_recovery_version == _last_sent_version`` AND store the passed html — in one
    call, so the two can never drift apart (#1817)."""
    p = _ArmProbe()

    v1 = p._next_version_armed("<html>v1</html>")
    assert v1 == 1, f"first armed version must be 1, got {v1!r}"
    assert p._last_sent_version == 1
    assert p._recovery_version == 1, (
        "armed call must leave _recovery_version == _last_sent_version (#1817)"
    )
    assert p._recovery_html == "<html>v1</html>", "armed call must store the passed html"

    v2 = p._next_version_armed("<html>v2</html>")
    assert v2 == 2
    assert p._recovery_version == 2, "_recovery_version must track each armed frame"
    assert p._recovery_html == "<html>v2</html>"


def test_bare_next_version_does_not_arm_then_armed_resyncs():
    """A bare ``_next_version()`` (a non-render baseline / unarmed path) leaves
    ``_recovery_version`` BEHIND; a following ``_next_version_armed`` resyncs it.

    This is the #1817 drift-and-recovery shape in miniature: bare advance creates
    the staleness, the armed helper closes it.
    """
    p = _ArmProbe()

    # Arm once at v1.
    p._next_version_armed("<html>v1</html>")
    assert p._recovery_version == 1

    # A bare advance (the pre-#1817 drift) leaves _recovery_version stale.
    bare = p._next_version()
    assert bare == 2
    assert p._last_sent_version == 2
    assert p._recovery_version == 1, (
        "bare _next_version() must NOT arm — this is exactly the staleness that "
        "made html_recovery stamp an old version (#1817)"
    )

    # The armed helper resyncs the recovery baseline to the current version.
    armed = p._next_version_armed("<html>v3</html>")
    assert armed == 3
    assert p._recovery_version == 3, (
        "_next_version_armed must bring _recovery_version current with _last_sent_version (#1817)"
    )


# ---------------------------------------------------------------------------
# The relocated stray-bump signal (#2215).
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_an_idle_connection_receives_no_unsolicited_frames():
    """Nothing may push a frame at a connection that did not ask for one.

    This is where the "did something else bump the version counter" question
    lives now. It used to be answered accidentally, by
    ``test_time_travel_jump_recovery_version_is_current`` asserting
    ``v_arm == v_mount + 1`` — so a stray broadcast surfaced as an off-by-N in
    a test about something else, with no indication of what had arrived.

    Here the failure message carries the intruding frame. That is the whole
    point: #2215 has been seen twice and reproduced never, and the reason it
    stayed unreproducible is that the only evidence it ever produced was
    "expected 2, got 3".

    Every unsolicited frame is a real defect regardless of this flake — a
    broadcast racing an event skews the wire version and costs the client a
    recovery round-trip (#1788, #1817). So this asserts a property worth
    holding on its own, not a workaround.

    Two limits, both real. It cannot *prove* absence: a producer that fires
    once an hour will not appear in a 1.5-second window. And it can only name
    a producer that SENDS something — the actual #2215 producer, found by the
    review of this PR, bumps the version without emitting a frame at all (see
    ``test_a_suppressed_hotreload_broadcast_consumes_no_wire_version``), so for
    that one the version assertion is the load-bearing half and the frame
    assertions would have stayed green forever.
    """
    from django.test import override_settings

    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], DEBUG=True):
        communicator, mount_frame = await _connect_and_mount(
            view_suffix="_TTRecoveryView", url="/tt/"
        )
        baseline = _frame_version(mount_frame)

        # `receive_nothing` rather than a timed-out `receive_json_from`, for a
        # harness reason worth recording because the first draft got it exactly
        # backwards in a comment that then shipped:
        #
        #   receive_json_from(timeout=N)  -> raises TimeoutError (an Exception)
        #                                    AND cancels the communicator's
        #                                    application task, so every LATER
        #                                    send_json_to raises CancelledError
        #                                    (a BaseException) from inside
        #                                    send_input.
        #   receive_nothing(timeout=N)    -> returns a bool, touches nothing.
        #
        # Confirmed empirically on this harness rather than reasoned about: the
        # receive raised `TimeoutError`, the later send raised
        # `asyncio.CancelledError`. The draft attributed CancelledError to the
        # RECEIVE, and so imposed an ordering constraint ("the idle wait must
        # be last") that does not actually exist. `receive_nothing` removes the
        # constraint outright.
        window_start = len(_FRAME_LOG)
        await communicator.send_json_to({"type": "event", "event": "bump", "params": {}, "ref": 1})
        after_event = _frame_version(await _receive_until(communicator, "patch"))

        # The mount -> event round-trip is the exact #2215 window, and the idle
        # wait below cannot see into it: `_receive_until` DRAINS whatever
        # arrives while hunting for the patch, so a stray at t=0.01s is
        # swallowed silently. `_FRAME_LOG` recorded it either way, so check the
        # window directly — the patch should be the only thing that arrived.
        #
        # "Unsolicited" means "not one of the frames this event is supposed to
        # produce" — an allowlist, not a count. This view has time travel on,
        # so every event legitimately emits a `time_travel_event` alongside its
        # `patch`; a naive `len(...) == 1` flagged that as an intruder on the
        # first run. A `hotreload`, `reload`, or second `mount` in this window
        # is still caught, which is the point.
        strays = [
            f for f in _FRAME_LOG[window_start:] if f.get("type") not in _EVENT_RESPONSE_TYPES
        ]
        # Reachability (#1859): this is not decorative — the first run of it
        # went red on a real frame captured from the window and named it, which
        # is how the allowlist above got written. A silent producer still slips
        # past, by construction; that is what the version assertion is for.
        assert not strays, (
            f"{len(strays)} unsolicited frame(s) arrived during the mount -> "
            f"event round-trip, the exact #2215 window: {strays!r}\n"
            f"Frames:\n{_log_frames()}"
        )

        # Now sit idle. Anything that arrives was not asked for.
        idle_clean = await communicator.receive_nothing(timeout=1.5)
        stray = None if idle_clean else await communicator.receive_json_from(timeout=1)
        assert idle_clean, (
            f"an unsolicited frame arrived at an idle connection: {stray!r}\n"
            "That is a #2215 producer. Whatever sent this is what bumps the "
            "wire version mid-test and skews every later frame. Frames:\n"
            f"{_log_frames()}"
        )
        assert after_event == baseline + 1, (
            f"the wire version moved between the mount and the first event: "
            f"mount was {baseline}, the event read {after_event} (expected "
            f"{baseline + 1}). Something advanced _next_version() without this "
            f"socket asking for it — and note it need not have SENT anything: "
            f"a suppressed hot-reload broadcast bumped the version silently "
            f"until #2215. Frames:\n{_log_frames()}"
        )
        await communicator.disconnect()


# ---------------------------------------------------------------------------
# The #2215 producer, found by the Stage 11 review of PR #2237.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_a_suppressed_hotreload_broadcast_consumes_no_wire_version():
    """The reproducer two years of hunting never produced (#2215).

    `hotreload` calls::

        await self._send_update(
            patches=patches,
            version=self._next_version_armed(html),   # <- evaluated FIRST
            hotreload=True, ...
        )

    and `_send_update` then suppresses an empty-patch hot-reload broadcast
    (#763) and returns. Python evaluates arguments before the call, so the wire
    version was consumed — and recovery armed — for a frame that never left the
    socket. An unrelated file change re-renders to zero patches, which is the
    COMMON case.

    Why it stayed unreproducible: it is a **silent** bump. Nothing reaches the
    socket, so every hunt that looked for a stray FRAME found nothing and
    concluded the hot-reload path was innocent — including the first draft of
    this PR, which shipped that conclusion in its CHANGELOG. The only evidence
    it ever left was a version that jumped, which surfaced as
    ``expected 2, got 3`` in a test about something else.

    Two-arm, because "the version is 2" proves nothing without knowing what it
    would have been: mount, broadcast, event, and compare against the same
    sequence with no broadcast.
    """
    from channels.layers import get_channel_layer
    from django.test import override_settings

    async def _mount_broadcast_event(*, broadcast: bool) -> tuple:
        communicator, mount = await _connect_and_mount(view_suffix="_TTRecoveryView", url="/tt/")
        v_mount = _frame_version(mount)
        if broadcast:
            # An unrelated file: re-renders to zero patches, so `_send_update`
            # suppresses it. This is the shape the watcher emits in dev.
            await get_channel_layer().group_send(
                "djust_hotreload", {"type": "hotreload", "file": "unrelated/module.py"}
            )
            await asyncio.sleep(0.4)  # let the broadcast be handled
        await communicator.send_json_to({"type": "event", "event": "bump", "params": {}, "ref": 1})
        v_event = _frame_version(await _receive_until(communicator, "patch"))
        await communicator.disconnect()
        return v_mount, v_event

    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], DEBUG=True):
        control_mount, control_event = await _mount_broadcast_event(broadcast=False)
        treat_mount, treat_event = await _mount_broadcast_event(broadcast=True)

    assert control_event - control_mount == 1, (
        f"control arm is broken: without any broadcast the event must be the "
        f"very next version ({control_mount} -> {control_event})"
    )
    assert treat_event - treat_mount == control_event - control_mount, (
        f"a suppressed hot-reload broadcast consumed a wire version: with no "
        f"broadcast the event advanced by {control_event - control_mount} "
        f"({control_mount} -> {control_event}); with one it advanced by "
        f"{treat_event - treat_mount} ({treat_mount} -> {treat_event}).\n"
        "The version was allocated as an ARGUMENT to `_send_update`, which then "
        "returned early on the empty-patch guard (#763) — so the client never "
        "saw it, `clientVdomVersion` lags, and the next real diff pays a "
        "recovery round-trip. Frames:\n" + _log_frames()
    )
