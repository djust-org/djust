"""The per-event auth re-check reads the session as stored, not the socket's cached copy.

``reauth_on_event`` re-resolved the user with ``channels.auth.get_user(scope)``,
which reads ``scope["session"]``: the session object loaded when the socket
connected, whose data stays cached. A sign-out in another tab deletes the stored
session, but the cached copy still named the user, so the open socket kept
running events. Found by a port of a production app (sign-out over HTTP, then an
event on the still-open page ran its handler).

These tests use a real database session and no mock of ``get_user``.
"""

from __future__ import annotations

import sys

import pytest

pytest.importorskip("channels")

from asgiref.sync import sync_to_async  # noqa: E402
from django.contrib.auth import BACKEND_SESSION_KEY, HASH_SESSION_KEY, SESSION_KEY  # noqa: E402
from django.contrib.auth import get_user_model  # noqa: E402
from django.contrib.sessions.backends.db import SessionStore  # noqa: E402
from django.test import override_settings  # noqa: E402

from djust import LiveView  # noqa: E402
from djust.config import config  # noqa: E402
from djust.decorators import event_handler  # noqa: E402

_RAN: list[str] = []


class _StoredSessionView(LiveView):
    login_required = True
    template = '<div dj-view="djust.tests.test_reauth_reads_stored_session._StoredSessionView" dj-id="0">x</div>'

    def mount(self, request, **kwargs):
        self.count = 0

    @event_handler
    def mutate(self, **kwargs):
        _RAN.append("mutate")


setattr(sys.modules[__name__], "_StoredSessionView", _StoredSessionView)
_VIEW_PATH = f"{__name__}._StoredSessionView"


def _signed_in_session() -> tuple[object, SessionStore]:
    user = get_user_model().objects.create_user(username="reauth-stored", password="x")
    session = SessionStore()
    session[SESSION_KEY] = str(user.pk)
    session[BACKEND_SESSION_KEY] = "django.contrib.auth.backends.ModelBackend"
    session[HASH_SESSION_KEY] = user.get_session_auth_hash()
    session.save()
    # Load it, as channels' SessionMiddleware does at connect: the data is now cached.
    loaded = SessionStore(session.session_key)
    loaded.get(SESSION_KEY)
    return user, loaded


async def _mount(user, session):
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    inner = LiveViewConsumer.as_asgi()

    async def app(scope, receive, send):
        scope = dict(scope)
        scope["user"] = user
        scope["session"] = session
        return await inner(scope, receive, send)

    communicator = WebsocketCommunicator(app, "/ws/")
    connected, _ = await communicator.connect()
    assert connected
    try:
        await communicator.receive_json_from(timeout=2)
    except Exception:
        pass
    await communicator.send_json_to({"type": "mount", "view": _VIEW_PATH})
    mounted = await communicator.receive_json_from()
    assert mounted.get("type") == "mount", mounted
    return communicator


_REPLIES = ("patch", "noop", "html_update", "error")


async def _outputs_after_event(communicator) -> list[dict]:
    """The frames up to the event's reply or the socket's close. Stops there: a
    timed-out receive makes asgiref cancel the application."""
    import json

    await communicator.send_json_to({"type": "event", "event": "mutate", "params": {}})
    outputs = []
    for _ in range(6):
        output = await communicator.receive_output()
        outputs.append(output)
        if output["type"] == "websocket.close":
            break
        if (
            output["type"] == "websocket.send"
            and json.loads(output.get("text") or "{}").get("type") in _REPLIES
        ):
            break
    return outputs


@pytest.mark.django_db(transaction=True)
@override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], LIVEVIEW_CONFIG={"reauth_on_event": True})
async def test_a_sign_out_elsewhere_stops_the_open_socket():
    _RAN.clear()
    config.reset()
    try:
        user, session = await sync_to_async(_signed_in_session)()
        communicator = await _mount(user, session)
        # Signed out in another tab: the stored session is gone; the socket's copy is not.
        await sync_to_async(SessionStore(session.session_key).delete)()
        outputs = await _outputs_after_event(communicator)
        closes = [o for o in outputs if o["type"] == "websocket.close"]
        assert closes and closes[0].get("code") == 4403, outputs
        assert _RAN == [], "the handler ran after the session was signed out elsewhere"
        await communicator.disconnect()
    finally:
        config.reset()


@pytest.mark.django_db(transaction=True)
@override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], LIVEVIEW_CONFIG={"reauth_on_event": True})
async def test_a_live_session_keeps_running_events():
    _RAN.clear()
    config.reset()
    try:
        user, session = await sync_to_async(_signed_in_session)()
        communicator = await _mount(user, session)
        outputs = await _outputs_after_event(communicator)
        assert not [o for o in outputs if o["type"] == "websocket.close"], outputs
        assert _RAN == ["mutate"]
        await communicator.disconnect()
    finally:
        config.reset()
