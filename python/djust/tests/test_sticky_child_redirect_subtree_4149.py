"""Carrying an explicit sticky child across ``live_redirect``: its subtree,
refusals and principals (#4149 review).

* A carried child's explicit descendants move with it: each is re-authorized
  against the destination request, its reuse identity is recorded for the new
  route and ancestry, and its declared server state is stored under the
  destination. Before, the redirect answered "Child state unavailable" and the
  nested child refused every event.
* Commit-or-rollback: nothing is recorded on the child until the destination
  authorized it. A refused child, or one whose state could not be moved, is
  disposed and dropped from the socket's preserved map, so no later mount,
  ``mount_batch`` or ``url_change`` re-attaches it.
* Not carried for a different user or a different tenant.

Driven over a real ``WebsocketCommunicator``.
"""

from __future__ import annotations

import inspect
import json

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.test import override_settings
from django.urls import path

from djust import LiveView, event_handler
from djust.decorators import state

pytestmark = [pytest.mark.asyncio, pytest.mark.django_db(transaction=True)]

MODULE = __name__
SETTINGS = dict(
    LIVEVIEW_ALLOWED_MODULES=[MODULE],
    DJUST_LIVE_RENDER_ALLOWED_MODULES=[MODULE],
    ROOT_URLCONF=MODULE,
    DEBUG=False,
)
CONSUMERS: list = []
FLAGS = {"deny_tag": False, "deny_inner_on": None, "fail_save": False}


class Inner(LiveView):
    """An explicit, non-sticky child nested in the sticky ``Outer``."""

    exposure_policy = "explicit"
    n = state(0)
    template = '<div>inner={{ n }}<button dj-click="inc">i</button></div>'
    mounts = 0

    def mount(self, request, **kwargs):
        type(self).mounts += 1
        self.n = 0

    def get_object(self):
        return object()

    def has_object_permission(self, request, obj):
        return request.path != FLAGS["deny_inner_on"]

    @event_handler()
    def inc(self, **kwargs):
        self.n += 1

    def get_context_data(self, **kwargs):
        return super().get_context_data(n=self.n, **kwargs)


class InnerSticky(Inner):
    """A sticky child nested inside a sticky child."""

    sticky = True
    sticky_id = "inner-sticky"


class StoredInner(InnerSticky):
    """A nested child with server state: only a sticky child may declare it
    (a non-sticky explicit child is transient, ADR-038 D-m)."""

    sticky_id = "stored-inner"
    n = state(0, persist="server")


def _outer(name, inner, extra=""):
    return type(
        name,
        (LiveView,),
        {
            "exposure_policy": "explicit",
            "sticky": True,
            "sticky_id": "outer",
            "m": state(0),
            "template": (
                "{% load live_tags %}<div>outer={{ m }}"
                f'{{% live_render "{MODULE}.{inner}" {extra} %}}</div>'
            ),
            "mount": lambda self, request, **kwargs: setattr(self, "m", 0),
            "bump": event_handler()(lambda self, **kwargs: setattr(self, "m", self.m + 1)),
            "get_context_data": lambda self, **kwargs: LiveView.get_context_data(
                self, m=self.m, **kwargs
            ),
        },
    )


Outer = _outer("Outer", "Inner", 'view_id="inner"')
StoredOuter = _outer("StoredOuter", "StoredInner", "sticky=True")
StickyOuter = _outer("StickyOuter", "InnerSticky", "sticky=True")


class Guarded(LiveView):
    """Refuses its object check only inside the destination tag's
    re-authorization (``_authorize_reused_child``), not at staging."""

    exposure_policy = "explicit"
    sticky = True
    sticky_id = "guarded"
    v = state(1, persist="server")
    template = "<div>v={{ v }}</div>"
    mounts = 0

    def mount(self, request, **kwargs):
        type(self).mounts += 1
        self.v = 1

    def get_object(self):
        return object()

    def has_object_permission(self, request, obj):
        return not (
            FLAGS["deny_tag"]
            and any(f.function == "_authorize_reused_child" for f in inspect.stack())
        )

    @event_handler()
    def bump(self, **kwargs):
        self.v += 1

    def get_context_data(self, **kwargs):
        return super().get_context_data(v=self.v, **kwargs)


class Player(LiveView):
    exposure_policy = "explicit"
    sticky = True
    sticky_id = "player"
    track = state("one")
    template = '<div>track={{ track }}<button dj-click="next_track">n</button></div>'
    mounts = 0

    def mount(self, request, **kwargs):
        type(self).mounts += 1
        self.track = "one"

    @event_handler()
    def next_track(self, **kwargs):
        self.track = "two"

    def get_context_data(self, **kwargs):
        return super().get_context_data(track=self.track, **kwargs)


def _page(name, body):
    return type(
        name,
        (LiveView,),
        {
            "exposure_policy": "explicit",
            "template": (
                "{% load live_tags %}"
                f'<div dj-root dj-view="{MODULE}.{name}" dj-id="0"><h1>{name}</h1>{body}</div>'
            ),
        },
    )


def _embed(cls):
    return f'{{% live_render "{MODULE}.{cls}" sticky=True %}}'


NA, NB, ND = (_page(n, _embed("Outer")) for n in ("NA", "NB", "ND"))
SA, SB = (_page(n, _embed("StoredOuter")) for n in ("SA", "SB"))
KA, KB = (_page(n, _embed("StickyOuter")) for n in ("KA", "KB"))
GA, GB, GC = (_page(n, _embed("Guarded")) for n in ("GA", "GB", "GC"))
Plain = _page("Plain", "")


def _broken_mount(self, request, **kwargs):
    raise RuntimeError("destination mount failed")


Broken = type("Broken", (_page("Broken", _embed("Guarded")),), {"mount": _broken_mount})
PA, PB = (_page(n, _embed("Player")) for n in ("PA", "PB"))

urlpatterns = [
    path("na/", NA.as_view()),
    path("nb/", NB.as_view()),
    path("nd/", ND.as_view()),
    path("sa/", SA.as_view()),
    path("sb/", SB.as_view()),
    path("ka/", KA.as_view()),
    path("kb/", KB.as_view()),
    path("ga/", GA.as_view()),
    path("gb/", GB.as_view()),
    path("gc/", GC.as_view()),
    path("plain/", Plain.as_view()),
    path("broken/", Broken.as_view()),
    path("pa/", PA.as_view()),
    path("pb/", PB.as_view()),
    path("<str:t>/pa/", PA.as_view()),
    path("<str:t>/pb/", PB.as_view()),
]


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    from djust.websocket import LiveViewConsumer

    Inner.mounts = InnerSticky.mounts = Guarded.mounts = Player.mounts = 0
    FLAGS.update(deny_tag=False, deny_inner_on=None, fail_save=False)
    CONSUMERS.clear()
    original = LiveViewConsumer.__init__

    def init(self, *args, **kwargs):
        original(self, *args, **kwargs)
        CONSUMERS.append(self)

    monkeypatch.setattr(LiveViewConsumer, "__init__", init)
    yield


def _new_session():
    session = SessionStore()
    session.create()
    return session.session_key


async def _connect(key, view, url, user=None):
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    comm = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    comm.scope["session"] = SessionStore(key)
    comm.scope["user"] = user or AnonymousUser()
    connected, _ = await comm.connect()
    assert connected
    await comm.receive_json_from()
    await comm.send_json_to({"type": "mount", "view": f"{MODULE}.{view}", "url": url})
    frame = await comm.receive_json_from()
    assert frame["type"] == "mount", frame
    return comm


async def _event(comm, event, **params):
    await comm.send_json_to({"type": "event", "event": event, "params": params})
    while True:
        frame = await comm.receive_json_from()
        if frame.get("type") in {"embedded_update", "error", "patch", "html_update", "noop"}:
            return frame


async def _send(comm, payload, until=("mount", "error", "mount_batch")):
    await comm.send_json_to(payload)
    frames = []
    while True:
        frame = await comm.receive_json_from()
        frames.append(frame)
        if frame.get("type") in until:
            return frames


async def _settle(comm):
    """Frames are handled in order: the pong means earlier work finished."""
    assert (await _send(comm, {"type": "ping"}, until=("pong",)))[-1]["type"] == "pong"


async def _redirect(comm, view, url):
    return await _send(
        comm, {"type": "live_redirect_mount", "view": f"{MODULE}.{view}", "url": url}
    )


def _held(frames):
    return [v for f in frames if f.get("type") == "sticky_hold" for v in f.get("views", [])]


async def _stored(key, route, slots):
    from djust._exposure_children import child_state_key

    stored = await sync_to_async(SessionStore(key).load)()
    return stored.get(child_state_key(route, slots), {}).get("state", {}).get("values")


# ---------------------------------------------------------------------------
# The carried subtree
# ---------------------------------------------------------------------------


async def test_nested_child_moves_with_its_carried_parent():
    key = await sync_to_async(_new_session)()
    with override_settings(**SETTINGS):
        comm = await _connect(key, "NA", "/na/")
        try:
            await _event(comm, "inc", view_id="inner")
            await _event(comm, "inc", view_id="inner")
            frames = await _redirect(comm, "NB", "/nb/")
            assert _held(frames) == ["outer"]
            assert frames[-1]["type"] == "mount", frames
            assert Inner.mounts == 1
            reply = await _event(comm, "inc", view_id="inner")
            assert reply["type"] == "embedded_update", reply
            assert "inner=3" in reply["html"], reply
            # Re-rendering the carried parent reuses the nested child.
            reply = await _event(comm, "bump", view_id="outer")
            assert "outer=1" in reply["html"] and "inner=3" in reply["html"], reply
            assert Inner.mounts == 1
        finally:
            await comm.disconnect()


async def test_sticky_child_nested_in_a_sticky_child_moves_with_it():
    key = await sync_to_async(_new_session)()
    with override_settings(**SETTINGS):
        comm = await _connect(key, "KA", "/ka/")
        try:
            await _event(comm, "inc", view_id="inner-sticky")
            frames = await _redirect(comm, "KB", "/kb/")
            assert _held(frames) == ["outer"]
            reply = await _event(comm, "inc", view_id="inner-sticky")
            assert reply["type"] == "embedded_update", reply
            assert "inner=2" in reply["html"], reply
            assert InnerSticky.mounts == 1
        finally:
            await comm.disconnect()


async def test_nested_child_server_state_moves_to_the_destination():
    key = await sync_to_async(_new_session)()
    with override_settings(**SETTINGS):
        comm = await _connect(key, "SA", "/sa/")
        try:
            await _event(comm, "inc", view_id="stored-inner")
            frames = await _redirect(comm, "SB", "/sb/")
            assert _held(frames) == ["outer"]
            assert (await _stored(key, "/sb/", ("outer", "stored-inner"))) == {"n": 1}
            reply = await _event(comm, "inc", view_id="stored-inner")
            assert reply["type"] == "embedded_update", reply
            assert "inner=2" in reply["html"], reply
        finally:
            await comm.disconnect()
        assert (await _stored(key, "/sb/", ("outer", "stored-inner"))) == {"n": 2}


async def test_refused_nested_child_is_not_carried():
    """A nested child the destination refuses keeps the whole subtree from
    being carried; the destination then mounts as a fresh embed would (here
    refused too), and the old instance is disposed."""
    key = await sync_to_async(_new_session)()
    with override_settings(**SETTINGS):
        comm = await _connect(key, "NA", "/na/")
        consumer = CONSUMERS[-1]
        try:
            outer = consumer.view_instance._get_child_view("outer")
            await _event(comm, "inc", view_id="inner")
            FLAGS["deny_inner_on"] = "/nd/"
            frames = await _redirect(comm, "ND", "/nd/")
            await _settle(comm)
            assert "outer" not in _held(frames)
            assert "inner=1" not in json.dumps(frames)
            assert consumer._sticky_preserved == {}
            assert outer._djust_child_disposed is True
        finally:
            await comm.disconnect()


# ---------------------------------------------------------------------------
# Commit-or-rollback
# ---------------------------------------------------------------------------


async def _refused_redirect(key):
    """Mount GA, change the child's state, then redirect to GB whose tag
    refuses the child. Returns ``(comm, consumer, child)``."""
    comm = await _connect(key, "GA", "/ga/")
    consumer = CONSUMERS[-1]
    await _event(comm, "bump", view_id="guarded")
    child = consumer.view_instance._get_child_view("guarded")
    FLAGS["deny_tag"] = True
    frames = await _redirect(comm, "GB", "/gb/")
    FLAGS["deny_tag"] = False
    assert frames[-1]["type"] == "error", frames
    await _settle(comm)
    return comm, consumer, child


async def test_refused_child_is_disposed_and_not_rewritten():
    key = await sync_to_async(_new_session)()
    with override_settings(**SETTINGS):
        comm, consumer, child = await _refused_redirect(key)
        try:
            assert consumer._sticky_preserved == {}
            assert child._djust_child_disposed is True
            assert child._parent_view is None
            assert child._explicit_child_reuse_identity is None
            assert (await _stored(key, "/gb/", ("guarded",))) is None
        finally:
            await comm.disconnect()


@pytest.mark.parametrize("kind", ["mount", "mount_batch"])
async def test_later_mount_does_not_pick_up_a_refused_child(kind):
    key = await sync_to_async(_new_session)()
    with override_settings(**SETTINGS):
        comm, consumer, child = await _refused_redirect(key)
        try:
            entry = {"view": f"{MODULE}.GC", "url": "/gc/"}
            if kind == "mount_batch":
                payload = {"type": "mount_batch", "views": [{**entry, "target_id": "t1"}]}
            else:
                payload = {"type": "mount", **entry}
            frames = await _send(comm, payload)
            assert not _held(frames)
            assert "v=2" not in json.dumps(frames)
            assert Guarded.mounts == 2
            assert all(
                getattr(c, "view_instance", None) is None
                or c.view_instance._get_child_view("guarded") is not child
                for c in CONSUMERS
            )
        finally:
            await comm.disconnect()


async def test_url_change_does_not_pick_up_a_refused_child():
    key = await sync_to_async(_new_session)()
    with override_settings(**SETTINGS):
        comm, consumer, child = await _refused_redirect(key)
        try:
            await _send(comm, {"type": "mount", "view": f"{MODULE}.GC", "url": "/gc/"})
            await comm.send_json_to({"type": "url_change", "url": "/gc/?x=1", "params": {"x": "1"}})
            await comm.receive_nothing(timeout=1)
            assert consumer.view_instance._get_child_view("guarded") is not child
            assert consumer._sticky_preserved == {}
        finally:
            await comm.disconnect()


async def test_failed_state_move_rolls_the_carry_back(monkeypatch):
    """The carried child is registered before its state is moved; if storing
    it fails, the child is unregistered and disposed, not left on a parent
    whose mount failed."""
    from djust import _exposure_children

    original = _exposure_children.rebind_child_state

    def failing(*args, **kwargs):
        if FLAGS["fail_save"]:
            raise RuntimeError("store down")
        return original(*args, **kwargs)

    monkeypatch.setattr(_exposure_children, "rebind_child_state", failing)
    key = await sync_to_async(_new_session)()
    with override_settings(**SETTINGS):
        comm = await _connect(key, "GA", "/ga/")
        consumer = CONSUMERS[-1]
        try:
            await _event(comm, "bump", view_id="guarded")
            child = consumer.view_instance._get_child_view("guarded")
            FLAGS["fail_save"] = True
            frames = await _redirect(comm, "GB", "/gb/")
            FLAGS["fail_save"] = False
            assert frames[-1]["type"] == "error", frames
            await _settle(comm)
            assert child._djust_child_disposed is True
            assert child._parent_view is None
            assert consumer._sticky_preserved == {}
            frames = await _send(comm, {"type": "mount", "view": f"{MODULE}.GC", "url": "/gc/"})
            assert "v=2" not in json.dumps(frames)
        finally:
            await comm.disconnect()


async def test_children_staged_for_a_destination_that_never_rendered_are_released():
    """The destination's mount failed before its template ran, so no tag ever
    saw the staged child: the socket releases it rather than keeping it for a
    later mount."""
    key = await sync_to_async(_new_session)()
    with override_settings(**SETTINGS):
        comm = await _connect(key, "GA", "/ga/")
        consumer = CONSUMERS[-1]
        try:
            await _event(comm, "bump", view_id="guarded")
            child = consumer.view_instance._get_child_view("guarded")
            frames = await _redirect(comm, "Broken", "/broken/")
            assert frames[-1]["type"] == "error", frames
            await _settle(comm)
            assert consumer._sticky_preserved == {}
            assert child._djust_child_disposed is True
            frames = await _send(comm, {"type": "mount", "view": f"{MODULE}.GC", "url": "/gc/"})
            assert "v=2" not in json.dumps(frames)
            assert Guarded.mounts == 2
        finally:
            await comm.disconnect()


# ---------------------------------------------------------------------------
# Principals
# ---------------------------------------------------------------------------


async def test_not_carried_for_a_different_tenant():
    key = await sync_to_async(_new_session)()
    tenancy = dict(SETTINGS, DJUST_CONFIG={"TENANT_RESOLVER": "path", "TENANT_PATH_POSITION": 1})
    with override_settings(**tenancy):
        comm = await _connect(key, "PA", "/t1/pa/")
        try:
            await _event(comm, "next_track", view_id="player")
            frames = await _redirect(comm, "PB", "/t1/pb/")
            assert _held(frames) == ["player"]
            assert Player.mounts == 1
            frames = await _redirect(comm, "PA", "/t2/pa/")
            assert "player" not in _held(frames)
            assert "track=two" not in json.dumps(frames)
            assert Player.mounts == 2
        finally:
            await comm.disconnect()


async def test_not_carried_for_a_different_user():
    """The socket's user is fixed at connect; the redirect runs as another
    user when the session that vouched for it is gone (#3201/#3212): an
    authenticated user's child is not carried to the anonymous page."""
    from django.contrib.auth import BACKEND_SESSION_KEY, HASH_SESSION_KEY, SESSION_KEY
    from django.contrib.auth.models import User

    def login():
        user = User.objects.create_user("u4149", password="pw-4149-x")
        session = SessionStore()
        session[SESSION_KEY] = str(user.pk)
        session[BACKEND_SESSION_KEY] = "django.contrib.auth.backends.ModelBackend"
        session[HASH_SESSION_KEY] = user.get_session_auth_hash()
        session.create()
        return user, session.session_key

    user, key = await sync_to_async(login)()
    with override_settings(**SETTINGS):
        comm = await _connect(key, "PA", "/pa/", user=user)
        try:
            await _event(comm, "next_track", view_id="player")
            frames = await _redirect(comm, "PB", "/pb/")
            assert _held(frames) == ["player"]
            await sync_to_async(SessionStore(key).delete)()
            frames = await _redirect(comm, "PA", "/pa/")
            assert "player" not in _held(frames)
            assert "track=two" not in json.dumps(frames)
            assert Player.mounts == 2
        finally:
            await comm.disconnect()
