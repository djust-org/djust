"""ADR-038 E2-10: deliberate ORM rendering end to end under the explicit policy.

A real ``auth.User`` row carries a password sentinel and an unrendered-field
sentinel. The explicit view persists only the approved identity (``member_id``)
and loads the model in ``get_context_data``, so the object is render-only. It is
rendered over HTTP GET and over the real WebSocket consumer (Channels
``WebsocketCommunicator``, DB sessions, DEBUG on so the frames carry the debug
payload). Only the staged construction gate is bypassed.
"""

import json

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth.models import AnonymousUser, User
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory, override_settings

from djust import LiveView, event_handler
from djust._exposure import explicit_debug_projection
from djust._exposure_sessions import server_state_adapter
from djust._exposure_snapshots import snapshot_codec
from djust.decorators import state

pytestmark = [pytest.mark.asyncio, pytest.mark.django_db(transaction=True)]

RENDERED = "ORM_RENDERED_NAME"
PASSWORD = "ORM_PASSWORD_SENTINEL"
UNRENDERED = "ORM_UNRENDERED_SENTINEL"
TEMPLATE = "<div dj-root><p>{{ member.username }}</p><span>{{ count }}</span></div>"


class OrmView(LiveView):
    exposure_policy = "explicit"
    template = TEMPLATE
    count = state(0, persist="server")
    member_id = state(0, persist="server")
    note = state("snap", persist="client", client=True)

    def mount(self, request, **kwargs):
        self.count = 1
        self.member_id = User.objects.get(username=RENDERED).pk

    def get_context_data(self, **kwargs):
        member = User.objects.get(pk=self.member_id)
        return super().get_context_data(member=member, count=self.count, **kwargs)

    @event_handler()
    def increment(self):
        self.count += 1


class LegacyOrmView(LiveView):
    """Legacy control: the same model is an ordinary public attribute."""

    template = TEMPLATE

    def mount(self, request, **kwargs):
        self.count = 1
        self.member = User.objects.get(username=RENDERED)

    @event_handler()
    def increment(self):
        self.count += 1


SETTINGS = {
    "LIVEVIEW_ALLOWED_MODULES": [__name__],
    "DEBUG": True,
    "DJUST_CONFIG": {},
    "DJUST_TENANTS": {},
}


@pytest.fixture
def staged(monkeypatch):
    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)


def make_user():
    return User.objects.create(username=RENDERED, password=PASSWORD, first_name=UNRENDERED)


def make_request(session_key=None):
    request = RequestFactory().get("/orm-explicit/")
    request.user = AnonymousUser()
    request.tenant = None
    request.session = SessionStore(session_key)
    if session_key is None:
        request.session.create()
    return request


def assert_clean(text):
    assert PASSWORD not in text
    assert UNRENDERED not in text


async def websocket_session(session_key, view_name):
    """Mount and send one event over the real consumer; return every frame."""
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    comm = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    comm.scope["session"] = SessionStore(session_key)
    comm.scope["user"] = AnonymousUser()
    connected, _ = await comm.connect()
    assert connected
    frames = [await comm.receive_json_from(timeout=5)]
    try:
        await comm.send_json_to(
            {"type": "mount", "view": __name__ + "." + view_name, "url": "/orm-explicit/"}
        )
        frames.append(await comm.receive_json_from(timeout=5))
        await comm.send_json_to({"type": "event", "event": "increment", "params": {}})
        frames.append(await comm.receive_json_from(timeout=5))
    finally:
        await comm.disconnect()
    return frames


class _TickingClock:
    """``time`` for the snapshot codec: one second per call (#3092).

    The signed snapshot embeds ``"created": int(time.time())`` under its
    signature, so a mount and an event that straddle a second boundary carry
    different tokens for identical state. Advancing on every call makes that
    boundary certain instead of occasional."""

    def __init__(self, start):
        self._now = start

    def time(self):
        self._now += 1
        return self._now


async def test_explicit_orm_render_reaches_html_but_not_storage_frames_or_debug(
    staged, monkeypatch
):
    import time

    from djust import _exposure_snapshots

    monkeypatch.setattr(_exposure_snapshots, "time", _TickingClock(int(time.time())))
    user = await sync_to_async(make_user)()
    request = await sync_to_async(make_request)()
    with override_settings(**SETTINGS):
        # HTTP GET renders the model field and persists only declared fields.
        response = await sync_to_async(OrmView.as_view())(request)
        assert response.status_code == 200
        body = response.content.decode()
        assert f"<p>{RENDERED}</p>" in body
        assert_clean(body)

        frames = await websocket_session(request.session.session_key, "OrmView")
    connect, mount, event = frames
    assert mount["type"] == "mount", mount
    assert f">{RENDERED}</p>" in mount["html"]
    assert event["type"] in {"patch", "html_update"}, event
    # The debug payload is really attached (DEBUG on); it is the projection.
    assert event["_debug"]["variables"]["count"]["type"] == "redacted"
    assert set(event["_debug"]["variables"]) == {"count", "member_id", "note"}
    wire = json.dumps(frames)
    assert_clean(wire)
    assert '"member"' not in wire

    # The signed snapshot holds only the client-persisted field.
    # Compare what the tokens carry, not their bytes: each embeds its signing
    # second, so identical state re-signed a second later differs (#3092).
    token = mount["state_snapshot_signed"]
    event_token = event["state_snapshot_signed"]
    assert token and event_token
    assert_clean(token)
    assert_clean(event_token)
    fresh = await sync_to_async(make_request)(request.session.session_key)
    view = OrmView.__new__(OrmView)
    codec = await sync_to_async(snapshot_codec)(view, fresh)
    assert codec.restore(token) == {"note": "snap"}
    assert codec.restore(event_token) == {"note": "snap"}

    # Server persistence: the validated adapter load and the raw stored session.
    adapter = await sync_to_async(server_state_adapter)(view, fresh)
    assert await sync_to_async(adapter.load)() == {"count": 2, "member_id": user.pk}
    raw = await sync_to_async(SessionStore(request.session.session_key).load)()
    assert any(key.startswith("_djust_explicit_") for key in raw)
    stored = json.dumps(raw)
    assert_clean(stored)
    assert RENDERED not in stored

    # The debug projection of a live instance whose context holds the model.
    live = OrmView.__new__(OrmView)
    live.count, live.member_id = 2, user.pk
    live._components, live._streams = {}, {}
    context = await sync_to_async(live.get_context_data)()
    assert context["member"].first_name == UNRENDERED  # positive control
    assert context["member"].password == PASSWORD
    projection = await sync_to_async(explicit_debug_projection)(live)
    assert projection == {"count": "[redacted]", "member_id": "[redacted]", "note": "snap"}
    debug_info = await sync_to_async(live.get_debug_info)()
    debug_update = await sync_to_async(live.get_debug_update)()
    for payload in (projection, debug_info, debug_update):
        assert_clean(json.dumps(payload, default=str))


async def test_legacy_orm_render_control(staged):
    """The legacy path renders the same row; its behavior is unchanged."""
    await sync_to_async(make_user)()
    request = await sync_to_async(make_request)()
    with override_settings(**SETTINGS):
        response = await sync_to_async(LegacyOrmView.as_view())(request)
        assert response.status_code == 200
        assert f"<p>{RENDERED}</p>" in response.content.decode()
        frames = await websocket_session(request.session.session_key, "LegacyOrmView")
    connect, mount, event = frames
    assert mount["type"] == "mount", mount
    assert f">{RENDERED}</p>" in mount["html"]
    assert event["type"] == "patch", event
    # Legacy debug output still reflects the undeclared model attribute.
    assert "member" in event["_debug"]["state_sizes"]
    assert "state_snapshot_signed" not in mount
