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


async def _receive_until(communicator, wanted_type, *, tries=8, timeout=3):
    """Drain frames until one whose ``type`` == ``wanted_type`` (or return last seen)."""
    last = None
    for _ in range(tries):
        last = await communicator.receive_json_from(timeout=timeout)
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
    await communicator.receive_json_from(timeout=2)  # drain connect frame

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
        assert v_mount == 1, f"fresh mount baseline must be 1, got {v_mount!r}"

        # (2) Arming event: a normal diff. Records time-travel snapshot[0]
        # (state_before={count:0}, state_after={count:1}) AND arms recovery at
        # this version (the canonical handle_event armed path).
        await communicator.send_json_to({"type": "event", "event": "bump", "params": {}, "ref": 1})
        ev1 = await _receive_until(communicator, "patch")
        assert ev1.get("type") == "patch", f"bump should patch, got {ev1!r}"
        v_arm = _frame_version(ev1)
        assert v_arm == v_mount + 1, f"arming event must be {v_mount + 1}, got {v_arm!r}"

        # (3) DRIFT path: jump back to snapshot[0]'s state_before (count → 0).
        # This re-renders (count 1 → 0 produces a patch) and advances the wire
        # version; the client applies it (clientVdomVersion = jump version).
        await communicator.send_json_to({"type": "time_travel_jump", "index": 0, "which": "before"})
        # The jump emits a patch/html_update render frame first, then a
        # time_travel_state frame. Capture the render frame's version.
        jump_render = None
        for _ in range(8):
            frame = await communicator.receive_json_from(timeout=3)
            if frame.get("type") in ("patch", "html_update"):
                jump_render = frame
                break
            if frame.get("type") == "error":
                pytest.fail(f"time_travel_jump errored: {frame!r}")
        assert jump_render is not None, "time_travel_jump must emit a render frame"
        v_jump = _frame_version(jump_render)
        assert v_jump == v_arm + 1, (
            f"jump render must advance the wire version to {v_arm + 1} "
            f"(client now at this version); got {v_jump!r}"
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
