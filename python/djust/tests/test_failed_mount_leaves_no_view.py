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
answered as the auth check answers it (``Permission denied``; the socket
closes with 4403 outside a ``mount_batch``), any other exception keeps its
error frame; either way the view is dropped, its groups are left, its
presence is untracked and its tick stops, so later frames find no view.
A view whose object is refused after ``mount()`` ran is cleaned up the same
way. Over WebSocket (single mount and ``mount_batch``), the SSE stream, the HTTP
fallback and the HTTP API. (An SSE ``mount`` message on a mounted session is
a no-op here, so the stream's mount is the SSE case.)
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

from djust import LiveView, sse
from djust.decorators import event_handler
from djust.presence import PresenceManager, PresenceMixin
from djust.sse import DjustSSEEventView, DjustSSEStreamView, _sse_sessions
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
    """Admits the user in ``mount()``; the object check after it refuses."""

    template = '<div dj-root dj-view="' + MOD + '.GuardedObject">{{ shown }}</div>'

    def admitted(self, user):
        return True

    def get_object(self):
        return {"pk": 1}

    def has_object_permission(self, request, obj):
        return False


class Page(LiveView):
    template = '<div dj-root dj-view="' + MOD + '.Page"><b>page</b></div>'

    @event_handler()
    def reveal(self, **kwargs):
        RAN.append("Page")


urlpatterns = [path("g/", Guarded.as_view()), path("page/", Page.as_view())]


class _Recorded(LiveViewConsumer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        CONSUMERS.append(self)


def _clear_room():
    for presence in PresenceManager.list_presences(ROOM):
        PresenceManager.leave_presence(ROOM, presence.get("id") or presence.get("user_id"))


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    monkeypatch.setattr(sse, "_SESSION_LINGER_S", 0)
    RAN.clear()
    TICKS.clear()
    CONSUMERS.clear()
    _sse_sessions.clear()
    _clear_room()
    with override_settings(
        ROOT_URLCONF=__name__, LIVEVIEW_ALLOWED_MODULES=["djust", MOD], DEBUG=False
    ):
        yield
    _sse_sessions.clear()
    _clear_room()


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


async def _mount(communicator, cls):
    await communicator.send_json_to(
        {"type": "mount", "view": MOD + "." + cls.__name__, "url": "/g/"}
    )
    return await _outputs(communicator)


async def _reveal(communicator):
    await communicator.send_json_to({"type": "event", "event": "reveal", "params": {}, "ref": 1})
    return await _outputs(communicator)


def _types(frames):
    return [f.get("type") for f in frames]


def _assert_left_nothing(consumer=None):
    assert [r for r in RAN if r != "Page"] == [], "a handler of a view whose mount() raised ran"
    assert PresenceManager.list_presences(ROOM) == [], "its presence is still listed"
    if consumer is not None:
        assert consumer.view_instance is None
        assert getattr(consumer, "_view_group", None) is None
        assert getattr(consumer, "_presence_group", None) is None
        task = getattr(consumer, "_tick_task", None)
        assert task is None or task.done(), "its tick task still runs"


async def test_page_mount_denied_in_mount_is_refused_and_closes_4403():
    communicator = await _connect()
    try:
        frames = await _mount(communicator, Guarded)
        errors = [f for f in frames if f.get("type") == "error"]
        assert [e.get("error") for e in errors] == ["Permission denied"], frames
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


async def test_an_object_refused_after_mount_leaves_no_view():
    """mount() admits the user and tracks presence; the object check refuses."""
    communicator = await _connect()
    try:
        frames = await _mount(communicator, GuardedObject)
        assert "mount" not in _types(frames), frames
        errors = [f for f in frames if f.get("type") == "error"]
        assert [e.get("code") for e in errors] == ["permission_denied"], frames
        assert {"type": "websocket.close", "code": 4403} in frames, frames
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
        assert frames[0].get("error") != "Permission denied"
        after = await _reveal(communicator)
        assert _types(after) == ["error"], after
        assert SECRET not in json.dumps(after)
        # Pushes to the view's group no longer reach the socket.
        await apush_to_view(MOD + "." + cls.__name__, payload={"x": 1}, handler="reveal")
        assert await _outputs(communicator) == []
        _assert_left_nothing(CONSUMERS[-1])
    finally:
        await communicator.disconnect()


@pytest.mark.parametrize("order", ["refused-first", "refused-last"])
async def test_a_batch_entry_whose_mount_raises_is_reported_and_not_mounted(order):
    entries = [
        {"view": MOD + ".Guarded", "url": "/g/", "target_id": "a"},
        {"view": MOD + ".GuardedMissing", "url": "/g/", "target_id": "b"},
    ]
    ok = {"view": MOD + ".Page", "url": "/page/", "target_id": "ok"}
    views = [*entries, ok] if order == "refused-first" else [ok, *entries]
    communicator = await _connect()
    try:
        await communicator.send_json_to({"type": "mount_batch", "views": views})
        frames = await _outputs(communicator)
        reply = [f for f in frames if f.get("type") == "mount_batch"][-1]
        assert [v["target_id"] for v in reply["views"]] == ["ok"], reply
        assert sorted(f["target_id"] for f in reply["failed"]) == ["a", "b"], reply
        assert "websocket.close" not in _types(frames)
        after = await _reveal(communicator)
        assert SECRET not in json.dumps(after)
        consumer = CONSUMERS[-1]
        assert not isinstance(consumer.view_instance, Guarded)
        _assert_left_nothing()
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
    request.user = get_user(request)
    request.tenant = None
    return request


async def _sse_frame(stream, *types, timeout=10.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        chunk = await asyncio.wait_for(stream.__anext__(), timeout=deadline - loop.time())
        if isinstance(chunk, bytes):
            chunk = chunk.decode()
        if chunk.startswith("data:"):
            frame = json.loads(chunk[len("data:") :])
            if frame.get("type") in types:
                return frame


async def _sse_open(cls, url):
    key = await sync_to_async(_fresh_key)()
    sid = str(uuid.uuid4())
    request = await sync_to_async(_request)(
        "GET", f"/djust/sse/{sid}/", {"view": MOD + "." + cls.__name__, "_djust_url": url}, key
    )
    response = await DjustSSEStreamView().get(request, session_id=sid)
    stream = response.streaming_content
    await _sse_frame(stream, "sse_connect")
    first = await _sse_frame(stream, "mount", "navigate", "error")
    return sid, key, stream, first


async def _sse_event(sid, key, body):
    request = await sync_to_async(_request)("POST", f"/djust/sse/{sid}/event/", body, key)
    return await DjustSSEEventView().post(request, session_id=sid)


@pytest.mark.parametrize("cls, error", [(Guarded, "Permission denied"), (GuardedMissing, None)])
async def test_over_sse_a_view_whose_mount_raises_is_not_mounted(cls, error):
    sid, key, stream, first = await _sse_open(cls, "/g/")
    try:
        assert first["type"] == "error", first
        if error:
            assert first["error"] == error
        assert _sse_sessions.get(sid) is None, "the refused stream was registered"
        response = await _sse_event(sid, key, {"event": "reveal", "params": {}})
        assert response.status_code >= 400
        assert SECRET not in response.content.decode()
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
