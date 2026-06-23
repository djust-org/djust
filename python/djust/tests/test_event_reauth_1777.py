"""Opt-in per-event auth re-check (#1777, threat model T3).

Auth runs at mount; the connect-time scope user is cached, so a user who logs
out / loses a permission mid-session would keep dispatching events on the open
socket. With ``LIVEVIEW_CONFIG['reauth_on_event'] = True``, handle_event
re-resolves the user from the session (channels.auth.get_user) and re-runs the
view's auth check, closing 4403 on failure.

The connect-time user is authenticated (so the login_required view mounts); the
event-time re-resolution is mocked to return AnonymousUser (= logged out) at the
``channels.auth.get_user`` seam (channels' own code, separately tested).
"""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("channels")

from django.contrib.auth.models import AnonymousUser  # noqa: E402
from django.test import override_settings  # noqa: E402

from djust import LiveView  # noqa: E402
from djust.config import config  # noqa: E402
from djust.decorators import event_handler  # noqa: E402

_HANDLER_RAN = False


class _LoginView(LiveView):
    login_required = True
    template = '<div dj-view="djust.tests.test_event_reauth_1777._LoginView" dj-id="0">x</div>'

    def mount(self, request, **kwargs):
        self.count = 0

    @event_handler
    def do_mutation(self, **kwargs):
        global _HANDLER_RAN
        _HANDLER_RAN = True


setattr(sys.modules[__name__], "_LoginView", _LoginView)
_VIEW_PATH = f"{__name__}._LoginView"


class _AuthedUser:
    """Minimal authenticated stand-in for the connect-time scope user."""

    is_authenticated = True
    is_active = True
    is_anonymous = False


def _auth_scope_middleware(app):
    """Inject an authenticated scope user + a session so the login_required view
    mounts (connect-time auth passes) and channels.auth.get_user has a session."""

    async def mw(scope, receive, send):
        scope = dict(scope)
        scope["user"] = _AuthedUser()
        scope.setdefault("session", {})
        return await app(scope, receive, send)

    return mw


async def _connect_and_mount():
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    app = _auth_scope_middleware(LiveViewConsumer.as_asgi())
    communicator = WebsocketCommunicator(app, "/ws/")
    connected, _ = await communicator.connect()
    assert connected
    try:
        await communicator.receive_json_from(timeout=2)
    except Exception:
        pass
    await communicator.send_json_to({"type": "mount", "view": _VIEW_PATH})
    # Drain the mount response (should be a real mount, not a redirect).
    mount_resp = await communicator.receive_json_from(timeout=2)
    assert mount_resp.get("type") != "navigate", f"unexpected mount redirect: {mount_resp!r}"
    return communicator


@pytest.mark.django_db
@override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], LIVEVIEW_CONFIG={"reauth_on_event": True})
async def test_reauth_on_closes_socket_when_user_logged_out_midsession():
    global _HANDLER_RAN
    _HANDLER_RAN = False
    config.reset()
    try:
        # Event-time re-resolution sees a logged-out (anonymous) user.
        with patch("channels.auth.get_user", AsyncMock(return_value=AnonymousUser())):
            communicator = await _connect_and_mount()
            await communicator.send_json_to({"type": "event", "event": "do_mutation", "params": {}})
            # Expect a navigate redirect then a websocket.close(4403).
            out_types = []
            for _ in range(4):
                try:
                    out = await communicator.receive_output(timeout=2)
                except Exception:
                    break
                out_types.append(out["type"])
                if out["type"] == "websocket.close":
                    assert out.get("code") == 4403
                    break
            assert "websocket.close" in out_types, f"socket not closed on deauth: {out_types}"
            assert _HANDLER_RAN is False, "handler ran despite mid-session logout (T3)"
            await communicator.disconnect()
    finally:
        config.reset()


@pytest.mark.django_db
@override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], LIVEVIEW_CONFIG={"reauth_on_event": False})
async def test_reauth_off_default_dispatches_event_normally():
    """Default OFF: no re-check, no extra session read — the event dispatches."""
    global _HANDLER_RAN
    _HANDLER_RAN = False
    config.reset()
    try:
        # get_user must NOT be consulted when the flag is off.
        with patch("channels.auth.get_user", AsyncMock(return_value=AnonymousUser())) as gu:
            communicator = await _connect_and_mount()
            await communicator.send_json_to({"type": "event", "event": "do_mutation", "params": {}})
            try:
                await communicator.receive_json_from(timeout=2)
            except Exception:
                pass
            assert _HANDLER_RAN is True, "handler did not run with reauth off (default)"
            gu.assert_not_called()
            await communicator.disconnect()
    finally:
        config.reset()
