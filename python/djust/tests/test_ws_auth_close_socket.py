"""Reproducer + regression for the WebSocket auth bypass (threat model T1/T2).

See ``docs/audits/websocket-auth-2026-06.md``. On a ``login_required`` WS mount
for an anonymous client, ``handle_mount`` sent a ``{"type":"navigate"}`` frame
but did NOT close the socket (the ``PermissionDenied`` branch did). The socket
stayed open with ``view_instance`` set but ``mount()`` never run, and
``handle_event`` never re-checks auth — so a raw client that ignores the
navigate frame could dispatch events to ``@event_handler`` methods
unauthenticated. The fix: send the navigate frame, then ``close(code=4403)`` and
clear ``view_instance`` on the redirect branch (mirroring ``PermissionDenied``).

These tests connect a real ``WebsocketCommunicator`` (no user in scope =
anonymous) and assert the socket is terminated and no handler runs. They FAIL
against the pre-fix code (socket open, handler dispatched) and PASS after.
"""

from __future__ import annotations

import sys

import pytest

pytest.importorskip("channels")

from django.test import override_settings  # noqa: E402

from djust import LiveView  # noqa: E402
from djust.decorators import event_handler  # noqa: E402

# Module-level flag the mutating handler flips if it ever executes. A raw client
# that bypasses auth and reaches the handler would set this True.
_HANDLER_RAN = False


class _LoginRequiredView(LiveView):
    """A login-gated view whose handler records execution."""

    login_required = True
    template = (
        '<div dj-view="djust.tests.test_ws_auth_close_socket._LoginRequiredView" dj-id="0">x</div>'
    )

    def mount(self, request, **kwargs):
        self.count = 0

    @event_handler
    def do_mutation(self, **kwargs):
        global _HANDLER_RAN
        _HANDLER_RAN = True  # a real handler would write to the DB here


class _PublicView(LiveView):
    """A public view used to prove a batch survives a login-redirecting sibling.

    ``login_required = False`` is load-bearing for test isolation, not just
    documentation: this is a *module-level* ``LiveView`` subclass, so it
    permanently joins ``LiveView.__subclasses__()`` and is walked by the
    ``djust.S005`` system check (LiveView exposes state without auth). Without
    the explicit ``login_required = False`` ("intentionally public"), S005 sees
    ``login_required is None`` + exposed state (``self.ok``) and fires on
    ``_PublicView``, polluting any later serial test that asserts the S005
    result set (issue #1794:
    ``test_s005_suppressed_with_login_required_false`` matched
    ``"PublicView" in msg``). Marking it public both reflects the view's actual
    contract and tells S005 the auth decision was made.
    """

    login_required = False

    template = (
        '<div dj-view="djust.tests.test_ws_auth_close_socket._PublicView" dj-id="0">pub</div>'
    )

    def mount(self, request, **kwargs):
        self.ok = True


# Ensure the dotted paths resolve for the consumer's import.
setattr(sys.modules[__name__], "_LoginRequiredView", _LoginRequiredView)
setattr(sys.modules[__name__], "_PublicView", _PublicView)

_VIEW_PATH = f"{__name__}._LoginRequiredView"
_PUBLIC_PATH = f"{__name__}._PublicView"


async def _connect_and_mount(communicator):
    connected, _ = await communicator.connect()
    assert connected
    # Drain the initial connect/handshake frame if one is sent.
    try:
        await communicator.receive_json_from(timeout=2)
    except Exception:
        pass
    await communicator.send_json_to({"type": "mount", "view": _VIEW_PATH})


@pytest.mark.django_db
@override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__])
async def test_login_required_ws_mount_closes_socket_after_redirect():
    """Anonymous mount of a login_required view → navigate frame THEN the
    socket is closed (4403). Pre-fix the socket stayed open (T1)."""
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    await _connect_and_mount(communicator)

    nav = await communicator.receive_json_from(timeout=2)
    assert nav.get("type") == "navigate", f"expected a navigate redirect frame, got {nav!r}"

    # The auth failure must terminate the transport, not just nudge the UI.
    out = await communicator.receive_output(timeout=2)
    assert out["type"] == "websocket.close", f"socket not closed after auth redirect: {out!r}"
    assert out.get("code") == 4403
    await communicator.disconnect()


@pytest.mark.django_db
@override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__])
async def test_anonymous_cannot_dispatch_event_after_login_redirect():
    """The actual bypass: after the login redirect, an event must NOT reach the
    handler. Pre-fix the open socket let handle_event dispatch unauthenticated."""
    global _HANDLER_RAN
    _HANDLER_RAN = False
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    await _connect_and_mount(communicator)

    nav = await communicator.receive_json_from(timeout=2)
    assert nav.get("type") == "navigate"

    # A raw client ignores the navigate and tries to mutate state.
    await communicator.send_json_to({"type": "event", "event": "do_mutation", "params": {}})
    # Give the consumer a chance to (wrongly) process it.
    try:
        await communicator.receive_json_from(timeout=1)
    except Exception:
        pass

    assert _HANDLER_RAN is False, (
        "AUTH BYPASS: an anonymous client dispatched an event to a login_required "
        "view's @event_handler after the mount redirect (T1)"
    )
    await communicator.disconnect()


@pytest.mark.django_db
@override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__])
async def test_mount_batch_with_login_view_does_not_close_shared_socket():
    """Regression (review of the T1/T2 fix): a login-redirecting view inside a
    mount_batch must NOT close the shared socket — the surviving public view
    mounts and the redirecting view is reported in navigate[]. (The close() is
    suppressed inside _mount_one; view_instance is still cleared.)"""
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    connected, _ = await communicator.connect()
    assert connected
    try:
        await communicator.receive_json_from(timeout=2)
    except Exception:
        pass
    await communicator.send_json_to(
        {
            "type": "mount_batch",
            "views": [
                {"view": _PUBLIC_PATH, "target_id": "pub", "url": "/pub/"},
                {"view": _VIEW_PATH, "target_id": "secret", "url": "/secret/"},
            ],
        }
    )
    resp = await communicator.receive_json_from(timeout=3)
    assert resp.get("type") == "mount_batch", f"got {resp!r}"
    # Public view survived; login-required view is a navigate[] redirect.
    survivor_targets = [v.get("target_id") for v in resp.get("views", [])]
    nav_targets = [n.get("target_id") for n in resp.get("navigate", [])]
    assert "pub" in survivor_targets, f"public view dropped: {resp!r}"
    assert "secret" in nav_targets, f"login view not reported as redirect: {resp!r}"
    # The shared socket must still be open (no mid-batch close).
    assert await communicator.receive_nothing(timeout=0.5) is True, (
        "shared socket received an unexpected frame (likely a websocket.close mid-batch)"
    )
    await communicator.disconnect()
