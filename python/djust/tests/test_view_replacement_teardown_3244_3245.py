"""#3244 / #3245: a view a transport discards is torn down, children included.

#3244 — a legacy view's embedded (``{% live_render %}``) children:

* WebSocket ``live_redirect`` left the non-sticky children registered and
  running (SSE ``_replace_view`` unregistered them), so an explicit child of a
  legacy parent was never disposed there either.
* A legacy CHILD's ``wait_for_event`` waiters were cancelled on no path: not on
  disconnect, not on either SSE path, not on navigation. Its task was later
  destroyed pending by the garbage collector, and its ``except
  CancelledError`` cleanup never ran.
* A sticky child that navigation keeps must survive intact: registered on the
  new page, its waiter still pending and answerable.

#3245 — a second ``mount`` (or ``mount_batch``) frame on a mounted WebSocket
replaced the view with no teardown: its channel groups kept the socket (a push
aimed at the old view was handled by the new one, and the membership outlived
the disconnect), its waiters were left for the garbage collector ("Task was
destroyed but it is pending!"), and its children were never unregistered. The
stock client sends these frames: lazy hydration mounts each ``dj-lazy`` view
with its own ``mount`` frame, or one ``mount_batch`` (13-lazy-hydration.js).

Both transports are driven through their real entry points: a
``WebsocketCommunicator`` against ``LiveViewConsumer``, and the SSE stream and
message views. Waiters are awaited by each view's own ``start_async`` work.
Views are told apart by a tag set at mount, never by ``id()``.
"""

import asyncio
import gc
import json
import logging
import uuid

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth import get_user
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory, override_settings
from django.urls import path
from django.utils.functional import SimpleLazyObject

from djust import LiveView, event_handler, sse
from djust.sse import DjustSSEMessageView, DjustSSEStreamView, _sse_sessions

pytestmark = [pytest.mark.asyncio, pytest.mark.django_db(transaction=True)]

MOD = __name__

#: ``(what, tag)`` records: "waiting", "cleanup" (the waiting task's own
#: ``except CancelledError`` block ran), "answered", "unregistered",
#: "pushed" (a server push was handled).
EVENTS: list = []
#: tag -> view, for every view mounted in the test.
VIEWS: dict = {}


class _Waits:
    """Background work that waits for the user's next click (a guided tour)."""

    def _tagged(self):
        self._tag = type(self).__name__ + ":" + uuid.uuid4().hex[:8]
        VIEWS[self._tag] = self

    @event_handler()
    def start_wait(self, **kwargs):
        self.start_async(self._wait_for_go, name="wait")

    @event_handler()
    def go(self, **kwargs):
        pass

    @event_handler()
    def on_push(self, **kwargs):
        EVENTS.append(("pushed", self._tag))

    async def _wait_for_go(self):
        EVENTS.append(("waiting", self._tag))
        try:
            await self.wait_for_event("go")
        except asyncio.CancelledError:
            EVENTS.append(("cleanup", self._tag))
            raise
        EVENTS.append(("answered", self._tag))

    def handle_async_result(self, name, result=None, error=None):
        pass

    def _cleanup_on_unregister(self):
        EVENTS.append(("unregistered", self._tag))


class LegacyKid(_Waits, LiveView):
    exposure_policy = "legacy"
    template = "<div><span>kid</span></div>"

    def mount(self, request, **kwargs):
        self._tagged()


class StickyKid(_Waits, LiveView):
    exposure_policy = "legacy"
    sticky = True
    sticky_id = "dock"
    template = "<div><span>dock</span></div>"

    def mount(self, request, **kwargs):
        self._tagged()


class ExplicitKid(_Waits, LiveView):
    exposure_policy = "explicit"
    template = "<div><span>explicit</span></div>"

    def mount(self, request, **kwargs):
        self._tagged()


class Parent(_Waits, LiveView):
    """A legacy page embedding a non-sticky legacy child, an explicit child
    and a sticky child."""

    exposure_policy = "legacy"
    template = (
        "{% load live_tags %}"
        '<div dj-root dj-view="' + MOD + '.Parent">'
        "<h1>parent</h1>"
        '{% live_render "' + MOD + '.LegacyKid" view_id="kid" %}'
        '{% live_render "' + MOD + '.ExplicitKid" view_id="ekid" %}'
        '{% live_render "' + MOD + '.StickyKid" sticky=True %}'
        "</div>"
    )

    def mount(self, request, **kwargs):
        self._tagged()

    def get_context_data(self, **kwargs):
        return {"view": self}


class Dest(_Waits, LiveView):
    """The navigation target: it has the sticky child's slot."""

    exposure_policy = "legacy"
    template = (
        "{% load live_tags %}"
        '<div dj-root dj-view="' + MOD + '.Dest">'
        "<h1>dest</h1>"
        '{% live_render "' + MOD + '.StickyKid" sticky=True %}'
        "</div>"
    )

    def mount(self, request, **kwargs):
        self._tagged()

    def get_context_data(self, **kwargs):
        return {"view": self}


class Alpha(_Waits, LiveView):
    """A plain legacy page (the view a second mount replaces) with a child."""

    exposure_policy = "legacy"
    template = (
        "{% load live_tags %}"
        '<div dj-root dj-view="' + MOD + '.Alpha">'
        "<h1>alpha</h1>"
        '{% live_render "' + MOD + '.LegacyKid" view_id="kid" %}'
        "</div>"
    )

    def mount(self, request, **kwargs):
        self._tagged()

    def get_context_data(self, **kwargs):
        return {"view": self}


class Beta(_Waits, LiveView):
    exposure_policy = "legacy"
    template = '<div dj-root dj-view="' + MOD + '.Beta"><h1>beta</h1></div>'

    def mount(self, request, **kwargs):
        self._tagged()


class Gamma(_Waits, LiveView):
    exposure_policy = "legacy"
    template = '<div dj-root dj-view="' + MOD + '.Gamma"><h1>gamma</h1></div>'

    def mount(self, request, **kwargs):
        self._tagged()


class Listener(_Waits, LiveView):
    """A page subscribed to a db_notify channel (as ``listen()`` records it,
    without starting a PostgreSQL listener)."""

    exposure_policy = "legacy"
    template = '<div dj-root dj-view="' + MOD + '.Listener"><h1>listener</h1></div>'

    def mount(self, request, **kwargs):
        self._tagged()
        self._listen_channels = {"wsx3245"}


urlpatterns = [
    path("listener/", Listener.as_view()),
    path("parent/", Parent.as_view()),
    path("dest/", Dest.as_view()),
    path("alpha/", Alpha.as_view()),
    path("beta/", Beta.as_view()),
    path("gamma/", Gamma.as_view()),
]


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    monkeypatch.setattr(sse, "_SESSION_LINGER_S", 0)
    EVENTS.clear()
    VIEWS.clear()
    _sse_sessions.clear()
    with override_settings(
        ROOT_URLCONF=__name__, LIVEVIEW_ALLOWED_MODULES=["djust", __name__], DEBUG=False
    ):
        yield
    _sse_sessions.clear()
    VIEWS.clear()


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


def _assert_waiter_cleaned(tag):
    assert ("cleanup", tag) in EVENTS, tag + ": the waiting task's cleanup never ran"
    assert ("answered", tag) not in EVENTS
    assert VIEWS[tag]._waiters == {}


def _assert_waiter_alive(tag):
    assert ("waiting", tag) in EVENTS
    assert ("cleanup", tag) not in EVENTS, tag + ": the kept child's waiter was cancelled"
    assert VIEWS[tag]._waiters.get("go"), tag + ": the kept child lost its waiter"


# --------------------------------------------------------------------------- #
# WebSocket harness
# --------------------------------------------------------------------------- #


def _members(group):
    from channels.layers import get_channel_layer

    return list(get_channel_layer().groups.get(group, {}).keys())


def _view_group(cls):
    from djust.push import view_group_name

    return view_group_name(MOD + "." + cls.__name__)


async def _ws_drain(communicator):
    frames = []
    while not await communicator.receive_nothing(timeout=0.2):
        frames.append(await communicator.receive_json_from(timeout=2))
    return frames


async def _ws_connect():
    pytest.importorskip("channels")
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    communicator.scope["session"] = SessionStore(await sync_to_async(_fresh_key)())
    communicator.scope["user"] = AnonymousUser()
    connected, _ = await communicator.connect()
    assert connected
    await communicator.receive_json_from(timeout=2)  # connect ack
    return communicator


async def _ws_mount(communicator, cls, url):
    await communicator.send_json_to({"type": "mount", "view": MOD + "." + cls.__name__, "url": url})
    frames = await _ws_drain(communicator)
    assert any(f.get("type") == "mount" for f in frames), frames


async def _ws_event(communicator, event, view_id=None):
    params = {"view_id": view_id} if view_id else {}
    await communicator.send_json_to({"type": "event", "event": event, "params": params})
    await _ws_drain(communicator)


async def _ws_start_parent_waits(communicator):
    """Mount Parent and start a waiter on it and on each of its children."""
    await _ws_mount(communicator, Parent, "/parent/")
    for view_id in ("kid", "dock"):
        await _ws_event(communicator, "start_wait", view_id)
    kid, dock = _one(LegacyKid), _one(StickyKid)
    await _until(lambda: ("waiting", kid) in EVENTS, "the legacy child to wait")
    await _until(lambda: ("waiting", dock) in EVENTS, "the sticky child to wait")
    return kid, dock


async def _close(communicator):
    try:
        await communicator.disconnect()
    except (asyncio.CancelledError, Exception):  # noqa: BLE001 - teardown only
        pass


# --------------------------------------------------------------------------- #
# #3244 — WebSocket
# --------------------------------------------------------------------------- #


async def test_websocket_live_redirect_tears_down_the_legacy_views_children():
    communicator = await _ws_connect()
    try:
        kid, dock = await _ws_start_parent_waits(communicator)
        explicit = VIEWS[_one(ExplicitKid)]

        await communicator.send_json_to(
            {"type": "live_redirect_mount", "view": MOD + ".Dest", "url": "/dest/", "params": {}}
        )
        await _ws_drain(communicator)

        # The non-sticky legacy child: unregistered, its waiter cancelled.
        await _until(lambda: ("cleanup", kid) in EVENTS, "the legacy child's waiter cleanup")
        _assert_waiter_cleaned(kid)
        assert ("unregistered", kid) in EVENTS
        assert VIEWS[kid]._djust_waiters_closed is True
        # The explicit child of the legacy parent: disposed.
        assert explicit._djust_child_disposed is True
        assert explicit._parent_view is None

        # The sticky child survives intact: reattached, its waiter answerable.
        dest = VIEWS[_one(Dest)]
        assert dest._get_all_child_views().get("dock") is VIEWS[dock]
        _assert_waiter_alive(dock)
        assert ("unregistered", dock) not in EVENTS
        await _ws_event(communicator, "go", "dock")
        await _until(lambda: ("answered", dock) in EVENTS, "the sticky child's waiter")
    finally:
        await _close(communicator)


async def test_websocket_live_redirect_without_a_slot_discards_the_sticky_child(monkeypatch):
    """A sticky child the destination has no slot for is dropped, and its
    waiter goes with it (``discard_sticky_child``)."""
    unmounted = []
    monkeypatch.setattr(
        StickyKid, "_on_sticky_unmount", lambda self: unmounted.append(self._tag), raising=False
    )
    communicator = await _ws_connect()
    try:
        kid, dock = await _ws_start_parent_waits(communicator)
        await communicator.send_json_to(
            {"type": "live_redirect_mount", "view": MOD + ".Beta", "url": "/beta/", "params": {}}
        )
        await _ws_drain(communicator)
        assert type(VIEWS[_one(Beta)]) is Beta
        await _until(lambda: ("cleanup", dock) in EVENTS, "the dropped sticky child's cleanup")
        _assert_waiter_cleaned(dock)
        assert unmounted == [dock]
        # Dropped once, by the navigation's hook, not unregistered again.
        assert ("unregistered", dock) not in EVENTS
    finally:
        await _close(communicator)


async def test_websocket_live_redirect_discards_a_sticky_child_its_auth_now_denies(monkeypatch):
    """The auth re-check against the new URL refuses the sticky child: it is
    dropped once (its navigation hook, not also the page's unregister), and
    its waiter goes with it."""
    unmounted = []
    monkeypatch.setattr(
        StickyKid, "_on_sticky_unmount", lambda self: unmounted.append(self._tag), raising=False
    )
    communicator = await _ws_connect()
    try:
        kid, dock = await _ws_start_parent_waits(communicator)
        monkeypatch.setattr(StickyKid, "login_required", True, raising=False)
        await communicator.send_json_to(
            {"type": "live_redirect_mount", "view": MOD + ".Beta", "url": "/beta/", "params": {}}
        )
        await _ws_drain(communicator)
        await _until(lambda: ("cleanup", dock) in EVENTS, "the refused sticky child's cleanup")
        _assert_waiter_cleaned(dock)
        assert unmounted == [dock]
        assert ("unregistered", dock) not in EVENTS
    finally:
        await _close(communicator)


async def test_websocket_disconnect_cancels_the_legacy_childrens_waiters():
    communicator = await _ws_connect()
    kid, dock = await _ws_start_parent_waits(communicator)
    explicit = VIEWS[_one(ExplicitKid)]
    await communicator.disconnect()
    for tag in (kid, dock):
        await _until(lambda tag=tag: ("cleanup", tag) in EVENTS, tag + "'s waiter cleanup")
        _assert_waiter_cleaned(tag)
        assert ("unregistered", tag) in EVENTS
    assert explicit._djust_child_disposed is True


# --------------------------------------------------------------------------- #
# #3244 — SSE
# --------------------------------------------------------------------------- #


def _request(method, url, body, key):
    factory = RequestFactory()
    if method == "GET":
        request = factory.get(url, data=body)
    else:
        request = factory.post(url, data=json.dumps(body), content_type="application/json")
    request.session = SessionStore(key)
    request.user = SimpleLazyObject(lambda: get_user(request))
    request.tenant = None
    return request


def _fresh_key():
    session = SessionStore()
    session.create()
    return session.session_key


async def _post(session, key, body):
    request = await sync_to_async(_request)(
        "POST", f"/djust/sse/{session.session_id}/message/", body, key
    )
    response = await DjustSSEMessageView().post(request, session_id=session.session_id)
    assert response.status_code == 200
    return response


async def _sse_parent_waits():
    key = await sync_to_async(_fresh_key)()
    sid = str(uuid.uuid4())
    request = await sync_to_async(_request)(
        "GET", f"/djust/sse/{sid}/", {"view": MOD + ".Parent", "_djust_url": "/parent/"}, key
    )
    response = await DjustSSEStreamView().get(request, session_id=sid)
    assert response.status_code == 200
    stream = response._iterator
    assert "sse_connect" in await stream.__anext__()
    session = _sse_sessions[sid]
    for view_id in ("kid", "dock"):
        await _post(
            session, key, {"type": "event", "event": "start_wait", "params": {"view_id": view_id}}
        )
    kid, dock = _one(LegacyKid), _one(StickyKid)
    await _until(lambda: ("waiting", kid) in EVENTS, "the legacy child to wait")
    await _until(lambda: ("waiting", dock) in EVENTS, "the sticky child to wait")
    return session, key, stream, kid, dock


async def test_sse_navigation_cancels_the_legacy_childrens_waiters(caplog):
    session, key, stream, kid, dock = await _sse_parent_waits()
    explicit = VIEWS[_one(ExplicitKid)]
    try:
        await _post(session, key, {"type": "live_redirect_mount", "url": "/dest/", "params": {}})
        assert type(session.view_instance) is Dest
        assert "SSE old view cleanup failed" not in caplog.text

        await _until(lambda: ("cleanup", kid) in EVENTS, "the legacy child's waiter cleanup")
        _assert_waiter_cleaned(kid)
        assert ("unregistered", kid) in EVENTS
        assert explicit._djust_child_disposed is True
        # SSE does not preserve sticky children (``_replace_view``): the old
        # page's sticky child is torn down like any other, not leaked with a
        # pending waiter. The new page mounts its own.
        await _until(lambda: ("cleanup", dock) in EVENTS, "the SSE sticky child's cleanup")
        _assert_waiter_cleaned(dock)
        assert session.view_instance._get_all_child_views().get("dock") is not VIEWS[dock]
    finally:
        await stream.aclose()


async def test_sse_close_cancels_the_legacy_childrens_waiters():
    session, key, stream, kid, dock = await _sse_parent_waits()
    explicit = VIEWS[_one(ExplicitKid)]
    await stream.aclose()  # the client went away
    await _until(lambda: session.view_instance is None, "the session to drop its view")
    for tag in (kid, dock):
        await _until(lambda tag=tag: ("cleanup", tag) in EVENTS, tag + "'s waiter cleanup")
        _assert_waiter_cleaned(tag)
        assert ("unregistered", tag) in EVENTS
    assert explicit._djust_child_disposed is True


# --------------------------------------------------------------------------- #
# #3245 — a second mount / mount_batch frame on a mounted WebSocket
# --------------------------------------------------------------------------- #


async def _ws_alpha_waiting(communicator):
    await _ws_mount(communicator, Alpha, "/alpha/")
    await _ws_event(communicator, "start_wait")
    await _ws_event(communicator, "start_wait", "kid")
    alpha, kid = _one(Alpha), _one(LegacyKid)
    await _until(lambda: ("waiting", alpha) in EVENTS, "Alpha to wait")
    await _until(lambda: ("waiting", kid) in EVENTS, "Alpha's child to wait")
    assert len(_members(_view_group(Alpha))) == 1
    return alpha, kid


async def _push(cls):
    from djust.push import apush_to_view

    await apush_to_view(MOD + "." + cls.__name__, handler="on_push")


def _destroyed_pending(caplog):
    return [r for r in caplog.records if "Task was destroyed but it is pending" in r.getMessage()]


async def _collect_garbage():
    for _ in range(5):
        gc.collect()
        await asyncio.sleep(0.01)


async def test_a_second_mount_tears_down_the_replaced_view(caplog, monkeypatch):
    caplog.set_level(logging.ERROR, logger="asyncio")
    from djust import websocket

    released = []
    real_clear = websocket._clear_live_handles

    def spy(view):
        released.append(getattr(view, "_tag", None))
        real_clear(view)

    monkeypatch.setattr(websocket, "_clear_live_handles", spy)
    communicator = await _ws_connect()
    try:
        alpha, kid = await _ws_alpha_waiting(communicator)

        await _ws_mount(communicator, Beta, "/beta/")
        beta = _one(Beta)
        # Its Rust live handles were dropped (they hold application objects).
        assert alpha in released and beta not in released

        # The replaced view left its group, and a push aimed at it no longer
        # reaches this socket (it used to be handled by Beta).
        assert _members(_view_group(Alpha)) == []
        assert len(_members(_view_group(Beta))) == 1
        # Channel messages reach the socket in order: once Beta's own push is
        # handled, Alpha's (sent first) has been too.
        await _push(Alpha)
        await _push(Beta)
        await _until(lambda: ("pushed", beta) in EVENTS, "the push to Beta")
        await _ws_drain(communicator)
        assert [e for e in EVENTS if e[0] == "pushed"] == [("pushed", beta)]

        # Its waiters and its child's were cancelled, their cleanup ran, and
        # the child was unregistered.
        await _until(lambda: ("cleanup", alpha) in EVENTS, "Alpha's waiter cleanup")
        await _until(lambda: ("cleanup", kid) in EVENTS, "Alpha's child's waiter cleanup")
        _assert_waiter_cleaned(alpha)
        _assert_waiter_cleaned(kid)
        assert ("unregistered", kid) in EVENTS
        assert VIEWS[alpha]._djust_waiters_closed is True
    finally:
        await _close(communicator)
    VIEWS.clear()
    await _collect_garbage()
    assert _destroyed_pending(caplog) == []
    assert _members(_view_group(Alpha)) == []
    assert _members(_view_group(Beta)) == []


async def test_a_mount_batch_tears_down_the_mounted_view_and_keeps_its_siblings(caplog):
    caplog.set_level(logging.ERROR, logger="asyncio")
    communicator = await _ws_connect()
    try:
        alpha, kid = await _ws_alpha_waiting(communicator)

        await communicator.send_json_to(
            {
                "type": "mount_batch",
                "views": [
                    {"view": MOD + ".Beta", "url": "/beta/", "target_id": "b"},
                    {"view": MOD + ".Gamma", "url": "/gamma/", "target_id": "g"},
                ],
            }
        )
        frames = await _ws_drain(communicator)
        batch = [f for f in frames if f.get("type") == "mount_batch"]
        assert batch and [v["target_id"] for v in batch[0]["views"]] == ["b", "g"]

        # The previously mounted view is replaced: torn down, groups left.
        assert _members(_view_group(Alpha)) == []
        await _until(lambda: ("cleanup", alpha) in EVENTS, "Alpha's waiter cleanup")
        await _until(lambda: ("cleanup", kid) in EVENTS, "Alpha's child's waiter cleanup")
        _assert_waiter_cleaned(alpha)
        _assert_waiter_cleaned(kid)
        await _push(Alpha)
        await _push(Gamma)
        gamma = _one(Gamma)
        await _until(lambda: ("pushed", gamma) in EVENTS, "the push to Gamma")
        await _ws_drain(communicator)
        assert [e for e in EVENTS if e[0] == "pushed"] == [("pushed", gamma)]

        # The batch's own views are siblings, not replacements: both keep
        # their group while the socket is open.
        assert len(_members(_view_group(Beta))) == 1
        assert len(_members(_view_group(Gamma))) == 1
        assert VIEWS[_one(Beta)]._waiters_refused() is False
    finally:
        await _close(communicator)
    # ...and the disconnect leaves every one of them (Beta's used to leak).
    assert _members(_view_group(Beta)) == []
    assert _members(_view_group(Gamma)) == []
    VIEWS.clear()
    await _collect_garbage()
    assert _destroyed_pending(caplog) == []


async def test_a_mount_after_a_mount_batch_tears_down_every_batch_view():
    communicator = await _ws_connect()
    try:
        await communicator.send_json_to(
            {
                "type": "mount_batch",
                "views": [
                    {"view": MOD + ".Beta", "url": "/beta/", "target_id": "b"},
                    {"view": MOD + ".Gamma", "url": "/gamma/", "target_id": "g"},
                ],
            }
        )
        await _ws_drain(communicator)
        beta, gamma = _one(Beta), _one(Gamma)
        assert len(_members(_view_group(Beta))) == 1

        await _ws_mount(communicator, Alpha, "/alpha/")
        assert _members(_view_group(Beta)) == []
        assert _members(_view_group(Gamma)) == []
        assert len(_members(_view_group(Alpha))) == 1
        assert VIEWS[beta]._djust_waiters_closed is True
        assert VIEWS[gamma]._djust_waiters_closed is True
    finally:
        await _close(communicator)
    assert _members(_view_group(Alpha)) == []


@pytest.mark.parametrize("how", ["live_redirect", "mount"])
async def test_replacing_a_view_leaves_its_db_notify_groups(how):
    """The db_notify groups a view's ``listen()`` joined are left when it is
    replaced; ``live_redirect`` used to keep them (the next mount reset the
    record without leaving them)."""
    group = "djust_db_notify_wsx3245"
    communicator = await _ws_connect()
    try:
        await _ws_mount(communicator, Listener, "/listener/")
        assert len(_members(group)) == 1
        if how == "mount":
            await _ws_mount(communicator, Beta, "/beta/")
        else:
            await communicator.send_json_to(
                {"type": "live_redirect_mount", "view": MOD + ".Beta", "url": "/beta/"}
            )
            await _ws_drain(communicator)
        assert type(VIEWS[_one(Beta)]) is Beta
        assert _members(group) == []
    finally:
        await _close(communicator)
