"""Unsolicited renders must deliver HTML when the differ loses its baseline."""

from unittest.mock import AsyncMock

import pytest
from asgiref.sync import sync_to_async

from djust import LiveView
from djust.websocket import LiveViewConsumer


class BackgroundView(LiveView):
    template = "<div dj-root>Count: {{ count }}</div>"

    def mount(self, request, **kwargs):
        self.count = 0

    def handle_tick(self):
        self.count += 1

    def handle_info(self, message):
        self.count += 1


@pytest.mark.asyncio
@pytest.mark.parametrize("producer", ["tick", "push", "notify"])
async def test_lost_background_baseline_delivers_html_and_arms_recovery(producer):
    view = BackgroundView()
    view.mount(None)
    await sync_to_async(view.render_with_diff)()
    # Actual baseline loss, not _force_full_html (which only forces context sync).
    view._rust_view.reset()
    consumer = LiveViewConsumer()
    consumer.view_instance = view
    consumer.use_binary = False
    consumer._last_sent_version = 7
    consumer.send_json = AsyncMock()
    consumer._flush_all_pending = AsyncMock()
    consumer._send_update = AsyncMock(wraps=consumer._send_update)

    if producer == "tick":
        assert await consumer._tick_once() is True
    elif producer == "push":
        await consumer.server_push({"state": {"count": 1}})
    else:
        await consumer.db_notify({"channel": "counts", "payload": {}})

    consumer._send_update.assert_awaited_once()
    frame = consumer.send_json.call_args.args[0]
    assert frame["type"] == "html_update"
    assert "Count: 1" in frame["html"]
    assert frame["version"] == 8
    assert frame["source"] == ("tick" if producer == "tick" else "broadcast")
    assert consumer._recovery_version == 8
    assert "Count: 1" in consumer._recovery_html
    assert not consumer._render_lock.locked()
    consumer._flush_all_pending.assert_awaited_once()
    await consumer.handle_request_html({})
    recovered = consumer.send_json.call_args.args[0]
    assert recovered["type"] == "html_recovery"
    assert recovered["version"] == 8
    assert recovered["html"] == frame["html"]
