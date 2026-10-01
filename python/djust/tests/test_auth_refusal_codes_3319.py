"""#3319: every auth refusal carries a stable machine-readable ``code``.

A client guard that must hide the page and leave when authority is revoked
used to regex the human message ("Event authorization failed" / "Permission
denied"). Each refusal now carries ``code="permission_denied"`` next to the
unchanged ``error`` text, on every transport:

* WebSocket: re-authorization of an explicit-policy event (+ close 4403), a
  ``@permission_required`` handler, and a mount refused with PermissionDenied;
* SSE: the ``reauth_on_event`` re-check and the session-owner refusal;
* HTTP fallback: the view-level, handler-level and object-level 403 bodies.

The code is a fixed value, never built from the user's input or an exception.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model
from django.test import RequestFactory, override_settings

from djust import LiveView, event_handler
from djust.decorators import permission_required, state
from djust.websocket import LiveViewConsumer

from ._ws_frames import drain_extra, has_type, receive_until
from .test_exposure_runtime import make_request

CODE = "permission_denied"
RAN = []


class ExplicitView(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root>{{ count }}</div>"
    count = state(0, persist="server")

    def get_context_data(self, **kwargs):
        return super().get_context_data(count=self.count, **kwargs)

    @event_handler()
    def bump(self, **kwargs):
        RAN.append("bump")


class GuardedHandlerView(LiveView):
    exposure_policy = "legacy"
    template = "<div dj-root>x</div>"

    @permission_required("auth.delete_user")
    @event_handler()
    def purge(self, **kwargs):
        RAN.append("purge")


class GuardedMountView(LiveView):
    exposure_policy = "legacy"
    template = "<div dj-root>x</div>"
    login_required = True
    permission_required = "auth.delete_user"


class ObjectView(LiveView):
    exposure_policy = "legacy"
    template = "<div dj-root>x</div>"

    def get_object(self):
        return object()

    def has_object_permission(self, request, obj):
        return False

    @event_handler()
    def touch(self, **kwargs):
        RAN.append("touch")


async def _frames(socket, until):
    frames = await receive_until(socket, until)
    frames += await drain_extra(socket)
    return [f for f in frames if f.get("type") != "websocket.close"], [
        f for f in frames if f.get("type") == "websocket.close"
    ]


async def _open(user, session):
    socket = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    socket.scope.update(session=session, user=user, tenant=None)
    assert (await socket.connect())[0]
    await socket.receive_json_from(timeout=3)
    return socket


# --- WebSocket -------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_ws_revoked_event_frame_carries_code(monkeypatch):
    RAN.clear()
    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)
    with override_settings(
        LIVEVIEW_ALLOWED_MODULES=[__name__], DEBUG=False, DJUST_TENANTS=None, DJUST_CONFIG={}
    ):
        request = await sync_to_async(make_request)()
        socket = await _open(request.user, request.session)
        try:
            await socket.send_json_to(
                {"type": "mount", "view": __name__ + ".ExplicitView", "url": "/a/"}
            )
            assert (await socket.receive_json_from(timeout=3))["type"] == "mount"
            await sync_to_async(request.session.delete)()
            await socket.send_json_to({"type": "event", "event": "bump", "params": {}})
            frames, closes = await _frames(socket, has_type("error"))
        finally:
            await socket.disconnect()
    errors = [f for f in frames if f.get("type") == "error"]
    assert [(e["error"], e["code"]) for e in errors] == [
        ("Event authorization failed. Please reload the page.", CODE)
    ]
    assert closes and closes[0]["code"] == 4403
    assert RAN == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_ws_handler_permission_denied_frame_carries_code(monkeypatch):
    RAN.clear()
    user = await sync_to_async(get_user_model().objects.create_user)(
        username="u3319h", password="x"
    )
    request = await sync_to_async(make_request)()
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], DEBUG=False, DJUST_TENANTS=None):
        socket = await _open(user, request.session)
        try:
            await socket.send_json_to(
                {"type": "mount", "view": __name__ + ".GuardedHandlerView", "url": "/g/"}
            )
            assert (await socket.receive_json_from(timeout=3))["type"] == "mount"
            await socket.send_json_to({"type": "event", "event": "purge", "params": {}})
            frames, closes = await _frames(socket, has_type("error"))
        finally:
            await socket.disconnect()
    errors = [f for f in frames if f.get("type") == "error"]
    assert [(e["error"], e.get("code")) for e in errors] == [("Permission denied", CODE)]
    assert not closes, "a refused handler leaves the socket open"
    assert RAN == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_ws_mount_permission_denied_frame_carries_code():
    user = await sync_to_async(get_user_model().objects.create_user)(
        username="u3319m", password="x"
    )
    request = await sync_to_async(make_request)()
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], DEBUG=False, DJUST_TENANTS=None):
        socket = await _open(user, request.session)
        try:
            await socket.send_json_to(
                {"type": "mount", "view": __name__ + ".GuardedMountView", "url": "/m/"}
            )
            frames, closes = await _frames(socket, has_type("error"))
        finally:
            await socket.disconnect()
    errors = [f for f in frames if f.get("type") == "error"]
    assert [(e["error"], e.get("code")) for e in errors] == [("Permission denied", CODE)]
    assert closes and closes[0]["code"] == 4403


# --- SSE -------------------------------------------------------------------


@override_settings(LIVEVIEW_CONFIG={"reauth_on_event": True})
@pytest.mark.asyncio
async def test_sse_reauth_refusal_code_is_the_string_enum():
    from djust.config import config
    from djust.runtime import SSESessionTransport

    config.reset()
    try:
        session = MagicMock()
        session.send_error = AsyncMock()
        session.close = AsyncMock()
        session._event_request = MagicMock()
        view = MagicMock(login_required=True, permission_required=None)
        with patch("djust.auth.core.check_view_auth_lightweight", return_value=False):
            ok = await SSESessionTransport(session).recheck_event_auth(view)
    finally:
        config.reset()
    assert ok is False
    (message,), kwargs = session.send_error.await_args
    assert message == "Session is no longer authorized. Please reload the page."
    assert kwargs == {"code": CODE}, "was the numeric close code 4403"
    session.close.assert_awaited_once_with(code=4403)


# --- HTTP fallback ---------------------------------------------------------


def _user(*, perms: bool):
    user = MagicMock(is_authenticated=True, is_active=True)
    user.has_perms.return_value = perms
    user.has_perm.return_value = perms
    return user


def _post(view_cls, event, user):
    request = RequestFactory().post(
        "/h/", data=json.dumps({}), content_type="application/json", HTTP_X_DJUST_EVENT=event
    )
    request.user, request.session, request.tenant = user, make_request().session, None
    response = view_cls().post(request)
    return response.status_code, json.loads(response.content)


@pytest.mark.django_db
@pytest.mark.parametrize(
    "view_cls, event",
    [
        (GuardedMountView, "anything"),  # view-level permission_required
        (GuardedHandlerView, "purge"),  # handler-level @permission_required
        (ObjectView, "touch"),  # object-level has_object_permission
    ],
)
def test_http_refusal_body_carries_code(view_cls, event):
    RAN.clear()
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], DEBUG=False):
        status, body = _post(view_cls, event, _user(perms=False))
    assert status == 403
    assert body["code"] == CODE
    assert body["error"] in ("Permission denied", "Access denied for this object.")
    assert RAN == []


@pytest.mark.django_db
def test_http_refusal_code_is_independent_of_user_input():
    """The code is a constant: a hostile event name never reaches it."""
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], DEBUG=False):
        status, body = _post(GuardedHandlerView, "purge", _user(perms=False))
    assert (status, body["code"]) == (403, CODE)
    assert "purge" not in json.dumps(body)
