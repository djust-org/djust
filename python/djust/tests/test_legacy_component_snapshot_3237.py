"""#3237: a legacy opt-in view refreshes its back-navigation snapshot on component events.

#3098 made the view-event route refresh a legacy ``enable_state_snapshot``
view's signed snapshot on every state-changing event, and #3231 (fixed in
#3234) gave explicit views the refreshed token on component-event frames. A
legacy view's component-event frames still carried nothing, so after a
component event the client kept the token from before it, and Back restored
the stale state.

Harness: a real ``WebsocketCommunicator`` against ``LiveViewConsumer`` and a
real DB session. The flow is the user's: a component event, navigate away with
``live_redirect``, then Back, which echoes the latest token the client holds
(``storeSignedSnapshot`` keeps the last one a primary-view ``source="event"``
frame carried). The server-saved state is dropped before Back, so the signed
snapshot is the path taken, as in the #3098 test.
"""

from __future__ import annotations

import pytest
from asgiref.sync import sync_to_async
from django.test import override_settings
from django.urls import path

from djust import LiveView
from djust.components.descriptors.base import LiveComponent as DescriptorComponent
from djust.components.descriptors.base import TypedState
from djust.decorators import event_handler

pytest.importorskip("channels")

pytestmark = [pytest.mark.asyncio, pytest.mark.django_db(transaction=True)]

_MOD = __name__
VIEW = f"{_MOD}.Stepper3237Page"
OTHER = f"{_MOD}.Elsewhere3237"
TOKEN = "state_snapshot_signed"


class Pinger(DescriptorComponent):
    """State-less: its event writes the parent's state through ``send_parent``,
    answered with a full ``html_update``."""

    template = "<b>ping</b>"

    def mount(self, **kwargs):
        pass

    def get_context_data(self):
        return {}

    @event_handler()
    def step(self, **kwargs):
        self.send_parent("stepped")


class Ticker(DescriptorComponent):
    """Bound (declares ``State``): ``tick`` changes only its own state and takes
    the scoped ``patch`` route; ``idle`` changes nothing and is answered ``noop``."""

    class State(TypedState):
        n: int = 0

    template = "<b>{{ n }}</b>"

    @event_handler()
    def tick(self, **kwargs):
        self.state.n += 1

    @event_handler()
    def idle(self, **kwargs):
        pass


class Stepper3237Page(LiveView):
    exposure_policy = "legacy"
    enable_state_snapshot = True
    template = (
        f'<div dj-root dj-view="{VIEW}" dj-id="0">'
        "{{ pinger }}{{ ticker }}<span>count={{ count }}</span></div>"
    )
    pinger = Pinger()
    ticker = Ticker()

    def mount(self, request, **kwargs):
        self.count = 0

    def get_context_data(self, **kwargs):
        return super().get_context_data(count=self.count, **kwargs)

    def handle_component_event(self, component_id, event, data):
        self.count += 1


class Elsewhere3237(LiveView):
    exposure_policy = "legacy"
    template = f'<div dj-root dj-view="{OTHER}" dj-id="0">elsewhere</div>'


urlpatterns = [
    path("c3237-back/", Stepper3237Page.as_view()),
    path("c3237-other/", Elsewhere3237.as_view()),
]
_SETTINGS = override_settings(LIVEVIEW_ALLOWED_MODULES=[_MOD], ROOT_URLCONF=_MOD)


class _ScopeSession:
    def __init__(self, key):
        self.session_key = key


async def _connect():
    from channels.testing import WebsocketCommunicator
    from django.contrib.sessions.backends.db import SessionStore

    from djust.websocket import LiveViewConsumer

    def _create():
        s = SessionStore()
        s.create()
        return s.session_key

    session_key = await sync_to_async(_create)()
    communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    communicator.scope["session"] = _ScopeSession(session_key)
    connected, _ = await communicator.connect()
    assert connected
    await communicator.receive_json_from(timeout=2)  # connect ack
    return communicator, session_key


async def _frames(communicator):
    """Every frame that arrives until the socket goes quiet."""
    frames = []
    while not await communicator.receive_nothing(timeout=0.3):
        frames.append(await communicator.receive_json_from(timeout=2))
    return frames


def _mount_frame(frames):
    [frame] = [f for f in frames if f.get("type") == "mount"]
    return frame


def _held_token(frames, token=None):
    """The token the client holds after ``frames``: ``storeSignedSnapshot``
    keeps the last one a primary-view event (or mount) frame carried."""
    for frame in frames:
        eligible = frame.get("type") == "mount" or (
            frame.get("source") == "event"
            and frame.get("view") == VIEW
            and frame.get("type") in ("patch", "html_update", "noop")
        )
        if eligible and TOKEN in frame:
            token = frame[TOKEN]
    return token


def _drop_saved_state(session_key, url):
    from django.contrib.sessions.backends.db import SessionStore

    s = SessionStore(session_key=session_key)
    s.pop(f"liveview_{url}", None)
    s.save()


async def _mounted(communicator, url):
    await communicator.send_json_to({"type": "mount", "view": VIEW, "url": url})
    frames = await _frames(communicator)
    mount = _mount_frame(frames)
    assert isinstance(mount.get(TOKEN), str)
    return frames


async def _component_event(communicator, component_id, name):
    await communicator.send_json_to(
        {"type": "event", "event": name, "params": {"component_id": component_id}}
    )
    return await _frames(communicator)


def _component_ids(frames):
    """The component ids the page rendered, as the client reads them."""
    import re

    html = _mount_frame(frames)["html"]
    return re.findall(r'data-component-id="([^"]+)"', html)


async def test_back_after_a_component_event_restores_the_events_change():
    url = "/c3237-back/"
    with _SETTINGS:
        communicator, session_key = await _connect()
        try:
            mounted = await _mounted(communicator, url)
            token = _held_token(mounted)
            pinger_id = _component_ids(mounted)[0]

            frames = await _component_event(communicator, pinger_id, "step")
            assert any("count=1" in f.get("html", "") for f in frames), frames
            token = _held_token(frames, token)

            # Navigate away, then Back with the token the client holds.
            await communicator.send_json_to(
                {"type": "live_redirect_mount", "view": OTHER, "url": "/c3237-other/"}
            )
            await _frames(communicator)
            await sync_to_async(_drop_saved_state)(session_key, url)
            await communicator.send_json_to(
                {
                    "type": "live_redirect_mount",
                    "view": VIEW,
                    "url": url,
                    "state_snapshot": {"view_slug": VIEW, "state_json": token},
                }
            )
            restored = _mount_frame(await _frames(communicator))
            assert "count=1" in restored["html"], restored["html"]
        finally:
            await communicator.disconnect()


@pytest.mark.parametrize(
    "component,event_name,frame_type,carries",
    [
        (0, "step", "html_update", True),  # the parent changed: full HTML
        (1, "tick", "patch", True),  # the scoped component route
        (1, "idle", "noop", False),  # nothing changed: the held token is current
    ],
)
async def test_component_frames_carry_the_refreshed_snapshot_like_the_view_route(
    component, event_name, frame_type, carries
):
    """The view route's shape (#3098): a state-changing frame carries the
    refreshed token; a ``noop`` changed nothing and carries none."""
    from djust.security import unsign_snapshot

    with _SETTINGS:
        communicator, session_key = await _connect()
        try:
            mounted = await _mounted(communicator, "/c3237-back/")
            component_id = _component_ids(mounted)[component]
            frames = await _component_event(communicator, component_id, event_name)
            [frame] = [f for f in frames if f.get("type") in ("html_update", "patch", "noop")]
            assert frame["type"] == frame_type, frame
            if not carries:
                assert TOKEN not in frame, frame
                return
            assert frame.get("view") == VIEW and frame.get("source") == "event", frame
            inner = unsign_snapshot(frame[TOKEN], VIEW, session_key)
            assert inner is not None, "the token must verify for this view and session"
            if event_name == "step":
                assert '"count":1' in inner, inner
        finally:
            await communicator.disconnect()
