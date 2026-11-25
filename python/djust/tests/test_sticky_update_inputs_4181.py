"""#4181 / #4238: ``sticky_update_inputs`` for explicit sticky children.

An explicit sticky child's ``{% live_render %}`` inputs were all part of its
reuse identity, so any change remounted it and ``_on_sticky_update`` was never
reached (#4238). The inputs it names in ``sticky_update_inputs`` are now left
out of that identity: a change to one reuses the child and calls the hook, and
its state (``persist="server"`` too) survives. A change to any other input
still remounts it. The rule holds across ``live_redirect``: a carried child
gets the hook and is re-rendered right after the mount frame.

Driven through ``LiveViewConsumer`` over a real ``WebsocketCommunicator``.
"""

from __future__ import annotations

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.template import TemplateSyntaxError
from django.test import RequestFactory, override_settings
from django.urls import path

from djust import LiveView, event_handler, state

pytestmark = [pytest.mark.django_db(transaction=True)]

MODULE = __name__
SETTINGS = dict(
    LIVEVIEW_ALLOWED_MODULES=["djust", MODULE],
    DJUST_LIVE_RENDER_ALLOWED_MODULES=[MODULE],
    ROOT_URLCONF=MODULE,
    DEBUG=False,
)


class Player(LiveView):
    exposure_policy = "explicit"
    sticky = True
    sticky_id = "player"
    sticky_update_inputs = ("title",)
    title = state("")
    track = state("")
    plays = state(0, persist="server")
    template = '<div>{{ title }}|{{ track }}|plays={{ plays }}<b dj-click="play">p</b></div>'

    mounts = 0
    updates: list = []

    def mount(self, request, title="", track="", **kwargs):
        type(self).mounts += 1
        self.title = str(title)
        self.track = str(track)

    def _on_sticky_update(self, changed):
        type(self).updates.append(dict(changed))
        if "title" in changed:
            self.title = str(changed["title"])

    @event_handler()
    def play(self, **kwargs):
        self.plays += 1


def _page(name, body):
    return f'{{% load live_tags %}}<div dj-root dj-view="{MODULE}.{name}" dj-id="0">{body}</div>'


class Shell(LiveView):
    exposure_policy = "explicit"
    title = state("a")
    track = state("t1")
    template = _page(
        "Shell", f'{{% live_render "{MODULE}.Player" sticky=True title=title track=track %}}'
    )

    @event_handler()
    def set_title(self, value: str = "", **kwargs):
        self.title = value

    @event_handler()
    def set_track(self, value: str = "", **kwargs):
        self.track = value


class OtherShell(LiveView):
    exposure_policy = "explicit"
    template = _page(
        "OtherShell",
        f'<h1>other</h1>{{% live_render "{MODULE}.Player" sticky=True title="b" track="t1" %}}',
    )


class OtherTrackShell(LiveView):
    exposure_policy = "explicit"
    template = _page(
        "OtherTrackShell",
        f'<h1>t9</h1>{{% live_render "{MODULE}.Player" sticky=True title="a" track="t9" %}}',
    )


class BadPlayer(Player):
    sticky_id = "bad"
    sticky_update_inputs = "title"  # a str, not a tuple


class BadShell(LiveView):
    exposure_policy = "explicit"
    template = _page("BadShell", f'{{% live_render "{MODULE}.BadPlayer" sticky=True title="x" %}}')


class ObjPlayer(LiveView):
    """Its object is an updatable input; one object is forbidden."""

    exposure_policy = "explicit"
    sticky = True
    sticky_id = "objplayer"
    sticky_update_inputs = ("object_id",)
    object_id = state("")
    template = '<div>obj={{ object_id }}<b dj-click="noop">p</b></div>'
    instances: list = []

    def mount(self, request, object_id="", **kwargs):
        type(self).instances.append(self)
        self.object_id = str(object_id)

    def _on_sticky_update(self, changed):
        self.object_id = str(changed["object_id"])

    def get_object(self):
        return self.object_id

    def has_object_permission(self, request, obj):
        return obj != "forbidden"

    @event_handler()
    def noop(self, **kwargs):
        pass


class ObjShell(LiveView):
    exposure_policy = "explicit"
    object_id = state("ok")
    template = _page(
        "ObjShell", f'{{% live_render "{MODULE}.ObjPlayer" sticky=True object_id=object_id %}}'
    )

    @event_handler()
    def set_object(self, value: str = "", **kwargs):
        self.object_id = value


class ObjOtherShell(LiveView):
    exposure_policy = "explicit"
    template = _page(
        "ObjOtherShell",
        f'<h1>o</h1>{{% live_render "{MODULE}.ObjPlayer" sticky=True object_id="forbidden" %}}',
    )


class TitlePlayer(LiveView):
    """An updatable input backing a persisted field."""

    exposure_policy = "explicit"
    sticky = True
    sticky_id = "titleplayer"
    sticky_update_inputs = ("title",)
    title = state("", persist="server")
    template = "<div>title={{ title }}</div>"

    def mount(self, request, title="", **kwargs):
        self.title = str(title)

    def _on_sticky_update(self, changed):
        self.title = str(changed["title"])


class TitleShell(LiveView):
    exposure_policy = "explicit"
    title = state("a")
    template = _page(
        "TitleShell", f'{{% live_render "{MODULE}.TitlePlayer" sticky=True title=title %}}'
    )

    @event_handler()
    def set_title(self, value: str = "", **kwargs):
        self.title = value


urlpatterns = [
    path("shell/", Shell.as_view()),
    path("other/", OtherShell.as_view()),
    path("t9/", OtherTrackShell.as_view()),
    path("obj/", ObjShell.as_view()),
    path("objother/", ObjOtherShell.as_view()),
    path("title/", TitleShell.as_view()),
]


@pytest.fixture(autouse=True)
def _reset():
    Player.mounts = 0
    Player.updates = []
    yield


def _new_session():
    session = SessionStore()
    session.create()
    return session.session_key


async def _connect(key):
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    comm = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    comm.scope["session"] = SessionStore(key)
    comm.scope["user"] = AnonymousUser()
    assert (await comm.connect())[0]
    await comm.receive_json_from()
    await comm.send_json_to({"type": "mount", "view": f"{MODULE}.Shell", "url": "/shell/"})
    frame = await comm.receive_json_from()
    assert frame["type"] == "mount", frame
    assert "a|t1|plays=0" in frame["html"]
    return comm


async def _event(comm, event, **params):
    await comm.send_json_to({"type": "event", "event": event, "params": params})
    while True:
        frame = await comm.receive_json_from(timeout=10)
        if frame.get("type") in {"embedded_update", "error", "patch", "html_update", "noop"}:
            return frame


async def _recovery(comm):
    await comm.send_json_to({"type": "request_html"})
    while True:
        frame = await comm.receive_json_from(timeout=10)
        if frame.get("type") == "html_recovery":
            return frame.get("html", "")


async def _play(comm):
    reply = await _event(comm, "play", view_id="player")
    assert reply["type"] == "embedded_update", reply
    assert "plays=1" in reply["html"], reply


async def _redirect(comm, view, url):
    """Live-redirect; the frames up to the mount frame, then what follows it."""
    from ._ws_frames import drain_extra

    await comm.send_json_to({"type": "live_redirect_mount", "view": f"{MODULE}.{view}", "url": url})
    frames = []
    while True:
        frame = await comm.receive_json_from(timeout=10)
        frames.append(frame)
        if frame.get("type") == "mount":
            break
    return frames + await drain_extra(comm)


def _held(frames):
    return [v for f in frames if f.get("type") == "sticky_hold" for v in f.get("views", [])]


@pytest.mark.asyncio
async def test_updatable_input_change_reuses_child_and_calls_hook():
    key = await sync_to_async(_new_session)()
    with override_settings(**SETTINGS):
        comm = await _connect(key)
        try:
            await _play(comm)
            await _event(comm, "set_title", value="z")
            html = await _recovery(comm)
        finally:
            await comm.disconnect()
    assert "z|t1|plays=1" in html, html
    assert Player.mounts == 1
    assert Player.updates == [{"title": "z"}]


@pytest.mark.asyncio
async def test_identity_input_change_remounts():
    key = await sync_to_async(_new_session)()
    with override_settings(**SETTINGS):
        comm = await _connect(key)
        try:
            await _play(comm)
            await _event(comm, "set_track", value="t2")
            html = await _recovery(comm)
        finally:
            await comm.disconnect()
    # The persisted plays is not restored either: the binding includes track.
    assert "a|t2|plays=0" in html, html
    assert Player.mounts == 2
    assert Player.updates == []


@pytest.mark.asyncio
async def test_updatable_change_across_live_redirect_carries_and_updates():
    key = await sync_to_async(_new_session)()
    with override_settings(**SETTINGS):
        comm = await _connect(key)
        try:
            await _play(comm)
            frames = await _redirect(comm, "OtherShell", "/other/")
            assert _held(frames) == ["player"]
            types = [f.get("type") for f in frames]
            mount_at = types.index("mount")
            updates = [
                f
                for f in frames[mount_at:]
                if f.get("type") == "embedded_update" and f.get("view_id") == "player"
            ]
            assert updates, types
            assert "b|t1|plays=1" in updates[0]["html"], updates[0]
            # It still takes events, with its state.
            reply = await _event(comm, "play", view_id="player")
            assert "b|t1|plays=2" in reply["html"], reply
        finally:
            await comm.disconnect()
    assert Player.mounts == 1
    assert Player.updates[-1] == {"title": "b"}


@pytest.mark.asyncio
async def test_identity_change_across_live_redirect_remounts():
    key = await sync_to_async(_new_session)()
    with override_settings(**SETTINGS):
        comm = await _connect(key)
        try:
            await _play(comm)
            frames = await _redirect(comm, "OtherTrackShell", "/t9/")
            assert "player" not in _held(frames)
            mount = [f for f in frames if f.get("type") == "mount"][0]
            assert "a|t9|plays=0" in mount["html"]
        finally:
            await comm.disconnect()
    assert Player.mounts == 2
    assert Player.updates == []


@pytest.mark.asyncio
async def test_object_permission_is_checked_again_after_the_hook():
    """An updatable input that names the object: switching it to a forbidden one
    drops the child and refuses the embed, as a fresh mount would."""
    from djust.websocket import LiveViewConsumer  # noqa: F401  (app import)

    ObjPlayer.instances.clear()
    key = await sync_to_async(_new_session)()
    with override_settings(**SETTINGS):
        from channels.testing import WebsocketCommunicator

        from djust.websocket import LiveViewConsumer as Consumer

        comm = WebsocketCommunicator(Consumer.as_asgi(), "/ws/")
        comm.scope["session"] = SessionStore(key)
        comm.scope["user"] = AnonymousUser()
        assert (await comm.connect())[0]
        await comm.receive_json_from()
        try:
            await comm.send_json_to({"type": "mount", "view": f"{MODULE}.ObjShell", "url": "/obj/"})
            frame = await comm.receive_json_from()
            assert "obj=ok" in frame["html"]
            await _event(comm, "set_object", value="forbidden")
            (child,) = ObjPlayer.instances
            assert child.object_id == "forbidden"  # the hook ran...
            # ...and the re-check dropped it: it takes no more events.
            assert getattr(child, "_djust_child_disposed", False)
            reply = await _event(comm, "noop", view_id="objplayer")
            assert reply["type"] == "error", reply
        finally:
            await comm.disconnect()


@pytest.mark.asyncio
async def test_carried_child_object_permission_is_checked_again_after_the_hook():
    ObjPlayer.instances.clear()
    key = await sync_to_async(_new_session)()
    with override_settings(**SETTINGS):
        from channels.testing import WebsocketCommunicator

        from djust.websocket import LiveViewConsumer as Consumer

        comm = WebsocketCommunicator(Consumer.as_asgi(), "/ws/")
        comm.scope["session"] = SessionStore(key)
        comm.scope["user"] = AnonymousUser()
        assert (await comm.connect())[0]
        await comm.receive_json_from()
        try:
            await comm.send_json_to({"type": "mount", "view": f"{MODULE}.ObjShell", "url": "/obj/"})
            assert "obj=ok" in (await comm.receive_json_from())["html"]
            await comm.send_json_to(
                {
                    "type": "live_redirect_mount",
                    "view": f"{MODULE}.ObjOtherShell",
                    "url": "/objother/",
                }
            )
            from ._ws_frames import drain_extra

            frames = [await comm.receive_json_from(timeout=10)]
            frames += await drain_extra(comm)
            carried = ObjPlayer.instances[0]
            assert carried.object_id == "forbidden"  # the hook ran...
            assert getattr(carried, "_djust_child_disposed", False)  # ...then it was dropped
            assert not any(
                f.get("type") == "embedded_update" and "obj=forbidden" in f.get("html", "")
                for f in frames
            )
        finally:
            await comm.disconnect()


@pytest.mark.asyncio
async def test_updatable_persisted_field_over_ws():
    key = await sync_to_async(_new_session)()
    with override_settings(**SETTINGS):
        from channels.testing import WebsocketCommunicator

        from djust.websocket import LiveViewConsumer as Consumer

        comm = WebsocketCommunicator(Consumer.as_asgi(), "/ws/")
        comm.scope["session"] = SessionStore(key)
        comm.scope["user"] = AnonymousUser()
        assert (await comm.connect())[0]
        await comm.receive_json_from()
        try:
            await comm.send_json_to(
                {"type": "mount", "view": f"{MODULE}.TitleShell", "url": "/title/"}
            )
            assert "title=a" in (await comm.receive_json_from())["html"]
            await _event(comm, "set_title", value="z")
            html = await _recovery(comm)
        finally:
            await comm.disconnect()
    assert "title=z" in html, html


def test_updatable_persisted_field_over_http_matches_ws():
    """Over HTTP every POST mounts the child afresh and restores its persisted
    title over mount(title=new); the hook then applies the new input again, so
    the page shows what a live socket shows (#4181 review)."""
    import json

    with override_settings(**SETTINGS):
        session = SessionStore()
        session.create()

        def call(method, body=None):
            factory = RequestFactory()
            request = (
                factory.get("/title/")
                if method == "get"
                else factory.post(
                    "/title/",
                    json.dumps(body),
                    content_type="application/json",
                    HTTP_X_DJUST_EVENT=body["event"],
                )
            )
            request.user = AnonymousUser()
            request.tenant = None
            request.session = SessionStore(session.session_key)
            response = TitleShell.as_view()(request)
            request.session.save()
            return response

        assert "title=a" in call("get").content.decode()
        response = call("post", {"event": "set_title", "params": {"value": "z"}})
        assert response.status_code == 200, response.content
        assert "title=z" in json.loads(response.content)["html"]
        # The next POST restores the saved title and still shows the page's input.
        response = call("post", {"event": "set_title", "params": {"value": "y"}})
        assert "title=y" in json.loads(response.content)["html"]


def test_sticky_update_inputs_where_ignored_warns():
    with pytest.warns(UserWarning, match="sticky_update_inputs"):

        class NotSticky(LiveView):
            exposure_policy = "explicit"
            sticky_update_inputs = ("x",)

    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("error")

        class Fine(LiveView):
            exposure_policy = "explicit"
            sticky = True
            sticky_id = "fine"
            sticky_update_inputs = ("x",)


def _http_get(view_class):
    request = RequestFactory().get("/shell/")
    request.user = AnonymousUser()
    request.tenant = None
    request.session = SessionStore()
    request.session.create()
    return view_class.as_view()(request)


def test_bad_sticky_update_inputs_is_refused(monkeypatch):
    """The tag refuses it; an explicit page answers the protected 500."""
    import djust._exposure_diagnostics as diagnostics

    seen = []
    outcome = diagnostics.protected_http_outcome

    def record(exc, *args, **kwargs):
        seen.append(exc)
        return outcome(exc, *args, **kwargs)

    monkeypatch.setattr(diagnostics, "protected_http_outcome", record)
    with override_settings(**SETTINGS):
        response = _http_get(BadShell)
    assert response.status_code == 500
    errors = [e for e in seen if isinstance(e, TemplateSyntaxError)]
    assert errors and "sticky_update_inputs must be a tuple" in str(errors[0])


def test_http_get_mounts_with_full_inputs():
    with override_settings(**SETTINGS):
        response = _http_get(Shell)
    assert response.status_code == 200
    assert "a|t1|plays=0" in response.content.decode()
    assert Player.mounts >= 1
