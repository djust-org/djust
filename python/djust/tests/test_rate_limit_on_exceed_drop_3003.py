"""#3003: ``@rate_limit(on_exceed="drop")`` drops the extra event without
counting toward the connection's 4429 abuse disconnect.

The default (``on_exceed="disconnect"``) is unchanged: rejections count, and
``max_warnings`` of them close the socket. The connection's global
per-message limiter still closes a flood whichever mode a handler uses.
"""

from __future__ import annotations

import sys

import pytest
from django.test import override_settings

from djust import LiveView
from djust.config import config
from djust.decorators import event_handler, rate_limit
from djust.rate_limit import reset_handler_buckets
from djust.tests._ws_frames import receive_until

#: Handler runs, by name: a dropped event must not reach the handler.
RAN: list = []


class _EmoteView(LiveView):
    template = '<div dj-root dj-view="{}" dj-id="0">n={{{{ n }}}}</div>'.format(
        "djust.tests.test_rate_limit_on_exceed_drop_3003._EmoteView"
    )

    def mount(self, request, **kwargs):
        self.n = 0

    @event_handler()
    @rate_limit(rate=0.01, burst=1, on_exceed="drop")
    def emote(self, **kwargs):
        RAN.append("emote")
        self.n += 1

    @event_handler()
    @rate_limit(rate=0.01, burst=1)
    def send_code(self, **kwargs):
        self.n += 1

    @event_handler()
    def bump(self, **kwargs):
        self.n += 1


setattr(sys.modules[__name__], "_EmoteView", _EmoteView)
_VIEW = f"{__name__}._EmoteView"


@pytest.fixture(autouse=True)
def _fresh_buckets():
    reset_handler_buckets()
    config.reset()
    RAN.clear()
    yield
    reset_handler_buckets()
    config.reset()


async def _connect():
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    comm = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    connected, _ = await comm.connect()
    assert connected
    try:
        await comm.receive_json_from(timeout=2)  # connect ack
    except Exception:  # noqa: BLE001 - no ack frame on this transport
        pass
    await comm.send_json_to({"type": "mount", "view": _VIEW})
    mount = await comm.receive_json_from(timeout=3)
    assert mount.get("type") == "mount", mount
    return comm


_ANSWERS = {"patch", "html_update", "noop", "error", "rate_limit_exceeded", "websocket.close"}


async def _send(comm, event):
    """Send one event; return its frames up to the one that answers it.

    Event-driven (#3156's helper): every event is answered by a render, a
    noop, an error, a rate-limit notice or a close, so this waits for that
    frame instead of for a quiet window.
    """
    await comm.send_json_to({"type": "event", "event": event, "params": {}})
    frames = await receive_until(
        comm,
        lambda got: any(f.get("type") in _ANSWERS for f in got),
        what="the answer to %r" % event,
        allow_close=True,
    )
    return [
        {"type": "close", "code": f.get("code")} if f.get("type") == "websocket.close" else f
        for f in frames
    ]


def _types(frames):
    return [f.get("type") for f in frames]


@pytest.mark.django_db
@pytest.mark.asyncio
@override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__])
async def test_drop_mode_rejections_never_close_the_socket():
    comm = await _connect()
    try:
        assert "error" not in _types(await _send(comm, "emote"))  # the one token
        assert RAN == ["emote"]
        # Twice the default max_warnings (3) of rejections.
        for _ in range(6):
            frames = await _send(comm, "emote")
            assert "close" not in _types(frames), frames
            assert any(
                f.get("type") == "error" and "Rate limit exceeded" in f.get("error", "")
                for f in frames
            ), frames
        # Dropped means dropped: the handler never ran for a rejected event.
        assert RAN == ["emote"]
        # Still connected: an unrelated event renders.
        assert {"patch", "html_update"} & set(_types(await _send(comm, "bump")))
    finally:
        await comm.disconnect()


@pytest.mark.django_db
@pytest.mark.asyncio
@override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__])
async def test_default_mode_rejections_still_close_with_4429():
    """Gate-off sibling: the default keeps the abuse disconnect."""
    comm = await _connect()
    try:
        await _send(comm, "send_code")
        closes = []
        for _ in range(6):
            frames = await _send(comm, "send_code")
            closes = [f for f in frames if f["type"] == "close"]
            if closes:
                break
        assert closes and closes[0]["code"] == 4429, closes
    finally:
        await comm.disconnect()


@pytest.mark.django_db
@pytest.mark.asyncio
@override_settings(
    LIVEVIEW_ALLOWED_MODULES=[__name__],
    LIVEVIEW_CONFIG={"rate_limit": {"rate": 0.01, "burst": 3, "max_warnings": 2}},
)
async def test_the_global_flood_limiter_still_closes_a_drop_mode_flood():
    config.reset()  # read the overridden LIVEVIEW_CONFIG
    assert config.get("rate_limit")["max_warnings"] == 2
    comm = await _connect()
    try:
        closes = []
        for _ in range(12):
            frames = await _send(comm, "emote")
            closes = [f for f in frames if f["type"] == "close"]
            if closes:
                break
        assert closes and closes[0]["code"] == 4429, closes
    finally:
        await comm.disconnect()


def test_an_unknown_on_exceed_value_is_refused():
    with pytest.raises(ValueError, match="on_exceed"):
        rate_limit(rate=1, on_exceed="ignore")  # type: ignore[arg-type]


def test_default_metadata_is_unchanged_and_drop_is_recorded():
    from djust.rate_limit import get_rate_limit_settings

    assert get_rate_limit_settings(_EmoteView.send_code) == {"rate": 0.01, "burst": 1}
    assert get_rate_limit_settings(_EmoteView.emote) == {
        "rate": 0.01,
        "burst": 1,
        "on_exceed": "drop",
    }
