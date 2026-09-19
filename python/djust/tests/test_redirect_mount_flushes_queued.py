"""A redirect mount must deliver what the new view's ``mount()`` queued.

An HTTP load carries the title and meta tags in the rendered document, so
anything queued during ``mount()`` never needed sending and nothing on the
mount path drained the queue. A ``live_redirect`` mount has no document
render: ``handle_live_redirect_mount`` called ``handle_mount`` and returned,
so a view setting ``self.page_title`` in ``mount()`` left the browser tab
naming the page the reader had navigated away from.

The risk in flushing there is double delivery, so these also pin that a second
flush in the same turn sends nothing.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from django.test import override_settings

from djust.websocket import LiveViewConsumer

URLCONF = "tests.redirect_mount_test_urls"
TARGET = "tests.redirect_mount_test_urls.RedirectTargetView"


class _QueueingView:
    """Stands in for a view whose ``mount()`` queued client-side commands."""

    def __init__(self):
        self._pending = [{"action": "title", "value": "Target — Components"}]

    def _drain_page_metadata(self):
        drained, self._pending = self._pending, []
        return drained


def _consumer_with_queued_view():
    consumer = LiveViewConsumer()
    consumer.scope = {"session": None, "user": None}
    consumer.view_instance = None
    consumer._sticky_auto_reattached = set()
    consumer._sticky_preserved = {}
    sent = []
    consumer.send_json = AsyncMock(side_effect=lambda payload: sent.append(payload))
    return consumer, sent


@pytest.mark.asyncio
@override_settings(ROOT_URLCONF=URLCONF)
async def test_a_title_queued_during_mount_reaches_the_client():
    consumer, sent = _consumer_with_queued_view()

    async def _fake_handle_mount(data, **kwargs):
        # The real handle_mount builds and attaches the new view; stand in for
        # the part that matters here, a mounted view with a queued command.
        consumer.view_instance = _QueueingView()

    with patch.object(consumer, "handle_mount", new=AsyncMock(side_effect=_fake_handle_mount)):
        await consumer.handle_live_redirect_mount(
            {"view": TARGET, "url": "/redirect-target/", "params": {}}
        )

    titles = [f for f in sent if f.get("type") == "page_metadata" and f.get("action") == "title"]
    assert titles, f"no page_metadata title frame was sent; got {[f.get('type') for f in sent]}"
    assert titles[0]["value"] == "Target — Components"


@pytest.mark.asyncio
@override_settings(ROOT_URLCONF=URLCONF)
async def test_the_queue_is_not_delivered_twice():
    """Each flush drains and clears, so a second flush in the same turn is a
    no-op. If it were not, every redirect mount would double-send whatever the
    event path had already delivered."""
    consumer, sent = _consumer_with_queued_view()

    async def _fake_handle_mount(data, **kwargs):
        consumer.view_instance = _QueueingView()

    with patch.object(consumer, "handle_mount", new=AsyncMock(side_effect=_fake_handle_mount)):
        await consumer.handle_live_redirect_mount(
            {"view": TARGET, "url": "/redirect-target/", "params": {}}
        )
        before = len(sent)
        await consumer._flush_all_pending()

    assert len(sent) == before, f"a second flush sent {len(sent) - before} extra frame(s)"


@pytest.mark.asyncio
@override_settings(ROOT_URLCONF=URLCONF)
async def test_a_view_that_queued_nothing_sends_nothing():
    consumer, sent = _consumer_with_queued_view()

    class _Quiet:
        def _drain_page_metadata(self):
            return []

    async def _fake_handle_mount(data, **kwargs):
        consumer.view_instance = _Quiet()

    with patch.object(consumer, "handle_mount", new=AsyncMock(side_effect=_fake_handle_mount)):
        await consumer.handle_live_redirect_mount(
            {"view": TARGET, "url": "/redirect-target/", "params": {}}
        )

    assert [f for f in sent if f.get("type") == "page_metadata"] == []
