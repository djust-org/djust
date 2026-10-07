"""#3252: a refused lazy or batched view fails alone.

A view mounted beside the page view (``dj-lazy``, ``mount_batch``) that the
user may not see (a login is required, a permission or an object permission is
missing, an ``on_mount`` hook redirects) used to answer with the page-level
refusal: a ``navigate`` frame that sent the whole page to the login page. It now
answers with a ``view_refused`` frame addressed to its container: the client
shows the refusal there, and the page view, the other views and the page's URL
are untouched. The page view's own refusal is the page's and is unchanged.

Over WebSocket and SSE, through the real consumer and the real stream and
message views. The HTTP fallback hosts no view beside the page view, so there is
nothing for it to refuse (``test_sse_view_slots_3252.py``,
``tests/playwright/test_multi_view_sse_http.py``).
"""

import asyncio
import json
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
from djust.hooks import on_mount
from djust.sse import DjustSSEMessageView, DjustSSEStreamView, _sse_sessions

pytestmark = [pytest.mark.asyncio, pytest.mark.django_db(transaction=True)]

MOD = __name__
CLICKS: list = []


@on_mount
def send_elsewhere(view, request, **kwargs):
    return "/elsewhere/"


class Page(LiveView):
    exposure_policy = "legacy"
    template = '<div dj-root dj-view="' + MOD + '.Page"><b>page</b></div>'

    @event_handler()
    def click(self, **kwargs):
        CLICKS.append("page")


class Widget(Page):
    template = '<div dj-view="' + MOD + '.Widget"><b>widget</b></div>'

    @event_handler()
    def click(self, **kwargs):
        CLICKS.append("widget")


class NeedsLogin(Page):
    login_required = True
    template = '<div dj-view="' + MOD + '.NeedsLogin"><b>secret</b></div>'


class NeedsPermission(Page):
    login_required = True
    permission_required = "auth.delete_user"
    template = '<div dj-view="' + MOD + '.NeedsPermission"><b>secret</b></div>'


class ObjectDenied(Page):
    template = '<div dj-view="' + MOD + '.ObjectDenied"><b>secret</b></div>'

    def get_object(self):
        return object()

    def has_object_permission(self, request, obj):
        return False


class Redirected(Page):
    on_mount = [send_elsewhere]
    template = '<div dj-view="' + MOD + '.Redirected"><b>secret</b></div>'


#: view -> the reason its refusal names, and whether it names where to go.
REFUSALS = [
    (NeedsLogin, "login_required", True),
    (ObjectDenied, "permission_denied", False),
    (Redirected, "redirect", True),
]

urlpatterns = [path("page/", Page.as_view())]


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    monkeypatch.setattr(sse, "_SESSION_LINGER_S", 0)
    CLICKS.clear()
    _sse_sessions.clear()
    with override_settings(
        ROOT_URLCONF=__name__, LIVEVIEW_ALLOWED_MODULES=["djust", MOD], DEBUG=False
    ):
        yield
    _sse_sessions.clear()


def _fresh_key():
    store = SessionStore()
    store.create()
    return store.session_key


# --------------------------------------------------------------------------- #
# WebSocket
# --------------------------------------------------------------------------- #


async def _connect():
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    communicator.scope["session"] = SessionStore(await sync_to_async(_fresh_key)())
    communicator.scope["user"] = AnonymousUser()
    assert (await communicator.connect())[0]
    await communicator.receive_json_from(timeout=3)
    return communicator


async def _frames(communicator, *types, timeout=10.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    seen = []
    while True:
        seen.append(await communicator.receive_json_from(timeout=deadline - loop.time()))
        if seen[-1].get("type") in types:
            return seen


async def _mount(communicator, cls, target_id=None):
    frame = {"type": "mount", "view": MOD + "." + cls.__name__, "url": "/page/"}
    if target_id:
        frame["target_id"] = target_id
    await communicator.send_json_to(frame)


async def _click(communicator, target_id=None):
    frame = {"type": "event", "event": "click", "params": {}, "ref": 1}
    if target_id:
        frame["target_id"] = target_id
    await communicator.send_json_to(frame)
    return (await _frames(communicator, "patch", "html_update", "noop", "error"))[-1]


@pytest.mark.parametrize("cls, reason, names_a_target", REFUSALS)
async def test_a_refused_lazy_view_answers_in_its_own_container_and_navigates_nothing(
    cls, reason, names_a_target
):
    communicator = await _connect()
    try:
        await _mount(communicator, Page)
        await _frames(communicator, "mount")
        await _mount(communicator, Widget, "w1")
        await _frames(communicator, "mount")

        await _mount(communicator, cls, "refused")
        frames = await _frames(communicator, "view_refused", "navigate", "error", "mount")
        assert frames[-1]["type"] == "view_refused", frames
        refusal = frames[-1]
        assert refusal["target_id"] == "refused"
        assert refusal["reason"] == reason
        assert ("to" in refusal) is names_a_target, refusal
        assert refusal["code"] == "permission_denied"
        # Nothing else: no page navigation, and no error frame for the page.
        assert [f["type"] for f in frames] == ["view_refused"], frames
        # The page view and the sibling are live; the refused address is not.
        assert (await _click(communicator))["type"] != "error"
        assert (await _click(communicator, "w1"))["type"] != "error"
        assert (await _click(communicator, "refused"))["type"] == "error"
        assert CLICKS == ["page", "widget"]
    finally:
        await communicator.disconnect()


async def test_a_signed_in_user_without_the_permission_is_refused_in_the_container():
    from channels.testing import WebsocketCommunicator
    from django.contrib.auth import get_user_model

    from djust.websocket import LiveViewConsumer

    user = await sync_to_async(get_user_model().objects.create_user)("plain3252", password="x")
    communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    communicator.scope["session"] = SessionStore(await sync_to_async(_fresh_key)())
    communicator.scope["user"] = user
    assert (await communicator.connect())[0]
    await communicator.receive_json_from(timeout=3)
    try:
        await _mount(communicator, Page)
        await _frames(communicator, "mount")
        await _mount(communicator, NeedsPermission, "p")
        frames = await _frames(communicator, "view_refused", "navigate", "error", "mount")
        assert [f["type"] for f in frames] == ["view_refused"], frames
        assert frames[-1]["reason"] == "permission_denied" and "to" not in frames[-1]
        assert (await _click(communicator))["type"] != "error"
    finally:
        await communicator.disconnect()


async def test_a_refused_view_is_reported_once_and_left_unmounted():
    communicator = await _connect()
    try:
        await _mount(communicator, Page)
        await _frames(communicator, "mount")
        for _ in range(2):
            await _mount(communicator, NeedsLogin, "refused")
            frames = await _frames(communicator, "view_refused")
            assert [f["type"] for f in frames] == ["view_refused"]
    finally:
        await communicator.disconnect()


async def test_the_page_views_own_refusal_is_still_the_pages():
    communicator = await _connect()
    try:
        await _mount(communicator, NeedsLogin)
        frames = await _frames(communicator, "navigate", "view_refused", "error")
        assert frames[-1]["type"] == "navigate", frames
        assert not any(f["type"] == "view_refused" for f in frames)
    finally:
        await communicator.disconnect()


async def test_a_batch_reports_each_refused_entry_alone():
    communicator = await _connect()
    try:
        await _mount(communicator, Page)
        await _frames(communicator, "mount")
        await communicator.send_json_to(
            {
                "type": "mount_batch",
                "views": [
                    {"view": MOD + "." + NeedsLogin.__name__, "url": "/page/", "target_id": "a"},
                    {"view": MOD + ".Widget", "url": "/page/", "target_id": "ok"},
                    {"view": MOD + "." + ObjectDenied.__name__, "url": "/page/", "target_id": "c"},
                    {"view": MOD + "." + Redirected.__name__, "url": "/page/", "target_id": "d"},
                ],
            }
        )
        reply = (await _frames(communicator, "mount_batch"))[-1]
        assert [v["target_id"] for v in reply["views"]] == ["ok"]
        assert [(r["target_id"], r["reason"]) for r in reply["refused"]] == [
            ("a", "login_required"),
            ("c", "permission_denied"),
            ("d", "redirect"),
        ]
        assert "navigate" not in reply and reply["failed"] == []
        assert (await _click(communicator, "ok"))["type"] != "error"
    finally:
        await communicator.disconnect()


# --------------------------------------------------------------------------- #
# Event time: a slot whose authority is revoked fails alone
# --------------------------------------------------------------------------- #

CONSUMERS: list = []


async def _signed_in_connect():
    """A socket for a signed-in user, with its consumer recorded."""
    from channels.testing import WebsocketCommunicator
    from django.contrib.auth import get_user_model

    from djust.websocket import LiveViewConsumer

    class _Recorded(LiveViewConsumer):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            CONSUMERS.append(self)

    user = await sync_to_async(get_user_model().objects.create_user)(
        "reauth" + uuid.uuid4().hex[:8], password="x"
    )
    communicator = WebsocketCommunicator(_Recorded.as_asgi(), "/ws/")
    communicator.scope["session"] = SessionStore(await sync_to_async(_fresh_key)())
    communicator.scope["user"] = user
    assert (await communicator.connect())[0]
    await communicator.receive_json_from(timeout=3)
    return communicator


@pytest.fixture
def revocable(monkeypatch):
    """``reauth_on_event`` on, and a way to revoke the signed-in user."""
    from djust.config import config

    async def anonymous(scope):
        return AnonymousUser()

    with override_settings(LIVEVIEW_CONFIG={"reauth_on_event": True}):
        config.reset()
        try:
            yield lambda: monkeypatch.setattr("channels.auth.get_user", anonymous)
        finally:
            config.reset()


async def test_a_slot_whose_authority_is_revoked_is_refused_alone_and_the_page_stays(revocable):
    # (Before: the re-check navigated the whole page to the login page, closed
    # the socket with 4403 and only then sent a view_refused nobody could show.)
    communicator = await _signed_in_connect()
    try:
        await _mount(communicator, Page)
        await _frames(communicator, "mount")
        for cls, target in ((Widget, "w1"), (NeedsLogin, "members")):
            await _mount(communicator, cls, target)
            await _frames(communicator, "mount")
        revocable()
        await _click_frame(communicator, "members", ref=7)
        frames = await _frames(communicator, "view_refused", "navigate")
        assert [f["type"] for f in frames] == ["view_refused"], frames
        assert frames[-1]["target_id"] == "members"
        assert frames[-1]["reason"] == "login_required" and frames[-1]["to"]
        assert set(CONSUMERS[-1]._slot_map()) == {"w1"}
        # The socket is alive and the page view and the sibling answer.
        await communicator.send_json_to({"type": "ping"})
        assert (await _frames(communicator, "pong"))[-1]["type"] == "pong"
        assert (await _click(communicator))["type"] != "error"
        assert (await _click(communicator, "w1"))["type"] != "error"
        assert CLICKS == ["page", "widget"]
    finally:
        await communicator.disconnect()


async def test_the_page_views_own_revoked_authority_still_navigates_and_closes_the_socket(
    revocable,
):
    communicator = await _signed_in_connect()
    try:
        await _mount(communicator, NeedsLogin)
        await _frames(communicator, "mount")
        revocable()
        await communicator.send_json_to({"type": "event", "event": "click", "params": {}, "ref": 1})
        seen = []
        while not seen or seen[-1]["type"] != "websocket.close":
            seen.append(await communicator.receive_output(timeout=5))
        assert [_kind(o) for o in seen] == ["navigate", "websocket.close"], seen
        assert seen[-1]["code"] == 4403
    finally:
        await communicator.disconnect()


def _kind(output):
    if output["type"] == "websocket.send" and output.get("text"):
        return json.loads(output["text"]).get("type")
    return output["type"]


async def _click_frame(communicator, target_id, ref=1):
    await communicator.send_json_to(
        {"type": "event", "event": "click", "params": {}, "ref": ref, "target_id": target_id}
    )


async def test_a_refusal_is_sent_once_per_view_whatever_ends_it():
    communicator = await _signed_in_connect()
    try:
        await _mount(communicator, Page)
        await _frames(communicator, "mount")
        await _mount(communicator, Widget, "w1")
        await _frames(communicator, "mount")
        facade = CONSUMERS[-1]._slot_map()["w1"].facade
        await facade.send_refusal("redirect", "/elsewhere/")
        await facade.send_refusal("redirect", "/elsewhere/")
        await facade.close(4403)  # the close of an already refused view adds nothing
        frames = await _frames(communicator, "view_refused")
        assert [f["type"] for f in frames] == ["view_refused"]
        assert frames[-1]["reason"] == "redirect"
        assert await communicator.receive_nothing(timeout=0.3)
        assert CONSUMERS[-1]._slot_map() == {}
    finally:
        await communicator.disconnect()


async def test_an_event_for_a_refused_or_unmounted_address_carries_view_unavailable():
    communicator = await _connect()
    try:
        await _mount(communicator, Page)
        await _frames(communicator, "mount")
        await _mount(communicator, NeedsLogin, "refused")
        await _frames(communicator, "view_refused")
        await _click_frame(communicator, "refused", ref=5)
        error = (await _frames(communicator, "error"))[-1]
        assert error["code"] == "view_unavailable" and error["ref"] == 5, error
    finally:
        await communicator.disconnect()


# --------------------------------------------------------------------------- #
# SSE
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


async def _sse_frame(stream, *types, timeout=10.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        chunk = await asyncio.wait_for(stream.__anext__(), timeout=deadline - loop.time())
        if chunk.startswith("data:"):
            frame = json.loads(chunk[len("data:") :])
            if frame.get("type") in types:
                return frame


async def _sse_open(cls=Page):
    key = await sync_to_async(_fresh_key)()
    sid = str(uuid.uuid4())
    request = await sync_to_async(_request)(
        "GET", f"/djust/sse/{sid}/", {"view": MOD + "." + cls.__name__, "_djust_url": "/page/"}, key
    )
    response = await DjustSSEStreamView().get(request, session_id=sid)
    stream = response._iterator
    await _sse_frame(stream, "sse_connect")
    first = await _sse_frame(stream, "mount", "navigate", "error")
    return _sse_sessions.get(sid), key, stream, first


async def _sse_post(session, key, body):
    request = await sync_to_async(_request)(
        "POST", f"/djust/sse/{session.session_id}/message/", body, key
    )
    response = await DjustSSEMessageView().post(request, session_id=session.session_id)
    assert response.status_code == 200


@pytest.mark.parametrize("cls, reason, names_a_target", REFUSALS)
async def test_over_sse_a_refused_view_answers_in_its_own_container(cls, reason, names_a_target):
    session, key, stream, first = await _sse_open()
    assert first["type"] == "mount"
    try:
        await _sse_post(
            session,
            key,
            {"type": "mount", "view": MOD + "." + cls.__name__, "url": "/page/", "target_id": "r"},
        )
        frame = await _sse_frame(stream, "view_refused", "navigate", "error", "mount")
        assert frame["type"] == "view_refused", frame
        assert frame["target_id"] == "r" and frame["reason"] == reason
        assert ("to" in frame) is names_a_target
        assert session._slots == {}
        # The page view is still live.
        await _sse_post(session, key, {"type": "event", "event": "click", "params": {}, "ref": 3})
        assert (await _sse_frame(stream, "patch", "html_update", "noop", "error"))[
            "type"
        ] != "error"
        assert CLICKS == ["page"]
    finally:
        await stream.aclose()


async def test_over_sse_the_page_views_own_refusal_is_still_a_navigation():
    session, key, stream, first = await _sse_open(NeedsLogin)
    try:
        assert first["type"] == "navigate", first
    finally:
        await stream.aclose()


async def test_over_sse_a_view_refused_after_it_mounted_shows_the_refusal_and_ends_alone():
    """An authorization failure at event time closes the view with 4403; the
    container shows it, once, and only that view goes."""
    session, key, stream, first = await _sse_open()
    try:
        for target in ("w1", "w2"):
            await _sse_post(
                session,
                key,
                {"type": "mount", "view": MOD + ".Widget", "url": "/page/", "target_id": target},
            )
            await _sse_frame(stream, "mount")
        slot = session._slots["w1"]
        await slot.session.close(code=4403)
        await slot.session.close(code=4403)  # a second close adds nothing
        frame = await _sse_frame(stream, "view_refused")
        assert frame["target_id"] == "w1" and frame["reason"] == "permission_denied"
        assert set(session._slots) == {"w2"}
        assert session.active
        await _sse_post(
            session,
            key,
            {"type": "event", "event": "click", "params": {}, "ref": 2, "target_id": "w2"},
        )
        reply = await _sse_frame(stream, "patch", "html_update", "noop", "error", "view_refused")
        assert reply["type"] != "view_refused" and reply.get("target_id") == "w2", reply
    finally:
        await stream.aclose()


async def test_over_sse_a_slot_whose_authority_is_revoked_is_refused_alone(revocable):
    from unittest.mock import patch

    session, key, stream, first = await _sse_open()
    try:
        await _sse_post(
            session,
            key,
            {"type": "mount", "view": MOD + ".Widget", "url": "/page/", "target_id": "w1"},
        )
        await _sse_frame(stream, "mount")
        session._slots["w1"].view.login_required = True  # a view that requires auth
        with patch("djust.auth.core.check_view_auth_lightweight", return_value=False):
            await _sse_post(
                session,
                key,
                {"type": "event", "event": "click", "params": {}, "ref": 4, "target_id": "w1"},
            )
        frames = []
        while not frames or frames[-1]["type"] != "view_refused":
            frames.append(await _sse_frame(stream, "view_refused", "error", "navigate"))
        assert [f["type"] for f in frames] == ["view_refused"], frames
        assert frames[-1]["reason"] == "permission_denied" and frames[-1]["target_id"] == "w1"
        assert session._slots == {} and session.active
        await _sse_post(session, key, {"type": "event", "event": "click", "params": {}, "ref": 5})
        assert (await _sse_frame(stream, "patch", "html_update", "noop", "error"))[
            "type"
        ] != "error"
    finally:
        await stream.aclose()
