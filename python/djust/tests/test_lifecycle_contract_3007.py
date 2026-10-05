"""#3007: the ``connected()`` / ``disconnected()`` view hooks, and the lifecycle contract.

``connected()`` runs once per live mount (WebSocket or SSE), after the view is
admitted and set up (``mount()`` or a state restore, ``handle_params()``) and
before its first render. ``disconnected()`` runs once for each view that got
that far, when its live mount ends: the socket or stream closed, or the view was
replaced (``live_redirect``, a second ``mount``), unmounted or revoked. Neither
runs on the HTTP render or the HTTP POST fallback, and neither is called if the
process dies. ``docs/website/api-reference/liveview.md`` ("Lifecycle contract")
states the contract these tests pin.

Both transports are driven through their real entry points: a
``WebsocketCommunicator`` against ``LiveViewConsumer``, and the SSE stream and
message views.
"""

import ast
import asyncio
import json
import logging
import pathlib
import threading
import uuid

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth import get_user
from django.contrib.auth.models import AnonymousUser, User
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory, override_settings
from django.urls import path
from django.utils.functional import SimpleLazyObject

from djust import LiveView, event_handler, sse
from djust.sse import DjustSSEMessageView, DjustSSEStreamView, _sse_sessions
from djust.tenants.middleware import get_current_tenant
from djust.tenants.resolvers import TenantInfo

pytestmark = [pytest.mark.django_db(transaction=True)]

MOD = __name__

#: ``(what, tag)`` records, in the order they happened.
EVENTS: list = []
#: tag -> view, for every view mounted in the test.
VIEWS: dict = {}
#: What a hook saw: ``(what, tag) -> {...}``.
SEEN: dict = {}
#: The consumers the tests' sockets run on, newest last.
CONSUMERS: list = []


def _on_the_event_loop() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


class _Hooked:
    """Records every lifecycle call and what the hooks could see."""

    exposure_policy = "legacy"
    fail_connected = False
    fail_disconnected = False

    def _tagged(self):
        self._tag = type(self).__name__ + ":" + uuid.uuid4().hex[:8]
        VIEWS[self._tag] = self

    def mount(self, request, **kwargs):
        self._tagged()
        self.status = "mounted"
        EVENTS.append(("mount", self._tag))

    def handle_params(self, params, uri):
        EVENTS.append(("handle_params", self._tag))

    def connected(self):
        EVENTS.append(("connected", self._tag))
        SEEN[("connected", self._tag)] = {
            "websocket_session_id": getattr(self, "_websocket_session_id", None),
            "on_loop": _on_the_event_loop(),
            "users": User.objects.count(),  # the ORM works: a worker thread
            "status_before": self.status,
        }
        self.status = "connected"
        if self.fail_connected:
            raise RuntimeError("connected failed")

    def disconnected(self):
        EVENTS.append(("disconnected", self._tag))
        SEEN[("disconnected", self._tag)] = {
            "on_loop": _on_the_event_loop(),
            "users": User.objects.count(),
            "tenant": get_current_tenant(),
        }
        if self.fail_disconnected:
            raise RuntimeError("disconnected failed")

    @event_handler()
    def ping(self, **kwargs):
        EVENTS.append(("ping", self._tag))


class Page(_Hooked, LiveView):
    template = '<div dj-root dj-view="' + MOD + '.Page"><b>{{ status }}</b></div>'


class Dest(_Hooked, LiveView):
    template = '<div dj-root dj-view="' + MOD + '.Dest"><b>dest {{ status }}</b></div>'


class Lazy(_Hooked, LiveView):
    template = '<div dj-root dj-view="' + MOD + '.Lazy"><b>lazy {{ status }}</b></div>'


class Lazy2(_Hooked, LiveView):
    template = '<div dj-root dj-view="' + MOD + '.Lazy2"><b>lazy2 {{ status }}</b></div>'


class FailsToConnect(_Hooked, LiveView):
    fail_connected = True
    template = '<div dj-root dj-view="' + MOD + '.FailsToConnect"><b>{{ status }}</b></div>'


class FailsToDisconnect(_Hooked, LiveView):
    fail_disconnected = True
    template = '<div dj-root dj-view="' + MOD + '.FailsToDisconnect"><b>{{ status }}</b></div>'


class Refused(_Hooked, LiveView):
    """Login is required and the test's user is anonymous: the mount is refused."""

    login_required = True
    template = '<div dj-root dj-view="' + MOD + '.Refused"><b>{{ status }}</b></div>'


class OnlyDisconnected(LiveView):
    """Defines ``disconnected`` but no ``connected``: it still runs."""

    exposure_policy = "legacy"
    template = '<div dj-root dj-view="' + MOD + '.OnlyDisconnected"><b>x</b></div>'

    def mount(self, request, **kwargs):
        self._tag = "OnlyDisconnected"
        VIEWS[self._tag] = self

    def disconnected(self):
        EVENTS.append(("disconnected", self._tag))


class StateNamed(LiveView):
    """Existing code can hold state under these names: not callable, not a hook."""

    exposure_policy = "legacy"
    template = '<div dj-root dj-view="' + MOD + '.StateNamed"><b>{{ label }}</b></div>'

    def mount(self, request, **kwargs):
        self._tag = "StateNamed"
        VIEWS[self._tag] = self
        self.connected = False
        self.disconnected = True
        self.label = "ok"


class Kid(_Hooked, LiveView):
    """A view a page embeds: only transport-mounted views get the hooks."""

    template = "<div><span>kid</span></div>"


class Parent(_Hooked, LiveView):
    template = (
        "{% load live_tags %}"
        '<div dj-root dj-view="' + MOD + '.Parent">'
        '{% live_render "' + MOD + '.Kid" view_id="kid" %}'
        "</div>"
    )

    def get_context_data(self, **kwargs):
        return {"view": self}


class _Plain(LiveView):
    exposure_policy = "legacy"

    def mount(self, request, **kwargs):
        self._tag = type(self).__name__
        VIEWS[self._tag] = self


class AsyncBoth(_Plain):
    """Dead code before #3007: an ``async def`` hook is warned about and skipped."""

    template = '<div dj-root dj-view="' + MOD + '.AsyncBoth"><b>x</b></div>'

    async def connected(self):
        EVENTS.append(("connected", "AsyncBoth"))

    async def disconnected(self):
        EVENTS.append(("disconnected", "AsyncBoth"))


class ExtraArgs(_Plain):
    """A method of that name that needs arguments is some other helper."""

    template = '<div dj-root dj-view="' + MOD + '.ExtraArgs"><b>x</b></div>'

    def connected(self, user):
        EVENTS.append(("connected", "ExtraArgs"))

    def disconnected(self, why):
        EVENTS.append(("disconnected", "ExtraArgs"))


class RaisingProperty(_Plain):
    """A property of that name is never evaluated."""

    template = '<div dj-root dj-view="' + MOD + '.RaisingProperty"><b>x</b></div>'

    @property
    def connected(self):
        raise RuntimeError("SECRET-connected")

    @property
    def disconnected(self):
        raise RuntimeError("SECRET-disconnected")


class NestedClass(_Plain):
    """A nested class is callable but is not a method: not instantiated."""

    template = '<div dj-root dj-view="' + MOD + '.NestedClass"><b>x</b></div>'

    class connected:
        def __init__(self):
            EVENTS.append(("connected", "NestedClass"))


class StaticHooks(_Plain):
    """Static and class methods that bind with no argument are hooks."""

    template = '<div dj-root dj-view="' + MOD + '.StaticHooks"><b>x</b></div>'

    @staticmethod
    def connected():
        EVENTS.append(("connected", "StaticHooks"))

    @classmethod
    def disconnected(cls):
        EVENTS.append(("disconnected", "StaticHooks"))


class NeitherHook(_Plain):
    template = '<div dj-root dj-view="' + MOD + '.NeitherHook"><b>x</b></div>'


class Blocking(_Hooked, LiveView):
    """Its ``disconnected()`` blocks until the test lets it go."""

    template = '<div dj-root dj-view="' + MOD + '.Blocking"><b>{{ status }}</b></div>'
    started = threading.Event()
    gate = threading.Event()

    def disconnected(self):
        type(self).started.set()
        type(self).gate.wait(10)


class Tenanted(_Hooked, LiveView):
    template = '<div dj-root dj-view="' + MOD + '.Tenanted"><b>{{ status }}</b></div>'

    def mount(self, request, **kwargs):
        super().mount(request, **kwargs)
        self._tenant = TenantInfo("tenant-3007", name="Tenant")


class Restorable(_Hooked, LiveView):
    """Opts in to the session-backed restore: a reconnect skips ``mount()``."""

    enable_state_snapshot = True
    template = '<div dj-root dj-view="' + MOD + '.Restorable"><b>{{ status }}</b></div>'


urlpatterns = [
    path("page/", Page.as_view()),
    path("dest/", Dest.as_view()),
]


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    monkeypatch.setattr(sse, "_SESSION_LINGER_S", 0)
    EVENTS.clear()
    VIEWS.clear()
    SEEN.clear()
    CONSUMERS.clear()
    _sse_sessions.clear()
    with override_settings(
        ROOT_URLCONF=__name__, LIVEVIEW_ALLOWED_MODULES=["djust", __name__], DEBUG=False
    ):
        yield
    _sse_sessions.clear()
    VIEWS.clear()
    SEEN.clear()
    CONSUMERS.clear()


def _calls(what):
    return [tag for (kind, tag) in EVENTS if kind == what]


def _order(tag):
    return [kind for (kind, t) in EVENTS if t == tag]


def _one(cls):
    tags = [tag for tag, view in VIEWS.items() if type(view) is cls]
    assert len(tags) == 1, (cls.__name__, tags)
    return tags[0]


async def _until(predicate, what):
    for _ in range(500):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("timed out waiting for " + what)


def _fresh_key():
    session = SessionStore()
    session.create()
    return session.session_key


# --------------------------------------------------------------------------- #
# WebSocket harness
# --------------------------------------------------------------------------- #


async def _ws_until(communicator, *types, timeout=15.0):
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


async def _ws_connect(session_key=None):
    pytest.importorskip("channels")
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    class _Recorded(LiveViewConsumer):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            CONSUMERS.append(self)

    communicator = WebsocketCommunicator(_Recorded.as_asgi(), "/ws/")
    key = session_key or await sync_to_async(_fresh_key)()
    communicator.scope["session"] = SessionStore(key)
    communicator.scope["user"] = AnonymousUser()
    connected, _ = await communicator.connect()
    assert connected
    await communicator.receive_json_from(timeout=2)  # connect ack
    return communicator


async def _ws_mount(communicator, cls, url="/page/", **extra):
    await communicator.send_json_to(
        {"type": "mount", "view": MOD + "." + cls.__name__, "url": url, **extra}
    )
    frames = await _ws_until(communicator, "mount", "error")
    return frames[-1]


def _kind(output):
    """The frame type of a ``websocket.send`` output, or its own type."""
    if output["type"] == "websocket.send" and output.get("text"):
        return json.loads(output["text"]).get("type")
    return output["type"]


async def _close(communicator):
    try:
        await communicator.disconnect()
    except (asyncio.CancelledError, Exception):  # noqa: BLE001 - teardown only
        pass


# --------------------------------------------------------------------------- #
# connected()
# --------------------------------------------------------------------------- #


async def test_connected_runs_after_mount_and_handle_params_before_the_first_render():
    communicator = await _ws_connect()
    try:
        frame = await _ws_mount(communicator, Page)
        assert frame["type"] == "mount", frame
        tag = _one(Page)

        assert _order(tag) == ["mount", "handle_params", "connected"]
        # It ran before the first render: state it set is in the mount frame.
        assert "connected" in frame["html"], frame["html"]
        seen = SEEN[("connected", tag)]
        assert seen["status_before"] == "mounted"
        # The live mount: the same marker ``mount()`` can read.
        assert seen["websocket_session_id"]
        # On a worker thread, so the ORM works.
        assert seen["on_loop"] is False
        assert _calls("disconnected") == []
    finally:
        await _close(communicator)


async def test_connected_runs_once_per_mount_not_per_event():
    communicator = await _ws_connect()
    try:
        await _ws_mount(communicator, Page)
        for _ in range(2):
            await communicator.send_json_to({"type": "event", "event": "ping", "params": {}})
            await _ws_until(communicator, "patch", "html_update", "noop", "error")
        assert len(_calls("connected")) == 1
        assert len(_calls("ping")) == 2
    finally:
        await _close(communicator)


async def test_a_connected_that_raises_fails_the_mount_like_mount_does():
    communicator = await _ws_connect()
    try:
        frame = await _ws_mount(communicator, FailsToConnect)
        assert frame["type"] == "error", frame
        tag = _one(FailsToConnect)
        assert _order(tag) == ["mount", "handle_params", "connected"]
    finally:
        await _close(communicator)
    # It was entered, so whatever it claimed is released.
    await _until(lambda: _calls("disconnected"), "the disconnected() of the failed mount")
    assert _calls("disconnected") == [tag]


async def test_a_view_the_mount_refuses_gets_neither_hook():
    communicator = await _ws_connect()
    try:
        await communicator.send_json_to(
            {"type": "mount", "view": MOD + ".Refused", "url": "/page/"}
        )
        seen = []
        while not seen or seen[-1]["type"] != "websocket.close":
            seen.append(await communicator.receive_output(timeout=5))
        assert all(_kind(o) != "mount" for o in seen), seen
    finally:
        await _close(communicator)
    assert _calls("connected") == []
    assert _calls("disconnected") == []


async def test_connected_runs_when_the_state_is_restored_and_mount_is_skipped():
    key = await sync_to_async(_fresh_key)()
    first = await _ws_connect(key)
    try:
        await _ws_mount(first, Restorable)
        await first.send_json_to({"type": "event", "event": "ping", "params": {}})
        await _ws_until(first, "patch", "html_update", "noop", "error")
    finally:
        await _close(first)
    await _until(lambda: _calls("disconnected"), "the first socket's disconnected()")
    EVENTS.clear()

    second = await _ws_connect(key)
    try:
        frame = await _ws_mount(second, Restorable, has_prerendered=True)
        assert frame["type"] == "mount", frame
        tag = _calls("connected")[-1]
        # mount() was skipped by the restore; connected() still ran.
        assert _calls("mount") == [], "the restore did not skip mount(); the test proves nothing"
        assert _order(tag) == ["handle_params", "connected"]
    finally:
        await _close(second)


@pytest.mark.parametrize("cls", [AsyncBoth, ExtraArgs, RaisingProperty, NestedClass, StateNamed])
async def test_a_member_that_is_not_a_hook_is_skipped_with_one_warning_per_class(cls, caplog):
    """Code that carried these names before the contract keeps working: the
    mount succeeds, the member is not run, and the class is warned about once
    per hook name (value-free), not once per mount."""
    with caplog.at_level(logging.WARNING, logger="djust._child_lifecycle"):
        for _ in range(2):
            communicator = await _ws_connect()
            frame = await _ws_mount(communicator, cls)
            assert frame["type"] == "mount", frame
            await communicator.disconnect()
    assert [k for (k, t) in EVENTS if t == cls.__name__] == []
    skipped = [r.getMessage() for r in caplog.records if cls.__qualname__ in r.getMessage()]
    expected = {"AsyncBoth": 2, "ExtraArgs": 2, "RaisingProperty": 2, "NestedClass": 1}
    # StateNamed sets both names as instance attributes in mount().
    assert len(skipped) == expected.get(cls.__name__, 2), skipped
    assert all("SECRET" not in m for m in skipped)
    for name in ("connected", "disconnected"):
        assert sum(1 for m in skipped if f".{name} " in m) <= 1, skipped


async def test_static_and_class_method_hooks_run():
    communicator = await _ws_connect()
    try:
        await _ws_mount(communicator, StaticHooks)
        assert _calls("connected") == ["StaticHooks"]
    finally:
        await _close(communicator)
    assert _calls("disconnected") == ["StaticHooks"]


async def test_a_view_with_neither_hook_is_not_marked_and_pays_no_thread_hop(monkeypatch):
    from djust import _child_lifecycle

    hops = []
    real = _child_lifecycle.sync_to_async

    def counting(func, *args, **kwargs):
        hops.append(getattr(func, "__name__", func))
        return real(func, *args, **kwargs)

    monkeypatch.setattr(_child_lifecycle, "sync_to_async", counting)
    communicator = await _ws_connect()
    try:
        frame = await _ws_mount(communicator, NeitherHook)
        assert frame["type"] == "mount", frame
        assert "_djust_connected_phase" not in VIEWS["NeitherHook"].__dict__
    finally:
        await communicator.disconnect()
    assert hops == []


async def test_cancelling_the_disconnect_during_the_hook_still_releases_the_view():
    Blocking.started.clear()
    Blocking.gate.clear()
    communicator = await _ws_connect()
    try:
        await _ws_mount(communicator, Blocking)
        view = VIEWS[_one(Blocking)]
        consumer = CONSUMERS[-1]
        task = asyncio.ensure_future(consumer.disconnect(1000))
        await _until(Blocking.started.is_set, "the hook to start")
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert view._djust_waiters_closed is True, "the view was never released"
    finally:
        Blocking.gate.set()
        await _close(communicator)


async def test_views_with_state_named_connected_or_disconnected_still_mount_and_close():
    communicator = await _ws_connect()
    try:
        frame = await _ws_mount(communicator, StateNamed)
        assert frame["type"] == "mount", frame
        assert "ok" in frame["html"]
    finally:
        await _close(communicator)


# --------------------------------------------------------------------------- #
# disconnected()
# --------------------------------------------------------------------------- #


async def test_disconnect_runs_disconnected_once_on_a_worker_thread():
    communicator = await _ws_connect()
    await _ws_mount(communicator, Page)
    tag = _one(Page)
    assert _calls("disconnected") == []

    await communicator.disconnect()

    assert _calls("disconnected") == [tag]
    assert _order(tag) == ["mount", "handle_params", "connected", "disconnected"]
    assert SEEN[("disconnected", tag)]["on_loop"] is False
    # Exactly once: it cannot be run again for the same view.
    from djust._child_lifecycle import fire_view_disconnected

    await fire_view_disconnected(VIEWS[tag])
    assert _calls("disconnected") == [tag]


async def test_a_view_without_connected_still_gets_disconnected():
    communicator = await _ws_connect()
    await _ws_mount(communicator, OnlyDisconnected)
    await communicator.disconnect()
    assert _calls("disconnected") == ["OnlyDisconnected"]


async def test_live_redirect_ends_the_old_views_live_mount_and_starts_the_new_ones():
    communicator = await _ws_connect()
    try:
        await _ws_mount(communicator, Page)
        old = _one(Page)

        await communicator.send_json_to(
            {"type": "live_redirect_mount", "view": MOD + ".Dest", "url": "/dest/", "params": {}}
        )
        await _ws_until(communicator, "mount")
        new = _one(Dest)

        # The old view is released: its disconnected() ran, once, before the new
        # view's connected().
        assert _calls("disconnected") == [old]
        assert EVENTS.index(("disconnected", old)) < EVENTS.index(("connected", new))
        assert _order(new) == ["mount", "handle_params", "connected"]
    finally:
        await _close(communicator)
    assert sorted(_calls("disconnected")) == sorted([old, new])


async def test_a_second_mount_frame_ends_the_replaced_views_live_mount():
    communicator = await _ws_connect()
    try:
        await _ws_mount(communicator, Page)
        old = _one(Page)
        await _ws_mount(communicator, Dest)
        new = _one(Dest)
        assert _calls("disconnected") == [old]
        assert EVENTS.index(("disconnected", old)) < EVENTS.index(("connected", new))
    finally:
        await _close(communicator)
    assert sorted(_calls("disconnected")) == sorted([old, new])


async def test_views_mounted_beside_the_page_view_get_both_hooks_each():
    communicator = await _ws_connect()
    try:
        await _ws_mount(communicator, Page)
        await _ws_mount(communicator, Lazy, target_id="lazy-1")
        await communicator.send_json_to(
            {
                "type": "mount_batch",
                "views": [
                    {"view": MOD + ".Lazy2", "url": "/page/", "target_id": "lazy-2"},
                ],
            }
        )
        await _ws_until(communicator, "mount_batch")
        page, lazy, lazy2 = _one(Page), _one(Lazy), _one(Lazy2)
        assert sorted(_calls("connected")) == sorted([page, lazy, lazy2])
        assert _calls("disconnected") == []

        # Unmounting one view ends only its live mount.
        await communicator.send_json_to({"type": "unmount", "target_id": "lazy-1"})
        await _until(lambda: _calls("disconnected"), "the unmounted view's disconnected()")
        assert _calls("disconnected") == [lazy]
    finally:
        await _close(communicator)
    assert sorted(_calls("disconnected")) == sorted([page, lazy, lazy2])
    assert len(_calls("disconnected")) == 3


async def test_an_embedded_child_gets_neither_hook_its_parent_gets_both():
    communicator = await _ws_connect()
    try:
        frame = await _ws_mount(communicator, Parent)
        assert frame["type"] == "mount", frame
        parent, kid = _one(Parent), _one(Kid)
        assert _calls("connected") == [parent]
    finally:
        await _close(communicator)
    assert _calls("disconnected") == [parent]
    assert kid not in _calls("connected") + _calls("disconnected")


async def test_a_disconnected_that_raises_is_logged_and_the_teardown_goes_on(caplog):
    communicator = await _ws_connect()
    await _ws_mount(communicator, FailsToDisconnect)
    await _ws_mount(communicator, Lazy, target_id="lazy-1")
    bad, good = _one(FailsToDisconnect), _one(Lazy)

    with caplog.at_level(logging.WARNING, logger="djust._child_lifecycle"):
        await communicator.disconnect()

    # Both hooks ran, and the views were still released afterwards.
    assert sorted(_calls("disconnected")) == sorted([bad, good])
    assert "Error in disconnected()" in caplog.text or "protected" in caplog.text.lower()
    assert VIEWS[bad]._djust_waiters_closed is True
    assert VIEWS[good]._djust_waiters_closed is True
    assert CONSUMERS[-1].view_instance is None


async def test_disconnected_runs_under_the_tenant_the_view_mounted_with():
    communicator = await _ws_connect()
    await _ws_mount(communicator, Tenanted)
    tag = _one(Tenanted)
    await communicator.disconnect()
    tenant = SEEN[("disconnected", tag)]["tenant"]
    assert tenant is VIEWS[tag]._tenant
    assert tenant.id == "tenant-3007"
    # And nothing stays bound to the consumer afterwards.
    assert get_current_tenant() is None


async def test_a_revoked_view_gets_disconnected_before_its_release():
    from djust.websocket import LiveViewConsumer

    communicator = await _ws_connect()
    await _ws_mount(communicator, Page)
    tag = _one(Page)
    view = VIEWS[tag]
    try:
        await LiveViewConsumer._release_revoked_view(view)
        assert _calls("disconnected") == [tag]
        assert view._djust_waiters_closed is True
    finally:
        await _close(communicator)
    assert _calls("disconnected") == [tag], "the disconnect ran it a second time"


async def test_deny_explicit_turn_gets_the_view_disconnected():
    communicator = await _ws_connect()
    try:
        await _ws_mount(communicator, Page)
        tag = _one(Page)
        runtime = CONSUMERS[-1]._runtime
        assert runtime.view_instance is VIEWS[tag]
        await runtime.deny_explicit_turn()
        assert _calls("disconnected") == [tag]
    finally:
        await _close(communicator)
    assert _calls("disconnected") == [tag]


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


async def _sse_open(cls, url="/page/"):
    key = await sync_to_async(_fresh_key)()
    sid = str(uuid.uuid4())
    request = await sync_to_async(_request)(
        "GET", f"/djust/sse/{sid}/", {"view": MOD + "." + cls.__name__, "_djust_url": url}, key
    )
    response = await DjustSSEStreamView().get(request, session_id=sid)
    assert response.status_code == 200
    stream = response._iterator
    assert "sse_connect" in await stream.__anext__()
    return _sse_sessions[sid], key, stream


async def _post(session, key, body):
    request = await sync_to_async(_request)(
        "POST", f"/djust/sse/{session.session_id}/message/", body, key
    )
    response = await DjustSSEMessageView().post(request, session_id=session.session_id)
    assert response.status_code == 200
    return response


async def test_sse_runs_connected_at_mount_and_disconnected_when_the_stream_closes():
    session, key, stream = await _sse_open(Page)
    tag = _one(Page)
    assert _order(tag) == ["mount", "handle_params", "connected"]
    assert SEEN[("connected", tag)]["websocket_session_id"] == session.session_id
    assert SEEN[("connected", tag)]["on_loop"] is False

    await stream.aclose()  # the client went away

    await _until(lambda: _calls("disconnected"), "the disconnected() of the closed stream")
    assert _calls("disconnected") == [tag]
    assert SEEN[("disconnected", tag)]["on_loop"] is False
    assert SEEN[("disconnected", tag)]["users"] == 0


async def test_sse_navigation_ends_the_old_views_live_mount():
    session, key, stream = await _sse_open(Page)
    old = _one(Page)
    try:
        await _post(session, key, {"type": "live_redirect_mount", "url": "/dest/", "params": {}})
        assert type(session.view_instance) is Dest
        new = _one(Dest)
        assert _calls("disconnected") == [old]
        assert EVENTS.index(("disconnected", old)) < EVENTS.index(("connected", new))
    finally:
        await stream.aclose()
    await _until(lambda: len(_calls("disconnected")) == 2, "the new view's disconnected()")


async def test_sse_shutdown_off_the_loop_runs_disconnected_inline_once():
    session, key, stream = await _sse_open(Page)
    tag = _one(Page)
    ran_on = []

    def close_from_a_plain_thread():
        session.shutdown()  # no running loop on this thread
        ran_on.append(threading.current_thread())
        session.shutdown()  # a second close does not run it again

    thread = threading.Thread(target=close_from_a_plain_thread)
    thread.start()
    await asyncio.get_running_loop().run_in_executor(None, thread.join)
    assert _calls("disconnected") == [tag]
    assert SEEN[("disconnected", tag)]["on_loop"] is False
    await stream.aclose()


# --------------------------------------------------------------------------- #
# HTTP: no connection, no hooks
# --------------------------------------------------------------------------- #


def test_the_http_render_and_the_http_post_fallback_call_neither_hook():
    request = RequestFactory().get("/page/")
    request.user, request.session, request.tenant = (
        AnonymousUser(),
        SessionStore(),
        None,
    )
    response = Page.as_view()(request)
    assert response.status_code == 200
    assert _calls("mount") != [] and _calls("connected") == []

    tag = _calls("mount")[-1]
    state_request = RequestFactory().post(
        "/page/",
        data=json.dumps({}),
        content_type="application/json",
        HTTP_X_DJUST_EVENT="ping",
    )
    state_request.user, state_request.session, state_request.tenant = (
        AnonymousUser(),
        request.session,
        None,
    )
    Page.as_view()(state_request)
    assert "connected" not in {kind for (kind, _t) in EVENTS}
    assert "disconnected" not in {kind for (kind, _t) in EVENTS}
    assert tag in VIEWS


# --------------------------------------------------------------------------- #
# Every teardown of a root view runs the hook
# --------------------------------------------------------------------------- #

_PRODUCTION = pathlib.Path(__file__).resolve().parents[1]
_HOOK_CALLS = {"fire_view_disconnected", "_run_disconnected"}


def _called_names(node):
    names = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            func = child.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
    return names


@pytest.mark.parametrize("module", ["websocket.py", "sse.py", "runtime.py"])
def test_every_function_that_releases_a_root_view_runs_its_disconnected_hook(module):
    """A new teardown path that calls ``release_root_view`` without the hook
    would skip ``disconnected()`` for the views it drops (#3007)."""
    tree = ast.parse((_PRODUCTION / module).read_text(encoding="utf-8"))
    releasing = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names = _called_names(node)
            if "release_root_view" in names:
                releasing.append(node.name)
                assert names & _HOOK_CALLS, (
                    f"{module}:{node.name} releases a root view without running "
                    "its disconnected() hook"
                )
    assert releasing, f"{module} no longer releases any root view; update this test"
