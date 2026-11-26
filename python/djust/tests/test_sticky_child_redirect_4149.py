"""An explicit sticky child survives ``live_redirect`` to another page (#4149).

Under ``exposure_policy = "explicit"`` a sticky child was re-attached only by a
``{% live_render %}`` whose reuse identity matched, and that identity included
the request path and the parent's class: both change on a redirect from
``/a/`` to ``/b/``, so the destination got a freshly mounted child and the
user's state on it was lost. A legacy child survived.

A preserved child now carries to a destination whose ``{% live_render %}``
names the same class, slot and mount inputs, for the same session, user and
tenant, regardless of route and parent class; it is re-authorized against the
destination request as before. These tests drive ``LiveViewConsumer`` over a
real ``WebsocketCommunicator``. SSE keeps no sticky children for either
policy (``SSESession._replace_view``), so there is no SSE carry to test.
"""

from __future__ import annotations

import json

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.test import override_settings
from django.urls import path

from djust import LiveView, event_handler
from djust.decorators import state
from djust.tests._frames import legacy_type

pytestmark = [pytest.mark.asyncio, pytest.mark.django_db(transaction=True)]

MODULE = __name__
SETTINGS = dict(
    LIVEVIEW_ALLOWED_MODULES=[MODULE],
    DJUST_LIVE_RENDER_ALLOWED_MODULES=[MODULE],
    ROOT_URLCONF=MODULE,
    DEBUG=False,
)


class Player(LiveView):
    """The sticky child. ``track`` is declared state; ``_private`` is not."""

    exposure_policy = "explicit"
    sticky = True
    sticky_id = "player"
    track = state("one")
    template = '<div>track={{ track }}<button dj-click="next_track">n</button></div>'

    mounts = 0

    def mount(self, request, **kwargs):
        type(self).mounts += 1
        self.track = "one"
        self._private = "PLAYER_PRIVATE_SENTINEL"

    @event_handler()
    def next_track(self, **kwargs):
        self.track = "two" if self.track == "one" else "three"

    def get_context_data(self, **kwargs):
        return super().get_context_data(track=self.track, **kwargs)


def _page(name, body):
    return f'{{% load live_tags %}}<div dj-root dj-view="{MODULE}.{name}" dj-id="0">{body}</div>'


class PageA(LiveView):
    exposure_policy = "explicit"
    template = _page("PageA", f'<h1>A</h1>{{% live_render "{MODULE}.Player" sticky=True %}}')


class PageB(LiveView):
    """A different parent class on a different route rendering the same child."""

    exposure_policy = "explicit"
    template = _page("PageB", f'<h1>B</h1>{{% live_render "{MODULE}.Player" sticky=True %}}')


class PageInputs(LiveView):
    """Renders the child with different mount inputs."""

    exposure_policy = "explicit"
    template = _page(
        "PageInputs", f'<h1>I</h1>{{% live_render "{MODULE}.Player" sticky=True album=2 %}}'
    )


class PageNoChild(LiveView):
    exposure_policy = "explicit"
    template = _page("PageNoChild", "<h1>none</h1>")


class PageBareSlot(LiveView):
    """A bare slot does not authorize an explicit child (unchanged)."""

    exposure_policy = "explicit"
    template = _page("PageBareSlot", '<h1>slot</h1><div dj-sticky-slot="player"></div>')


class StoredPlayer(LiveView):
    """A sticky child whose declared state is stored server-side."""

    exposure_policy = "explicit"
    sticky = True
    sticky_id = "stored"
    count = state(1, persist="server")
    template = '<div>count={{ count }}<button dj-click="bump">+</button></div>'

    @event_handler()
    def bump(self, **kwargs):
        self.count += 1

    def get_context_data(self, **kwargs):
        return super().get_context_data(count=self.count, **kwargs)


class StoredPageA(LiveView):
    exposure_policy = "explicit"
    template = _page(
        "StoredPageA", f'<h1>SA</h1>{{% live_render "{MODULE}.StoredPlayer" sticky=True %}}'
    )


class StoredPageB(LiveView):
    exposure_policy = "explicit"
    template = _page(
        "StoredPageB", f'<h1>SB</h1>{{% live_render "{MODULE}.StoredPlayer" sticky=True %}}'
    )


urlpatterns = [
    path("sa/", StoredPageA.as_view()),
    path("sb/", StoredPageB.as_view()),
    path("a/", PageA.as_view()),
    path("b/", PageB.as_view()),
    path("inputs/", PageInputs.as_view()),
    path("none/", PageNoChild.as_view()),
    path("slot/", PageBareSlot.as_view()),
]


@pytest.fixture(autouse=True)
def _reset_mounts():
    Player.mounts = 0
    yield


def _new_session():
    session = SessionStore()
    session.create()
    return session.session_key


async def _connect(session_key, view="PageA", url="/a/"):
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    app = LiveViewConsumer.as_asgi()
    comm = WebsocketCommunicator(app, "/ws/")
    comm.scope["session"] = SessionStore(session_key)
    comm.scope["user"] = AnonymousUser()
    connected, _ = await comm.connect()
    assert connected
    await comm.receive_json_from()  # connect frame
    await comm.send_json_to({"type": "mount", "view": f"{MODULE}.{view}", "url": url})
    frame = await comm.receive_json_from()
    assert frame["type"] == "mount", frame
    return comm


async def _event(comm, event, **params):
    await comm.send_json_to({"type": "event", "event": event, "params": params})
    while True:
        frame = await comm.receive_json_from()
        if legacy_type(frame) in {"embedded_update", "error", "patch", "html_update", "noop"}:
            return frame


async def _redirect(comm, view, url):
    """Live-redirect; return the frames up to and including the mount frame."""
    await comm.send_json_to({"type": "live_redirect_mount", "view": f"{MODULE}.{view}", "url": url})
    frames = []
    while True:
        frame = await comm.receive_json_from()
        frames.append(frame)
        if frame.get("type") == "mount":
            return frames


def _hold(frames):
    holds = [f for f in frames if f.get("type") == "sticky_hold"]
    assert holds, [legacy_type(f) for f in frames]
    types = [legacy_type(f) for f in frames]
    assert types.index("sticky_hold") < types.index("mount"), types
    return holds[0]["views"]


async def _advance(comm):
    reply = await _event(comm, "next_track", view_id="player")
    assert legacy_type(reply) == "embedded_update", reply
    assert "track=two" in reply["html"], reply
    return reply


async def test_explicit_sticky_child_survives_redirect_to_another_page():
    """The reproducer: state changed before the redirect is still there after
    it, the child was not mounted again, and it still takes events."""
    key = await sync_to_async(_new_session)()
    with override_settings(**SETTINGS):
        comm = await _connect(key)
        try:
            await _advance(comm)
            frames = await _redirect(comm, "PageB", "/b/")
            assert _hold(frames) == ["player"]
            mount = frames[-1]
            # The destination placeholder, not a re-rendered child.
            assert 'dj-sticky-slot="player"' in mount["html"]
            assert "track=one" not in mount["html"]
            assert Player.mounts == 1
            reply = await _event(comm, "next_track", view_id="player")
            assert legacy_type(reply) == "embedded_update", reply
            assert "track=three" in reply["html"], reply
            # Explicit exposure: nothing outside get_context_data/state() is sent.
            wire = json.dumps(frames) + json.dumps(reply)
            assert "PLAYER_PRIVATE_SENTINEL" not in wire
        finally:
            await comm.disconnect()


async def test_redirect_back_and_forth_keeps_the_same_child():
    key = await sync_to_async(_new_session)()
    with override_settings(**SETTINGS):
        comm = await _connect(key)
        try:
            await _advance(comm)
            assert _hold(await _redirect(comm, "PageB", "/b/")) == ["player"]
            assert _hold(await _redirect(comm, "PageA", "/a/")) == ["player"]
            reply = await _event(comm, "next_track", view_id="player")
            assert "track=three" in reply["html"], reply
            assert Player.mounts == 1
        finally:
            await comm.disconnect()


async def test_destination_without_the_child_releases_it():
    key = await sync_to_async(_new_session)()
    with override_settings(**SETTINGS):
        comm = await _connect(key)
        try:
            await _advance(comm)
            assert _hold(await _redirect(comm, "PageNoChild", "/none/")) == []
            reply = await _event(comm, "next_track", view_id="player")
            assert reply["type"] == "error", reply
            # Navigating on to a page that renders it mounts a fresh child.
            frames = await _redirect(comm, "PageB", "/b/")
            assert not any(f.get("type") == "sticky_hold" for f in frames)
            assert "track=one" in frames[-1]["html"]
            assert Player.mounts == 2
        finally:
            await comm.disconnect()


async def test_bare_slot_does_not_carry_an_explicit_child():
    """Unchanged: only the destination's ``{% live_render %}`` can authorize
    an explicit child's reattachment, never bare slot markup."""
    key = await sync_to_async(_new_session)()
    with override_settings(**SETTINGS):
        comm = await _connect(key)
        try:
            await _advance(comm)
            assert _hold(await _redirect(comm, "PageBareSlot", "/slot/")) == []
        finally:
            await comm.disconnect()


async def test_changed_mount_inputs_mount_a_fresh_child():
    key = await sync_to_async(_new_session)()
    with override_settings(**SETTINGS):
        comm = await _connect(key)
        try:
            await _advance(comm)
            frames = await _redirect(comm, "PageInputs", "/inputs/")
            assert "track=one" in frames[-1]["html"]
            assert 'dj-sticky-slot="player"' not in frames[-1]["html"]
            assert Player.mounts == 2
        finally:
            await comm.disconnect()


async def test_two_tabs_keep_their_own_child():
    """Same session, two sockets: a redirect in one tab never picks up the
    other tab's child or its state."""
    key = await sync_to_async(_new_session)()
    with override_settings(**SETTINGS):
        one = await _connect(key)
        two = await _connect(key)
        try:
            await _advance(one)
            reply = await _event(one, "next_track", view_id="player")
            assert "track=three" in reply["html"], reply
            frames = await _redirect(two, "PageB", "/b/")
            assert _hold(frames) == ["player"]
            reply = await _event(two, "next_track", view_id="player")
            # Tab two's own child, which was at "one".
            assert "track=two" in reply["html"], reply
            frames = await _redirect(one, "PageB", "/b/")
            reply = await _event(one, "next_track", view_id="player")
            assert "track=three" in reply["html"], reply
            assert Player.mounts == 2
        finally:
            await one.disconnect()
            await two.disconnect()


async def test_vanished_session_does_not_carry_the_child():
    """A redirect whose session is gone runs as anonymous under a replacement
    session (#3201/#3212); the child is not carried into that session."""
    key = await sync_to_async(_new_session)()
    with override_settings(**SETTINGS):
        comm = await _connect(key)
        try:
            await _advance(comm)
            await sync_to_async(SessionStore(key).delete)()
            frames = await _redirect(comm, "PageB", "/b/")
            assert "track=one" in frames[-1]["html"]
            assert Player.mounts == 2
        finally:
            await comm.disconnect()


async def _stored(key, route):
    from djust._exposure_children import child_state_key

    stored = await sync_to_async(SessionStore(key).load)()
    return stored.get(child_state_key(route, ("stored",)), {}).get("state", {}).get("values")


async def test_carried_child_stores_its_state_under_the_destination():
    """A child with server-persisted state keeps taking events after the
    carry, and its state is saved under the destination's scope."""
    key = await sync_to_async(_new_session)()
    with override_settings(**SETTINGS):
        comm = await _connect(key, "StoredPageA", "/sa/")
        try:
            reply = await _event(comm, "bump", view_id="stored")
            assert "count=2" in reply["html"], reply
            frames = await _redirect(comm, "StoredPageB", "/sb/")
            assert _hold(frames) == ["stored"]
            assert (await _stored(key, "/sb/")) == {"count": 2}
            reply = await _event(comm, "bump", view_id="stored")
            assert legacy_type(reply) == "embedded_update", reply
            assert "count=3" in reply["html"], reply
        finally:
            await comm.disconnect()
        assert (await _stored(key, "/sb/")) == {"count": 3}
