"""Channel-layer group membership follows the mount's auth outcome (1.0 consumer).

A mounted view joins its server-push view group, its presence group and any
db_notify groups, and may start a tick task. On ``LiveViewConsumer.handle_mount``
those happen only once the pre-mount auth sequence (``check_view_auth``,
including the ``LoginRequiredMiddleware`` rule) and the on_mount hooks have
admitted the view. A refused or redirected mount joins nothing and starts no
tick, on a single mount, a ``mount_batch`` entry (where the socket stays open
for the sibling views) and a lazy mount beside the page view. A mount refused
after ``mount()`` (the object-permission check) leaves the groups it joined,
stops its tick and drops the presence ``mount()`` tracked.

The ``presence_event`` and ``client_push_event`` channel-layer handlers forward
only while the connection has a mounted view, like ``server_push`` and
``db_notify`` already did.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("channels")

from django.contrib.auth.models import AnonymousUser  # noqa: E402
from django.test import override_settings  # noqa: E402

from djust import LiveView  # noqa: E402
from djust.hooks import on_mount  # noqa: E402
from djust.presence import PresenceManager, PresenceMixin  # noqa: E402

pytestmark = pytest.mark.django_db

TICKS: list = []
LISTEN_CHANNEL = "mgm_test_channel"


class _NoPermsUser:
    """Signed in, without any Django permission."""

    id = 7
    pk = 7
    username = "noperms"
    is_authenticated = True
    is_active = True
    is_anonymous = False

    def has_perms(self, perms):
        return False

    def has_perm(self, perm, obj=None):
        return False


@on_mount
def _redirect_hook(view, request, **kwargs):
    return "/hook-elsewhere/"


class _GroupedBase(PresenceMixin, LiveView):
    """Joins every group kind (view, presence, db_notify) and ticks."""

    login_required = False
    tick_interval = 20
    _listen_channels = {LISTEN_CHANNEL}

    def mount(self, request, **kwargs):
        self.x = 1

    def handle_tick(self):
        TICKS.append(type(self).__name__)


class _LoginGatedView(_GroupedBase):
    login_required = True
    template = '<div dj-view="login" dj-id="0">members only</div>'


class _PermissionGatedView(_GroupedBase):
    permission_required = "mgm.view_secret"
    template = '<div dj-view="perm" dj-id="0">secret</div>'


class _HookGatedView(_GroupedBase):
    on_mount = [_redirect_hook]
    template = '<div dj-view="hook" dj-id="0">hooked</div>'


class _ObjectGatedView(_GroupedBase):
    """Admitted pre-mount; its object check refuses after mount() tracked presence."""

    template = '<div dj-view="obj" dj-id="0">object</div>'

    def mount(self, request, **kwargs):
        self.x = 1
        self.track_presence(meta={"name": "someone"})
        time.sleep(0.08)  # several tick intervals: a tick started before the check would run

    def get_object(self):
        return object()

    def has_object_permission(self, request, obj):
        return False


class _OpenView(_GroupedBase):
    template = '<div dj-view="open" dj-id="0">open</div>'


class _PlainView(LiveView):
    login_required = False
    template = '<div dj-view="plain" dj-id="0">plain</div>'

    def mount(self, request, **kwargs):
        self.ok = True


REFUSED = {
    "login": (_LoginGatedView, AnonymousUser),
    "permission": (_PermissionGatedView, _NoPermsUser),
    "hook": (_HookGatedView, AnonymousUser),
}


def _path(view_cls) -> str:
    return f"{__name__}.{view_cls.__name__}"


def _groups_of(view_cls) -> list:
    from djust.push import view_group_name

    return [
        view_group_name(_path(view_cls)),
        PresenceManager.presence_group_name(_path(view_cls)),
        f"djust_db_notify_{LISTEN_CHANNEL}",
    ]


def _layer():
    from channels.layers import get_channel_layer

    return get_channel_layer()


def _joined(view_cls) -> list:
    """Groups of *view_cls* that have any member."""
    groups = _layer().groups
    return [g for g in _groups_of(view_cls) if groups.get(g)]


@pytest.fixture(autouse=True)
def _clean_state():
    _layer().groups.clear()
    TICKS.clear()
    yield
    _layer().groups.clear()
    TICKS.clear()


@pytest.fixture
def auth_spy(monkeypatch):
    """Record, each time the pre-mount auth runs, which view groups have members."""
    import djust.auth as auth_pkg

    seen: list = []
    original = auth_pkg.run_pre_mount_auth

    def spy(view_instance, request):
        seen.append((type(view_instance).__name__, _joined(type(view_instance))))
        return original(view_instance, request)

    monkeypatch.setattr(auth_pkg, "run_pre_mount_auth", spy)
    return seen


def _scoped(app, user):
    async def mw(scope, receive, send):
        scope = dict(scope)
        scope["user"] = user
        return await app(scope, receive, send)

    return mw


async def _connect(user=None):
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    communicator = WebsocketCommunicator(
        _scoped(LiveViewConsumer.as_asgi(), user if user is not None else AnonymousUser()), "/ws/"
    )
    connected, _ = await communicator.connect()
    assert connected
    await communicator.receive_json_from(timeout=2)  # connect ack
    return communicator


async def _frames_until(communicator, *wanted: str) -> tuple:
    """Read frames until one of *wanted* (or the socket's close) arrives.

    Returns ``(frame_or_None, closed)``.
    """
    import json

    for _ in range(12):
        out = await communicator.receive_output(timeout=3)
        if out.get("type") == "websocket.close":
            return None, True
        frame = json.loads(out.get("text") or "{}")
        if frame.get("type") in wanted:
            return frame, False
    raise AssertionError(f"none of {wanted!r} received")


async def _presence_broadcast(view_cls) -> None:
    await _layer().group_send(
        PresenceManager.presence_group_name(_path(view_cls)),
        {"type": "presence_event", "event": "cursor_move", "payload": {"user": "alice"}},
    )


async def _close(communicator) -> None:
    try:
        await communicator.disconnect()
    except (asyncio.CancelledError, Exception):  # noqa: BLE001 - teardown only
        pass


def _ticks_of(view_cls) -> int:
    return TICKS.count(view_cls.__name__)


# --------------------------------------------------------------------------- #
# Refused before mount(): login_required, permission, on_mount hook
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", sorted(REFUSED))
async def test_single_refused_mount_joins_no_group_and_starts_no_tick(kind, auth_spy):
    view_cls, user_cls = REFUSED[kind]
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        communicator = await _connect(user_cls())
        try:
            await communicator.send_json_to(
                {"type": "mount", "view": _path(view_cls), "url": "/a/"}
            )
            frame, closed = await _frames_until(communicator, "navigate", "error")
            assert auth_spy == [(view_cls.__name__, [])], "no group is joined before auth"
            assert _joined(view_cls) == []
            await asyncio.sleep(0.1)
            assert _ticks_of(view_cls) == 0
        finally:
            await _close(communicator)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", sorted(REFUSED))
async def test_batch_refused_entry_joins_no_group_and_gets_no_presence_events(kind, auth_spy):
    view_cls, user_cls = REFUSED[kind]
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        communicator = await _connect(user_cls())
        try:
            await communicator.send_json_to(
                {
                    "type": "mount_batch",
                    "views": [
                        {"view": _path(view_cls), "target_id": "a", "url": "/a/"},
                        {"view": _path(_PlainView), "target_id": "b", "url": "/b/"},
                    ],
                }
            )
            batch, closed = await _frames_until(communicator, "mount_batch")
            assert auth_spy[0] == (view_cls.__name__, []), "no group is joined before auth"
            assert _joined(view_cls) == []
            if kind == "permission":
                # 1.0 closes the socket on a PermissionDenied, batch included.
                pass
            else:
                assert not closed
                assert [v["target_id"] for v in batch["views"]] == ["b"]
                assert [n["target_id"] for n in batch.get("navigate", [])] == ["a"]
                await _presence_broadcast(view_cls)
                assert await communicator.receive_nothing(timeout=0.3)
            await asyncio.sleep(0.1)
            assert _ticks_of(view_cls) == 0
        finally:
            await _close(communicator)


@pytest.mark.asyncio
async def test_lazy_refused_view_beside_the_page_joins_no_group(auth_spy):
    """A dj-lazy mount (target_id, page view live) refused by its own login_required."""
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        communicator = await _connect()
        try:
            await communicator.send_json_to(
                {"type": "mount", "view": _path(_PlainView), "url": "/p/"}
            )
            await _frames_until(communicator, "mount")
            await communicator.send_json_to(
                {
                    "type": "mount",
                    "view": _path(_LoginGatedView),
                    "url": "/p/",
                    "target_id": "lazy",
                }
            )
            await _frames_until(communicator, "navigate", "error")
            assert auth_spy[-1] == (_LoginGatedView.__name__, [])
            assert _joined(_LoginGatedView) == []
            await asyncio.sleep(0.1)
            assert _ticks_of(_LoginGatedView) == 0
        finally:
            await _close(communicator)


# --------------------------------------------------------------------------- #
# Refused after mount(): object permission
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
@pytest.mark.parametrize("batch", [False, True])
async def test_object_refusal_leaves_groups_tick_and_presence(batch):
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        communicator = await _connect()
        try:
            if batch:
                await communicator.send_json_to(
                    {
                        "type": "mount_batch",
                        "views": [
                            {"view": _path(_ObjectGatedView), "target_id": "a", "url": "/a/"},
                            {"view": _path(_PlainView), "target_id": "b", "url": "/b/"},
                        ],
                    }
                )
                await _frames_until(communicator, "mount_batch")
            else:
                await communicator.send_json_to(
                    {"type": "mount", "view": _path(_ObjectGatedView), "url": "/a/"}
                )
                frame, _ = await _frames_until(communicator, "error")
                assert frame is None or frame["message"] == "Permission denied"
            assert _joined(_ObjectGatedView) == []
            assert PresenceManager.list_presences(_path(_ObjectGatedView)) == []
            await asyncio.sleep(0.1)
            assert _ticks_of(_ObjectGatedView) == 0
        finally:
            await _close(communicator)


# --------------------------------------------------------------------------- #
# Admitted mounts are unchanged
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_single_admitted_mount_joins_its_groups_ticks_and_receives_presence():
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        communicator = await _connect()
        try:
            await communicator.send_json_to(
                {"type": "mount", "view": _path(_OpenView), "url": "/a/"}
            )
            await _frames_until(communicator, "mount")
            assert _joined(_OpenView) == _groups_of(_OpenView)
            await _presence_broadcast(_OpenView)
            frame, _ = await _frames_until(communicator, "presence_event")
            assert frame["event"] == "cursor_move"
            assert frame["payload"] == {"user": "alice"}
            await asyncio.sleep(0.15)
            assert _ticks_of(_OpenView) >= 1
        finally:
            await _close(communicator)
        assert _joined(_OpenView) == []


@pytest.mark.asyncio
async def test_batch_admitted_view_beside_a_refused_one_receives_presence():
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        communicator = await _connect()
        try:
            await communicator.send_json_to(
                {
                    "type": "mount_batch",
                    "views": [
                        {"view": _path(_LoginGatedView), "target_id": "a", "url": "/a/"},
                        {"view": _path(_OpenView), "target_id": "b", "url": "/b/"},
                    ],
                }
            )
            batch, _ = await _frames_until(communicator, "mount_batch")
            assert [v["target_id"] for v in batch["views"]] == ["b"]
            assert _joined(_OpenView) == _groups_of(_OpenView)
            assert _joined(_LoginGatedView) == [f"djust_db_notify_{LISTEN_CHANNEL}"]  # shared
            await _presence_broadcast(_OpenView)
            frame, _ = await _frames_until(communicator, "presence_event")
            assert frame["payload"] == {"user": "alice"}
            await _presence_broadcast(_LoginGatedView)
            assert await communicator.receive_nothing(timeout=0.3)
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
