"""Regression tests for #1785 — the DJE-053 ``html_update`` fallback must arm recovery.

Root cause (verified by local reproduction of the djust.org ``/insights/`` incident):
when an event's VDOM diff returns ``patches=None``, ``handle_event`` sends a full
``html_update`` frame but historically did NOT call ``self._arm_recovery(html, version)``
— only the patches branch did. So when the client (which sees a version mismatch on the
``html_update``) follows up with ``request_html``, the server's ``_recovery_html`` is
``None`` and it replies with the non-recoverable error
``"Recovery HTML unavailable — the server may have restarted"`` → the client does a full
page reload.

The fix arms recovery on the ``html_update`` branch too. These tests pin both the runtime
behavior (a real ``WebsocketCommunicator`` round-trip) and the source.

A view that sets ``self._force_full_html = True`` in its event handler deterministically
forces the ``patches=None`` / ``html_update`` branch, so the test does not depend on
losing the Rust ``last_vdom`` baseline.
"""

from __future__ import annotations

import inspect

import pytest
from asgiref.sync import sync_to_async

from djust import LiveView
from djust.decorators import event_handler


class _WSForceFullView(LiveView):
    """Module-level view whose event forces the full-HTML (``html_update``) path."""

    template = (
        '<div dj-view="djust.tests.test_ws_recovery_html_update_1785._WSForceFullView" '
        'dj-id="0">Count: {{ count }}</div>'
    )

    def mount(self, request, **kwargs):
        self.count = 0

    @event_handler()
    def bump_full(self, **kwargs):
        self.count += 1
        # Force the DJE-053-class fallback: patches=None → html_update frame.
        self._force_full_html = True


async def _receive_until(communicator, wanted_type, *, tries=5, timeout=3):
    """Drain frames until one whose ``type`` == ``wanted_type`` (or return last seen)."""
    last = None
    for _ in range(tries):
        last = await communicator.receive_json_from(timeout=timeout)
        if last.get("type") == wanted_type:
            return last
    return last


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_html_update_fallback_arms_recovery_so_request_html_succeeds():
    """The load-bearing regression test.

    Drive ``handle_event`` down the ``html_update`` (patches=None) path via a real
    ``WebsocketCommunicator``, then send ``request_html`` and assert the server returns
    an ``html_recovery`` frame — NOT the "Recovery HTML unavailable" error.

    Gate-off: reverting the ``_arm_recovery`` call in the ``html_update`` branch makes
    ``request_html`` return ``{"type":"error","error":"Recovery HTML unavailable ..."}``,
    failing the final assertion. (Verified during the #1785 investigation.)
    """
    pytest.importorskip("channels")
    from channels.testing import WebsocketCommunicator
    from django.contrib.sessions.backends.db import SessionStore
    from django.test import override_settings

    from djust.websocket import LiveViewConsumer

    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):

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

        # Mount.
        await communicator.send_json_to(
            {
                "type": "mount",
                "view": f"{__name__}._WSForceFullView",
                "url": "/force-full/",
            }
        )
        mount_resp = await _receive_until(communicator, "mount")
        assert mount_resp.get("type") == "mount", f"expected mount, got {mount_resp!r}"

        # Event that forces the html_update (no-patches) fallback.
        await communicator.send_json_to(
            {"type": "event", "event": "bump_full", "params": {}, "ref": 1}
        )
        event_resp = await _receive_until(communicator, "html_update")
        assert event_resp.get("type") == "html_update", (
            f"event with _force_full_html must fall back to html_update; got {event_resp!r}"
        )
        assert not event_resp.get("patches"), "html_update fallback must carry no patches"

        # Client behavior: a version mismatch on the html_update makes the client
        # request recovery. Simulate it directly.
        await communicator.send_json_to({"type": "request_html"})
        recovery_resp = await _receive_until(communicator, "html_recovery")

        assert recovery_resp.get("type") == "html_recovery", (
            "After an html_update fallback, request_html must return an 'html_recovery' "
            "frame — meaning the html_update branch armed recovery. Got: "
            f"{recovery_resp!r}. Without _arm_recovery in the html_update branch this is "
            "an error frame 'Recovery HTML unavailable' → forced page reload (#1785)."
        )
        assert recovery_resp.get("html"), "html_recovery frame must carry the recovery HTML"

        await communicator.disconnect()


def test_html_update_branch_arms_recovery_source():
    """Belt-and-suspenders source pin: ``handle_event`` must arm recovery on BOTH the
    patches branch and the ``html_update`` (patches=None) fallback branch.

    If a future refactor drops the ``_arm_recovery`` call from the html_update branch,
    this fails fast even if the integration test is skipped under some CI config.
    """
    import djust.websocket as ws_mod

    source = inspect.getsource(ws_mod.LiveViewConsumer.handle_event)
    assert source.count("self._arm_recovery(") >= 2, (
        "handle_event must arm recovery in BOTH the patches branch and the html_update "
        "fallback branch (#1785) — found fewer than 2 _arm_recovery calls."
    )
