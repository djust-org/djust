"""Channel-layer group membership follows the mount's auth outcome.

A mounted view joins its server-push view group, its presence group and any
db_notify groups. Those joins happen only once the pre-mount auth sequence and
the on_mount hooks have admitted the view. A mount that is refused or
redirected joins nothing, and a mount refused after ``mount()`` (the
object-permission check) leaves the groups it had joined. This holds inside a
``mount_batch`` too, where the socket stays open for the sibling views.

The ``presence_event`` and ``client_push_event`` channel-layer handlers forward
only while the connection has a mounted view, like ``server_push`` and
``db_notify`` already did.

The SSE transport has no channel groups, but its ``on_view_mounted`` hook marks
the session's mounted view, so a refused SSE mount must leave that unset.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, override_settings

from djust import LiveView
from djust.presence import PresenceManager, PresenceMixin

pytest.importorskip("channels")

pytestmark = pytest.mark.django_db(transaction=True)


class _GatedPresenceView(PresenceMixin, LiveView):
    """Presence view that requires a logged-in user."""

    exposure_policy = "explicit"
    login_required = True
    template = '<div dj-view="gated" dj-id="0">members only</div>'

    def mount(self, request, **kwargs):
        self.x = 1


class _OpenPresenceView(PresenceMixin, LiveView):
    """Presence view open to anonymous users."""

    exposure_policy = "explicit"
    login_required = False
    template = '<div dj-view="open" dj-id="0">open</div>'

    def mount(self, request, **kwargs):
        self.x = 1


class _ObjectGatedPresenceView(PresenceMixin, LiveView):
    """Presence view whose object check refuses every request (runs after mount())."""

    exposure_policy = "explicit"
    login_required = False
    template = '<div dj-view="obj" dj-id="0">object</div>'

    def mount(self, request, **kwargs):
        self.x = 1

    def get_object(self):
        return object()

    def has_object_permission(self, request, obj):
        return False


class _PlainView(LiveView):
    exposure_policy = "explicit"
    login_required = False
    template = '<div dj-view="plain" dj-id="0">plain</div>'

    def mount(self, request, **kwargs):
        self.ok = True


#: The members of ``_RaisingView``'s view group when its ``mount()`` ran.
JOINED_AT_MOUNT: list = []


class _RaisingView(LiveView):
    """Admitted, joins its groups (``on_view_mounted``), then ``mount()`` raises an
    error that is not a refusal: the mount fails without a 4403 close."""

    exposure_policy = "explicit"
    login_required = False
    template = '<div dj-view="raising" dj-id="0">never rendered</div>'

    def mount(self, request, **kwargs):
        JOINED_AT_MOUNT.append(_members(_view_group(_RaisingView)))
        raise RuntimeError("mount failed")


def _path(view_cls) -> str:
    return f"{__name__}.{view_cls.__name__}"


def _presence_group(view_cls) -> str:
    return PresenceManager.presence_group_name(_path(view_cls))


def _view_group(view_cls) -> str:
    from djust.push import view_group_name

    return view_group_name(_path(view_cls))


def _members(group: str) -> list:
    from channels.layers import get_channel_layer

    return list(get_channel_layer().groups.get(group, {}).keys())


async def _connect():
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    from djust.testing import with_live_session

    communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    # A live server session: an explicit mount binds to one, so a refusal
    # below is the view's own and not a missing session.
    connected, _ = await with_live_session(communicator).connect()
    assert connected
    await communicator.receive_json_from()  # connect ack
    return communicator


async def _receive_type(communicator, wanted: str, *, tries: int = 6):
    for _ in range(tries):
        frame = await communicator.receive_json_from()
        if frame.get("type") == wanted:
            return frame
    raise AssertionError(f"no {wanted!r} frame received")


async def _presence_broadcast(view_cls) -> None:
    from channels.layers import get_channel_layer

    await get_channel_layer().group_send(
        _presence_group(view_cls),
        {"type": "presence_event", "event": "cursor_move", "payload": {"user": "alice"}},
    )


async def _close(communicator) -> None:
    try:
        await communicator.disconnect()
    except (asyncio.CancelledError, Exception):  # noqa: BLE001 - teardown only
        pass


# --------------------------------------------------------------------------- #
# WebSocket: mount_batch
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_batch_refused_view_joins_no_groups_and_gets_no_presence_events():
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        communicator = await _connect()
        try:
            await communicator.send_json_to(
                {
                    "type": "mount_batch",
                    "views": [
                        {"view": _path(_GatedPresenceView), "target_id": "a", "url": "/a/"},
                        {"view": _path(_PlainView), "target_id": "b", "url": "/b/"},
                    ],
                }
            )
            batch = await _receive_type(communicator, "mount_batch")
            assert [v["target_id"] for v in batch["views"]] == ["b"]
            assert [n["target_id"] for n in batch.get("refused", [])] == ["a"]

            assert _members(_presence_group(_GatedPresenceView)) == []
            assert _members(_view_group(_GatedPresenceView)) == []

            await _presence_broadcast(_GatedPresenceView)
            assert await communicator.receive_nothing(timeout=0.5)
        finally:
            await _close(communicator)


@pytest.mark.asyncio
async def test_batch_object_refusal_leaves_groups_joined_before_mount():
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        communicator = await _connect()
        try:
            await communicator.send_json_to(
                {
                    "type": "mount_batch",
                    "views": [
                        {"view": _path(_ObjectGatedPresenceView), "target_id": "a", "url": "/a/"},
                        {"view": _path(_PlainView), "target_id": "b", "url": "/b/"},
                    ],
                }
            )
            batch = await _receive_type(communicator, "mount_batch")
            assert [v["target_id"] for v in batch["views"]] == ["b"]
            assert [f["target_id"] for f in batch["refused"]] == ["a"]

            assert _members(_presence_group(_ObjectGatedPresenceView)) == []
            assert _members(_view_group(_ObjectGatedPresenceView)) == []

            await _presence_broadcast(_ObjectGatedPresenceView)
            assert await communicator.receive_nothing(timeout=0.5)
        finally:
            await _close(communicator)


@pytest.mark.asyncio
async def test_batch_admitted_presence_view_still_receives_presence_events():
    """Control: an admitted presence view in a batch joins and receives events."""
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        communicator = await _connect()
        try:
            await communicator.send_json_to(
                {
                    "type": "mount_batch",
                    "views": [
                        {"view": _path(_OpenPresenceView), "target_id": "a", "url": "/a/"},
                    ],
                }
            )
            batch = await _receive_type(communicator, "mount_batch")
            assert [v["target_id"] for v in batch["views"]] == ["a"]
            assert len(_members(_presence_group(_OpenPresenceView))) == 1
            assert len(_members(_view_group(_OpenPresenceView))) == 1

            await _presence_broadcast(_OpenPresenceView)
            frame = await _receive_type(communicator, "presence_event")
            assert frame["event"] == "cursor_move"
            assert frame["payload"] == {"user": "alice"}
        finally:
            await _close(communicator)


# --------------------------------------------------------------------------- #
# WebSocket: single mount
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_single_refused_mount_joins_no_groups():
    from djust.runtime import ViewRuntime

    joined: list = []
    original = ViewRuntime._check_auth

    async def _spy_check_auth(self, request):
        # Snapshot membership at the moment auth runs.
        joined.extend(_members(_presence_group(_GatedPresenceView)))
        joined.extend(_members(_view_group(_GatedPresenceView)))
        return await original(self, request)

    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        ViewRuntime._check_auth = _spy_check_auth
        communicator = await _connect()
        try:
            await communicator.send_json_to(
                {"type": "mount", "view": _path(_GatedPresenceView), "url": "/a/"}
            )
            frame = await _receive_type(communicator, "navigate")
            assert frame["to"]
            assert joined == [], "no group is joined before auth has admitted the view"
            assert _members(_presence_group(_GatedPresenceView)) == []
        finally:
            ViewRuntime._check_auth = original
            await _close(communicator)


@pytest.mark.asyncio
async def test_single_admitted_mount_receives_presence_events():
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        communicator = await _connect()
        try:
            await communicator.send_json_to(
                {"type": "mount", "view": _path(_OpenPresenceView), "url": "/a/"}
            )
            await _receive_type(communicator, "mount")
            await _presence_broadcast(_OpenPresenceView)
            frame = await _receive_type(communicator, "presence_event")
            assert frame["payload"] == {"user": "alice"}
        finally:
            await _close(communicator)


@pytest.mark.asyncio
async def test_single_mount_that_raises_leaves_the_groups_it_joined():
    """#4155: a mount that fails after ``on_view_mounted`` joined the view group,
    for a reason that is not a refusal, keeps the socket open (no 4403). Only the
    transport's ``on_mount_failed`` leaves the group then; nothing else does until
    the socket closes."""
    JOINED_AT_MOUNT.clear()
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        communicator = await _connect()
        try:
            await communicator.send_json_to(
                {"type": "mount", "view": _path(_RaisingView), "url": "/a/"}
            )
            await _receive_type(communicator, "error")
            assert [len(m) for m in JOINED_AT_MOUNT] == [1], "joined before mount() runs"
            assert _members(_view_group(_RaisingView)) == []
            # The socket is still open: a further mount on it is answered.
            await communicator.send_json_to(
                {"type": "mount", "view": _path(_PlainView), "url": "/b/"}
            )
            await _receive_type(communicator, "mount")
            assert _members(_view_group(_RaisingView)) == []
        finally:
            await _close(communicator)


# --------------------------------------------------------------------------- #
# Channel-layer handlers
# --------------------------------------------------------------------------- #


def _bare_consumer(view_instance):
    from djust.websocket import LiveViewConsumer

    consumer = LiveViewConsumer()
    consumer.view_instance = view_instance
    consumer.send_json = AsyncMock()
    # This file tests what the turn does once authorized; the authorization
    # itself (#4201) is covered by test_explicit_turn_kinds_4201.py.
    consumer._authorize_explicit_consumer_turn = AsyncMock(return_value=True)
    return consumer


@pytest.mark.asyncio
@pytest.mark.parametrize("handler", ["presence_event", "client_push_event"])
async def test_handler_drops_event_without_mounted_view(handler):
    consumer = _bare_consumer(None)
    await getattr(consumer, handler)({"event": "e", "payload": {"k": "v"}})
    consumer.send_json.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "handler,frame_type",
    [("presence_event", "presence_event"), ("client_push_event", "push_event")],
)
async def test_handler_forwards_event_with_mounted_view(handler, frame_type):
    consumer = _bare_consumer(MagicMock())
    await getattr(consumer, handler)({"event": "e", "payload": {"k": "v"}})
    consumer.send_json.assert_awaited_once_with(
        {"type": frame_type, "event": "e", "payload": {"k": "v"}}
    )


# --------------------------------------------------------------------------- #
# SSE
# --------------------------------------------------------------------------- #


def _sse_session(path: str):
    from djust.sse import SSESession

    from djust.testing import with_live_session

    session = SSESession("22222222-2222-2222-2222-222222222222")
    request = with_live_session(RequestFactory().get(path), AnonymousUser())
    session._request = request
    return session


@pytest.mark.django_db(transaction=True)
@override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__])
@pytest.mark.asyncio
async def test_sse_refused_mount_leaves_session_unmounted():
    session = _sse_session("/a/")
    await session.runtime.dispatch_mount(
        {"type": "mount", "view": _path(_GatedPresenceView), "url": "/a/", "params": {}}
    )
    assert session.runtime.view_instance is None
    assert session.view_instance is None


@pytest.mark.django_db(transaction=True)
@override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__])
@pytest.mark.asyncio
async def test_sse_admitted_mount_marks_session_mounted():
    session = _sse_session("/b/")
    await session.runtime.dispatch_mount(
        {"type": "mount", "view": _path(_PlainView), "url": "/b/", "params": {}}
    )
    assert session.runtime.view_instance is not None
    assert session.view_instance is session.runtime.view_instance
