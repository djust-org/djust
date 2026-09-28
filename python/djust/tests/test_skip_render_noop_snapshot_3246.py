"""#3246: a handler that changes state and sets ``_skip_render`` refreshes the client token.

A ``_skip_render`` turn answers ``noop``. On a legacy ``enable_state_snapshot``
view that noop carried no refreshed ``state_snapshot_signed`` token on either
route, so the client kept the token from before the change, and Back (or a
reconnect) restored the older state whenever the token was the source.

The noop now carries the refreshed token when the turn changed the view's
state AND the session save succeeded. A noop that changed nothing, or whose
save failed or was deferred, carries none: the held token is then either
current, or the only copy that matches storage.

An explicit view already attached ``_explicit_event_snapshot`` to every noop
after a committed turn, on both routes; its cases pin that, and that a
withheld commit sends no token.

Legacy harness: a real ``WebsocketCommunicator`` against ``LiveViewConsumer``
and a real DB session, as ``test_legacy_component_snapshot_3237``. For a
legacy view the session save wins over the token on Back (``dispatch_mount``
restores from it first), so the end-to-end cases run twice: with the save in
place, as in production, and with the page's session copy gone, where the
token is the only source.
"""

from __future__ import annotations

import time

import pytest
from asgiref.sync import sync_to_async
from django.test import override_settings
from django.urls import path

from djust import LiveView
from djust.components.descriptors.base import LiveComponent as DescriptorComponent
from djust.decorators import event_handler, state

from ._ws_frames import has_type, receive_settled

pytest.importorskip("channels")

pytestmark = [pytest.mark.asyncio, pytest.mark.django_db(transaction=True)]

_MOD = __name__
VIEW = f"{_MOD}.Quiet3246Page"
OTHER = f"{_MOD}.Elsewhere3246"
URL = "/q3246/"
TOKEN = "state_snapshot_signed"


class Pinger(DescriptorComponent):
    template = "<b>ping</b>"

    def mount(self, **kwargs):
        pass

    def get_context_data(self):
        return {}

    @event_handler()
    def step(self, **kwargs):
        self.send_parent("stepped")


class Hush(DescriptorComponent):
    template = "<b>hush</b>"

    def mount(self, **kwargs):
        pass

    def get_context_data(self):
        return {}

    @event_handler()
    def hush(self, **kwargs):
        self.send_parent("hushed")


class Quiet3246Page(LiveView):
    """Every handler asks for no render; ``quiet_bump`` and the ``stepped``
    component event change ``count``, the others change nothing."""

    exposure_policy = "legacy"
    enable_state_snapshot = True
    template = (
        f'<div dj-root dj-view="{VIEW}" dj-id="0">'
        "{{ pinger }}{{ hush }}<span>count={{ count }}</span></div>"
    )
    pinger = Pinger()
    hush = Hush()

    def mount(self, request, **kwargs):
        self.count = 0

    def get_context_data(self, **kwargs):
        return super().get_context_data(count=self.count, **kwargs)

    def handle_component_event(self, component_id, event, data):
        if event == "stepped":
            self.count += 1
        self._skip_render = True

    @event_handler()
    def quiet_bump(self, **kwargs):
        self.count += 1
        self._skip_render = True

    @event_handler()
    def quiet_nothing(self, **kwargs):
        self._skip_render = True


class Elsewhere3246(LiveView):
    exposure_policy = "legacy"
    template = f'<div dj-root dj-view="{OTHER}" dj-id="0">elsewhere</div>'


urlpatterns = [
    path("q3246/", Quiet3246Page.as_view()),
    path("q3246-other/", Elsewhere3246.as_view()),
]
_SETTINGS = override_settings(LIVEVIEW_ALLOWED_MODULES=[_MOD], ROOT_URLCONF=_MOD)


class _ScopeSession:
    def __init__(self, key):
        self.session_key = key


def _create_session():
    from django.contrib.sessions.backends.db import SessionStore

    session = SessionStore()
    session.create()
    return session.session_key


def _drop_page_copy(session_key):
    """The page's session copy is gone (evicted, expired): Back must use the token."""
    from django.contrib.sessions.backends.db import SessionStore

    session = SessionStore(session_key)
    assert session.get(f"liveview_{URL}") is not None, "no session save to drop; vacuous"
    for suffix in ("", "__private", "_components"):
        session.pop(f"liveview_{URL}{suffix}", None)
    session.save()


async def _connect(session_key=None):
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    if session_key is None:
        session_key = await sync_to_async(_create_session)()
    communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    communicator.scope["session"] = _ScopeSession(session_key)
    connected, _ = await communicator.connect()
    assert connected
    await communicator.receive_json_from(timeout=2)  # connect ack
    return communicator, session_key


#: The frames that answer an event: its reply, or a refusal.
EVENT_REPLIES = ("noop", "patch", "html_update", "error")


async def _frames(communicator, *expected):
    """The frames up to the first of type ``expected``, then any that follow.

    Waits for the expected frame instead of stopping at a quiet window, which
    returned ``[]`` when a loaded runner was slow to send the first one (#3256).
    """
    return await receive_settled(communicator, has_type(*expected), what=f"a {expected} frame")


def _mount_frame(frames):
    [frame] = [f for f in frames if f.get("type") == "mount"]
    return frame


def _held_token(frames, token=None):
    """The token the client holds after ``frames`` (``storeSignedSnapshot``)."""
    for frame in frames:
        eligible = frame.get("type") == "mount" or (
            frame.get("source") == "event"
            and frame.get("view") == VIEW
            and frame.get("type") in ("patch", "html_update", "noop")
        )
        if eligible and TOKEN in frame:
            token = frame[TOKEN]
    return token


async def _mounted(communicator, state_snapshot=None):
    message = {"type": "mount", "view": VIEW, "url": URL}
    if state_snapshot is not None:
        message["state_snapshot"] = {"view_slug": VIEW, "state_json": state_snapshot}
    await communicator.send_json_to(message)
    frames = await _frames(communicator, "mount", "error")
    assert isinstance(_mount_frame(frames).get(TOKEN), str)
    return frames


def _component_ids(frames):
    import re

    return re.findall(r'data-component-id="([^"]+)"', _mount_frame(frames)["html"])


async def _send(communicator, name, component_id=None):
    params = {"component_id": component_id} if component_id else {}
    await communicator.send_json_to({"type": "event", "event": name, "params": params})
    return await _frames(communicator, *EVENT_REPLIES)


def _noop(frames):
    [noop] = [f for f in frames if f.get("type") in ("noop", "patch", "html_update")]
    assert noop["type"] == "noop", noop
    return noop


async def _event(communicator, mounted, route, changes):
    """Fire the skip-render event for ``route`` ("view"/"component")."""
    if route == "view":
        return await _send(communicator, "quiet_bump" if changes else "quiet_nothing")
    pinger_id, hush_id = _component_ids(mounted)
    if changes:
        return await _send(communicator, "step", pinger_id)
    return await _send(communicator, "hush", hush_id)


# --------------------------------------------------------------------------- #
# Legacy opt-in view, both routes.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("route", ["view", "component"])
async def test_a_legacy_skip_render_noop_that_changed_state_carries_the_token(route):
    from djust.security import unsign_snapshot

    with _SETTINGS:
        communicator, session_key = await _connect()
        try:
            mounted = await _mounted(communicator)
            noop = _noop(await _event(communicator, mounted, route, changes=True))
            assert noop.get("view") == VIEW and noop.get("source") == "event", noop
            inner = unsign_snapshot(noop.get(TOKEN) or "", VIEW, session_key)
            assert inner is not None, f"the noop must carry a verifying token: {noop}"
            assert '"count":1' in inner, inner
        finally:
            await communicator.disconnect()


@pytest.mark.parametrize("route", ["view", "component"])
async def test_a_legacy_skip_render_noop_that_changed_nothing_carries_no_token(route):
    with _SETTINGS:
        communicator, _ = await _connect()
        try:
            mounted = await _mounted(communicator)
            noop = _noop(await _event(communicator, mounted, route, changes=False))
            assert TOKEN not in noop, noop
        finally:
            await communicator.disconnect()


async def _back(communicator, token):
    await communicator.send_json_to(
        {"type": "live_redirect_mount", "view": OTHER, "url": "/q3246-other/"}
    )
    await _frames(communicator, "mount", "error")
    await communicator.send_json_to(
        {
            "type": "live_redirect_mount",
            "view": VIEW,
            "url": URL,
            "state_snapshot": {"view_slug": VIEW, "state_json": token},
        }
    )
    return _mount_frame(await _frames(communicator, "mount", "error"))


@pytest.mark.parametrize("session_copy", ["kept", "gone"])
@pytest.mark.parametrize("route", ["view", "component"])
async def test_back_after_a_legacy_skip_render_event_restores_its_change(route, session_copy):
    with _SETTINGS:
        communicator, session_key = await _connect()
        try:
            mounted = await _mounted(communicator)
            token = _held_token(mounted)
            token = _held_token(await _event(communicator, mounted, route, changes=True), token)
            if session_copy == "gone":
                await sync_to_async(_drop_page_copy)(session_key)
            restored = await _back(communicator, token)
            assert "count=1" in restored["html"], restored["html"]
        finally:
            await communicator.disconnect()


@pytest.mark.parametrize("route", ["view", "component"])
async def test_a_reconnect_after_a_legacy_skip_render_event_restores_its_change(route):
    """A new socket for the same session, echoing the token the old one held."""
    with _SETTINGS:
        communicator, session_key = await _connect()
        try:
            mounted = await _mounted(communicator)
            token = _held_token(mounted)
            token = _held_token(await _event(communicator, mounted, route, changes=True), token)
        finally:
            await communicator.disconnect()
        await sync_to_async(_drop_page_copy)(session_key)
        communicator, _ = await _connect(session_key)
        try:
            frames = await _mounted(communicator, state_snapshot=token)
            assert "count=1" in _mount_frame(frames)["html"], _mount_frame(frames)
        finally:
            await communicator.disconnect()


@pytest.mark.parametrize("outcome", ["failed", "deferred"])
@pytest.mark.parametrize("route", ["view", "component"])
async def test_a_legacy_skip_render_noop_whose_save_did_not_land_carries_no_token(
    route, outcome, monkeypatch
):
    """Storage does not have the change, so the held token is the only copy
    that matches it: the noop must not replace it."""
    from django.contrib.sessions.backends.db import SessionStore

    original = SessionStore.save

    def failing(self, *args, **kwargs):
        raise OSError("storage down")

    def slow(self, *args, **kwargs):
        time.sleep(0.3)
        return original(self, *args, **kwargs)

    with _SETTINGS:
        communicator, _ = await _connect()
        try:
            mounted = await _mounted(communicator)
            monkeypatch.setattr(SessionStore, "save", failing if outcome == "failed" else slow)
            if outcome == "deferred":
                monkeypatch.setattr("djust.runtime.EVENT_STATE_SAVE_TIMEOUT_S", 0.05)
            frames = await _event(communicator, mounted, route, changes=True)
            noop = _noop(frames)
            assert TOKEN not in noop, noop
            assert not [f for f in frames if f.get("type") == "error"], frames
        finally:
            await communicator.disconnect()


# --------------------------------------------------------------------------- #
# Explicit view, both routes (the ViewRuntime harness of #3231).
# --------------------------------------------------------------------------- #


class QuietExplicitPage(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root>{{ pinger }}<span>{{ count }}</span><i>{{ step_name }}</i></div>"
    count = state(0, persist="server")
    step_name = state("initial", persist="client", client=True)
    pinger = Pinger()

    def get_context_data(self, **kwargs):
        return super().get_context_data(count=self.count, step_name=self.step_name, **kwargs)

    def _step(self):
        self.count += 1
        self.step_name = "step-%d" % self.count
        self._skip_render = True

    def handle_component_event(self, component_id, event, data):
        self._step()

    @event_handler()
    def quiet_step(self, **kwargs):
        self._step()


async def _explicit_mount(request, **extra):
    from djust.runtime import ViewRuntime
    from djust.tests.test_exposure_runtime import make_request
    from djust.tests.test_runtime_state_save_tt_1894 import MockTransport

    transport = MockTransport()
    transport.build_request = lambda: request

    async def fresh_event_request(view):
        return await sync_to_async(make_request)(request.session.session_key)

    transport.explicit_event_request = fresh_event_request
    runtime = ViewRuntime(transport)
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[_MOD]):
        await runtime.dispatch_mount(
            {"type": "mount", "view": f"{_MOD}.QuietExplicitPage", "url": request.path, **extra}
        )
    assert not transport.errors, transport.errors
    return runtime, transport


def _explicit_event(view, route):
    if route == "view":
        return {"type": "event", "event": "quiet_step", "params": {}}
    return {
        "type": "event",
        "event": "step",
        "params": {"component_id": view.pinger.component_id},
    }


@pytest.mark.parametrize("route", ["view", "component"])
async def test_an_explicit_skip_render_noop_carries_the_token_and_a_reconnect_restores(route):
    from djust.tests.test_exposure_runtime import make_request

    request = await sync_to_async(make_request)()
    runtime, transport = await _explicit_mount(request)
    transport.sent.clear()

    await runtime.dispatch_event(_explicit_event(runtime.view_instance, route))
    assert not transport.errors, transport.errors
    [noop] = [f for f in transport.sent if f.get("type") in ("noop", "patch", "html_update")]
    assert noop["type"] == "noop", noop
    assert noop.get(TOKEN), noop

    fresh = await sync_to_async(make_request)(request.session.session_key)
    restored, _ = await _explicit_mount(
        fresh, state_snapshot={"view_slug": f"{_MOD}.QuietExplicitPage", "state_json": noop[TOKEN]}
    )
    view = restored.view_instance
    assert (view.count, view.step_name) == (1, "step-1")


@pytest.mark.parametrize("route", ["view", "component"])
async def test_an_explicit_skip_render_turn_whose_commit_failed_sends_no_token(route, monkeypatch):
    from djust.tests.test_exposure_runtime import make_request

    request = await sync_to_async(make_request)()
    runtime, transport = await _explicit_mount(request)
    transport.sent.clear()

    def refuse(view, request):
        raise OSError("storage down")

    monkeypatch.setattr("djust._exposure_sessions.save_server_state", refuse)
    await runtime.dispatch_event(_explicit_event(runtime.view_instance, route))
    assert transport.errors, "the failed commit must answer an error; vacuous"
    assert not [f for f in transport.sent if f.get("type") == "noop"], transport.sent
    assert not [f for f in transport.sent if f.get(TOKEN)], transport.sent
