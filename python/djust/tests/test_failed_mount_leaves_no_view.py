"""A view whose ``mount()`` raises is not left mounted on any transport.

``mount()`` is where an application checks what the user may see::

    def mount(self, request, **kwargs):
        if not request.user.is_authenticated:
            raise PermissionDenied("Must be logged in")

The HTTP GET turns that into a 403. The live mount used to answer with an
error frame and keep the view as the connection's mounted view: later
``event`` frames ran its handlers and the replies carried its rendered HTML,
and the view stayed in the groups it joined, with its presence tracked and
its tick running.

A mount that raises now ends like a refused mount. ``PermissionDenied`` is
answered as the auth check answers it (``code="permission_denied"``; the page
view's socket closes with 4403, a view beside the page gets ``view_refused``),
any other exception keeps its error frame; either way the view is dropped, its
groups are left, its presence is untracked and its tick stops, so later frames
for it are refused. Over WebSocket (page view, lazy view, ``mount_batch``),
SSE (stream and message mount), the HTTP fallback and the HTTP API.
"""

import asyncio
import json
import uuid

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth import get_user, get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.test import RequestFactory, override_settings
from django.urls import path
from django.utils.functional import SimpleLazyObject

from djust import LiveView, event_handler, sse
from djust.presence import PresenceManager, PresenceMixin
from djust.sse import DjustSSEMessageView, DjustSSEStreamView, _sse_sessions
from djust.websocket import LiveViewConsumer

pytestmark = [pytest.mark.asyncio, pytest.mark.django_db(transaction=True)]

MOD = __name__
ROOM = "failed-mount-room"
SECRET = "TOP-SECRET-ROW"
RAN: list = []
TICKS: list = []
CONSUMERS: list = []


class Guarded(PresenceMixin, LiveView):
    """Checks the user in ``mount()``, after joining presence, as apps do."""

    exposure_policy = "legacy"
    login_required = False
    presence_key = ROOM
    tick_interval = 20
    refusal = PermissionDenied
    template = '<div dj-root dj-view="' + MOD + '.Guarded">{{ shown }}</div>'

    def mount(self, request, **kwargs):
        self.shown = "nothing"
        self.track_presence()
        if not self.admitted(request.user):
            raise self.refusal("Must be logged in")

    def admitted(self, user):
        return user.is_authenticated

    def handle_tick(self):
        TICKS.append(type(self).__name__)

    @event_handler()
    def reveal(self, **kwargs):
        RAN.append(type(self).__name__)
        self.shown = SECRET


class GuardedMissing(Guarded):
    refusal = Http404
    template = '<div dj-root dj-view="' + MOD + '.GuardedMissing">{{ shown }}</div>'


class GuardedBroken(Guarded):
    refusal = ValueError
    template = '<div dj-root dj-view="' + MOD + '.GuardedBroken">{{ shown }}</div>'


class GuardedObject(Guarded):
    """Admitted by ``mount()``, then refused by the object check after it."""

    template = '<div dj-root dj-view="' + MOD + '.GuardedObject">{{ shown }}</div>'

    def admitted(self, user):
        return True

    def get_object(self):
        return {"pk": 1}

    def has_object_permission(self, request, obj):
        return False


class GuardedLate(Guarded):
    """Admitted by ``mount()``; a mount step after it raises."""

    template = '<div dj-root dj-view="' + MOD + '.GuardedLate">{{ shown }}</div>'

    def admitted(self, user):
        return True

    def _capture_dirty_baseline(self):
        raise RuntimeError("baseline failed")


class Page(LiveView):
    exposure_policy = "legacy"
    template = '<div dj-root dj-view="' + MOD + '.Page"><b>page</b></div>'

    @event_handler()
    def reveal(self, **kwargs):
        RAN.append("Page")


urlpatterns = [path("g/", Guarded.as_view()), path("page/", Page.as_view())]


class _Recorded(LiveViewConsumer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        CONSUMERS.append(self)


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    monkeypatch.setattr(sse, "_SESSION_LINGER_S", 0)
    RAN.clear()
    TICKS.clear()
    CONSUMERS.clear()
    _sse_sessions.clear()
    with override_settings(
        ROOT_URLCONF=__name__, LIVEVIEW_ALLOWED_MODULES=["djust", MOD], DEBUG=False
    ):
        yield
    _sse_sessions.clear()
    for presence in PresenceManager.list_presences(ROOM):
        PresenceManager.leave_presence(ROOM, presence.get("id"))


def _fresh_key():
    store = SessionStore()
    store.create()
    return store.session_key


# --------------------------------------------------------------------------- #
# WebSocket
# --------------------------------------------------------------------------- #


async def _connect(user=None):
    from channels.testing import WebsocketCommunicator

    communicator = WebsocketCommunicator(_Recorded.as_asgi(), "/ws/")
    communicator.scope["session"] = SessionStore(await sync_to_async(_fresh_key)())
    communicator.scope["user"] = user or AnonymousUser()
    assert (await communicator.connect())[0]
    await communicator.receive_json_from(timeout=3)
    return communicator


async def _outputs(communicator, quiet=0.5):
    """Every frame (and a close) until the socket is quiet."""
    seen = []
    while not await communicator.receive_nothing(timeout=quiet):
        output = await communicator.receive_output(timeout=3)
        if output["type"] == "websocket.close":
            seen.append({"type": "websocket.close", "code": output.get("code")})
        else:
            seen.append(json.loads(output.get("text") or "{}"))
    return seen


async def _mount(communicator, cls, target_id=None):
    frame = {"type": "mount", "view": MOD + "." + cls.__name__, "url": "/g/"}
    if target_id:
        frame["target_id"] = target_id
    await communicator.send_json_to(frame)
    return await _outputs(communicator)


async def _reveal(communicator, target_id=None, ref=1):
    frame = {"type": "event", "event": "reveal", "params": {}, "ref": ref}
    if target_id:
        frame["target_id"] = target_id
    await communicator.send_json_to(frame)
    return await _outputs(communicator)


def _types(frames):
    return [f.get("type") for f in frames]


def _assert_left_nothing(consumer=None):
    assert RAN == [], "a handler of a view whose mount() raised ran"
    assert PresenceManager.list_presences(ROOM) == [], "its presence is still listed"
    if consumer is not None:
        assert consumer.view_instance is None
        assert getattr(consumer, "_view_group", None) is None
        assert not getattr(consumer, "_presence_groups", None)
        task = getattr(consumer, "_tick_task", None)
        assert task is None or task.done(), "its tick task still runs"


async def test_page_mount_denied_in_mount_is_refused_and_closes_4403():
    communicator = await _connect()
    try:
        frames = await _mount(communicator, Guarded)
        errors = [f for f in frames if f.get("type") == "error"]
        assert [e.get("code") for e in errors] == ["permission_denied"], frames
        assert {"type": "websocket.close", "code": 4403} in frames, frames
        assert "mount" not in _types(frames)
        # The event after the refusal reaches no view.
        after = await _reveal(communicator)
        assert SECRET not in json.dumps(after), after
        ticks = len(TICKS)
        await asyncio.sleep(0.1)
        assert len(TICKS) == ticks, "the refused view keeps ticking"
        _assert_left_nothing(CONSUMERS[-1])
    finally:
        await communicator.disconnect()


@pytest.mark.parametrize("cls", [GuardedMissing, GuardedBroken])
async def test_page_mount_that_raises_otherwise_leaves_no_view(cls):
    from djust.push import apush_to_view

    communicator = await _connect()
    try:
        frames = await _mount(communicator, cls)
        assert _types(frames) == ["error"], frames
        assert "permission_denied" != frames[0].get("code")
        after = await _reveal(communicator)
        assert _types(after) == ["error"], after
        assert SECRET not in json.dumps(after)
        # Pushes to the view's group no longer reach the socket.
        await apush_to_view(MOD + "." + cls.__name__, payload={"x": 1}, handler="reveal")
        assert await _outputs(communicator) == []
        _assert_left_nothing(CONSUMERS[-1])
    finally:
        await communicator.disconnect()


async def test_a_lazy_view_whose_mount_raises_is_refused_in_its_container():
    communicator = await _connect()
    try:
        await _mount(communicator, Page)
        frames = await _mount(communicator, Guarded, "g")
        # Only the refusal is addressed to the container. (A presence message
        # already queued for the socket may still re-render the page view.)
        for_slot = [f for f in frames if f.get("target_id") == "g"]
        assert _types(for_slot) == ["view_refused"], json.dumps(frames)
        assert for_slot[0]["reason"] == "permission_denied"
        assert "error" not in _types(frames) and SECRET not in json.dumps(frames)
        after = await _reveal(communicator, "g")
        assert _types(after) == ["error"] and after[0]["code"] == "view_unavailable", after
        assert SECRET not in json.dumps(after)
        assert CONSUMERS[-1]._slot_map() == {}
        page = await _reveal(communicator)
        assert "error" not in _types(page) and RAN == ["Page"]
        RAN.clear()
        _assert_left_nothing()
    finally:
        await communicator.disconnect()


async def test_a_batch_entry_whose_mount_raises_is_refused_alone():
    communicator = await _connect()
    try:
        await _mount(communicator, Page)
        await communicator.send_json_to(
            {
                "type": "mount_batch",
                "views": [
                    {"view": MOD + ".Guarded", "url": "/g/", "target_id": "a"},
                    {"view": MOD + ".Page", "url": "/page/", "target_id": "ok"},
                    {"view": MOD + ".GuardedMissing", "url": "/g/", "target_id": "b"},
                ],
            }
        )
        frames = await _outputs(communicator)
        reply = [f for f in frames if f.get("type") == "mount_batch"][-1]
        assert [v["target_id"] for v in reply["views"]] == ["ok"], reply
        assert [(r["target_id"], r["reason"]) for r in reply["refused"]] == [
            ("a", "permission_denied")
        ]
        assert [f["target_id"] for f in reply["failed"]] == ["b"]
        assert "websocket.close" not in _types(frames)
        for target in ("a", "b"):
            after = await _reveal(communicator, target)
            assert _types(after) == ["error"], after
            assert SECRET not in json.dumps(after)
        assert set(CONSUMERS[-1]._slot_map()) == {"ok"}
        assert "error" not in _types(await _reveal(communicator, "ok"))
        assert RAN == ["Page"]
        RAN.clear()
        _assert_left_nothing()
    finally:
        await communicator.disconnect()


async def test_an_object_refusal_after_mount_leaves_no_view():
    """mount() ran (presence tracked, groups joined); the object check refuses."""
    communicator = await _connect()
    try:
        await communicator.send_json_to(
            {"type": "mount", "view": MOD + ".GuardedObject", "url": "/g/"}
        )
        frames = await _outputs(communicator, quiet=1.0)
        errors = [f for f in frames if f.get("type") == "error"]
        assert [e.get("code") for e in errors] == ["permission_denied"], frames
        assert {"type": "websocket.close", "code": 4403} in frames, frames
        assert "mount" not in _types(frames)
        after = await _reveal(communicator)
        assert SECRET not in json.dumps(after), after
        _assert_left_nothing(CONSUMERS[-1])
    finally:
        await communicator.disconnect()


async def test_an_object_refusal_beside_the_page_leaves_no_view():
    communicator = await _connect()
    try:
        await _mount(communicator, Page)
        await communicator.send_json_to(
            {"type": "mount", "view": MOD + ".GuardedObject", "url": "/g/", "target_id": "o"}
        )
        frames = await _outputs(communicator, quiet=1.0)
        for_slot = [f for f in frames if f.get("target_id") == "o"]
        assert _types(for_slot) == ["view_refused"], json.dumps(frames)
        assert for_slot[0]["reason"] == "permission_denied"
        assert "websocket.close" not in _types(frames)
        after = await _reveal(communicator, "o")
        assert _types(after) == ["error"] and after[0]["code"] == "view_unavailable", after
        assert CONSUMERS[-1]._slot_map() == {}
        page = await _reveal(communicator)
        assert "error" not in _types(page) and RAN == ["Page"]
        RAN.clear()
        _assert_left_nothing()
    finally:
        await communicator.disconnect()


async def test_a_mount_step_after_mount_that_raises_leaves_no_view():
    """mount() ran (presence tracked, groups joined); a later step raises."""
    communicator = await _connect()
    try:
        frames = await _mount(communicator, GuardedLate)
        assert _types(frames) == ["error"], frames
        assert "permission_denied" != frames[0].get("code")
        consumer = CONSUMERS[-1]
        assert consumer._runtime.view_instance is None
        after = await _reveal(communicator)
        assert _types(after) == ["error"], after
        assert SECRET not in json.dumps(after)
        _assert_left_nothing(consumer)
    finally:
        await communicator.disconnect()


async def test_the_same_view_still_mounts_for_a_signed_in_user():
    user = await sync_to_async(get_user_model().objects.create_user)(
        "fm" + uuid.uuid4().hex[:8], password="x"
    )
    communicator = await _connect(user)
    try:
        frames = await _mount(communicator, Guarded)
        assert "mount" in _types(frames), frames
        after = await _reveal(communicator)
        assert SECRET in json.dumps(after)
        assert RAN == ["Guarded"]
        assert len(PresenceManager.list_presences(ROOM)) == 1
        assert CONSUMERS[-1].view_instance is not None
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


async def _sse_open(cls):
    key = await sync_to_async(_fresh_key)()
    sid = str(uuid.uuid4())
    request = await sync_to_async(_request)(
        "GET", f"/djust/sse/{sid}/", {"view": MOD + "." + cls.__name__, "_djust_url": "/g/"}, key
    )
    response = await DjustSSEStreamView().get(request, session_id=sid)
    stream = response._iterator
    await _sse_frame(stream, "sse_connect")
    first = await _sse_frame(stream, "mount", "navigate", "error")
    return sid, key, stream, first


async def _sse_message(sid, key, body):
    request = await sync_to_async(_request)("POST", f"/djust/sse/{sid}/message/", body, key)
    return await DjustSSEMessageView().post(request, session_id=sid)


@pytest.mark.parametrize("cls, code", [(Guarded, "permission_denied"), (GuardedMissing, None)])
async def test_over_sse_a_page_view_whose_mount_raises_is_not_mounted(cls, code):
    sid, key, stream, first = await _sse_open(cls)
    try:
        assert first["type"] == "error" and first.get("code") == code, first
        assert _sse_sessions.get(sid) is None, "the refused stream was registered"
        response = await _sse_message(sid, key, {"type": "event", "event": "reveal", "params": {}})
        assert response.status_code >= 400
        assert SECRET not in response.content.decode()
        _assert_left_nothing()
    finally:
        await stream.aclose()


async def test_over_sse_a_mount_step_after_mount_that_raises_leaves_no_view():
    """The exception still reaches the stream view, as before; nothing stays."""
    with pytest.raises(RuntimeError, match="baseline failed"):
        await _sse_open(GuardedLate)
    assert _sse_sessions == {}
    _assert_left_nothing()


async def test_over_sse_a_message_mount_whose_mount_raises_is_refused_in_its_container():
    sid, key, stream, first = await _sse_open(Page)
    try:
        assert first["type"] == "mount"
        session = _sse_sessions[sid]
        response = await _sse_message(
            sid, key, {"type": "mount", "view": MOD + ".Guarded", "url": "/g/", "target_id": "g"}
        )
        assert response.status_code == 200
        frame = await _sse_frame(stream, "view_refused", "error", "mount")
        assert frame["type"] == "view_refused" and frame["reason"] == "permission_denied", frame
        assert session._slots == {}
        await _sse_message(
            sid, key, {"type": "event", "event": "reveal", "params": {}, "ref": 2, "target_id": "g"}
        )
        reply = await _sse_frame(stream, "patch", "html_update", "noop", "error")
        assert reply["type"] == "error" and SECRET not in json.dumps(reply), reply
        _assert_left_nothing()
    finally:
        await stream.aclose()


# --------------------------------------------------------------------------- #
# HTTP fallback and HTTP API: the view is built per request, and refused there
# --------------------------------------------------------------------------- #


async def test_http_fallback_post_runs_no_handler_when_mount_raises():
    from django.test import AsyncClient

    response = await AsyncClient().post(
        "/g/", data=json.dumps({"event": "reveal", "params": {}}), content_type="application/json"
    )
    assert response.status_code in (403, 500), (response.status_code, response.content)
    assert SECRET not in response.content.decode()
    assert RAN == []


async def test_http_api_runs_no_handler_when_mount_raises():
    from djust.api.dispatch import dispatch_api
    from djust.api.registry import register_api_view, reset_registry

    class Api(Guarded):
        api_name = "failed-mount.guarded"

        def admitted(self, user):
            return user.username == "owner"

        @event_handler(expose_api=True)
        def reveal(self, **kwargs):
            RAN.append("Api")
            return SECRET

    reset_registry()
    try:
        register_api_view("failed-mount.guarded", Api)
        request = RequestFactory().post("/djust/api/x/", data="{}", content_type="application/json")
        request.user = await sync_to_async(get_user_model().objects.create_user)(
            "other" + uuid.uuid4().hex[:8], password="x"
        )
        request.session = SessionStore()
        await sync_to_async(request.session.save)()
        request._dont_enforce_csrf_checks = True
        response = await sync_to_async(dispatch_api)(request, "failed-mount.guarded", "reveal")
    finally:
        reset_registry()
    assert response.status_code == 403
    assert SECRET not in response.content.decode()
    assert RAN == []
