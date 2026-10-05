"""#3252: views of one page keep their own saved state.

The page view and the views mounted beside it (``dj-lazy``, ``mount_batch``)
share one URL and one Django session. Whatever a view saves must be keyed by the
view, or a sibling of the same class reads and overwrites it:

* an explicit view's ``persist="server"`` envelope (``_exposure_sessions``), and
* the signed state-snapshot token the client caches per view class
  (``storeSignedSnapshot``; see ``tests/js/view_slots_state_isolation_3252.test.js``).

Driven through the real ``LiveViewConsumer`` with a ``WebsocketCommunicator``,
reconnecting on the same Django session as a browser does.
"""

import asyncio

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.test import override_settings

from djust import LiveView, event_handler
from djust.decorators import state

pytestmark = [pytest.mark.asyncio, pytest.mark.django_db(transaction=True)]

MOD = __name__
PATH = "/page/"
#: ``(target_id or None)`` -> the newest view mounted there.
VIEWS: dict = {}


class Counter(LiveView):
    """An explicit view whose count persists on the server."""

    exposure_policy = "explicit"
    count = state(0, persist="server")
    template = '<div dj-view="' + MOD + '.Counter" dj-id="0"><b>count={{ count }}</b></div>'

    def mount(self, request, **kwargs):
        VIEWS[getattr(self, "_djust_slot_target", None)] = self

    @event_handler()
    def bump(self, **kwargs):
        self.count += 1

    def get_context_data(self, **kwargs):
        return super().get_context_data(count=self.count, **kwargs)


class OtherCounter(Counter):
    template = '<div dj-view="' + MOD + '.OtherCounter" dj-id="0"><b>count={{ count }}</b></div>'


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)
    VIEWS.clear()
    with override_settings(
        LIVEVIEW_ALLOWED_MODULES=[MOD],
        DEBUG=False,
        DJUST_CONFIG={},
        DJUST_TENANTS={},
    ):
        yield
    VIEWS.clear()


def _fresh_key():
    store = SessionStore()
    store.create()
    return store.session_key


async def _connect(key):
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    communicator.scope["session"] = SessionStore(key)
    communicator.scope["user"] = AnonymousUser()
    connected, _ = await communicator.connect()
    assert connected
    await communicator.receive_json_from(timeout=3)  # connect ack
    return communicator


async def _until_frame(communicator, *types, timeout=15.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    frames = []
    while True:
        remaining = deadline - loop.time()
        assert remaining > 0, "no frame of type %r; got %r" % (types, frames)
        frames.append(await communicator.receive_json_from(timeout=remaining))
        if frames[-1].get("type") in types:
            return frames[-1]


async def _mount(communicator, cls, target_id=None):
    frame = {"type": "mount", "view": MOD + "." + cls.__name__, "url": PATH}
    if target_id is not None:
        frame["target_id"] = target_id
    await communicator.send_json_to(frame)
    reply = await _until_frame(communicator, "mount", "error")
    assert reply["type"] == "mount", reply
    return reply


async def _bump(communicator, target_id=None):
    frame = {"type": "event", "event": "bump", "params": {}}
    if target_id is not None:
        frame["target_id"] = target_id
    await communicator.send_json_to(frame)
    reply = await _until_frame(communicator, "patch", "html_update", "noop", "error")
    assert reply["type"] != "error", reply


async def test_two_views_of_one_class_keep_their_own_persisted_state_across_a_reconnect():
    key = await sync_to_async(_fresh_key)()
    first = await _connect(key)
    try:
        await _mount(first, Counter)  # the page view
        await _mount(first, Counter, "a")
        await _mount(first, Counter, "b")
        await _bump(first)
        for _ in range(2):
            await _bump(first, "a")
        for _ in range(3):
            await _bump(first, "b")
        assert [VIEWS[t].count for t in (None, "a", "b")] == [1, 2, 3]
    finally:
        await first.disconnect()

    VIEWS.clear()
    second = await _connect(key)
    try:
        page = await _mount(second, Counter)
        a = await _mount(second, Counter, "a")
        b = await _mount(second, Counter, "b")
        assert "count=1" in page["html"], page["html"]
        assert "count=2" in a["html"], a["html"]
        assert "count=3" in b["html"], b["html"]
        assert [VIEWS[t].count for t in (None, "a", "b")] == [1, 2, 3]
    finally:
        await second.disconnect()


class Snap(LiveView):
    """A legacy view that opts in to signed state snapshots."""

    exposure_policy = "legacy"
    enable_state_snapshot = True
    template = '<div dj-view="' + MOD + '.Snap" dj-id="0"><b>n={{ n }}</b></div>'

    def mount(self, request, **kwargs):
        self.n = 0
        VIEWS[getattr(self, "_djust_slot_target", None)] = self

    @event_handler()
    def bump(self, **kwargs):
        self.n += 1


async def _bump_frame(communicator, target_id=None):
    frame = {"type": "event", "event": "bump", "params": {}}
    if target_id is not None:
        frame["target_id"] = target_id
    await communicator.send_json_to(frame)
    reply = await _until_frame(communicator, "patch", "html_update", "noop", "error")
    assert reply["type"] != "error", reply
    return reply


async def test_a_view_beside_the_page_view_mints_no_navigation_snapshot():
    """The client keeps one signed snapshot per view class, for the page view's
    back-navigation. A sibling of that class must not overwrite or revoke it."""
    key = await sync_to_async(_fresh_key)()
    socket = await _connect(key)
    try:
        page = await _mount(socket, Snap)
        slot = await _mount(socket, Snap, "a")
        assert isinstance(page.get("state_snapshot_signed"), str), page.keys()
        assert "state_snapshot_signed" not in slot, "a slot mount minted a snapshot"
        page_event = await _bump_frame(socket)
        assert isinstance(page_event.get("state_snapshot_signed"), str)
        slot_event = await _bump_frame(socket, "a")
        assert "state_snapshot_signed" not in slot_event, slot_event.keys()
    finally:
        await socket.disconnect()


async def test_a_page_snapshot_does_not_restore_a_view_beside_the_page_view():
    key = await sync_to_async(_fresh_key)()
    first = await _connect(key)
    try:
        await _mount(first, Snap)
        await _bump_frame(first)
        reply = await _bump_frame(first)
        token = reply["state_snapshot_signed"]
    finally:
        await first.disconnect()

    VIEWS.clear()
    second = await _connect(key)
    try:
        snapshot = {"view_slug": MOD + ".Snap", "state_json": token}
        frame = {"type": "mount", "view": MOD + ".Snap", "url": PATH, "state_snapshot": snapshot}
        await second.send_json_to(frame)
        restored = await _until_frame(second, "mount", "error")
        assert "n=2" in restored["html"], "the control: the page view restores its own snapshot"
        await second.send_json_to({**frame, "target_id": "a"})
        sibling = await _until_frame(second, "mount", "error")
        assert "n=0" in sibling["html"], sibling["html"]
    finally:
        await second.disconnect()


SEEN_TENANT: list = []


class Tenanted(LiveView):
    """Each view resolves its own tenant, as a ``TenantMixin`` view does."""

    exposure_policy = "legacy"
    template = '<div dj-view="' + MOD + '.Tenanted" dj-id="0"><b>x</b></div>'

    def mount(self, request, **kwargs):
        from djust.tenants.resolvers import TenantInfo

        slot = getattr(self, "_djust_slot_target", None) or "page"
        self._tenant = TenantInfo("tenant-" + slot)

    @event_handler()
    def who(self, **kwargs):
        from djust.tenants.middleware import get_current_tenant

        SEEN_TENANT.append((getattr(self, "_djust_slot_target", None), get_current_tenant().id))


async def test_each_view_runs_its_events_under_its_own_tenant():
    SEEN_TENANT.clear()
    key = await sync_to_async(_fresh_key)()
    socket = await _connect(key)
    try:
        await _mount(socket, Tenanted)
        await _mount(socket, Tenanted, "a")
        await _mount(socket, Tenanted, "b")
        for target in ("a", None, "b", "a"):
            frame = {"type": "event", "event": "who", "params": {}}
            if target:
                frame["target_id"] = target
            await socket.send_json_to(frame)
            await _until_frame(socket, "patch", "html_update", "noop", "error")
        assert SEEN_TENANT == [
            ("a", "tenant-a"),
            (None, "tenant-page"),
            ("b", "tenant-b"),
            ("a", "tenant-a"),
        ]
    finally:
        await socket.disconnect()
