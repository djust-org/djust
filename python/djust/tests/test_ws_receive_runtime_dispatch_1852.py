"""Regression tests for #1852 (T1-B) — WS ``receive()`` routes runtime-owned
verbs through the single ``ViewRuntime.dispatch_message`` chokepoint.

Goal of #1852: a future security/policy control added at
``runtime.py:ViewRuntime.dispatch_message`` (the chokepoint the SSE transport
already routes every inbound frame through) should auto-apply to the WebSocket
transport too. Before this change ``receive()`` called ``handle_url_change``,
which dispatched straight to ``dispatch_url_change`` — BYPASSING
``dispatch_message``. After this change ``receive()`` routes ``url_change``
through ``dispatch_message`` so the verb passes the chokepoint.

SCOPE (see the routing comment in ``websocket.receive``): ONLY ``url_change`` is
routed through the chokepoint today.

* ``mount`` stays on the WS ``handle_mount`` (sticky-child preservation, signed
  ``state_snapshot`` restore, actor channel-layer) — folded into runtime hooks
  by T1-A (#1853). The runtime ``dispatch_mount`` even refuses ``use_actors``.
* ``event`` stays on the WS ``handle_event`` (~16 WS-only behaviors the runtime
  ``dispatch_event`` lacks: event-``ref`` echo, actors, ``view_id``/
  ``component_id`` routing, ``dj_activity`` gating, time-travel, render-lock,
  push origin-channel tagging, SQL observability, waiter notification,
  session/sticky-child state-save, identity-snapshot push-only auto-skip,
  patch compression, ``_force_full_html``, ``embedded_update``).

These tests are end-to-end against ``LiveViewConsumer.as_asgi()`` via
``WebsocketCommunicator`` (per the project's reproduction-fidelity rule — the
harness must exercise the real WS path, not a proxy). The harness shape is
lifted from ``test_ws_send_version_1788.py``.

Test classes:
  * ``TestUrlChangeRoutedThroughChokepoint`` — (gate-off) ``dispatch_message``
    is actually called for ``url_change``, and a source-pin that ``receive()``
    routes via the chokepoint helper, not ``dispatch_url_change`` directly.
  * ``TestUrlChangeEndToEndPreserved`` — a ``url_change`` frame still produces
    the correct outbound frame end-to-end through the WS transport.
  * ``TestWSOnlyFramesPreserved`` — binary upload (0x01/0x02/0x03) and
    ``request_html`` (a WS-only JSON verb) still work unchanged.
  * ``TestRuntimeOwnedVerbsContract`` — the explicit ``RUNTIME_OWNED_VERBS``
    set pins exactly which verbs go through the chokepoint.
"""

from __future__ import annotations

import inspect
import uuid

import pytest
from asgiref.sync import sync_to_async

from djust import LiveView
from djust.decorators import event_handler


# ------------------------------------------------------------------ #
# View under test — count drives a diffable patch; handle_params lets a
# url_change mutate state so the re-render is observable.
# ------------------------------------------------------------------ #


class _ReceiveView(LiveView):
    template = (
        '<div dj-view="djust.tests.test_ws_receive_runtime_dispatch_1852._ReceiveView" '
        'dj-id="0">Count: {{ count }} Page: {{ page }}</div>'
    )

    def mount(self, request, **kwargs):
        self.count = 0
        self.page = "1"

    def handle_params(self, params, uri):
        # url_change carries params; reflect ``page`` into state so the
        # subsequent re-render produces an observable diff.
        if "page" in params:
            self.page = str(params["page"])

    @event_handler()
    def bump(self, **kwargs):
        self.count += 1


_VIEW_PATH = f"{__name__}._ReceiveView"


async def _receive_until(communicator, wanted_type, *, tries=6, timeout=3):
    """Drain frames until one whose ``type`` == ``wanted_type`` (or last seen)."""
    last = None
    for _ in range(tries):
        last = await communicator.receive_json_from(timeout=timeout)
        if last.get("type") == wanted_type:
            return last
    return last


async def _connect_and_mount(url="/page/"):
    """Connect a real WebsocketCommunicator and mount ``_ReceiveView``.

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

    await communicator.send_json_to({"type": "mount", "view": _VIEW_PATH, "url": url})
    mount_frame = await _receive_until(communicator, "mount")
    assert mount_frame.get("type") == "mount", f"expected mount, got {mount_frame!r}"
    return communicator, mount_frame


# ------------------------------------------------------------------ #
# Gate-off: dispatch_message IS the path url_change takes (load-bearing).
# ------------------------------------------------------------------ #


class TestUrlChangeRoutedThroughChokepoint:
    @pytest.mark.django_db
    @pytest.mark.asyncio
    async def test_url_change_calls_dispatch_message(self, monkeypatch):
        """End-to-end: a ``url_change`` frame on the live WS path invokes
        ``ViewRuntime.dispatch_message`` exactly once with the frame.

        GATE-OFF (#1468): if ``receive()`` is reverted to call
        ``handle_url_change`` (which dispatches straight to
        ``dispatch_url_change``) the spy is NEVER called and this test FAILS —
        proving the routing through the chokepoint is load-bearing, not an
        already-true tautology. ``dispatch_event``/``dispatch_mount`` are NOT
        on the WS path, so this can only be satisfied by url_change routing.
        """
        from django.test import override_settings

        from djust.runtime import ViewRuntime

        calls = []
        real_dispatch = ViewRuntime.dispatch_message

        async def _spy(self, data):
            calls.append(data)
            return await real_dispatch(self, data)

        monkeypatch.setattr(ViewRuntime, "dispatch_message", _spy)

        with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
            communicator, _ = await _connect_and_mount()

            await communicator.send_json_to(
                {"type": "url_change", "params": {"page": "2"}, "uri": "/page/?page=2"}
            )
            # Drain the response (patch or html_update) so dispatch completed.
            await communicator.receive_json_from(timeout=3)

            url_change_calls = [c for c in calls if c.get("type") == "url_change"]
            assert len(url_change_calls) == 1, (
                "url_change must flow through ViewRuntime.dispatch_message exactly "
                f"once (the #1852 chokepoint); saw calls={calls!r}. If this is 0, "
                "receive() bypassed dispatch_message (the bug #1852 fixes)."
            )
            assert url_change_calls[0]["uri"] == "/page/?page=2"

            await communicator.disconnect()

    def test_receive_routes_url_change_via_chokepoint_helper(self):
        """Source-pin: the ``url_change`` arm of ``receive()`` calls
        ``_dispatch_runtime_owned`` (which calls ``dispatch_message``) and does
        NOT call ``handle_url_change`` (the old direct-to-dispatch_url_change
        path). Catches an accidental revert of the routing.
        """
        from djust.websocket import LiveViewConsumer

        src = inspect.getsource(LiveViewConsumer.receive)
        assert 'msg_type == "url_change"' in src, "receive() must still handle url_change"
        # The url_change arm must dispatch through the chokepoint helper.
        assert "_dispatch_runtime_owned(data)" in src, (
            "receive() must route url_change through _dispatch_runtime_owned "
            "(→ dispatch_message), the #1852 chokepoint."
        )
        # And must NOT call the old direct shim from receive().
        assert "self.handle_url_change(" not in src, (
            "receive() must NOT call handle_url_change directly — that bypassed "
            "the dispatch_message chokepoint (#1852)."
        )

    def test_dispatch_runtime_owned_calls_dispatch_message_not_dispatch_url_change(self):
        """Source-pin: the seam helper goes through ``dispatch_message``."""
        from djust.websocket import LiveViewConsumer

        src = inspect.getsource(LiveViewConsumer._dispatch_runtime_owned)
        assert "dispatch_message(data)" in src, (
            "_dispatch_runtime_owned must call runtime.dispatch_message(data) — "
            "the single chokepoint (#1852)."
        )
        assert "dispatch_url_change" not in src, (
            "_dispatch_runtime_owned must NOT call dispatch_url_change directly "
            "— that skips the chokepoint."
        )


# ------------------------------------------------------------------ #
# url_change end-to-end behavior is preserved.
# ------------------------------------------------------------------ #


class TestUrlChangeEndToEndPreserved:
    @pytest.mark.django_db
    @pytest.mark.asyncio
    async def test_url_change_produces_update_frame(self):
        """A ``url_change`` frame still reaches handle_params + re-render and
        emits a client-applicable update frame (patch or html_update) through
        the WS transport — behavior unchanged by the routing seam.
        """
        from django.test import override_settings

        with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
            communicator, mount_frame = await _connect_and_mount()
            assert "Page: 1" in mount_frame.get("html", ""), mount_frame

            await communicator.send_json_to(
                {"type": "url_change", "params": {"page": "7"}, "uri": "/page/?page=7"}
            )
            frame = await communicator.receive_json_from(timeout=3)
            assert frame.get("type") in ("patch", "html_update"), (
                f"url_change must emit a patch/html_update update frame, got {frame!r}"
            )
            # The state mutation from handle_params must be reflected: for an
            # html_update the new HTML is on the wire; for a patch the new value
            # appears in the patch payload. Either way "7" must show up.
            blob = str(frame)
            assert "7" in blob, (
                f"url_change handle_params(page=7) must be reflected in the "
                f"update frame; got {frame!r}"
            )

            await communicator.disconnect()

    @pytest.mark.django_db
    @pytest.mark.asyncio
    async def test_url_change_not_mounted_sends_error(self):
        """A ``url_change`` before any mount still produces a 'View not mounted'
        error frame (the runtime inner method's guard), routed through the
        chokepoint — same observable shape as the old shim's guard.
        """
        from django.test import override_settings

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

        with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
            communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
            communicator.scope["session"] = _ScopeSession(session_key)
            connected, _ = await communicator.connect()
            assert connected
            await communicator.receive_json_from(timeout=2)  # connect frame

            # url_change with NO prior mount.
            await communicator.send_json_to(
                {"type": "url_change", "params": {"page": "2"}, "uri": "/page/?page=2"}
            )
            frame = await communicator.receive_json_from(timeout=3)
            assert frame.get("type") == "error", f"expected error, got {frame!r}"
            assert "not mounted" in str(frame.get("error", "")).lower(), frame

            await communicator.disconnect()


# ------------------------------------------------------------------ #
# WS-only frames (NOT runtime-owned) still work unchanged.
# ------------------------------------------------------------------ #


def _cancel_frame() -> bytes:
    """Minimal valid binary upload frame (0x03 cancel = 17 bytes)."""
    return bytes([0x03]) + uuid.uuid4().bytes


class TestWSOnlyFramesPreserved:
    @pytest.mark.django_db
    @pytest.mark.asyncio
    async def test_binary_upload_frame_still_dispatched(self):
        """A binary upload frame (0x03, len>=17) is still handled by the WS-only
        ``_handle_upload_frame`` path (it is decoded/dispatched BEFORE the
        msg_type switch). The seam change must not touch this.
        """
        from django.test import override_settings

        with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
            communicator, _ = await _connect_and_mount()

            # Send a cancel frame for an unknown upload — the upload handler
            # accepts it (no registered upload to cancel) without erroring out
            # the connection. The key assertion is that the socket stays open
            # and the binary frame did NOT fall through to "Unknown message
            # type" (which only the JSON switch can emit).
            await communicator.send_to(bytes_data=_cancel_frame())

            # Prove the connection is still alive + the JSON switch still works
            # by driving a normal event afterward.
            await communicator.send_json_to(
                {"type": "event", "event": "bump", "params": {}, "ref": 1}
            )
            frame = await _receive_until(communicator, "patch")
            assert frame.get("type") in ("patch", "html_update"), (
                f"binary upload frame must not break the connection; the "
                f"following event must still respond, got {frame!r}"
            )

            await communicator.disconnect()

    @pytest.mark.django_db
    @pytest.mark.asyncio
    async def test_request_html_ws_only_frame_still_handled(self):
        """``request_html`` is a WS-only JSON verb (no runtime equivalent). It
        must still route to ``handle_request_html`` and return an
        ``html_recovery`` frame — proving the WS-only set is untouched.

        ``_recovery_html`` is armed on a render-send (``_send_update``), not on
        mount, so drive one ``bump`` event first to arm recovery; then the
        WS-only ``request_html`` handler replays it as ``html_recovery``.
        """
        from django.test import override_settings

        with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
            communicator, _ = await _connect_and_mount()

            # Arm recovery via a normal event render.
            await communicator.send_json_to(
                {"type": "event", "event": "bump", "params": {}, "ref": 1}
            )
            await _receive_until(communicator, "patch")

            await communicator.send_json_to({"type": "request_html"})
            frame = await _receive_until(communicator, "html_recovery")
            assert frame.get("type") == "html_recovery", (
                f"request_html (WS-only) must return html_recovery, got {frame!r}"
            )
            assert "html" in frame, frame

            await communicator.disconnect()

    @pytest.mark.django_db
    @pytest.mark.asyncio
    async def test_event_still_handled_by_ws_handle_event(self, monkeypatch):
        """``event`` is a runtime-owned verb that is DELIBERATELY NOT routed
        through the chokepoint in T1-B (the WS handle_event carries ~16 WS-only
        behaviors). Pin that an event does NOT go through dispatch_message and
        still produces the correct patch — guards against an over-eager future
        change routing ``event`` before the runtime grows those hooks.
        """
        from django.test import override_settings

        from djust.runtime import ViewRuntime

        calls = []
        real_dispatch = ViewRuntime.dispatch_message

        async def _spy(self, data):
            calls.append(data)
            return await real_dispatch(self, data)

        monkeypatch.setattr(ViewRuntime, "dispatch_message", _spy)

        with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
            communicator, _ = await _connect_and_mount()

            await communicator.send_json_to(
                {"type": "event", "event": "bump", "params": {}, "ref": 1}
            )
            frame = await _receive_until(communicator, "patch")
            assert frame.get("type") in ("patch", "html_update"), (
                f"event must still be handled by WS handle_event, got {frame!r}"
            )
            # event must NOT have gone through the runtime chokepoint in T1-B.
            event_calls = [c for c in calls if c.get("type") == "event"]
            assert event_calls == [], (
                "event must NOT route through ViewRuntime.dispatch_message in "
                f"T1-B (it stays on WS handle_event); saw {event_calls!r}"
            )

            await communicator.disconnect()

    @pytest.mark.django_db
    @pytest.mark.asyncio
    async def test_mount_still_handled_by_ws_handle_mount(self, monkeypatch):
        """``mount`` is runtime-owned but DELIBERATELY stays on WS handle_mount
        until T1-A (#1853) for sticky/snapshot/actor support. Pin that mount
        does NOT go through dispatch_message.
        """
        from django.test import override_settings

        from djust.runtime import ViewRuntime

        calls = []
        real_dispatch = ViewRuntime.dispatch_message

        async def _spy(self, data):
            calls.append(data)
            return await real_dispatch(self, data)

        monkeypatch.setattr(ViewRuntime, "dispatch_message", _spy)

        with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
            communicator, mount_frame = await _connect_and_mount()
            assert mount_frame.get("type") == "mount"

            mount_calls = [c for c in calls if c.get("type") == "mount"]
            assert mount_calls == [], (
                "mount must NOT route through ViewRuntime.dispatch_message in "
                f"T1-B (deferred to T1-A #1853); saw {mount_calls!r}"
            )

            await communicator.disconnect()


# ------------------------------------------------------------------ #
# Explicit chokepoint-set contract.
# ------------------------------------------------------------------ #


class TestRuntimeOwnedVerbsContract:
    def test_runtime_owned_verbs_is_exactly_url_change(self):
        """The explicit chokepoint set pins which verbs go through
        dispatch_message. If a future PR routes ``event`` or ``mount`` it MUST
        update this set (and the routing + this test) deliberately.
        """
        from djust.websocket import LiveViewConsumer

        assert LiveViewConsumer.RUNTIME_OWNED_VERBS == frozenset({"url_change"}), (
            "RUNTIME_OWNED_VERBS pins the verbs routed through the #1852 "
            f"dispatch_message chokepoint; got {LiveViewConsumer.RUNTIME_OWNED_VERBS!r}"
        )

    def test_runtime_owned_verbs_subset_of_dispatch_message_known_types(self):
        """Every verb in RUNTIME_OWNED_VERBS must be a type that
        ``ViewRuntime.dispatch_message`` actually routes (not the else→error
        branch) — otherwise routing it would emit 'Unknown message type'.
        """
        from djust.runtime import ViewRuntime
        from djust.websocket import LiveViewConsumer

        dispatch_src = inspect.getsource(ViewRuntime.dispatch_message)
        for verb in LiveViewConsumer.RUNTIME_OWNED_VERBS:
            assert f'== "{verb}"' in dispatch_src, (
                f"RUNTIME_OWNED_VERBS contains {verb!r} but dispatch_message has "
                f"no explicit branch for it — routing would hit the unknown-type "
                f"error envelope."
            )
