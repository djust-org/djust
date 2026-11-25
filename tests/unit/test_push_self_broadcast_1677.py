"""#1677 — a session must skip its OWN push_to_view self-broadcast.

When a handler on session A calls `push_to_view` for its own view, A already
received the new state via its direct event response. Re-rendering A for the
redundant self-broadcast bumps the single client-side VDOM version counter;
under rapid event bursts those versions arrive non-sequentially at the client
→ a full-HTML `request_html` recovery storm + intermittent WS reconnect.

Fix: `push_to_view` tags the broadcast with the originating channel (a
ContextVar set by the consumer during event handling), and `server_push` skips
the broadcast when `sender_channel == self.channel_name`. Broadcasts to OTHER
sessions, and external pushes (Celery/cross-view, `sender_channel is None`), are
unaffected.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from djust import LiveView, state
from djust.push import origin_channel, push_to_view


class PushedView(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root>{{ count }}</div>"
    count = state(0)


@pytest.fixture(autouse=True)
def _allow_this_module(settings):
    """The consumer mounts this module's view by dotted path."""
    settings.LIVEVIEW_ALLOWED_MODULES = [__name__]


async def _make_consumer(channel_name="session.A.channel"):
    """A consumer that really mounted ``PushedView``, with its render and sends pinned."""
    from djust.tests._live_session import amount_on_consumer
    from djust.websocket import LiveViewConsumer

    consumer = LiveViewConsumer()
    consumer.channel_name = channel_name
    consumer.use_binary = False
    consumer.send = AsyncMock()
    view = await amount_on_consumer(consumer, f"{__name__}.PushedView")
    view._sync_state_to_rust = MagicMock()
    view.render_with_diff = MagicMock(return_value=("<div>ok</div>", '[{"op":"replace"}]', 2))
    consumer._send_update = AsyncMock()
    return consumer


# --- push_to_view tags the originating channel --------------------------------


@patch("djust.push.get_channel_layer")
def test_push_to_view_tags_origin_channel(mock_get_layer):
    layer = MagicMock()
    layer.group_send = AsyncMock()
    mock_get_layer.return_value = layer

    token = origin_channel.set("session.A.channel")
    try:
        push_to_view("app.views.V", state={"x": 1})
    finally:
        origin_channel.reset(token)

    msg = layer.group_send.call_args[0][1]
    assert msg["sender_channel"] == "session.A.channel"


@patch("djust.push.get_channel_layer")
def test_push_to_view_sender_channel_none_outside_handler(mock_get_layer):
    layer = MagicMock()
    layer.group_send = AsyncMock()
    mock_get_layer.return_value = layer

    push_to_view("app.views.V", state={"x": 1})  # no origin set

    assert layer.group_send.call_args[0][1]["sender_channel"] is None


# --- server_push skips own self-broadcast -------------------------------------


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_server_push_skips_own_self_broadcast():
    """sender_channel == channel_name → skipped (no re-render/send)."""
    consumer = await _make_consumer("session.A.channel")
    event = {"state": {"count": 5}, "sender_channel": "session.A.channel"}

    await consumer.server_push(event)

    consumer._send_update.assert_not_awaited()
    consumer.view_instance._sync_state_to_rust.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_server_push_applies_other_sessions_broadcast():
    """A broadcast from ANOTHER session is applied (not skipped)."""
    consumer = await _make_consumer("session.A.channel")
    event = {"state": {"count": 7}, "sender_channel": "session.B.channel"}

    await consumer.server_push(event)

    consumer._send_update.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_server_push_applies_external_push_no_sender():
    """An external push (Celery/cross-view; sender_channel None) is applied."""
    consumer = await _make_consumer("session.A.channel")
    event = {"state": {"count": 9}, "sender_channel": None}

    await consumer.server_push(event)

    consumer._send_update.assert_awaited_once()
