"""#3003: ``@rate_limit(on_exceed="drop")`` drops the extra event without
counting toward the connection's 4429 abuse disconnect.

The default (``on_exceed="disconnect"``) is unchanged: rejections count, and
``max_warnings`` of them close the socket. The connection's global
per-message limiter still closes a flood whichever mode a handler uses.
"""

from __future__ import annotations

import json
import sys

import pytest
from django.test import override_settings

from djust import LiveView
from djust.config import config
from djust.decorators import event_handler, rate_limit
from djust.rate_limit import reset_handler_buckets


class _EmoteView(LiveView):
    template = '<div dj-root dj-view="{}" dj-id="0">n={{{{ n }}}}</div>'.format(
        "djust.tests.test_rate_limit_on_exceed_drop_3003._EmoteView"
    )

    def mount(self, request, **kwargs):
        self.n = 0

    @event_handler()
    @rate_limit(rate=0.01, burst=1, on_exceed="drop")
    def emote(self, **kwargs):
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


async def _send(comm, event):
    """Send one event and return the frames it produced, up to a close."""
    await comm.send_json_to({"type": "event", "event": event, "params": {}})
    frames = []
    while True:
        try:
            out = await comm.receive_output(timeout=1)
        except Exception:  # noqa: BLE001 - nothing more for this event
            return frames
        if out["type"] == "websocket.close":
            frames.append({"type": "close", "code": out.get("code")})
            return frames
        frame = json.loads(out["text"])
        frames.append(frame)
        if frame.get("type") in ("patch", "html_update", "noop", "error", "rate_limit_exceeded"):
            return frames


def _types(frames):
    return [f.get("type") for f in frames]


@pytest.mark.django_db
@pytest.mark.asyncio
@override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__])
async def test_drop_mode_rejections_never_close_the_socket():
    comm = await _connect()
    try:
        assert "error" not in _types(await _send(comm, "emote"))  # the one token
        # Twice the default max_warnings (3) of rejections.
        for _ in range(6):
            frames = await _send(comm, "emote")
            assert "close" not in _types(frames), frames
            assert any(
                f.get("type") == "error" and "Rate limit exceeded" in f.get("error", "")
                for f in frames
            ), frames
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
