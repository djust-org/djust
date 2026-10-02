"""``reauth_on_event`` for server-originated turns on a socket with several views.

A socket can carry the page view plus any number of slot views (lazy, batched).
A push, ``db_notify``, tick or presence frame is routed to the view it is for,
which may be a slot rather than ``consumer.view_instance``. The re-check must run
for THAT view, a pass for one view must not cover another, and a view whose
principal was revoked is torn down on its own (an authorization refusal ends the
view, not the socket the other views still use).
"""

import asyncio
import uuid

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.test import override_settings
from unittest.mock import patch

from djust import LiveView, event_handler
from djust.config import config
from djust.push import apush_to_view, view_group_name

pytestmark = [pytest.mark.asyncio, pytest.mark.django_db(transaction=True)]

MOD = __name__
SECRET = "PUSHED-AFTER-REVOCATION"

#: ``(what, tag)`` records: "pushed".
EVENTS: list = []
VIEWS: dict = {}
CONSUMERS: list = []


class _User:
    is_authenticated = True
    is_active = True
    is_anonymous = False
    pk = 1

    def has_perms(self, perms):
        return True


class _Principal:
    def __init__(self):
        self.user = _User()
        self.calls = 0

    async def get_user(self, scope):
        self.calls += 1
        return self.user


class _Guarded:
    exposure_policy = "legacy"
    login_required = True

    def mount(self, request, **kwargs):
        self._tag = type(self).__name__ + ":" + uuid.uuid4().hex[:8]
        self.secret = "initial"
        VIEWS[self._tag] = self

    @event_handler()
    def on_push(self, **kwargs):
        self.secret = SECRET
        EVENTS.append(("pushed", self._tag))


class Page(_Guarded, LiveView):
    template = '<div dj-root dj-view="' + MOD + '.Page"><b>page {{ secret }}</b></div>'


class Lazy(_Guarded, LiveView):
    template = '<div dj-root dj-view="' + MOD + '.Lazy"><b>lazy {{ secret }}</b></div>'


class LazyOpen(Lazy):
    """A slot view with no auth requirement: never re-checked."""

    login_required = False


@pytest.fixture(autouse=True)
def setup():
    EVENTS.clear()
    VIEWS.clear()
    CONSUMERS.clear()
    with override_settings(
        LIVEVIEW_ALLOWED_MODULES=["djust", __name__],
        LIVEVIEW_CONFIG={"reauth_on_event": True, "reauth_server_turn_interval": 3600},
        DEBUG=False,
    ):
        config.reset()
        yield
    config.reset()
    VIEWS.clear()
    CONSUMERS.clear()


def _fresh_key():
    store = SessionStore()
    store.create()
    return store.session_key


def _members(group):
    from channels.layers import get_channel_layer

    return list(get_channel_layer().groups.get(group, {}).keys())


def _group(cls):
    return view_group_name(MOD + "." + cls.__name__)


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


async def _connect():
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    class _Recorded(LiveViewConsumer):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            CONSUMERS.append(self)

    communicator = WebsocketCommunicator(_Recorded.as_asgi(), "/ws/")
    communicator.scope["session"] = SessionStore(await sync_to_async(_fresh_key)())
    communicator.scope["user"] = _User()
    communicator.scope["tenant"] = None
    connected, _ = await communicator.connect()
    assert connected
    await communicator.receive_json_from(timeout=2)
    return communicator


async def _close(communicator):
    try:
        await communicator.disconnect()
    except (asyncio.CancelledError, Exception):  # noqa: BLE001 - teardown only
        pass


async def _mount(communicator, cls, target_id=None):
    frame = {"type": "mount", "view": MOD + "." + cls.__name__, "url": "/page/"}
    if target_id is not None:
        frame["target_id"] = target_id
    await communicator.send_json_to(frame)
    frames = await _frames_until(communicator, "mount", "error")
    assert frames[-1]["type"] == "mount", frames


async def _drain(communicator, wait=0.5):
    out = []
    while True:
        try:
            out.append(await asyncio.wait_for(communicator.output_queue.get(), wait))
        except asyncio.TimeoutError:
            return out
        if out[-1]["type"] == "websocket.close":
            return out


def _text(frames):
    return " ".join(f.get("text") or "" for f in frames if f["type"] == "websocket.send")


def _closed_4403(frames):
    return [f for f in frames if f["type"] == "websocket.close" and f.get("code") == 4403]


async def test_revoked_principal_tears_down_the_slot_a_push_targets_not_the_socket():
    principal = _Principal()
    with patch("channels.auth.get_user", principal.get_user):
        communicator = await _connect()
        try:
            await _mount(communicator, Page)
            await _mount(communicator, Lazy, "lazy-1")
            consumer = CONSUMERS[-1]
            lazy = _one(Lazy)
            page = _one(Page)

            principal.user = AnonymousUser()  # logged out after mount
            await apush_to_view(MOD + ".Lazy", handler="on_push")
            await _until(lambda: _members(_group(Lazy)) == [], "the revoked slot's teardown")
            frames = await _drain(communicator, 0.3)

            # The turn did not run, nothing the hook would have set was sent,
            # and the revoked slot is gone.
            assert ("pushed", lazy) not in EVENTS
            assert SECRET not in _text(frames)
            assert "lazy-1" not in consumer._slot_map()
            assert VIEWS[lazy].secret == "initial"
            # The page view was not the target: it is untouched until its own
            # next turn, and the socket is still open (the principal is
            # re-resolved per view, so a sibling refuses on its own turn).
            assert consumer.view_instance is VIEWS[page]
            assert not _closed_4403(frames)

            await apush_to_view(MOD + ".Page", handler="on_push")
            frames = await _drain(communicator, 0.5)
            assert ("pushed", page) not in EVENTS
            assert _closed_4403(frames)
            assert consumer.view_instance is None
        finally:
            await _close(communicator)


async def test_authorized_push_to_a_slot_still_runs():
    principal = _Principal()
    with patch("channels.auth.get_user", principal.get_user):
        communicator = await _connect()
        try:
            await _mount(communicator, Page)
            await _mount(communicator, Lazy, "lazy-1")
            await apush_to_view(MOD + ".Lazy", handler="on_push")
            frames = await _frames_until(communicator, "patch", "html_update")
            assert frames[-1]["target_id"] == "lazy-1"
            assert EVENTS == [("pushed", _one(Lazy))]
            assert principal.calls >= 1
        finally:
            await _close(communicator)


async def test_a_pass_for_one_view_does_not_cover_a_sibling():
    """The throttle is per view: each view re-checks its own authority."""
    principal = _Principal()
    with patch("channels.auth.get_user", principal.get_user):
        communicator = await _connect()
        try:
            await _mount(communicator, Page)
            await _mount(communicator, Lazy, "lazy-1")
            await apush_to_view(MOD + ".Page", handler="on_push")
            await _frames_until(communicator, "patch", "html_update")
            assert principal.calls == 1

            # Interval is an hour: a second push to the SAME view is covered...
            await apush_to_view(MOD + ".Page", handler="on_push")
            await _frames_until(communicator, "patch", "html_update")
            assert principal.calls == 1

            # ...but the sibling's first turn is its own check.
            await apush_to_view(MOD + ".Lazy", handler="on_push")
            await _frames_until(communicator, "patch", "html_update")
            assert principal.calls == 2
        finally:
            await _close(communicator)


async def test_presence_event_is_rechecked_for_the_view_that_receives_it():
    principal = _Principal()
    with patch("channels.auth.get_user", principal.get_user):
        communicator = await _connect()
        try:
            await _mount(communicator, Page)
            await _mount(communicator, Lazy, "lazy-1")
            consumer = CONSUMERS[-1]
            group = view_group_name(MOD + ".Lazy")

            principal.user = AnonymousUser()
            await consumer.presence_event(
                {"group": group, "event": "join", "payload": {"secret": SECRET}}
            )
            frames = await _drain(communicator, 0.3)
            assert SECRET not in _text(frames)
            assert _members(_group(Lazy)) == []
            assert "lazy-1" not in consumer._slot_map()
            assert consumer.view_instance is VIEWS[_one(Page)]
        finally:
            await _close(communicator)


async def test_presence_event_reaches_an_authorized_slot():
    principal = _Principal()
    with patch("channels.auth.get_user", principal.get_user):
        communicator = await _connect()
        try:
            await _mount(communicator, Page)
            await _mount(communicator, Lazy, "lazy-1")
            consumer = CONSUMERS[-1]
            await consumer.presence_event(
                {
                    "group": view_group_name(MOD + ".Lazy"),
                    "event": "join",
                    "payload": {"who": "peer"},
                }
            )
            frames = await _frames_until(communicator, "presence_event")
            assert frames[-1]["payload"] == {"who": "peer"}
        finally:
            await _close(communicator)


async def test_slot_without_auth_requirements_is_not_checked():
    principal = _Principal()
    with patch("channels.auth.get_user", principal.get_user):
        communicator = await _connect()
        try:
            await _mount(communicator, Page)
            await _mount(communicator, LazyOpen, "open-1")
            principal.user = AnonymousUser()
            await apush_to_view(MOD + ".LazyOpen", handler="on_push")
            frames = await _frames_until(communicator, "patch", "html_update")
            assert frames[-1]["target_id"] == "open-1"
            assert principal.calls == 0
        finally:
            await _close(communicator)
