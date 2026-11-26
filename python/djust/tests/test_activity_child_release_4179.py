"""#4179: an event queued on a hidden activity inside an embedded child is
delivered to the child.

A child-targeted event (``view_id``) whose ``_activity`` names a hidden activity
of the child used to run at once: the activity gate was reached only for root
events. Now it is queued on the child, and both drains (the runtime's event
turn and the consumer's ``db_notify``) release it through the child path: the
child's own checks, its handler, its own render and an ``embedded_update``. A
release for a child that is no longer on the page is dropped; a revoked root
authority still closes the socket 4403.
"""

import pytest
from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator
from django.test import override_settings

from djust import LiveView, event_handler, state
from djust.tests._frames import legacy_type
from djust.websocket import LiveViewConsumer

from ._ws_frames import drain_extra, has_frame, has_type, receive_until
from .test_exposure_runtime import make_request

MOD = __name__
BUMPS: list = []
CHANNEL = "child_activity_4179"


class Kid(LiveView):
    exposure_policy = "explicit"
    open = state(False)
    count = state(0)
    template = (
        '{% load live_tags %}<div>{% dj_activity "panel" visible=open %}'
        '<b dj-click="bump">{{ count }}</b>{% enddj_activity %}</div>'
    )

    def mount(self, request, **kwargs):
        self.open = False

    @event_handler()
    def bump(self, **kwargs):
        self.count += 1
        BUMPS.append(True)

    @event_handler()
    def show_panel(self, **kwargs):
        self.open = True
        self.set_activity_visible("panel", True)


class Host(LiveView):
    exposure_policy = "explicit"
    root_open = state(False)
    _listen_channels = frozenset({CHANNEL})
    template = (
        "{% load live_tags %}<div dj-root>"
        '{% dj_activity "rootpanel" visible=root_open %}<i>root</i>{% enddj_activity %}'
        '{% live_render "' + MOD + '.Kid" view_id="kid" %}</div>'
    )

    def _open_kid(self):
        kid = self._get_child_view("kid")
        if kid is not None:
            kid.open = True
            kid.set_activity_visible("panel", True)

    def handle_info(self, message):
        self.root_open = True
        self.set_activity_visible("rootpanel", True)
        self._open_kid()

    @event_handler()
    def open_kid(self, **kwargs):
        self._open_kid()

    @event_handler()
    def drop_kid(self, **kwargs):
        self._unregister_child("kid")

    @event_handler()
    def ping(self, **kwargs):
        pass


def _closed(frames):
    return any(f.get("type") == "websocket.close" for f in frames)


async def _notify():
    await get_channel_layer().group_send(
        "djust_db_notify_" + CHANNEL,
        {"type": "db_notify", "channel": CHANNEL, "payload": {}},
    )


class _Socket:
    def __init__(self, monkeypatch):
        monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)
        self.settings = override_settings(
            LIVEVIEW_ALLOWED_MODULES=["djust", MOD],
            DEBUG=False,
            DJUST_TENANTS=None,
            DJUST_CONFIG={},
        )

    async def __aenter__(self):
        self.settings.enable()
        BUMPS.clear()
        self.request = await sync_to_async(make_request)()
        self.socket = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        self.socket.scope.update(session=self.request.session, user=self.request.user, tenant=None)
        assert (await self.socket.connect())[0]
        await self.socket.receive_json_from()
        await self.socket.send_json_to({"type": "mount", "view": MOD + ".Host", "url": "/h/"})
        assert (await self.socket.receive_json_from())["type"] == "mount"
        return self

    async def __aexit__(self, *exc):
        try:
            await self.socket.disconnect()
        finally:
            self.settings.disable()

    async def send(self, event, ref, **params):
        await self.socket.send_json_to(
            {"type": "event", "event": event, "params": params, "ref": ref}
        )

    async def queue_kid_bump(self, ref=1):
        await self.send("bump", ref, view_id="kid", _activity="panel")
        frames = await receive_until(self.socket, has_frame(ref=ref))
        assert [legacy_type(f) for f in frames if f.get("ref") == ref] == ["noop"]
        assert BUMPS == [], "the event must be queued on the child, not run"

    async def root_answers(self, ref=99):
        await self.send("ping", ref)
        frames = await receive_until(self.socket, has_frame(ref=ref))
        assert not _closed(frames)


def _kid_updates(frames):
    return [f for f in frames if legacy_type(f) == "embedded_update" and f.get("view_id") == "kid"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_queued_child_event_released_by_db_notify(monkeypatch):
    async with _Socket(monkeypatch) as ws:
        await ws.queue_kid_bump()
        await _notify()
        frames = await receive_until(ws.socket, lambda fs: bool(_kid_updates(fs)) or _closed(fs))
        frames += await drain_extra(ws.socket)
        assert not _closed(frames)
        (update,) = _kid_updates(frames)
        assert ">1</b>" in update["html"]
        assert BUMPS == [True]
        assert not [f for f in frames if f.get("type") == "error"]
        await ws.root_answers()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_queued_child_event_released_by_runtime_turn(monkeypatch):
    async with _Socket(monkeypatch) as ws:
        await ws.queue_kid_bump()
        await ws.send("open_kid", 2)
        frames = await receive_until(ws.socket, lambda fs: bool(_kid_updates(fs)) or _closed(fs))
        frames += await drain_extra(ws.socket)
        assert not _closed(frames)
        (update,) = _kid_updates(frames)
        assert ">1</b>" in update["html"]
        assert BUMPS == [True]
        await ws.root_answers()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_child_handler_showing_its_panel_releases_its_queue(monkeypatch):
    """The child's own live event releases its queue in the same round trip."""
    async with _Socket(monkeypatch) as ws:
        await ws.queue_kid_bump()
        await ws.send("show_panel", 5, view_id="kid")
        frames = await receive_until(
            ws.socket,
            lambda fs: any(">1</b>" in u["html"] for u in _kid_updates(fs)) or _closed(fs),
        )
        assert not _closed(frames)
        assert BUMPS == [True]
        await ws.root_answers()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_released_event_for_a_gone_child_is_dropped_without_closing(monkeypatch):
    async with _Socket(monkeypatch) as ws:
        # The root's drop_kid is queued on its hidden panel, the kid's bump on its own.
        await ws.send("drop_kid", 3, _activity="rootpanel")
        await receive_until(ws.socket, has_frame(ref=3))
        await ws.queue_kid_bump(ref=4)
        await _notify()
        # The root's drain runs drop_kid; the kid's queued bump then finds no kid.
        frames = await receive_until(
            ws.socket, lambda fs: any(legacy_type(f) != "noop" for f in fs) or _closed(fs)
        )
        frames += await drain_extra(ws.socket)
        assert not _closed(frames)
        assert not [f for f in frames if f.get("type") == "error"], frames
        assert BUMPS == []
        await ws.root_answers()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_revoked_root_authority_still_closes_4403(monkeypatch):
    async with _Socket(monkeypatch) as ws:
        await ws.queue_kid_bump()
        await sync_to_async(ws.request.session.delete)()
        await _notify()
        frames = await receive_until(ws.socket, _closed, allow_close=True)
        assert BUMPS == []
        closes = [f for f in frames if f.get("type") == "websocket.close"]
        assert closes and closes[0].get("code") == 4403
        assert not has_type("embedded_update")(frames)
