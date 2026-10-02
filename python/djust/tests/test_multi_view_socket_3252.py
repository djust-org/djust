"""#3252: several LiveViews on one WebSocket are independently live.

A page can hold an eager view, ``dj-lazy`` views that hydrate later, and a
``mount_batch``. They share one socket. Before #3252 the consumer held a single
``view_instance``: hydrating a lazy sibling replaced the page view, so a click
on it got "Event rejected", a push aimed at it ran on the sibling, and a
``mount_batch`` kept only its last view live. The table in the issue (measured
with probes over a real WebSocket) is reproduced here, row by row, through the
real ``LiveViewConsumer`` driven by a ``WebsocketCommunicator``.

Views are told apart by a tag set at mount, never by ``id()``. Each step waits
for the frame it produces, never for a quiet window.
"""

import asyncio
import uuid

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.test import override_settings

from djust import LiveView, event_handler
from djust.decorators import state
from djust.presence import PresenceManager, PresenceMixin
from djust.push import apush_to_view, view_group_name

pytestmark = [pytest.mark.asyncio, pytest.mark.django_db(transaction=True)]

MOD = __name__

#: ``(what, tag)`` records: "clicked", "pushed", "unmounted".
EVENTS: list = []
#: tag -> view, for every view mounted in the test.
VIEWS: dict = {}
#: The consumers the tests' sockets run on, newest last.
CONSUMERS: list = []


class _Counted:
    """A view that counts its clicks and records the pushes it handles."""

    exposure_policy = "legacy"

    def _tagged(self):
        self._tag = type(self).__name__ + ":" + uuid.uuid4().hex[:8]
        self.count = 0
        VIEWS[self._tag] = self

    def mount(self, request, **kwargs):
        self._tagged()

    @event_handler()
    def click(self, **kwargs):
        self.count += 1
        EVENTS.append(("clicked", self._tag))

    @event_handler()
    def on_push(self, **kwargs):
        self.count += 100
        EVENTS.append(("pushed", self._tag))

    def _cleanup_on_unregister(self):
        EVENTS.append(("unregistered", self._tag))


class Page(_Counted, LiveView):
    template = '<div dj-root dj-view="' + MOD + '.Page"><b>page {{ count }}</b></div>'

    @event_handler()
    def page_only(self, **kwargs):
        EVENTS.append(("page_only", self._tag))


class Lazy(_Counted, LiveView):
    template = '<div dj-root dj-view="' + MOD + '.Lazy"><b>lazy {{ count }}</b></div>'

    @event_handler()
    def lazy_only(self, **kwargs):
        EVENTS.append(("lazy_only", self._tag))


class Lazy2(_Counted, LiveView):
    template = '<div dj-root dj-view="' + MOD + '.Lazy2"><b>lazy2 {{ count }}</b></div>'


class Lazy3(_Counted, LiveView):
    template = '<div dj-root dj-view="' + MOD + '.Lazy3"><b>lazy3 {{ count }}</b></div>'


@pytest.fixture(autouse=True)
def setup():
    EVENTS.clear()
    VIEWS.clear()
    CONSUMERS.clear()
    with override_settings(LIVEVIEW_ALLOWED_MODULES=["djust", __name__], DEBUG=False):
        yield
    VIEWS.clear()
    CONSUMERS.clear()


# --------------------------------------------------------------------------- #
# Harness
# --------------------------------------------------------------------------- #


def _fresh_key():
    store = SessionStore()
    store.create()
    return store.session_key


async def _until(predicate, what):
    for _ in range(500):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("timed out waiting for " + what)


def _one(cls):
    tags = [tag for tag, view in VIEWS.items() if type(view) is cls]
    assert len(tags) == 1, (cls.__name__, tags)
    return tags[0]


def _members(group):
    from channels.layers import get_channel_layer

    return list(get_channel_layer().groups.get(group, {}).keys())


def _group(cls):
    return view_group_name(MOD + "." + cls.__name__)


async def _frames_until(communicator, *types, timeout=15.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    frames = []
    while True:
        remaining = deadline - loop.time()
        assert remaining > 0, "no frame of type %r; got %r" % (types, frames)
        frame = await communicator.receive_json_from(timeout=remaining)
        frames.append(frame)
        if frame.get("type") in types:
            return frames


async def _connect(session=None, user=None):
    pytest.importorskip("channels")
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    class _Recorded(LiveViewConsumer):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            CONSUMERS.append(self)

    communicator = WebsocketCommunicator(_Recorded.as_asgi(), "/ws/")
    communicator.scope["session"] = session or SessionStore(await sync_to_async(_fresh_key)())
    communicator.scope["user"] = user or AnonymousUser()
    communicator.scope["tenant"] = None
    connected, _ = await communicator.connect()
    assert connected
    await communicator.receive_json_from(timeout=2)  # connect ack
    return communicator


async def _close(communicator):
    try:
        await communicator.disconnect()
    except (asyncio.CancelledError, Exception):  # noqa: BLE001 - teardown only
        pass


async def _mount_page(communicator, cls=Page):
    await communicator.send_json_to(
        {"type": "mount", "view": MOD + "." + cls.__name__, "url": "/page/"}
    )
    frames = await _frames_until(communicator, "mount", "error")
    assert frames[-1]["type"] == "mount", frames
    assert "target_id" not in frames[-1]
    return frames[-1]


async def _hydrate(communicator, cls, target_id):
    """What the stock client sends when a ``dj-lazy`` view hydrates."""
    await communicator.send_json_to(
        {
            "type": "mount",
            "view": MOD + "." + cls.__name__,
            "url": "/page/",
            "target_id": target_id,
        }
    )
    frames = await _frames_until(communicator, "mount", "error")
    assert frames[-1]["type"] == "mount", frames
    assert frames[-1]["target_id"] == target_id
    return frames[-1]


async def _mount_batch(communicator, *entries):
    await communicator.send_json_to(
        {
            "type": "mount_batch",
            "views": [
                {"view": MOD + "." + cls.__name__, "url": "/page/", "target_id": target}
                for cls, target in entries
            ],
        }
    )
    frames = await _frames_until(communicator, "mount_batch")
    return frames[-1]


async def _event(communicator, event, target_id=None, params=None, ref=1):
    frame = {"type": "event", "event": event, "params": params or {}, "ref": ref}
    if target_id is not None:
        frame["target_id"] = target_id
    await communicator.send_json_to(frame)
    frames = await _frames_until(communicator, "patch", "html_update", "noop", "error")
    return frames[-1]


def _html_of(frame):
    """The text a patch / html_update frame writes (the counter's value)."""
    import json

    return json.dumps(frame.get("patches", frame.get("html")))


# --------------------------------------------------------------------------- #
# The issue's table: an eager page view, then a lazy sibling hydrates
# --------------------------------------------------------------------------- #


async def test_hydrating_a_lazy_sibling_does_not_replace_the_page_view():
    communicator = await _connect()
    try:
        await _mount_page(communicator)
        page = _one(Page)
        assert len(_members(_group(Page))) == 1

        await _hydrate(communicator, Lazy, "lazy-1")
        lazy = _one(Lazy)

        # Both are mounted and each joined its own view group.
        assert len(_members(_group(Page))) == 1
        assert len(_members(_group(Lazy))) == 1
        assert ("unregistered", page) not in EVENTS
        assert VIEWS[page].count == 0
        consumer = CONSUMERS[-1]
        assert consumer.view_instance is VIEWS[page]
        assert consumer._slot_map()["lazy-1"].facade.view_instance is VIEWS[lazy]
    finally:
        await _close(communicator)


async def test_a_click_on_the_page_view_still_runs_after_a_lazy_sibling_hydrates():
    """Issue table row 1: the click used to be answered "Event rejected"."""
    communicator = await _connect()
    try:
        await _mount_page(communicator)
        await _hydrate(communicator, Lazy, "lazy-1")
        page, lazy = _one(Page), _one(Lazy)

        reply = await _event(communicator, "click")
        assert reply["type"] in ("patch", "html_update"), reply
        assert "target_id" not in reply  # the page view's frames are unchanged
        assert EVENTS == [("clicked", page)]
        assert VIEWS[page].count == 1
        assert VIEWS[lazy].count == 0
    finally:
        await _close(communicator)


async def test_a_click_in_the_lazy_view_runs_on_it_and_its_reply_is_addressed_to_it():
    communicator = await _connect()
    try:
        await _mount_page(communicator)
        await _hydrate(communicator, Lazy, "lazy-1")
        page, lazy = _one(Page), _one(Lazy)

        reply = await _event(communicator, "click", target_id="lazy-1")
        assert reply["type"] in ("patch", "html_update"), reply
        assert reply["target_id"] == "lazy-1"
        assert reply["ref"] == 1
        assert EVENTS == [("clicked", lazy)]
        assert VIEWS[lazy].count == 1
        assert VIEWS[page].count == 0
    finally:
        await _close(communicator)


async def test_each_view_has_its_own_wire_version():
    communicator = await _connect()
    try:
        mount = await _mount_page(communicator)
        slot_mount = await _hydrate(communicator, Lazy, "lazy-1")
        first = await _event(communicator, "click", ref=1)
        second = await _event(communicator, "click", target_id="lazy-1", ref=2)
        third = await _event(communicator, "click", ref=3)
        # Per view the sequence is mount, +1, +2...: the client keeps one cursor
        # per container.
        assert first["version"] == mount["version"] + 1
        assert second["version"] == slot_mount["version"] + 1
        assert third["version"] == mount["version"] + 2
    finally:
        await _close(communicator)


async def test_a_handler_only_the_page_view_has_is_refused_in_the_lazy_view():
    """Routing by address, not by luck: the page view's handler is not run for
    an event the lazy view was sent."""
    communicator = await _connect()
    try:
        await _mount_page(communicator)
        await _hydrate(communicator, Lazy, "lazy-1")
        reply = await _event(communicator, "page_only", target_id="lazy-1")
        assert reply["type"] == "error"
        assert reply["target_id"] == "lazy-1"
        assert EVENTS == []
        reply = await _event(communicator, "lazy_only")
        assert reply["type"] == "error"
        assert EVENTS == []
    finally:
        await _close(communicator)


async def test_an_event_for_a_view_that_is_not_mounted_is_refused_not_run_elsewhere():
    communicator = await _connect()
    try:
        await _mount_page(communicator)
        for forged in ("never-mounted", "x" * 500, 7, ["lazy-1"]):
            reply = await _event(communicator, "click", target_id=forged, ref=9)
            assert reply["type"] == "error", forged
            assert reply["ref"] == 9
        assert EVENTS == []
        assert VIEWS[_one(Page)].count == 0
    finally:
        await _close(communicator)


# --------------------------------------------------------------------------- #
# Pushes
# --------------------------------------------------------------------------- #


async def test_a_push_to_the_page_view_runs_on_the_page_view():
    """Issue table row 2: it used to run on the lazy view (misrouted)."""
    communicator = await _connect()
    try:
        await _mount_page(communicator)
        await _hydrate(communicator, Lazy, "lazy-1")
        page, lazy = _one(Page), _one(Lazy)

        await apush_to_view(MOD + ".Page", handler="on_push")
        frames = await _frames_until(communicator, "patch", "html_update")
        assert EVENTS == [("pushed", page)]
        assert "target_id" not in frames[-1]
        assert frames[-1]["source"] == "broadcast"
        assert VIEWS[lazy].count == 0
    finally:
        await _close(communicator)


async def test_a_push_to_the_lazy_view_runs_on_it_and_is_addressed_to_it():
    communicator = await _connect()
    try:
        await _mount_page(communicator)
        await _hydrate(communicator, Lazy, "lazy-1")
        page, lazy = _one(Page), _one(Lazy)

        await apush_to_view(MOD + ".Lazy", handler="on_push")
        frames = await _frames_until(communicator, "patch", "html_update")
        assert EVENTS == [("pushed", lazy)]
        assert frames[-1]["target_id"] == "lazy-1"
        assert VIEWS[page].count == 0
    finally:
        await _close(communicator)


async def test_pushes_reach_each_of_several_lazy_views():
    """Issue table row 4: with two lazy views a push to the first ran on the
    second."""
    communicator = await _connect()
    try:
        await _mount_page(communicator)
        await _hydrate(communicator, Lazy, "lazy-1")
        await _hydrate(communicator, Lazy2, "lazy-2")
        lazy, lazy2 = _one(Lazy), _one(Lazy2)

        await apush_to_view(MOD + ".Lazy", handler="on_push")
        frames = await _frames_until(communicator, "patch", "html_update")
        assert frames[-1]["target_id"] == "lazy-1"
        await apush_to_view(MOD + ".Lazy2", handler="on_push")
        frames = await _frames_until(communicator, "patch", "html_update")
        assert frames[-1]["target_id"] == "lazy-2"
        assert EVENTS == [("pushed", lazy), ("pushed", lazy2)]
    finally:
        await _close(communicator)


async def test_a_push_reaches_every_view_of_the_class_it_was_sent_to():
    """Two views of one class share a view group: one push, one turn each."""
    communicator = await _connect()
    try:
        await _mount_page(communicator, Lazy)
        await _hydrate(communicator, Lazy, "again")
        await apush_to_view(MOD + ".Lazy", handler="on_push")
        first = await _frames_until(communicator, "patch", "html_update")
        second = await _frames_until(communicator, "patch", "html_update")
        assert sorted("target_id" in f[-1] for f in (first, second)) == [False, True]
        assert [e[0] for e in EVENTS] == ["pushed", "pushed"]
        assert len({e[1] for e in EVENTS}) == 2
    finally:
        await _close(communicator)


async def test_a_push_without_a_group_is_dropped_when_several_views_are_mounted():
    """A raw channel message names no group: applying it to the wrong view would
    change the wrong view's state, so it is dropped."""
    from channels.layers import get_channel_layer

    communicator = await _connect()
    try:
        await _mount_page(communicator)
        await _hydrate(communicator, Lazy, "lazy-1")
        await get_channel_layer().group_send(
            _group(Page),
            {"type": "server_push", "state": {"count": 5}, "handler": "on_push", "payload": {}},
        )
        await asyncio.sleep(0.2)
        assert EVENTS == []
        assert VIEWS[_one(Page)].count == 0
        assert VIEWS[_one(Lazy)].count == 0
    finally:
        await _close(communicator)


# --------------------------------------------------------------------------- #
# mount_batch
# --------------------------------------------------------------------------- #


async def test_a_mount_batch_keeps_every_view_live():
    """Issue: only the most recently mounted view of a batch was live."""
    communicator = await _connect()
    try:
        await _mount_page(communicator)
        reply = await _mount_batch(communicator, (Lazy, "b-1"), (Lazy2, "b-2"), (Lazy3, "b-3"))
        assert [v["target_id"] for v in reply["views"]] == ["b-1", "b-2", "b-3"]
        assert reply["failed"] == []
        page, lazy, lazy2, lazy3 = _one(Page), _one(Lazy), _one(Lazy2), _one(Lazy3)

        for target, tag in (("b-1", lazy), ("b-2", lazy2), ("b-3", lazy3), (None, page)):
            reply = await _event(communicator, "click", target_id=target)
            assert reply["type"] in ("patch", "html_update"), (target, reply)
            if target is not None:
                assert reply["target_id"] == target
            assert EVENTS[-1] == ("clicked", tag)
        assert [e[1] for e in EVENTS] == [lazy, lazy2, lazy3, page]
    finally:
        await _close(communicator)


async def test_a_mount_batch_does_not_replace_the_page_view():
    communicator = await _connect()
    try:
        await _mount_page(communicator)
        page = _one(Page)
        await _mount_batch(communicator, (Lazy, "b-1"))
        assert ("unregistered", page) not in EVENTS
        assert len(_members(_group(Page))) == 1
        reply = await _event(communicator, "click")
        assert reply["type"] in ("patch", "html_update")
        assert EVENTS == [("clicked", page)]
    finally:
        await _close(communicator)


async def test_a_batch_only_socket_answers_an_event_without_a_target_on_the_last_view():
    """A client that predates ``target_id`` addresses the view it mounted last,
    as it always did."""
    communicator = await _connect()
    try:
        await _mount_batch(communicator, (Lazy, "b-1"), (Lazy2, "b-2"))
        reply = await _event(communicator, "click")
        assert reply["type"] in ("patch", "html_update")
        assert EVENTS == [("clicked", _one(Lazy2))]
    finally:
        await _close(communicator)


async def test_every_batch_view_joins_and_leaves_its_own_groups():
    communicator = await _connect()
    try:
        await _mount_batch(communicator, (Lazy, "b-1"), (Lazy2, "b-2"))
        assert len(_members(_group(Lazy))) == 1
        assert len(_members(_group(Lazy2))) == 1
    finally:
        await _close(communicator)
    assert _members(_group(Lazy)) == []
    assert _members(_group(Lazy2)) == []


# --------------------------------------------------------------------------- #
# Teardown of one view leaves the others live
# --------------------------------------------------------------------------- #


async def test_unmounting_one_view_leaves_the_others_live():
    communicator = await _connect()
    try:
        await _mount_page(communicator)
        await _hydrate(communicator, Lazy, "lazy-1")
        await _hydrate(communicator, Lazy2, "lazy-2")
        page, lazy, lazy2 = _one(Page), _one(Lazy), _one(Lazy2)

        await communicator.send_json_to({"type": "unmount", "target_id": "lazy-1"})
        await _until(
            lambda: ("unregistered", lazy) not in EVENTS and _members(_group(Lazy)) == [],
            "the unmount",
        )
        assert _members(_group(Lazy)) == []
        assert len(_members(_group(Page))) == 1
        assert len(_members(_group(Lazy2))) == 1

        # The unmounted view no longer answers; the others still do.
        reply = await _event(communicator, "click", target_id="lazy-1")
        assert reply["type"] == "error"
        assert (await _event(communicator, "click", target_id="lazy-2"))["type"] != "error"
        assert (await _event(communicator, "click"))["type"] != "error"
        assert EVENTS == [("clicked", lazy2), ("clicked", page)]

        # Unmounting a view that is not mounted is a no-op.
        await communicator.send_json_to({"type": "unmount", "target_id": "lazy-1"})
        await apush_to_view(MOD + ".Page", handler="on_push")
        await _frames_until(communicator, "patch", "html_update")
    finally:
        await _close(communicator)


async def test_hydrating_the_same_container_again_replaces_only_that_view():
    communicator = await _connect()
    try:
        await _mount_page(communicator)
        await _hydrate(communicator, Lazy, "lazy-1")
        await _hydrate(communicator, Lazy2, "lazy-2")
        first = _one(Lazy)

        await _hydrate(communicator, Lazy3, "lazy-1")
        assert _members(_group(Lazy)) == []
        assert len(_members(_group(Lazy3))) == 1
        assert len(_members(_group(Lazy2))) == 1
        assert len(_members(_group(Page))) == 1
        await _event(communicator, "click", target_id="lazy-1")
        assert EVENTS[-1] == ("clicked", _one(Lazy3))
        assert VIEWS[first].count == 0
    finally:
        await _close(communicator)


async def test_a_page_mount_replaces_every_view_on_the_socket():
    """The page view's own replacement (a ``mount`` without a ``target_id``, or
    ``live_redirect``) still tears everything down."""
    communicator = await _connect()
    try:
        await _mount_page(communicator)
        await _hydrate(communicator, Lazy, "lazy-1")
        await _mount_page(communicator, Lazy2)
        assert _members(_group(Page)) == []
        assert _members(_group(Lazy)) == []
        assert len(_members(_group(Lazy2))) == 1
        assert CONSUMERS[-1]._slot_map() == {}
    finally:
        await _close(communicator)


async def test_disconnect_leaves_every_view_s_groups():
    communicator = await _connect()
    try:
        await _mount_page(communicator)
        await _hydrate(communicator, Lazy, "lazy-1")
        await _mount_batch(communicator, (Lazy2, "b-2"))
    finally:
        await _close(communicator)
    for cls in (Page, Lazy, Lazy2):
        assert _members(_group(cls)) == []
    assert CONSUMERS[-1]._slot_map() == {}


# --------------------------------------------------------------------------- #
# Authorization differs per view
# --------------------------------------------------------------------------- #


class Guarded(_Counted, LiveView):
    """A view only a signed-in user may mount."""

    login_required = True
    template = '<div dj-root dj-view="' + MOD + '.Guarded"><b>guarded {{ count }}</b></div>'


class ExplicitSlot(LiveView):
    """An explicit-exposure view (ADR-038): every event is authorized afresh
    against the session it was mounted under."""

    exposure_policy = "explicit"
    template = "<div dj-root>{{ count }}</div>"

    count = state(0, persist="server")

    def get_context_data(self, **kwargs):
        return super().get_context_data(count=self.count, **kwargs)

    @event_handler()
    def bump(self, **kwargs):
        self.count += 1
        EVENTS.append(("bumped", "explicit"))


async def test_a_view_the_user_may_not_mount_is_refused_and_the_socket_stays_live():
    """Authorization is per view: a sibling that needs a login does not get one,
    and does not take the page view's socket down with it (#291)."""
    communicator = await _connect()  # anonymous
    try:
        await _mount_page(communicator)
        await communicator.send_json_to(
            {
                "type": "mount",
                "view": MOD + ".Guarded",
                "url": "/page/",
                "target_id": "guarded",
            }
        )
        frames = await _frames_until(communicator, "navigate", "error")
        assert frames[-1]["type"] == "navigate"
        assert not _members(_group(Guarded))
        assert CONSUMERS[-1]._slot_map() == {}
        # The page view is still live on the same socket, and the refused view's
        # address answers nothing.
        reply = await _event(communicator, "click")
        assert reply["type"] in ("patch", "html_update")
        reply = await _event(communicator, "click", target_id="guarded")
        assert reply["type"] == "error"
        assert [e[0] for e in EVENTS] == ["clicked"]
    finally:
        await _close(communicator)


async def test_a_batch_entry_the_user_may_not_mount_fails_alone():
    communicator = await _connect()
    try:
        await _mount_page(communicator)
        await communicator.send_json_to(
            {
                "type": "mount_batch",
                "views": [
                    {"view": MOD + ".Guarded", "url": "/page/", "target_id": "g"},
                    {"view": MOD + ".Lazy", "url": "/page/", "target_id": "ok"},
                ],
            }
        )
        reply = (await _frames_until(communicator, "mount_batch"))[-1]
        assert [v["target_id"] for v in reply["views"]] == ["ok"]
        assert reply["navigate"][0]["target_id"] == "g"
        assert set(CONSUMERS[-1]._slot_map()) == {"ok"}
        assert (await _event(communicator, "click", target_id="ok"))["type"] != "error"
    finally:
        await _close(communicator)


async def test_an_explicit_view_in_a_slot_is_authorized_on_its_own():
    """Explicit exposure works in a slot, and revoking it ends that view only."""
    from djust.tests.test_exposure_runtime import make_request

    request = await sync_to_async(make_request)()
    communicator = await _connect(session=request.session, user=request.user)
    try:
        await _mount_page(communicator)
        await communicator.send_json_to(
            {
                "type": "mount",
                "view": MOD + ".ExplicitSlot",
                "url": "/page/",
                "target_id": "explicit",
            }
        )
        mounted = (await _frames_until(communicator, "mount", "error"))[-1]
        assert mounted["type"] == "mount", mounted
        assert mounted["target_id"] == "explicit"

        reply = await _event(communicator, "bump", target_id="explicit")
        assert reply["type"] in ("patch", "html_update"), reply
        assert reply["target_id"] == "explicit"
        assert EVENTS == [("bumped", "explicit")]

        # The session the explicit view was mounted under is revoked: its next
        # event is refused, and that view is torn down. The page view (legacy,
        # not bound to the session) and the socket are not.
        await sync_to_async(request.session.delete)()
        await communicator.send_json_to(
            {"type": "event", "event": "bump", "params": {}, "target_id": "explicit", "ref": 5}
        )
        frames = await _frames_until(communicator, "error")
        assert frames[-1]["code"] == "permission_denied"
        assert frames[-1]["target_id"] == "explicit"
        await _until(
            lambda: "explicit" not in CONSUMERS[-1]._slot_map(), "the refused view's teardown"
        )
        assert EVENTS == [("bumped", "explicit")]
        reply = await _event(communicator, "click")
        assert reply["type"] in ("patch", "html_update")
    finally:
        await _close(communicator)


# --------------------------------------------------------------------------- #
# Presence, ticks, db_notify, async work: each routed to its own view
# --------------------------------------------------------------------------- #


class Present(PresenceMixin, _Counted, LiveView):
    presence_key = "mv3252"
    template = '<div dj-root dj-view="' + MOD + '.Present"><b>present {{ count }}</b></div>'

    def mount(self, request, **kwargs):
        self._tagged()
        self.track_presence(meta={})

    def get_presence_user_id(self):
        return "user-" + self._tag


async def test_presence_follows_each_view_s_own_lifetime():
    """Every view tracks its own presence; unmounting one untracks only it, and
    the ping refreshes all of them."""
    communicator = await _connect()
    try:
        await _mount_page(communicator, Present)
        await _hydrate(communicator, Lazy, "lazy-1")
        await communicator.send_json_to(
            {
                "type": "mount",
                "view": MOD + ".Present",
                "url": "/page/",
                "target_id": "present-2",
            }
        )
        await _frames_until(communicator, "mount", "error")
        assert len(await sync_to_async(PresenceManager.list_presences)("mv3252")) == 2

        # A ping heartbeats every mounted view's presence without error.
        await communicator.send_json_to({"type": "ping"})
        await _frames_until(communicator, "pong")

        await communicator.send_json_to({"type": "unmount", "target_id": "present-2"})
        await _until(
            lambda: "present-2" not in CONSUMERS[-1]._slot_map(),
            "the unmount",
        )
        remaining = await sync_to_async(PresenceManager.list_presences)("mv3252")
        assert len(remaining) == 1
        assert remaining[0]["id"] == "user-" + _one_of(Present, first=True)
    finally:
        await _close(communicator)
    assert await sync_to_async(PresenceManager.list_presences)("mv3252") == []


def _one_of(cls, first=False):
    tags = [tag for tag, view in VIEWS.items() if type(view) is cls]
    return tags[0] if first else tags


class Ticking(_Counted, LiveView):
    tick_interval = 30
    template = '<div dj-root dj-view="' + MOD + '.Ticking"><b>ticks {{ count }}</b></div>'

    def handle_tick(self):
        self.count += 1


async def test_a_view_in_a_slot_ticks_and_its_ticks_are_addressed_to_it():
    communicator = await _connect()
    try:
        await _mount_page(communicator)
        await _hydrate(communicator, Ticking, "ticking")
        frames = await _frames_until(communicator, "patch", "html_update")
        assert frames[-1]["source"] == "tick"
        assert frames[-1]["target_id"] == "ticking"
        assert VIEWS[_one(Page)].count == 0
        # Unmounting stops that view's ticking and nothing else.
        await communicator.send_json_to({"type": "unmount", "target_id": "ticking"})
        await _until(lambda: "ticking" not in CONSUMERS[-1]._slot_map(), "the unmount")
        assert CONSUMERS[-1]._tick_task is None
    finally:
        await _close(communicator)


class Notified(_Counted, LiveView):
    template = '<div dj-root dj-view="' + MOD + '.Notified"><b>notified {{ count }}</b></div>'

    def mount(self, request, **kwargs):
        self._tagged()
        self._listen_channels = {"mv3252"}

    def handle_info(self, message):
        self.count += 1
        EVENTS.append(("notified", self._tag))


async def test_a_db_notify_reaches_only_the_views_that_listen():
    from channels.layers import get_channel_layer

    communicator = await _connect()
    try:
        await _mount_page(communicator)
        await _hydrate(communicator, Notified, "notified")
        await _hydrate(communicator, Lazy, "lazy-1")
        await get_channel_layer().group_send(
            "djust_db_notify_mv3252",
            {"type": "db_notify", "channel": "mv3252", "payload": {"id": 1}},
        )
        frames = await _frames_until(communicator, "patch", "html_update")
        assert frames[-1]["target_id"] == "notified"
        assert EVENTS == [("notified", _one(Notified))]
    finally:
        await _close(communicator)
    assert _members("djust_db_notify_mv3252") == []


class Working(_Counted, LiveView):
    template = '<div dj-root dj-view="' + MOD + '.Working"><b>done {{ count }}</b></div>'

    @event_handler()
    def start(self, **kwargs):
        self.start_async(self._work, name="work")

    def _work(self):
        self.count += 1
        EVENTS.append(("worked", self._tag))


async def test_background_work_started_in_a_slot_renders_into_that_slot():
    communicator = await _connect()
    try:
        await _mount_page(communicator)
        await _hydrate(communicator, Working, "working")
        await communicator.send_json_to(
            {"type": "event", "event": "start", "params": {}, "target_id": "working", "ref": 3}
        )
        frames = await _frames_until(communicator, "noop", "patch", "html_update")
        assert frames[-1]["target_id"] == "working"
        async_frames = await _frames_until(communicator, "patch", "html_update")
        assert async_frames[-1]["source"] == "async"
        assert async_frames[-1]["target_id"] == "working"
        assert EVENTS == [("worked", _one(Working))]
        assert VIEWS[_one(Page)].count == 0
    finally:
        await _close(communicator)


# --------------------------------------------------------------------------- #
# The rest of the frame verbs are routed too
# --------------------------------------------------------------------------- #


async def test_url_change_and_recovery_are_addressed_per_view():
    communicator = await _connect()
    try:
        await _mount_page(communicator)
        await _hydrate(communicator, Lazy, "lazy-1")
        await _event(communicator, "click", target_id="lazy-1")

        # The slot's own recovery HTML, stamped for its container.
        await communicator.send_json_to({"type": "request_html", "target_id": "lazy-1"})
        frames = await _frames_until(communicator, "html_recovery", "error")
        assert frames[-1]["type"] == "html_recovery"
        assert frames[-1]["target_id"] == "lazy-1"
        assert "lazy 1" in frames[-1]["html"]

        # An unmounted address is refused, not answered with the page's.
        await communicator.send_json_to({"type": "request_html", "target_id": "gone"})
        frames = await _frames_until(communicator, "html_recovery", "error")
        assert frames[-1]["type"] == "error"
    finally:
        await _close(communicator)


async def test_the_number_of_views_on_a_socket_is_bounded():
    communicator = await _connect()
    try:
        with override_settings(LIVEVIEW_CONFIG={"max_views_per_connection": 2}):
            from djust.config import config

            config.reset()
            try:
                await _mount_page(communicator)
                await _hydrate(communicator, Lazy, "a")
                await _hydrate(communicator, Lazy2, "b")
                await communicator.send_json_to(
                    {
                        "type": "mount",
                        "view": MOD + ".Lazy3",
                        "url": "/page/",
                        "target_id": "c",
                    }
                )
                frames = await _frames_until(communicator, "mount", "error")
                assert frames[-1]["type"] == "error"
                assert set(CONSUMERS[-1]._slot_map()) == {"a", "b"}
                # Re-hydrating an address that is already mounted is not a new view.
                await _hydrate(communicator, Lazy3, "a")
                assert set(CONSUMERS[-1]._slot_map()) == {"a", "b"}
            finally:
                config.reset()
    finally:
        await _close(communicator)


@pytest.mark.parametrize(
    "target", ["", " ", "a b", "x" * 201, 7, ["a"], {"a": 1}, "a\nb", '"quoted"']
)
async def test_an_unusable_target_id_is_refused_and_replaces_nothing(target):
    """A mount whose ``target_id`` is present but unusable is never taken for a
    page mount: it would tear down the page view."""
    communicator = await _connect()
    try:
        await _mount_page(communicator)
        await communicator.send_json_to(
            {"type": "mount", "view": MOD + ".Lazy", "url": "/page/", "target_id": target}
        )
        frames = await _frames_until(communicator, "mount", "error")
        assert frames[-1]["type"] == "error", frames[-1]
        assert len(_members(_group(Page))) == 1
        assert CONSUMERS[-1]._slot_map() == {}
        assert (await _event(communicator, "click"))["type"] != "error"
    finally:
        await _close(communicator)


async def test_a_push_from_one_view_to_another_on_the_same_socket_is_delivered():
    """The #1677 self-broadcast skip is per view: a handler of one view pushing
    to another view of the same socket is not its own echo."""
    communicator = await _connect()
    try:
        await _mount_page(communicator)
        await _hydrate(communicator, Lazy, "lazy-1")
        page, lazy = _one(Page), _one(Lazy)

        # A push the page view's handler sends, tagged as that view's own
        # (what ``event_context`` does), to the lazy view's group.
        from djust.push import origin_channel

        consumer = CONSUMERS[-1]
        token = origin_channel.set(consumer._origin_token())
        try:
            await apush_to_view(MOD + ".Lazy", handler="on_push")
        finally:
            origin_channel.reset(token)
        frames = await _frames_until(communicator, "patch", "html_update")
        assert frames[-1]["target_id"] == "lazy-1"
        assert EVENTS == [("pushed", lazy)]

        # The lazy view pushing to itself IS its own echo: skipped.
        slot = consumer._slot_map()["lazy-1"].facade
        token = origin_channel.set(slot._origin_token())
        try:
            await apush_to_view(MOD + ".Lazy", handler="on_push")
        finally:
            origin_channel.reset(token)
        await asyncio.sleep(0.2)
        assert EVENTS == [("pushed", lazy)]
        assert VIEWS[page].count == 0
    finally:
        await _close(communicator)


# --------------------------------------------------------------------------- #
# Saved state: each view has its own
# --------------------------------------------------------------------------- #


class SnapPage(_Counted, LiveView):
    enable_state_snapshot = True
    template = '<div dj-root dj-view="' + MOD + '.SnapPage"><b>page {{ count }}</b></div>'


class SnapLazy(_Counted, LiveView):
    enable_state_snapshot = True
    template = '<div dj-root dj-view="' + MOD + '.SnapLazy"><b>lazy {{ count }}</b></div>'


async def test_the_page_view_and_a_lazy_view_keep_separate_saved_state():
    """They share one URL and one session. Without a scope of their own, each
    would write the other's state under ``liveview_<path>``."""
    key = await sync_to_async(_fresh_key)()
    communicator = await _connect(session=SessionStore(key))
    try:
        await _mount_page(communicator, SnapPage)
        await _hydrate(communicator, SnapLazy, "lazy-1")
        await _event(communicator, "click")
        await _event(communicator, "click", target_id="lazy-1")
        await _event(communicator, "click", target_id="lazy-1")
        await _event(communicator, "on_push", target_id="lazy-1")
    finally:
        await _close(communicator)

    def saved():
        store = SessionStore(key)
        return {
            k: v.get("count")
            for k, v in store.items()
            if k.startswith("liveview_") and isinstance(v, dict) and "count" in v
        }

    assert await sync_to_async(saved)() == {
        "liveview_/page/": 1,
        "liveview_slot:lazy-1:/page/": 102,
    }


class ExplicitPage(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root>{{ count }}</div>"

    count = state(0, persist="server")

    def get_context_data(self, **kwargs):
        return super().get_context_data(count=self.count, **kwargs)

    @event_handler()
    def bump(self, **kwargs):
        self.count += 1


async def test_explicit_views_beside_each_other_each_restore_their_own_state():
    """Two explicit views with persisted server state share one session; each
    saves, and each gets its own back on the next mount."""
    from djust.tests.test_exposure_runtime import make_request

    request = await sync_to_async(make_request)()
    communicator = await _connect(session=request.session, user=request.user)
    try:
        await _mount_page(communicator, ExplicitPage)
        await _hydrate(communicator, ExplicitSlot, "explicit")
        for target, times in ((None, 1), ("explicit", 3), (None, 1)):
            for _ in range(times):
                reply = await _event(communicator, "bump", target_id=target)
                assert reply["type"] in ("patch", "html_update"), reply
    finally:
        await _close(communicator)

    # A new socket on the same session: both views mount with their saved count.
    communicator = await _connect(
        session=SessionStore(request.session.session_key), user=request.user
    )
    try:
        page = await _mount_page(communicator, ExplicitPage)
        slot = await _hydrate(communicator, ExplicitSlot, "explicit")
        assert page["html"].strip() == "2", page["html"]
        assert slot["html"].strip() == "3", slot["html"]
    finally:
        await _close(communicator)


class SameUser(PresenceMixin, _Counted, LiveView):
    presence_key = "mv3252-same"
    template = '<div dj-root dj-view="' + MOD + '.SameUser"><b>same {{ count }}</b></div>'

    def mount(self, request, **kwargs):
        self._tagged()
        self.track_presence(meta={})

    def get_presence_user_id(self):
        return "one-user"

    def handle_presence_join(self, presence):
        EVENTS.append(("joined", presence["id"]))

    def handle_presence_leave(self, presence):
        EVENTS.append(("left", presence["id"]))


async def test_hydrating_a_container_again_does_not_make_the_user_leave_and_rejoin():
    """Per-connection presence (#3254): the replacement view joins as a second
    connection before the replaced one leaves, so the user is present throughout
    and no leave/join fires; the leave comes with their last view."""
    communicator = await _connect()
    try:
        await _hydrate(communicator, SameUser, "same")
        assert EVENTS == [("joined", "one-user")]
        await _hydrate(communicator, SameUser, "same")
        assert [e[0] for e in EVENTS] == ["joined"]
        assert len(await sync_to_async(PresenceManager.list_presences)("mv3252-same")) == 1

        await communicator.send_json_to({"type": "unmount", "target_id": "same"})
        await _until(lambda: ("left", "one-user") in EVENTS, "the leave after the last view")
        assert [e[0] for e in EVENTS] == ["joined", "left"]
    finally:
        await _close(communicator)


# --------------------------------------------------------------------------- #
# Views that share a channel-layer group (review of #3333, I1)
# --------------------------------------------------------------------------- #


async def _wait_gone(communicator, target):
    await communicator.send_json_to({"type": "unmount", "target_id": target})
    await _until(lambda: target not in CONSUMERS[-1]._slot_map(), "the unmount of " + target)


async def test_unmounting_a_same_class_view_leaves_the_sibling_its_push_group():
    """Two views of one class join one view group; leaving it for the unmounted
    view must not silence the view that remains."""
    communicator = await _connect()
    try:
        await _mount_page(communicator, Lazy)
        await _hydrate(communicator, Lazy, "again")
        assert len(_members(_group(Lazy))) == 1  # one channel, two views
        await _wait_gone(communicator, "again")
        assert len(_members(_group(Lazy))) == 1
        EVENTS.clear()
        await apush_to_view(MOD + ".Lazy", handler="on_push")
        frames = await _frames_until(communicator, "patch", "html_update")
        assert "target_id" not in frames[-1]  # the page view answered
        assert [e[0] for e in EVENTS] == ["pushed"]
    finally:
        await _close(communicator)
    assert _members(_group(Lazy)) == []  # the last view's leave still leaves


async def test_replacing_a_slot_with_another_class_keeps_the_page_view_live():
    communicator = await _connect()
    try:
        await _mount_page(communicator, Lazy)
        await _hydrate(communicator, Lazy, "again")
        await _hydrate(communicator, Lazy2, "again")  # replaces only that slot
        assert len(_members(_group(Lazy))) == 1
        EVENTS.clear()
        await apush_to_view(MOD + ".Lazy", handler="on_push")
        await _frames_until(communicator, "patch", "html_update")
        assert [e[0] for e in EVENTS] == ["pushed"]
    finally:
        await _close(communicator)


async def test_views_listening_on_one_db_notify_channel_keep_it_until_the_last_leaves():
    from channels.layers import get_channel_layer

    communicator = await _connect()
    try:
        await _mount_page(communicator, Notified)
        await _hydrate(communicator, Notified, "n2")
        assert len(_members("djust_db_notify_mv3252")) == 1
        await _wait_gone(communicator, "n2")
        assert len(_members("djust_db_notify_mv3252")) == 1
        EVENTS.clear()
        await get_channel_layer().group_send(
            "djust_db_notify_mv3252",
            {"type": "db_notify", "channel": "mv3252", "payload": {"id": 1}},
        )
        frames = await _frames_until(communicator, "patch", "html_update")
        assert "target_id" not in frames[-1]
        assert [e[0] for e in EVENTS] == ["notified"]
    finally:
        await _close(communicator)
    assert _members("djust_db_notify_mv3252") == []


async def test_views_tracking_one_presence_key_keep_the_presence_group():
    group = PresenceManager.presence_group_name("mv3252")
    communicator = await _connect()
    try:
        await _mount_page(communicator, Present)
        await _hydrate(communicator, Present, "p2")
        assert len(_members(group)) == 1
        await _wait_gone(communicator, "p2")
        assert len(_members(group)) == 1  # the page view still tracks the key
    finally:
        await _close(communicator)
    assert _members(group) == []


# --------------------------------------------------------------------------- #
# Uploads and hook events from a view beside the page view (review of #3333, I2)
# --------------------------------------------------------------------------- #


def _upload_view_classes():
    from djust.uploads import UploadMixin

    class UpLazy(UploadMixin, _Counted, LiveView):
        template = '<div dj-root dj-view="' + MOD + '.UpLazy"><b>up {{ count }}</b></div>'

        def mount(self, request, **kwargs):
            self._tagged()
            self.allow_upload("doc", accept=".txt")

    return UpLazy


UpLazy = _upload_view_classes()


async def _register_upload(communicator, target_id=None, ref=None):
    ref = ref or str(uuid.uuid4())
    frame = {
        "type": "upload_register",
        "upload_name": "doc",
        "ref": ref,
        "client_name": "a.txt",
        "client_type": "text/plain",
        "client_size": 3,
    }
    if target_id is not None:
        frame["target_id"] = target_id
    await communicator.send_json_to(frame)
    return ref, (await _frames_until(communicator, "upload_registered", "error"))[-1]


async def test_a_lazy_view_with_an_upload_registers_and_receives_it():
    """A file input inside a lazy view uploads to that view: its register frame
    is addressed to it, and the binary chunks (which carry only the upload's
    ref) reach the manager that registered the ref."""
    from djust.uploads import FRAME_CHUNK, FRAME_COMPLETE
    import struct

    communicator = await _connect()
    try:
        await _mount_page(communicator)
        await _hydrate(communicator, UpLazy, "up-1")
        lazy = VIEWS[_one(UpLazy)]

        ref, reply = await _register_upload(communicator, "up-1")
        assert reply["type"] == "upload_registered", reply
        assert reply["target_id"] == "up-1"
        assert ref in lazy._upload_manager._entries

        raw = uuid.UUID(ref).bytes
        await communicator.send_to(
            bytes_data=bytes([FRAME_CHUNK]) + raw + struct.pack(">I", 0) + b"abc"
        )
        await communicator.send_to(bytes_data=bytes([FRAME_COMPLETE]) + raw)
        frames = await _frames_until(communicator, "upload_progress")
        while frames[-1].get("status") != "complete":
            frames += await _frames_until(communicator, "upload_progress")
        assert frames[-1]["ref"] == ref
        entry = lazy._upload_manager._entries[ref]
        assert entry.complete
    finally:
        await _close(communicator)


async def test_an_upload_register_with_no_address_goes_to_the_page_view():
    """Pinned: the address is what picks the view. The page view has no upload
    slot here, so an unaddressed register is refused rather than finding the
    lazy view's (the stock client addresses every register from a slot)."""
    communicator = await _connect()
    try:
        await _mount_page(communicator)
        await _hydrate(communicator, UpLazy, "up-1")
        _, reply = await _register_upload(communicator)
        assert reply["type"] == "error"
        assert "No uploads configured" in reply["error"]
        assert "target_id" not in reply
    finally:
        await _close(communicator)


async def test_an_upload_resume_is_routed_by_its_address():
    communicator = await _connect()
    try:
        await _mount_page(communicator)
        await _hydrate(communicator, UpLazy, "up-1")
        await communicator.send_json_to(
            {"type": "upload_resume", "ref": str(uuid.uuid4()), "target_id": "up-1"}
        )
        frame = (await _frames_until(communicator, "upload_resumed", "error"))[-1]
        assert frame["type"] == "upload_resumed"
        assert frame["status"] == "not_found"
        assert frame["target_id"] == "up-1"
    finally:
        await _close(communicator)


async def test_a_hook_event_runs_on_the_view_it_names_and_only_there():
    """A ``dj-hook`` ``pushEvent`` is an ``event`` frame with no ``ref``: from a
    lazy view it is addressed to it, and a handler the page view also has runs
    on the lazy view."""
    communicator = await _connect()
    try:
        await _mount_page(communicator)
        await _hydrate(communicator, Lazy, "lazy-1")
        page, lazy = _one(Page), _one(Lazy)
        await communicator.send_json_to(
            {"type": "event", "event": "click", "params": {}, "target_id": "lazy-1"}
        )
        frames = await _frames_until(communicator, "patch", "html_update", "noop", "error")
        assert frames[-1]["target_id"] == "lazy-1"
        assert EVENTS == [("clicked", lazy)]
        await communicator.send_json_to({"type": "event", "event": "click", "params": {}})
        await _frames_until(communicator, "patch", "html_update", "noop", "error")
        assert EVENTS == [("clicked", lazy), ("clicked", page)]
    finally:
        await _close(communicator)


# --------------------------------------------------------------------------- #
# A slot under a tenant (review of #3333)
# --------------------------------------------------------------------------- #

#: The tenant ``TenantSlot`` resolves (``None``: none resolved).
TENANT: list = [None]


class TenantSlot(_Counted, LiveView):
    enable_state_snapshot = True
    template = '<div dj-root dj-view="' + MOD + '.TenantSlot"><b>t {{ count }}</b></div>'

    def get_state_key_prefix(self):
        return "tenant:" + TENANT[0] if TENANT[0] else ""


def _saved_counts(key):
    session = SessionStore(key)
    return {
        k: v["count"]
        for k, v in session.items()
        if k.startswith("liveview_") and isinstance(v, dict) and "count" in v
    }


async def test_a_slot_of_a_tenant_view_saves_under_the_tenant_and_the_slot():
    TENANT[0] = "acme"
    key = await sync_to_async(_fresh_key)()
    communicator = await _connect(session=SessionStore(key))
    try:
        await _mount_page(communicator, SnapPage)
        await _hydrate(communicator, TenantSlot, "t-1")
        await _event(communicator, "click", target_id="t-1")
        await _event(communicator, "click")
    finally:
        await _close(communicator)
        TENANT[0] = None
    saved = await sync_to_async(_saved_counts)(key)
    assert saved == {
        "liveview_/page/": 1,
        "liveview_tenant:acme:slot:t-1:/page/": 1,
    }


async def test_a_slot_of_a_tenant_view_with_no_tenant_saves_nothing():
    """Fail closed, as for the page view (#2973): no tenant, no saved state, and
    no fallback to a key another tenant's view could read."""
    TENANT[0] = None
    key = await sync_to_async(_fresh_key)()
    communicator = await _connect(session=SessionStore(key))
    try:
        await _mount_page(communicator, SnapPage)
        await _hydrate(communicator, TenantSlot, "t-1")
        await _event(communicator, "click", target_id="t-1")
    finally:
        await _close(communicator)
    saved = await sync_to_async(_saved_counts)(key)
    assert not any("slot:t-1" in k for k in saved), saved


class Scoped(_Counted, LiveView):
    template = '<div dj-root dj-view="' + MOD + '.Scoped"><b>s {{ count }}</b></div>'
    push_scope = "r1"

    @event_handler()
    def go_r2(self, **kwargs):
        self.push_scope = "r2"


async def test_a_view_leaving_a_push_scope_leaves_the_other_view_in_it():
    """Review of #3333 (N2): the scoped group is the channel's, shared by every
    view in the scope: one view moving to another scope must not drop it."""
    communicator = await _connect()
    try:
        await _mount_page(communicator, Scoped)
        await _hydrate(communicator, Scoped, "s2")
        await _event(communicator, "go_r2")  # the page view moves; the slot stays in r1
        EVENTS.clear()
        await apush_to_view(MOD + ".Scoped", handler="on_push", scope="r1")
        frames = await _frames_until(communicator, "patch", "html_update", timeout=5)
        assert frames[-1]["target_id"] == "s2"
        assert [e[0] for e in EVENTS] == ["pushed"]
    finally:
        await _close(communicator)


class RoomWatcher(_Counted, PresenceMixin, LiveView):
    """Watches a room's presence without tracking its own: the scoped
    presence group is the only group it joins for the room."""

    presence_key = "mv3335:{room}"
    template = '<div dj-root dj-view="' + MOD + '.RoomWatcher"><b>w {{ count }}</b></div>'

    def mount(self, request, **kwargs):
        self._tagged()
        self.room = "r1"  # get_presence_key() formats from instance attributes
        self.push_scope = "r1"
        self.online_count = 0

    @event_handler()
    def go_r2(self, **kwargs):
        self.room = "r2"
        self.push_scope = "r2"


async def test_a_view_leaving_a_presence_scope_leaves_the_other_view_in_it():
    """The presence-scope group is the channel's, shared by every view whose
    presence key it is, like the push-scope group above (#3335): one view moving
    to another room must not drop it for the view that stays."""
    from djust.push import presence_scope_group_name

    path = MOD + ".RoomWatcher"
    r1 = presence_scope_group_name(path, "mv3335:r1")
    r2 = presence_scope_group_name(path, "mv3335:r2")
    communicator = await _connect()
    try:
        await _mount_page(communicator, RoomWatcher)
        await _hydrate(communicator, RoomWatcher, "rw2")
        assert len(_members(r1)) == 1  # one channel, two views
        await _event(communicator, "go_r2")  # the page view moves; the slot stays in r1
        await _until(lambda: len(_members(r2)) == 1, "the page view joining r2")
        assert len(_members(r1)) == 1  # the slot is still in r1
    finally:
        await _close(communicator)
    assert _members(r1) == []
    assert _members(r2) == []
    CONSUMERS.clear()
