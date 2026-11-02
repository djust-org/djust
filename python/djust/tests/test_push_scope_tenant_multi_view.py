"""Tenant-qualified scope groups on a socket that hosts several views.

``message["group"]`` is stamped with the same tenant-qualified name the
publisher sent to, ``_channel_targets`` routes by the groups each view joined,
and a group a sibling view still names stays joined (#3252, #3333). The
existing multi-view tests use views that are not tenant-scoped; these use a
``TenantMixin`` view, so a view that moves room, a view that changes tenant and
a ``mount_batch`` each have to keep their own tenant's group names.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from asgiref.sync import sync_to_async
from django.contrib.sessions.backends.db import SessionStore
from django.test import override_settings

from djust import LiveView
from djust.decorators import event_handler
from djust.push import apush_to_view, push_scope_group_name
from djust.tenants.mixin import TenantMixin

pytest.importorskip("channels")

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.django_db(transaction=True),
    pytest.mark.tenants,
]

MOD = __name__
VIEW = f"{MOD}.TV"
HOSTS = [".example.com", "testserver"]
CONFIG = {"TENANT_RESOLVER": "subdomain"}
EVENTS: list = []
VIEWS: dict = {}
BARRIER = "barrier"


class TV(TenantMixin, LiveView):
    template = '<div dj-root dj-view="' + MOD + '.TV"><b>{{ n }}</b></div>'

    def mount(self, request, **kwargs):
        self._tag = uuid.uuid4().hex[:6]
        VIEWS[self._tag] = self
        self.n = 0
        self.push_scope = request.GET.get("room", "r1")

    @event_handler()
    def move(self, room: str = "r2", **kwargs):
        self.push_scope = room

    @event_handler()
    def swap(self, tenant: str = "globex", **kwargs):
        self.set_tenant(tenant)

    def handle_ping(self, label: str = "", **kwargs):
        self.n += 1
        EVENTS.append((label, self._tag))


@pytest.fixture(autouse=True)
def _settings():
    EVENTS.clear()
    VIEWS.clear()
    with override_settings(
        LIVEVIEW_ALLOWED_MODULES=["djust", MOD],
        DEBUG=False,
        ALLOWED_HOSTS=HOSTS,
        DJUST_CONFIG=CONFIG,
    ):
        yield


def _session_key():
    session = SessionStore()
    session.create()
    return session.session_key


async def _frame(comm, *types):
    while True:
        frame = await comm.receive_json_from(timeout=10)
        if frame.get("type") in types:
            return frame


async def _connect(host="acme.example.com"):
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    key = await sync_to_async(_session_key)()

    class _Scope:
        session_key = key

    comm = WebsocketCommunicator(
        LiveViewConsumer.as_asgi(), "/ws/", headers=[(b"host", host.encode())]
    )
    comm.scope["session"] = _Scope()
    connected, _ = await comm.connect()
    assert connected
    await comm.receive_json_from()
    return comm


async def _mount(comm, target=None, room="r1"):
    message = {"type": "mount", "view": VIEW, "url": f"/p/?room={room}"}
    if target:
        message["target_id"] = target
    await comm.send_json_to(message)
    await _frame(comm, "mount", "error")


async def _event(comm, name, target=None, params=None):
    frame = {"type": "event", "event": name, "params": params or {}, "ref": 1}
    if target:
        frame["target_id"] = target
    await comm.send_json_to(frame)
    # The patches of earlier pushes may still be queued ahead of the reply,
    # and a view's event turn runs in its own lane (#3252), so wait for the
    # reply to this event itself: its turn has then synced the push groups.
    while True:
        reply = await _frame(comm, "patch", "html_update", "noop", "error")
        if reply.get("type") == "error" or reply.get("ref") == 1:
            return reply


async def _recipients(room, tenant, label):
    """The views that applied a push to ``room`` of ``tenant``.

    The socket handles its channel messages in order, so a push to a group one
    of its views is surely in, sent right after, is the barrier: when it has
    been applied, everything sent before it has been delivered or never will be.
    """
    await apush_to_view(
        VIEW, handler="handle_ping", payload={"label": label}, scope=room, tenant=tenant
    )
    await apush_to_view(
        VIEW, handler="handle_ping", payload={"label": BARRIER + label}, scope="r1", tenant="acme"
    )
    for _ in range(1000):
        if any(seen == BARRIER + label for seen, _ in EVENTS):
            break
        await asyncio.sleep(0.01)
    else:
        raise AssertionError(f"the barrier push was never applied: {EVENTS!r}")
    return sorted(tag for seen, tag in EVENTS if seen == label)


async def test_a_view_that_moves_room_leaves_its_sibling_in_the_old_one():
    comm = await _connect()
    try:
        await _mount(comm)
        await _mount(comm, "lazy")
        page, lazy = list(VIEWS)
        assert await _recipients("r1", "acme", "both-in-r1") == sorted([page, lazy])
        await _event(comm, "move", "lazy", {"room": "r2"})
        assert await _recipients("r1", "acme", "r1") == [page]
        assert await _recipients("r2", "acme", "r2") == [lazy]
        assert await _recipients("r1", "globex", "other-tenant") == []
    finally:
        await comm.disconnect()


async def test_a_view_that_changes_tenant_leaves_its_sibling_in_the_old_tenants_group():
    comm = await _connect()
    try:
        await _mount(comm)
        await _mount(comm, "lazy")
        page, lazy = list(VIEWS)
        await _event(comm, "swap", "lazy", {"tenant": "globex"})
        assert await _recipients("r1", "acme", "acme") == [page]
        assert await _recipients("r1", "globex", "globex") == [lazy]
    finally:
        await comm.disconnect()


async def test_a_mount_batch_routes_each_view_by_room_and_tenant():
    comm = await _connect()
    try:
        await comm.send_json_to(
            {
                "type": "mount_batch",
                "views": [
                    {"view": VIEW, "url": "/p/?room=r1", "target_id": "b1"},
                    {"view": VIEW, "url": "/p/?room=r2", "target_id": "b2"},
                ],
            }
        )
        await _frame(comm, "mount_batch")
        first, second = list(VIEWS)
        assert await _recipients("r1", "acme", "r1") == [first]
        assert await _recipients("r2", "acme", "r2") == [second]
        assert await _recipients("r1", "globex", "other-tenant") == []
    finally:
        await comm.disconnect()


async def test_disconnect_leaves_the_tenants_scope_groups():
    from channels.layers import get_channel_layer

    comm = await _connect()
    await _mount(comm)
    await _mount(comm, "lazy")
    name = push_scope_group_name(VIEW, "r1", "tenant:acme")
    layer = get_channel_layer()
    assert len(layer.groups.get(name, {})) == 1  # one channel, two views
    await comm.disconnect()
    assert layer.groups.get(name, {}) == {}
