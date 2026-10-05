"""#3252 over SSE: views mounted beside the page view are independently live.

An SSE session hosts the page view (the stream GET mounts it) and any number of
views mounted beside it: a ``dj-lazy`` container hydrating posts a ``mount``
frame with the container's ``target_id``. Each is a root view of its own (own
runtime, state, saves, authorization and teardown); its frames carry its
``target_id``; and a page with no sibling views behaves as before.

Driven through the real stream and message views, as the browser does. The
WebSocket counterpart is ``test_multi_view_socket_3252.py``.
"""

import asyncio
import json
import uuid

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth import get_user
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory, override_settings
from django.urls import path
from django.utils.functional import SimpleLazyObject

from djust import LiveView, event_handler, sse
from djust.decorators import state
from djust.sse import DjustSSEMessageView, DjustSSEStreamView, _sse_sessions

pytestmark = [pytest.mark.asyncio, pytest.mark.django_db(transaction=True)]

MOD = __name__
#: ``(what, tag)`` records.
EVENTS: list = []
#: ``target_id or None`` -> the newest view mounted there.
VIEWS: dict = {}


class _Counted:
    exposure_policy = "legacy"

    def mount(self, request, **kwargs):
        self.count = 0
        self.tag = getattr(self, "_djust_slot_target", None) or "page"
        VIEWS[self.tag] = self
        EVENTS.append(("mount", self.tag))

    @event_handler()
    def bump(self, **kwargs):
        self.count += 1
        EVENTS.append(("bump", self.tag))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["count"] = self.count
        return context


class Page(_Counted, LiveView):
    template = '<div dj-root dj-view="' + MOD + '.Page"><b>page={{ count }}</b></div>'


class Widget(_Counted, LiveView):
    template = '<div dj-view="' + MOD + '.Widget"><b>widget={{ count }}</b></div>'


class Guarded(_Counted, LiveView):
    login_required = True
    template = '<div dj-view="' + MOD + '.Guarded"><b>guarded</b></div>'


class Persisted(LiveView):
    """An explicit view whose count persists on the server."""

    exposure_policy = "explicit"
    count = state(0, persist="server")
    template = '<div dj-view="' + MOD + '.Persisted"><b>count={{ count }}</b></div>'

    def mount(self, request, **kwargs):
        VIEWS[getattr(self, "_djust_slot_target", None) or "page"] = self

    @event_handler()
    def bump(self, **kwargs):
        self.count += 1

    def get_context_data(self, **kwargs):
        return super().get_context_data(count=self.count, **kwargs)


class Snap(LiveView):
    exposure_policy = "legacy"
    enable_state_snapshot = True
    template = '<div dj-view="' + MOD + '.Snap"><b>n={{ n }}</b></div>'

    def mount(self, request, **kwargs):
        self.n = 0

    @event_handler()
    def bump(self, **kwargs):
        self.n += 1


urlpatterns = [path("page/", Page.as_view())]


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    monkeypatch.setattr(sse, "_SESSION_LINGER_S", 0)
    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)
    EVENTS.clear()
    VIEWS.clear()
    _sse_sessions.clear()
    with override_settings(
        ROOT_URLCONF=__name__,
        LIVEVIEW_ALLOWED_MODULES=["djust", __name__],
        DEBUG=False,
        DJUST_CONFIG={},
        DJUST_TENANTS={},
    ):
        yield
    _sse_sessions.clear()
    VIEWS.clear()


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


async def _frame(stream, *types, timeout=10.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        remaining = deadline - loop.time()
        assert remaining > 0, "no frame of type %r" % (types,)
        chunk = await asyncio.wait_for(stream.__anext__(), timeout=remaining)
        if not chunk.startswith("data:"):
            continue
        frame = json.loads(chunk[len("data:") :])
        if frame.get("type") in types:
            return frame


async def _open(cls=Page, key=None):
    key = key or await sync_to_async(_fresh_key)()
    sid = str(uuid.uuid4())
    request = await sync_to_async(_request)(
        "GET", f"/djust/sse/{sid}/", {"view": MOD + "." + cls.__name__, "_djust_url": "/page/"}, key
    )
    response = await DjustSSEStreamView().get(request, session_id=sid)
    assert response.status_code == 200
    stream = response._iterator
    await _frame(stream, "sse_connect")
    await _frame(stream, "mount")
    return _sse_sessions[sid], key, stream


async def _post(session, key, body, *, status=200):
    request = await sync_to_async(_request)(
        "POST", f"/djust/sse/{session.session_id}/message/", body, key
    )
    response = await DjustSSEMessageView().post(request, session_id=session.session_id)
    assert response.status_code == status, response.content
    return response


async def _mount(session, key, stream, cls, target_id, **extra):
    frame = {"type": "mount", "view": MOD + "." + cls.__name__, "url": "/page/", "params": {}}
    await _post(session, key, {**frame, "target_id": target_id, **extra})
    return await _frame(stream, "mount", "error", "navigate")


async def _bump(session, key, stream, target_id=None, ref=1):
    body = {"type": "event", "event": "bump", "params": {}, "ref": ref}
    if target_id:
        body["target_id"] = target_id
    await _post(session, key, body)
    return await _frame(stream, "patch", "html_update", "noop", "error")


async def _until(predicate, what):
    for _ in range(500):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("timed out waiting for " + what)


# --------------------------------------------------------------------------- #
# Mount and events
# --------------------------------------------------------------------------- #


async def test_a_view_mounts_beside_the_page_view_and_leaves_it_as_it_was():
    session, key, stream = await _open()
    try:
        page = session.view_instance
        reply = await _mount(session, key, stream, Widget, "w1")
        assert reply["type"] == "mount", reply
        assert reply["target_id"] == "w1"
        assert reply["view"] == MOD + ".Widget"
        assert "widget=0" in reply["html"]
        # The page view is still the session's, and was not remounted.
        assert session.view_instance is page
        assert EVENTS == [("mount", "page"), ("mount", "w1")]
        assert set(session._slots) == {"w1"}
        assert VIEWS["w1"]._websocket_session_id == session.session_id + ".w1"
    finally:
        await stream.aclose()


async def test_an_event_runs_on_the_view_it_names_and_only_there():
    session, key, stream = await _open()
    try:
        await _mount(session, key, stream, Widget, "w1")
        await _mount(session, key, stream, Widget, "w2")
        for target, times in (("w1", 2), ("w2", 3), (None, 1)):
            for i in range(times):
                reply = await _bump(session, key, stream, target, ref=10 + i)
                assert reply["type"] != "error", reply
                # A slot's frames are addressed to its container; the page's are not.
                assert reply.get("target_id") == target
        assert [VIEWS[t].count for t in ("page", "w1", "w2")] == [1, 2, 3]
        assert [t for (k, t) in EVENTS if k == "bump"] == ["w1", "w1", "w2", "w2", "w2", "page"]
    finally:
        await stream.aclose()


async def test_the_page_view_still_works_with_no_sibling_views_and_its_frames_carry_no_address():
    session, key, stream = await _open()
    try:
        reply = await _bump(session, key, stream)
        assert reply["type"] != "error" and "target_id" not in reply, reply
        assert session._slots == {}
    finally:
        await stream.aclose()


async def test_mounting_the_same_container_again_replaces_only_that_view():
    session, key, stream = await _open()
    try:
        await _mount(session, key, stream, Widget, "w1")
        await _mount(session, key, stream, Widget, "w2")
        first = VIEWS["w1"]
        await _bump(session, key, stream, "w1")
        await _mount(session, key, stream, Widget, "w1")
        assert VIEWS["w1"] is not first and VIEWS["w1"].count == 0
        assert first._djust_waiters_closed is True  # the old view was released
        assert not getattr(VIEWS["w2"], "_djust_waiters_closed", False)
        assert set(session._slots) == {"w1", "w2"}
    finally:
        await stream.aclose()


# --------------------------------------------------------------------------- #
# Refusals
# --------------------------------------------------------------------------- #


async def test_a_view_the_user_may_not_open_is_refused_on_its_own():
    session, key, stream = await _open()
    try:
        reply = await _mount(session, key, stream, Guarded, "g1")
        assert reply["type"] in ("navigate", "error"), reply
        assert "g1" not in session._slots
        # The page view and the session are untouched.
        assert session.view_instance is not None and session.active
        bumped = await _bump(session, key, stream)
        assert bumped["type"] != "error", bumped
    finally:
        await stream.aclose()


async def test_a_mount_needs_a_valid_address_url_and_params():
    session, key, stream = await _open()
    try:
        for bad in (
            {"target_id": "has space"},
            {"target_id": "w" * 201},
            {"target_id": "w1", "url": "http://evil.example/"},
            {"target_id": "w1", "params": ["not", "a", "dict"]},
        ):
            frame = {
                "type": "mount",
                "view": MOD + ".Widget",
                "url": "/page/",
                "params": {},
                **bad,
            }
            await _post(session, key, frame)
            reply = await _frame(stream, "error")
            assert reply["code"] == "view_unavailable", (bad, reply)
        assert session._slots == {}
    finally:
        await stream.aclose()


async def test_the_number_of_views_a_session_hosts_is_bounded():
    from djust.config import config

    session, key, stream = await _open()
    try:
        with override_settings(LIVEVIEW_CONFIG={"max_views_per_connection": 2}):
            config.reset()
            try:
                assert (await _mount(session, key, stream, Widget, "w1"))["type"] == "mount"
                assert (await _mount(session, key, stream, Widget, "w2"))["type"] == "mount"
                refused = await _mount(session, key, stream, Widget, "w3")
                assert refused["type"] == "error", refused
                assert set(session._slots) == {"w1", "w2"}
                # A view already mounted may still be replaced at the limit.
                assert (await _mount(session, key, stream, Widget, "w2"))["type"] == "mount"
            finally:
                config.reset()
    finally:
        await stream.aclose()


async def test_another_user_cannot_mount_a_view_on_the_session():
    session, key, stream = await _open()
    try:
        other = await sync_to_async(_fresh_key)()
        await _post(
            session,
            other,
            {"type": "mount", "view": MOD + ".Widget", "url": "/page/", "target_id": "w1"},
            status=403,
        )
        assert session._slots == {}
    finally:
        await stream.aclose()


# --------------------------------------------------------------------------- #
# Teardown and navigation
# --------------------------------------------------------------------------- #


async def test_unmount_releases_only_the_view_it_names():
    session, key, stream = await _open()
    try:
        await _mount(session, key, stream, Widget, "w1")
        await _mount(session, key, stream, Widget, "w2")
        w1, w2 = VIEWS["w1"], VIEWS["w2"]
        await _post(session, key, {"type": "unmount", "target_id": "w1"})
        assert set(session._slots) == {"w2"}
        assert w1._djust_waiters_closed is True
        assert not getattr(w2, "_djust_waiters_closed", False)
        # The unmounted address is refused; the others still answer.
        await _post(
            session, key, {"type": "event", "event": "bump", "params": {}, "target_id": "w1"}
        )
        assert (await _frame(stream, "error"))["code"] == "view_unavailable"
        assert (await _bump(session, key, stream, "w2"))["type"] != "error"
        # Unmounting a view that is not mounted does nothing.
        await _post(session, key, {"type": "unmount", "target_id": "never"})
    finally:
        await stream.aclose()


async def test_closing_the_stream_tears_every_view_down():
    session, key, stream = await _open()
    await _mount(session, key, stream, Widget, "w1")
    await _mount(session, key, stream, Widget, "w2")
    views = [VIEWS["page"], VIEWS["w1"], VIEWS["w2"]]
    await stream.aclose()  # the client went away
    await _until(lambda: session.view_instance is None, "the session to drop its view")
    for view in views:
        await _until(lambda v=view: v._djust_waiters_closed is True, "a view's teardown")
    assert session._slots == {}


async def test_a_navigation_replaces_every_view_including_the_ones_beside_the_page():
    session, key, stream = await _open()
    try:
        await _mount(session, key, stream, Widget, "w1")
        w1 = VIEWS["w1"]
        await _post(session, key, {"type": "live_redirect_mount", "url": "/page/", "params": {}})
        await _frame(stream, "mount")
        assert session._slots == {}
        assert w1._djust_waiters_closed is True
        # The new page view is live, and the old address is gone.
        assert (await _bump(session, key, stream))["type"] != "error"
        await _post(
            session, key, {"type": "event", "event": "bump", "params": {}, "target_id": "w1"}
        )
        assert (await _frame(stream, "error"))["code"] == "view_unavailable"
    finally:
        await stream.aclose()


# --------------------------------------------------------------------------- #
# State isolation
# --------------------------------------------------------------------------- #


async def test_views_of_one_class_keep_their_own_persisted_state_across_a_reconnect():
    key = await sync_to_async(_fresh_key)()
    session, key, stream = await _open(Persisted, key)
    try:
        await _mount(session, key, stream, Persisted, "a")
        await _mount(session, key, stream, Persisted, "b")
        await _bump(session, key, stream)
        for _ in range(2):
            await _bump(session, key, stream, "a")
        for _ in range(3):
            await _bump(session, key, stream, "b")
        assert [VIEWS[t].count for t in ("page", "a", "b")] == [1, 2, 3]
    finally:
        await stream.aclose()

    VIEWS.clear()
    session, key, stream = await _open(Persisted, key)
    try:
        a = await _mount(session, key, stream, Persisted, "a")
        b = await _mount(session, key, stream, Persisted, "b")
        assert "count=2" in a["html"] and "count=3" in b["html"], (a["html"], b["html"])
        assert VIEWS["page"].count == 1
    finally:
        await stream.aclose()


async def test_a_view_beside_the_page_view_mints_no_navigation_snapshot():
    session, key, stream = await _open(Snap)
    try:
        slot = await _mount(session, key, stream, Snap, "s1")
        assert "state_snapshot_signed" not in slot, slot.keys()
        page = await _bump(session, key, stream)
        assert isinstance(page.get("state_snapshot_signed"), str)
        sibling = await _bump(session, key, stream, "s1")
        assert "state_snapshot_signed" not in sibling, sibling.keys()
    finally:
        await stream.aclose()
