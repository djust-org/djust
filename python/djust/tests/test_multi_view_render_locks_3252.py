"""#3252: a slow handler in one view does not delay the other views of its connection.

Every view of a connection used to share one render lock and one receive loop:
a handler that took a second delayed the frames of every other view by that
second. Each view now has its own render lock, deferred-push queue and
"user event in progress" flag, and while a connection hosts several views its
inbound frames run on a lane per view (``djust._view_lanes``): one view's turns
run in order, other views' turns run beside them.

Deterministic by construction: handlers block on ``threading.Event``s the test
holds, and every "does not wait" assertion is "the other view's reply arrives
while this view's handler is still blocked" (a generous timeout that only a
real wait can exceed), never a sleep.

What stays shared: handlers declared with plain ``def`` run on the connection's
single synchronous thread, so a ``def`` handler that blocks that thread holds up
the other views' ``def`` handlers (their ``async def`` handlers, and everything
else that awaits, run).
"""

import asyncio
import struct
import threading
import uuid

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.test import override_settings

from djust import LiveView, event_handler
from djust.push import push_to_view
from djust.uploads import FRAME_CHUNK, FRAME_COMPLETE, UploadMixin

pytestmark = [pytest.mark.asyncio, pytest.mark.django_db(transaction=True)]

MOD = __name__
#: Set when a ``hold`` handler is running; cleared by the fixture.
STARTED = threading.Event()
#: The test releases every blocked ``hold`` handler with this.
RELEASE = threading.Event()
#: What ran, in order: ``(what, view tag)``.
ORDER: list = []
#: Every view mounted, by ``tag`` ("page" or the slot's target).
VIEWS: dict = {}
CONSUMERS: list = []
#: Generous: only a real wait can exceed it, and a pass takes milliseconds.
PATIENCE = 20.0


class _View(LiveView):
    exposure_policy = "legacy"

    def mount(self, request, **kwargs):
        self.tag = getattr(self, "_djust_slot_target", None) or "page"
        self.bumps = 0
        VIEWS[self.tag] = self

    @event_handler()
    async def hold(self, **kwargs):
        """Blocks (without blocking the event loop) until the test lets go."""
        ORDER.append(("hold-start", self.tag))
        STARTED.set()
        await asyncio.get_running_loop().run_in_executor(None, RELEASE.wait, PATIENCE)
        ORDER.append(("hold-end", self.tag))

    @event_handler()
    def hold_sync(self, **kwargs):
        """Blocks the connection's one synchronous thread until the test lets go."""
        ORDER.append(("hold_sync-start", self.tag))
        STARTED.set()
        RELEASE.wait(PATIENCE)
        ORDER.append(("hold_sync-end", self.tag))

    @event_handler()
    async def quick(self, **kwargs):
        ORDER.append(("quick", self.tag))

    @event_handler()
    def quick_sync(self, **kwargs):
        ORDER.append(("quick_sync", self.tag))

    @event_handler()
    def bump(self, **kwargs):
        self.bumps += 1
        ORDER.append(("bump", self.tag))

    @event_handler()
    def explode(self, **kwargs):
        raise RuntimeError("boom")


class Page(_View):
    template = '<div dj-root dj-view="' + MOD + '.Page"><b>page {{ bumps }}</b></div>'

    @event_handler()
    def push_widget(self, **kwargs):
        """An event in this view that pushes to the other view's class."""
        push_to_view(MOD + ".Widget", handler="bump")


class Uploader(UploadMixin, _View):
    template = '<div dj-view="' + MOD + '.Uploader"><b>uploader</b></div>'

    def mount(self, request, **kwargs):
        _View.mount(self, request, **kwargs)
        self.allow_upload("doc", accept=".txt")


class Widget(_View):
    template = '<div dj-view="' + MOD + '.Widget"><b>widget {{ bumps }}</b></div>'


@pytest.fixture(autouse=True)
def setup():
    STARTED.clear()
    RELEASE.clear()
    ORDER.clear()
    VIEWS.clear()
    CONSUMERS.clear()
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[MOD], DEBUG=False):
        yield
    RELEASE.set()


async def _connect():
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    class _Recorded(LiveViewConsumer):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            CONSUMERS.append(self)

    store = SessionStore()
    await sync_to_async(store.create)()
    communicator = WebsocketCommunicator(_Recorded.as_asgi(), "/ws/")
    communicator.scope["session"] = store
    communicator.scope["user"] = AnonymousUser()
    assert (await communicator.connect())[0]
    await communicator.receive_json_from(timeout=3)
    return communicator


async def _until(communicator, *types, timeout=PATIENCE):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    seen = []
    while True:
        seen.append(await communicator.receive_json_from(timeout=deadline - loop.time()))
        if seen[-1].get("type") in types:
            return seen


async def _mount(communicator, cls, target=None):
    frame = {"type": "mount", "view": MOD + "." + cls.__name__, "url": "/p/"}
    if target:
        frame["target_id"] = target
    await communicator.send_json_to(frame)
    await _until(communicator, "mount")


async def _send(communicator, event, target=None, ref=1):
    frame = {"type": "event", "event": event, "params": {}, "ref": ref}
    if target:
        frame["target_id"] = target
    await communicator.send_json_to(frame)


REPLIES = ("patch", "html_update", "noop", "error")


async def _reply(communicator, ref, timeout=PATIENCE):
    """The reply to the event with ``ref`` (others' frames are skipped)."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        frame = await communicator.receive_json_from(timeout=deadline - loop.time())
        if frame.get("type") in REPLIES and frame.get("ref") == ref:
            return frame


async def _replies(communicator, *refs):
    """The replies to the events with these refs, in whatever order they arrive."""
    pending = set(refs)
    got = {}
    loop = asyncio.get_running_loop()
    deadline = loop.time() + PATIENCE
    while pending:
        frame = await communicator.receive_json_from(timeout=deadline - loop.time())
        if frame.get("type") in REPLIES and frame.get("ref") in pending:
            pending.discard(frame["ref"])
            got[frame["ref"]] = frame
    return got


async def _wait_started():
    assert await asyncio.get_running_loop().run_in_executor(None, STARTED.wait, PATIENCE)


async def _page_and_widget(communicator):
    await _mount(communicator, Page)
    await _mount(communicator, Widget, "w")


# --------------------------------------------------------------------------- #
# A slow handler does not delay another view
# --------------------------------------------------------------------------- #


async def test_a_slow_handler_in_the_page_view_does_not_delay_a_view_beside_it():
    communicator = await _connect()
    try:
        await _page_and_widget(communicator)
        await _send(communicator, "hold")
        await _wait_started()
        await _send(communicator, "quick", "w", ref=2)
        reply = await _reply(communicator, 2)
        assert reply["type"] != "error" and reply.get("target_id") == "w", reply
        assert ("hold-end", "page") not in ORDER, "the page view's handler had already finished"
        RELEASE.set()
        assert (await _reply(communicator, 1))["type"] != "error"
        assert ORDER == [
            ("hold-start", "page"),
            ("quick", "w"),
            ("hold-end", "page"),
        ]
    finally:
        RELEASE.set()
        await communicator.disconnect()


async def test_a_slow_handler_in_a_view_beside_the_page_does_not_delay_the_page_view():
    communicator = await _connect()
    try:
        await _page_and_widget(communicator)
        await _send(communicator, "hold", "w")
        await _wait_started()
        await _send(communicator, "quick", ref=2)
        reply = await _reply(communicator, 2)
        assert reply["type"] != "error" and "target_id" not in reply, reply
        assert ("hold-end", "w") not in ORDER
        RELEASE.set()
        await _reply(communicator, 1)
    finally:
        RELEASE.set()
        await communicator.disconnect()


async def test_two_slow_views_run_their_handlers_at_the_same_time():
    """Both ``hold`` handlers are running at once: neither waited for the other."""
    communicator = await _connect()
    try:
        await _page_and_widget(communicator)
        await _send(communicator, "hold", ref=1)
        await _send(communicator, "hold", "w", ref=2)
        for _ in range(2000):
            if [e for e in ORDER if e[0] == "hold-start"] == [
                ("hold-start", "page"),
                ("hold-start", "w"),
            ]:
                break
            await asyncio.sleep(0)
            await asyncio.sleep(0.001)
        else:
            pytest.fail(
                "the second view's handler never started while the first was blocked: %r" % ORDER
            )
        assert not [e for e in ORDER if e[0] == "hold-end"]
        RELEASE.set()
        await _replies(communicator, 1, 2)
    finally:
        RELEASE.set()
        await communicator.disconnect()


# --------------------------------------------------------------------------- #
# One view's turns keep their order
# --------------------------------------------------------------------------- #


async def test_one_views_turns_run_in_order_one_at_a_time():
    communicator = await _connect()
    try:
        await _page_and_widget(communicator)
        await _send(communicator, "hold", "w", ref=1)
        await _wait_started()
        await _send(communicator, "quick", "w", ref=2)
        await _send(communicator, "quick_sync", "w", ref=3)
        # The page view is not behind them...
        await _send(communicator, "quick", ref=4)
        await _reply(communicator, 4)
        # ...and they have not run: they queue behind the blocked turn.
        assert [e for e in ORDER if e[1] == "w"] == [("hold-start", "w")]
        RELEASE.set()
        for ref in (1, 2, 3):
            await _reply(communicator, ref)
        assert [e for e in ORDER if e[1] == "w"] == [
            ("hold-start", "w"),
            ("hold-end", "w"),
            ("quick", "w"),
            ("quick_sync", "w"),
        ]
    finally:
        RELEASE.set()
        await communicator.disconnect()


async def test_a_failing_turn_is_answered_and_the_views_lane_goes_on():
    communicator = await _connect()
    try:
        await _page_and_widget(communicator)
        await _send(communicator, "explode", "w", ref=1)
        failed = (await _until(communicator, "error"))[-1]
        assert failed["type"] == "error" and failed.get("target_id") == "w", failed
        await _send(communicator, "bump", "w", ref=2)
        assert (await _reply(communicator, 2))["type"] != "error"
        assert VIEWS["w"].bumps == 1
    finally:
        await communicator.disconnect()


# --------------------------------------------------------------------------- #
# Each view has its own lock; a page with one view is unchanged
# --------------------------------------------------------------------------- #


async def test_a_view_cannot_pile_up_more_than_a_bounded_number_of_queued_turns(monkeypatch):
    from djust import _view_lanes

    monkeypatch.setattr(_view_lanes, "MAX_QUEUED_TURNS", 2)
    communicator = await _connect()
    try:
        await _page_and_widget(communicator)
        await _send(communicator, "hold", "w", ref=1)
        await _wait_started()
        for ref in (2, 3):
            await _send(communicator, "quick", "w", ref=ref)
        await _send(communicator, "quick", "w", ref=4)
        refused = (await _until(communicator, "error"))[-1]
        assert refused["code"] == "view_busy" and refused["ref"] == 4, refused
        assert refused["target_id"] == "w"
        # The page view is not affected, and the queued turns still run.
        await _send(communicator, "quick", ref=5)
        assert (await _reply(communicator, 5))["type"] != "error"
        RELEASE.set()
        await _replies(communicator, 1, 2, 3)
        assert [e for e in ORDER if e[1] == "w"] == [
            ("hold-start", "w"),
            ("hold-end", "w"),
            ("quick", "w"),
            ("quick", "w"),
        ]
    finally:
        RELEASE.set()
        await communicator.disconnect()


# What is NOT concurrent over WebSocket: a plain ``def`` handler runs on the
# connection's one synchronous thread, and so does the connection-cleanup hop
# every inbound frame makes before it is dispatched. While that thread is
# blocked, no other view's frame is answered, whether its handler is ``def`` or
# ``async def``. Pinned as strict expected failures (a thread per view would fix
# them, at one database connection per view; see the pull request).

BLOCKED_SYNC_THREAD = (
    "a blocked synchronous thread stalls every frame of the connection: handlers declared "
    "with def, and the per-frame connection cleanup that precedes dispatch, share it"
)


@pytest.mark.xfail(strict=True, reason=BLOCKED_SYNC_THREAD)
async def test_a_blocking_def_handler_in_one_view_does_not_delay_a_def_event_in_another():
    communicator = await _connect()
    try:
        await _page_and_widget(communicator)
        await _send(communicator, "hold_sync")
        await _wait_started()
        await _send(communicator, "quick_sync", "w", ref=2)
        assert (await _reply(communicator, 2, timeout=3.0))["type"] != "error"
    finally:
        RELEASE.set()
        await communicator.disconnect()


@pytest.mark.xfail(strict=True, reason=BLOCKED_SYNC_THREAD)
async def test_a_blocking_def_handler_in_one_view_does_not_delay_an_async_event_in_another():
    communicator = await _connect()
    try:
        await _page_and_widget(communicator)
        await _send(communicator, "hold_sync")
        await _wait_started()
        await _send(communicator, "quick", "w", ref=2)
        assert (await _reply(communicator, 2, timeout=3.0))["type"] != "error"
    finally:
        RELEASE.set()
        await communicator.disconnect()


async def test_each_view_has_its_own_render_lock_and_busy_flag():
    communicator = await _connect()
    try:
        await _page_and_widget(communicator)
        consumer = CONSUMERS[-1]
        facade = consumer._slot_map()["w"].facade
        assert facade._render_lock is not consumer._render_lock
        await _send(communicator, "hold")
        await _wait_started()
        assert consumer._render_lock.locked() and consumer._processing_user_event is True
        assert not facade._render_lock.locked() and facade._processing_user_event is False
        RELEASE.set()
        await _reply(communicator, 1)
    finally:
        RELEASE.set()
        await communicator.disconnect()


async def test_a_page_with_one_view_runs_its_frames_inline_with_no_lane():
    communicator = await _connect()
    try:
        await _mount(communicator, Page)
        for ref in (1, 2):
            await _send(communicator, "bump", ref=ref)
            assert (await _reply(communicator, ref))["type"] != "error"
        assert CONSUMERS[-1]._lane is None
        assert VIEWS["page"].bumps == 2
    finally:
        await communicator.disconnect()


async def test_the_last_view_beside_the_page_going_returns_the_page_to_inline_frames():
    communicator = await _connect()
    try:
        await _page_and_widget(communicator)
        await _send(communicator, "bump", ref=1)
        await _reply(communicator, 1)
        assert CONSUMERS[-1]._lane is not None
        await communicator.send_json_to({"type": "unmount", "target_id": "w"})
        for _ in range(2000):
            if not CONSUMERS[-1]._slot_map():
                break
            await asyncio.sleep(0.001)
        await _send(communicator, "bump", ref=2)
        assert (await _reply(communicator, 2))["type"] != "error"
        assert [e for e in ORDER if e[0] == "bump"] == [("bump", "page"), ("bump", "page")]
        assert not CONSUMERS[-1]._lane.busy()
    finally:
        await communicator.disconnect()


# --------------------------------------------------------------------------- #
# A push from one view to another
# --------------------------------------------------------------------------- #


async def test_an_event_that_pushes_to_a_busy_view_neither_deadlocks_nor_loses_the_push():
    communicator = await _connect()
    try:
        await _page_and_widget(communicator)
        # The widget is busy: its handler holds its own lock.
        await _send(communicator, "hold", "w", ref=1)
        await _wait_started()
        # An event in the page view pushes to the widget's class: the page view's
        # event is answered at once, and the push waits for the widget.
        await _send(communicator, "push_widget", ref=2)
        assert (await _reply(communicator, 2))["type"] != "error"
        assert VIEWS["w"].bumps == 0
        RELEASE.set()
        await _reply(communicator, 1)
        for _ in range(4000):
            if VIEWS["w"].bumps == 1:
                break
            await asyncio.sleep(0.001)
        assert VIEWS["w"].bumps == 1, "the push never reached the widget after it was free"
        assert VIEWS["page"].bumps == 0
    finally:
        RELEASE.set()
        await communicator.disconnect()


# --------------------------------------------------------------------------- #
# Teardown while a handler is running
# --------------------------------------------------------------------------- #


def _waiters(lock):
    """Whether a task is queued on ``lock`` (asyncio keeps them in ``_waiters``)."""
    return bool([w for w in (getattr(lock, "_waiters", None) or ()) if not w.cancelled()])


async def _spin_until(predicate, what):
    for _ in range(4000):
        if predicate():
            return
        await asyncio.sleep(0.001)
    raise AssertionError("timed out waiting for " + what)


async def test_unmount_waits_for_the_running_handler_then_releases_only_that_view():
    communicator = await _connect()
    try:
        await _page_and_widget(communicator)
        await _send(communicator, "hold", "w", ref=1)
        await _wait_started()
        consumer = CONSUMERS[-1]
        lane = consumer._slot_map()["w"].facade._lane
        await communicator.send_json_to({"type": "unmount", "target_id": "w"})
        # The unmount is queued behind the running handler (a deterministic
        # signal), and the page view answers meanwhile.
        await _spin_until(lambda: lane.queued() == 1, "the unmount to queue on the view's lane")
        await _send(communicator, "quick", ref=2)
        await _reply(communicator, 2)
        assert "w" in consumer._slot_map()
        assert ("hold-end", "w") not in ORDER
        RELEASE.set()
        await _spin_until(lambda: "w" not in consumer._slot_map(), "the view's release")
        # The handler finished before the view went.
        assert ("hold-end", "w") in ORDER
    finally:
        RELEASE.set()
        await communicator.disconnect()


async def test_a_navigation_waits_for_every_running_handler():
    communicator = await _connect()
    try:
        await _page_and_widget(communicator)
        await _send(communicator, "hold", "w", ref=1)
        await _wait_started()
        consumer = CONSUMERS[-1]
        lane = consumer._slot_map()["w"].facade._lane
        await communicator.send_json_to(
            {"type": "live_redirect_mount", "view": MOD + ".Page", "url": "/p/", "params": {}}
        )
        # The navigation is blocked waiting for the view's running turn.
        await _spin_until(lambda: lane.waiting() == 1, "the navigation to wait on the lane")
        # The view is still mounted and its handler has not ended: the navigation waits.
        assert getattr(VIEWS["w"], "_djust_waiters_closed", False) is False
        assert ("hold-end", "w") not in ORDER, "the navigation released a view mid-handler"
        RELEASE.set()
        await _until(communicator, "mount")
        assert ("hold-end", "w") in ORDER
        assert consumer._slot_map() == {}
    finally:
        RELEASE.set()
        await communicator.disconnect()


async def test_a_disconnect_waits_for_the_running_handler_and_drops_the_queued_ones():
    communicator = await _connect()
    await _page_and_widget(communicator)
    consumer = CONSUMERS[-1]
    await _send(communicator, "hold", "w", ref=1)
    await _wait_started()
    lane = consumer._slot_map()["w"].facade._lane
    await _send(communicator, "bump", "w", ref=2)  # queued behind the handler
    await _spin_until(lambda: lane.queued() == 1, "the event to queue")
    disconnecting = asyncio.ensure_future(communicator.disconnect())
    await _spin_until(lambda: lane.waiting() == 1, "the disconnect to wait on the lane")
    assert lane.queued() == 0, "the queued turn was not dropped"
    assert not disconnecting.done(), "the disconnect did not wait for the running handler"
    assert consumer._slot_map()["w"].facade.view_instance is not None
    RELEASE.set()
    await asyncio.wait_for(disconnecting, PATIENCE)
    assert ("hold-end", "w") in ORDER
    assert ("bump", "w") not in ORDER, "a turn queued behind the disconnect still ran"
    assert consumer.view_instance is None and consumer._slot_map() == {}


# --------------------------------------------------------------------------- #
# SSE: a POST takes the lock of its own view
# --------------------------------------------------------------------------- #

import json  # noqa: E402

from django.contrib.auth import get_user  # noqa: E402
from django.test import RequestFactory  # noqa: E402
from django.urls import path  # noqa: E402
from django.utils.functional import SimpleLazyObject  # noqa: E402

from djust import sse  # noqa: E402
from djust.sse import DjustSSEMessageView, DjustSSEStreamView, _sse_sessions  # noqa: E402

urlpatterns = [path("p/", Page.as_view())]


@pytest.fixture
def sse_setup(monkeypatch):
    monkeypatch.setattr(sse, "_SESSION_LINGER_S", 0)
    _sse_sessions.clear()
    with override_settings(ROOT_URLCONF=__name__):
        yield
    _sse_sessions.clear()


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


async def _sse_frame(stream, *types, ref=None, timeout=PATIENCE):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        chunk = await asyncio.wait_for(stream.__anext__(), timeout=deadline - loop.time())
        if not chunk.startswith("data:"):
            continue
        frame = json.loads(chunk[len("data:") :])
        if frame.get("type") in types and (ref is None or frame.get("ref") == ref):
            return frame


async def _sse_open():
    store = SessionStore()
    await sync_to_async(store.create)()
    key = store.session_key
    sid = str(uuid.uuid4())
    request = await sync_to_async(_request)(
        "GET", f"/djust/sse/{sid}/", {"view": MOD + ".Page", "_djust_url": "/p/"}, key
    )
    response = await DjustSSEStreamView().get(request, session_id=sid)
    stream = response._iterator
    await _sse_frame(stream, "sse_connect")
    await _sse_frame(stream, "mount")
    return _sse_sessions[sid], key, stream


async def _sse_post(session, key, body):
    request = await sync_to_async(_request)(
        "POST", f"/djust/sse/{session.session_id}/message/", body, key
    )
    response = await DjustSSEMessageView().post(request, session_id=session.session_id)
    assert response.status_code == 200


async def _sse_event(session, key, event, target=None, ref=1):
    body = {"type": "event", "event": event, "params": {}, "ref": ref}
    if target:
        body["target_id"] = target
    await _sse_post(session, key, body)


async def _sse_widget(session, key, stream, target="w"):
    await _sse_post(
        session,
        key,
        {"type": "mount", "view": MOD + ".Widget", "url": "/p/", "target_id": target},
    )
    await _sse_frame(stream, "mount")


async def test_over_sse_a_slow_turn_in_one_view_does_not_delay_another_view(sse_setup):
    session, key, stream = await _sse_open()
    try:
        await _sse_widget(session, key, stream)
        slow = asyncio.ensure_future(_sse_event(session, key, "hold", "w", ref=1))
        await _wait_started()
        # The page view's POST is dispatched and answered while the widget's
        # handler is still blocked.
        await _sse_event(session, key, "quick", ref=2)
        reply = await _sse_frame(stream, "patch", "html_update", "noop", "error", ref=2)
        assert reply["type"] != "error" and "target_id" not in reply, reply
        assert ("hold-end", "w") not in ORDER
        RELEASE.set()
        await asyncio.wait_for(slow, PATIENCE)
        await _sse_frame(stream, "patch", "html_update", "noop", "error", ref=1)
        assert ORDER == [("hold-start", "w"), ("quick", "page"), ("hold-end", "w")]
    finally:
        RELEASE.set()
        await stream.aclose()


async def test_over_sse_a_slow_turn_in_the_page_view_does_not_delay_a_view_beside_it(sse_setup):
    session, key, stream = await _sse_open()
    try:
        await _sse_widget(session, key, stream)
        slow = asyncio.ensure_future(_sse_event(session, key, "hold", ref=1))
        await _wait_started()
        await _sse_event(session, key, "quick", "w", ref=2)
        reply = await _sse_frame(stream, "patch", "html_update", "noop", "error", ref=2)
        assert reply["type"] != "error" and reply.get("target_id") == "w", reply
        assert ("hold-end", "page") not in ORDER
        RELEASE.set()
        await asyncio.wait_for(slow, PATIENCE)
    finally:
        RELEASE.set()
        await stream.aclose()


async def test_over_sse_one_views_turns_still_run_one_at_a_time_in_order(sse_setup):
    session, key, stream = await _sse_open()
    try:
        await _sse_widget(session, key, stream)
        first = asyncio.ensure_future(_sse_event(session, key, "hold", "w", ref=1))
        await _wait_started()
        second = asyncio.ensure_future(_sse_event(session, key, "quick", "w", ref=2))
        for _ in range(50):
            await asyncio.sleep(0)
        assert [e for e in ORDER if e[1] == "w"] == [("hold-start", "w")], ORDER
        RELEASE.set()
        await asyncio.wait_for(asyncio.gather(first, second), PATIENCE)
        assert [e for e in ORDER if e[1] == "w"] == [
            ("hold-start", "w"),
            ("hold-end", "w"),
            ("quick", "w"),
        ]
    finally:
        RELEASE.set()
        await stream.aclose()


async def test_over_sse_each_view_has_its_own_locks_and_request(sse_setup):
    session, key, stream = await _sse_open()
    try:
        await _sse_widget(session, key, stream)
        slot = session._slots["w"]
        assert slot.dispatch_lock is not session._dispatch_lock
        assert slot.session._render_lock is not session._render_lock
        slow = asyncio.ensure_future(_sse_event(session, key, "hold", "w", ref=1))
        await _wait_started()
        assert slot.dispatch_lock.locked() and not session._dispatch_lock.locked()
        # Each turn sees its own POST: the page's is not the widget's.
        assert slot.session._event_request is not None and session._event_request is None
        RELEASE.set()
        await asyncio.wait_for(slow, PATIENCE)
        assert slot.session._event_request is None
    finally:
        RELEASE.set()
        await stream.aclose()


async def test_over_sse_an_unmount_waits_for_that_views_running_turn_only(sse_setup):
    session, key, stream = await _sse_open()
    try:
        await _sse_widget(session, key, stream)
        slow = asyncio.ensure_future(_sse_event(session, key, "hold", "w", ref=1))
        await _wait_started()
        unmount = asyncio.ensure_future(
            _sse_post(session, key, {"type": "unmount", "target_id": "w"})
        )
        # The unmount is waiting for the view's lock (a deterministic signal: it
        # is queued on it), and the page view still answers meanwhile.
        await _spin_until(
            lambda: _waiters(session._slots["w"].dispatch_lock), "the unmount to queue"
        )
        await _sse_event(session, key, "quick", ref=2)
        await _sse_frame(stream, "patch", "html_update", "noop", "error", ref=2)
        assert not unmount.done() and "w" in session._slots
        RELEASE.set()
        await asyncio.wait_for(asyncio.gather(slow, unmount), PATIENCE)
        assert "w" not in session._slots and ("hold-end", "w") in ORDER
    finally:
        RELEASE.set()
        await stream.aclose()


async def test_over_sse_a_navigation_waits_for_every_views_running_turn(sse_setup):
    session, key, stream = await _sse_open()
    try:
        await _sse_widget(session, key, stream)
        slow = asyncio.ensure_future(_sse_event(session, key, "hold", "w", ref=1))
        await _wait_started()
        navigation = asyncio.ensure_future(
            _sse_post(session, key, {"type": "live_redirect_mount", "url": "/p/", "params": {}})
        )
        # The navigation holds the page's locks and is queued on the widget's.
        await _spin_until(
            lambda: _waiters(session._slots["w"].dispatch_lock), "the navigation to queue"
        )
        assert not navigation.done() and "w" in session._slots
        assert ("hold-end", "w") not in ORDER
        RELEASE.set()
        await asyncio.wait_for(asyncio.gather(slow, navigation), PATIENCE)
        await _sse_frame(stream, "mount")
        assert session._slots == {}
    finally:
        RELEASE.set()
        await stream.aclose()


# --------------------------------------------------------------------------- #
# Uploads keep their order behind a busy view
# --------------------------------------------------------------------------- #


async def test_an_upload_to_a_busy_view_is_applied_in_order_not_dropped():
    """The register waits on the view's lane behind a running handler; the binary
    chunk and complete frames (which carry only the ref) must follow it on the
    same lane. Before, they were applied at once, found no manager that knew the
    ref, and were dropped: the upload never completed."""
    communicator = await _connect()
    try:
        await _mount(communicator, Page)
        await _mount(communicator, Uploader, "up")
        uploader = VIEWS["up"]
        await _send(communicator, "hold", "up", ref=1)
        await _wait_started()

        ref = str(uuid.uuid4())
        await communicator.send_json_to(
            {
                "type": "upload_register",
                "target_id": "up",
                "upload_name": "doc",
                "ref": ref,
                "client_name": "a.txt",
                "client_type": "text/plain",
                "client_size": 3,
            }
        )
        raw = uuid.UUID(ref).bytes
        await communicator.send_to(
            bytes_data=bytes([FRAME_CHUNK]) + raw + struct.pack(">I", 0) + b"abc"
        )
        await communicator.send_to(bytes_data=bytes([FRAME_COMPLETE]) + raw)
        lane = CONSUMERS[-1]._slot_map()["up"].facade._lane
        await _spin_until(lambda: lane.queued() == 3, "the register and its frames to queue")
        # Nothing has been applied: the view is still busy.
        assert ref not in uploader._upload_manager._entries

        RELEASE.set()
        frames = await _until(communicator, "upload_progress")
        while frames[-1].get("status") != "complete":
            frames += await _until(communicator, "upload_progress")
        assert [f["type"] for f in frames if f["type"] == "error"] == []
        assert uploader._upload_manager._entries[ref].complete
    finally:
        RELEASE.set()
        await communicator.disconnect()


async def test_an_upload_to_an_idle_view_is_still_applied_inline():
    communicator = await _connect()
    try:
        await _mount(communicator, Page)
        await _mount(communicator, Uploader, "up")
        ref = str(uuid.uuid4())
        await communicator.send_json_to(
            {
                "type": "upload_register",
                "target_id": "up",
                "upload_name": "doc",
                "ref": ref,
                "client_name": "a.txt",
                "client_type": "text/plain",
                "client_size": 3,
            }
        )
        await _until(communicator, "upload_registered")
        raw = uuid.UUID(ref).bytes
        await communicator.send_to(
            bytes_data=bytes([FRAME_CHUNK]) + raw + struct.pack(">I", 0) + b"abc"
        )
        await communicator.send_to(bytes_data=bytes([FRAME_COMPLETE]) + raw)
        frames = await _until(communicator, "upload_progress")
        while frames[-1].get("status") != "complete":
            frames += await _until(communicator, "upload_progress")
        assert VIEWS["up"]._upload_manager._entries[ref].complete
    finally:
        await communicator.disconnect()


# --------------------------------------------------------------------------- #
# What an upload may queue behind a busy view is bounded
# --------------------------------------------------------------------------- #


def _register_frame(ref, target="up", size=3):
    return {
        "type": "upload_register",
        "target_id": target,
        "upload_name": "doc",
        "ref": ref,
        "client_name": "a.txt",
        "client_type": "text/plain",
        "client_size": size,
    }


def _chunk(ref, index, data):
    return bytes([FRAME_CHUNK]) + uuid.UUID(ref).bytes + struct.pack(">I", index) + data


def _complete(ref):
    return bytes([FRAME_COMPLETE]) + uuid.UUID(ref).bytes


async def _busy_uploader(communicator):
    await _mount(communicator, Page)
    await _mount(communicator, Uploader, "up")
    await _send(communicator, "hold", "up", ref=1)
    await _wait_started()
    return CONSUMERS[-1]._slot_map()["up"].facade._lane


async def test_frames_of_unregistered_refs_are_not_queued_behind_a_busy_view():
    """Only an upload whose ref is known is queued: forged refs are answered
    inline as they always were, so a client cannot pile bytes up in a lane."""
    communicator = await _connect()
    try:
        await _page_and_widget(communicator)  # two views: the page view has a lane
        await _send(communicator, "hold", ref=1)  # and it is busy
        await _wait_started()
        lane = CONSUMERS[-1]._lane
        for _ in range(400):
            ref = str(uuid.uuid4())
            await communicator.send_to(bytes_data=_chunk(ref, 0, b"x" * 1024))
        # Frames go through the receive loop in order: a ping after them is
        # answered only once all 400 have been handled (and none was queued).
        await communicator.send_json_to({"type": "ping"})
        await _until(communicator, "pong")
        assert lane.queued() == 0 and lane.queued_bytes() == 0
        assert CONSUMERS[-1]._pending_uploads == {}
    finally:
        RELEASE.set()
        await communicator.disconnect()


async def test_an_upload_past_the_lane_budget_is_stopped_visibly_not_dropped(monkeypatch):
    communicator = await _connect()
    try:
        lane = await _busy_uploader(communicator)
        consumer = CONSUMERS[-1]
        monkeypatch.setattr(type(consumer), "_upload_queue_budget", lambda self: 1000)
        ref = str(uuid.uuid4())
        await communicator.send_json_to(_register_frame(ref, size=600))
        await communicator.send_to(bytes_data=_chunk(ref, 0, b"a" * 300))  # fits
        await _spin_until(lambda: lane.queued() == 2, "the register and the first chunk")
        await communicator.send_to(bytes_data=_chunk(ref, 1, b"b" * 300))  # does not
        error = (await _until(communicator, "upload_progress"))[-1]
        assert error["status"] == "error" and error["ref"] == ref, error
        assert "busy" in error["error"]
        assert ref in consumer._aborted_uploads
        # Later frames of the stopped upload are answered like an unknown upload's.
        await communicator.send_to(bytes_data=_complete(ref))
        await communicator.send_json_to({"type": "ping"})
        await _until(communicator, "pong")
        assert lane.queued() == 2
        RELEASE.set()
        await _replies(communicator, 1)
        await _spin_until(lambda: not lane.busy(), "the lane to drain")
        # The queued register ran and its upload was cancelled: it did not complete.
        entries = VIEWS["up"]._upload_manager._entries
        assert ref not in entries
        assert ref not in consumer._pending_uploads
        assert lane.queued_bytes() == 0
    finally:
        RELEASE.set()
        await communicator.disconnect()


async def test_a_large_upload_behind_a_busy_view_completes_within_the_default_budget():
    """150 chunks of the stock chunk size (a 9.7 MB file, just under the slot's
    10 MB default limit) wait behind a busy view and the file arrives whole."""
    from djust.uploads import DEFAULT_CHUNK_SIZE

    communicator = await _connect()
    try:
        lane = await _busy_uploader(communicator)
        consumer = CONSUMERS[-1]
        chunks = 150
        data = b"z" * DEFAULT_CHUNK_SIZE
        assert chunks * (len(data) + 256) < consumer._upload_queue_budget()
        ref = str(uuid.uuid4())
        await communicator.send_json_to(_register_frame(ref, size=chunks * len(data)))
        for index in range(chunks):
            await communicator.send_to(bytes_data=_chunk(ref, index, data))
        await communicator.send_to(bytes_data=_complete(ref))
        await _spin_until(lambda: lane.queued() == chunks + 2, "every frame to queue")
        RELEASE.set()
        frames = await _until(communicator, "upload_progress")
        while frames[-1].get("status") != "complete":
            frames += await _until(communicator, "upload_progress")
        assert not [f for f in frames if f.get("status") == "error"], frames
        assert VIEWS["up"]._upload_manager._entries[ref].complete
        assert lane.queued_bytes() == 0
        assert consumer._pending_uploads == {}
    finally:
        RELEASE.set()
        await communicator.disconnect()


async def test_a_register_that_has_run_is_no_longer_remembered_as_pending():
    communicator = await _connect()
    try:
        lane = await _busy_uploader(communicator)
        consumer = CONSUMERS[-1]
        ref = str(uuid.uuid4())
        await communicator.send_json_to(_register_frame(ref))
        await _spin_until(lambda: lane.queued() == 1, "the register to queue")
        assert ref in consumer._pending_uploads
        RELEASE.set()
        await _until(communicator, "upload_registered")
        await _spin_until(lambda: not lane.busy(), "the lane to drain")
        # Registered but neither completed nor cancelled: the manager knows it now.
        assert ref in VIEWS["up"]._upload_manager._entries
        assert consumer._pending_uploads == {}
    finally:
        RELEASE.set()
        await communicator.disconnect()


async def test_upload_byte_budgets_are_independent_for_two_busy_views(monkeypatch):
    communicator = await _connect()
    try:
        await _mount(communicator, Page)
        for target in ("up", "other"):
            await _mount(communicator, Uploader, target)
            await _send(communicator, "hold", target, ref=target)
        await _spin_until(
            lambda: all(("hold-start", target) in ORDER for target in ("up", "other")),
            "both upload views to hold",
        )
        consumer = CONSUMERS[-1]
        monkeypatch.setattr(type(consumer), "_upload_queue_budget", lambda self: 1000)
        refs = {}
        for target in ("up", "other"):
            ref = refs[target] = str(uuid.uuid4())
            await communicator.send_json_to(_register_frame(ref, target, size=300))
            await communicator.send_to(bytes_data=_chunk(ref, 0, b"a" * 300))
            await communicator.send_to(bytes_data=_complete(ref))
        lanes = [consumer._slot_map()[target].facade._lane for target in refs]
        await _spin_until(lambda: all(lane.queued() == 3 for lane in lanes), "both queues")
        assert [lane.queued_bytes() for lane in lanes] == [812, 812]
        RELEASE.set()
        await _spin_until(lambda: all(not lane.busy() for lane in lanes), "both queues to drain")
        for target, ref in refs.items():
            assert VIEWS[target]._upload_manager._entries[ref].complete
        assert [lane.queued_bytes() for lane in lanes] == [0, 0]
    finally:
        RELEASE.set()
        await communicator.disconnect()


async def test_a_raising_queued_register_forgets_its_pending_ref(monkeypatch):
    communicator = await _connect()
    try:
        lane = await _busy_uploader(communicator)
        consumer = CONSUMERS[-1]

        async def fail_register(self, data):
            raise RuntimeError("register failed")

        monkeypatch.setattr(type(consumer), "_handle_upload_register", fail_register)
        ref = str(uuid.uuid4())
        await communicator.send_json_to(_register_frame(ref))
        await _spin_until(lambda: lane.queued() == 1, "the register to queue")
        RELEASE.set()
        await _until(communicator, "error")
        await _spin_until(lambda: not lane.busy(), "the failed register to drain")
        assert consumer._pending_uploads == {}
    finally:
        RELEASE.set()
        await communicator.disconnect()


async def test_release_forgets_an_overflowed_upload_with_no_accepted_chunks(monkeypatch):
    communicator = await _connect()
    try:
        lane = await _busy_uploader(communicator)
        consumer = CONSUMERS[-1]
        monkeypatch.setattr(type(consumer), "_upload_queue_budget", lambda self: 1)
        ref = str(uuid.uuid4())
        await communicator.send_json_to(_register_frame(ref))
        await communicator.send_to(bytes_data=_chunk(ref, 0, b"abc"))
        assert (await _until(communicator, "upload_progress"))[-1]["status"] == "error"
        await communicator.send_json_to({"type": "unmount", "target_id": "up"})
        await _spin_until(lambda: lane.queued() == 2, "the register and unmount")
        RELEASE.set()
        await _spin_until(lambda: "up" not in consumer._slot_map(), "the view's release")
        assert consumer._pending_uploads == {}
        assert consumer._aborted_uploads == {}
    finally:
        RELEASE.set()
        await communicator.disconnect()


async def test_releasing_a_view_forgets_the_uploads_remembered_for_it():
    communicator = await _connect()
    try:
        await _mount(communicator, Page)
        await _mount(communicator, Uploader, "up")
        consumer = CONSUMERS[-1]
        facade = consumer._slot_map()["up"].facade
        ref = str(uuid.uuid4())
        consumer._note_upload_owner(ref, facade)  # a register that never got to run
        await communicator.send_json_to({"type": "unmount", "target_id": "up"})
        await _spin_until(lambda: "up" not in consumer._slot_map(), "the view's release")
        assert consumer._pending_uploads == {}
    finally:
        await communicator.disconnect()


async def test_a_disconnect_drops_the_uploads_whose_register_never_ran():
    communicator = await _connect()
    lane = await _busy_uploader(communicator)
    consumer = CONSUMERS[-1]
    await communicator.send_json_to(_register_frame(str(uuid.uuid4())))
    await _spin_until(lambda: lane.queued() == 1, "the register to queue")
    assert len(consumer._pending_uploads) == 1
    disconnecting = asyncio.ensure_future(communicator.disconnect())
    await _spin_until(lambda: lane.waiting() == 1, "the disconnect to wait on the lane")
    RELEASE.set()
    await asyncio.wait_for(disconnecting, PATIENCE)
    assert consumer._pending_uploads == {} and consumer._aborted_uploads == {}


async def test_nothing_is_remembered_of_an_upload_after_it_ends_its_view_goes_or_the_socket_closes():
    communicator = await _connect()
    try:
        lane = await _busy_uploader(communicator)
        consumer = CONSUMERS[-1]
        done, cancelled, orphan = (str(uuid.uuid4()) for _ in range(3))
        await communicator.send_json_to(_register_frame(done))
        await communicator.send_to(bytes_data=_chunk(done, 0, b"abc"))
        await communicator.send_to(bytes_data=_complete(done))
        await communicator.send_json_to(_register_frame(cancelled))
        await communicator.send_to(bytes_data=bytes([0x03]) + uuid.UUID(cancelled).bytes)
        await communicator.send_json_to(_register_frame(orphan))
        await _spin_until(lambda: lane.queued() == 6, "the frames to queue")
        assert set(consumer._pending_uploads) == {done, cancelled, orphan}
        # The register of the last upload is queued behind an unmount of the view.
        await communicator.send_json_to({"type": "unmount", "target_id": "up"})
        await _spin_until(lambda: lane.queued() == 7, "the unmount to queue")
        RELEASE.set()
        await _spin_until(lambda: "up" not in consumer._slot_map(), "the view's release")
        await _spin_until(lambda: not lane.busy(), "the lane to drain")
        assert consumer._pending_uploads == {}, consumer._pending_uploads
        assert consumer._aborted_uploads == {}
    finally:
        RELEASE.set()
        await communicator.disconnect()
    assert consumer._pending_uploads == {} and consumer._aborted_uploads == {}
