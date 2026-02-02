"""
Tests for the StreamingMixin — real-time partial DOM updates.
"""

import asyncio
import sys
import time
import pytest

try:
    from djust.streaming import StreamingMixin, MIN_STREAM_INTERVAL_S
    HAS_DEPS = True
except ImportError:
    HAS_DEPS = False

pytestmark = pytest.mark.skipif(not HAS_DEPS, reason="djust deps not available")


class FakeConsumer:
    def __init__(self):
        self.sent_messages = []

    async def send_json(self, data):
        self.sent_messages.append(data)

    async def _flush_push_events(self):
        pass


def _make_view():
    view = type("View", (), {})()
    view._ws_consumer = None
    view._stream_batch = {}
    view._last_stream_time = 0.0
    view._stream_flush_task = None
    for name in ("stream_to", "stream_insert", "stream_delete",
                  "_send_stream_ops", "_flush_stream_batch"):
        setattr(view, name, getattr(StreamingMixin, name).__get__(view))
    return view


def test_stream_to_sends_replace_op():
    view = _make_view()
    consumer = FakeConsumer()
    view._ws_consumer = consumer
    asyncio.run(view.stream_to("messages", target="#msg-list", html="<div>Hello</div>"))
    assert len(consumer.sent_messages) == 1
    msg = consumer.sent_messages[0]
    assert msg["type"] == "stream"
    assert msg["stream"] == "messages"
    assert msg["ops"][0] == {"op": "replace", "target": "#msg-list", "html": "<div>Hello</div>"}


def test_stream_to_default_target():
    view = _make_view()
    consumer = FakeConsumer()
    view._ws_consumer = consumer
    asyncio.run(view.stream_to("chat", html="<p>Hi</p>"))
    assert consumer.sent_messages[0]["ops"][0]["target"] == "[dj-stream='chat']"


def test_stream_insert_append():
    view = _make_view()
    consumer = FakeConsumer()
    view._ws_consumer = consumer
    asyncio.run(view.stream_insert("feed", "<li>New</li>", at="append"))
    assert consumer.sent_messages[0]["ops"][0]["op"] == "append"


def test_stream_insert_prepend():
    view = _make_view()
    consumer = FakeConsumer()
    view._ws_consumer = consumer
    asyncio.run(view.stream_insert("feed", "<li>First!</li>", at="prepend"))
    assert consumer.sent_messages[0]["ops"][0]["op"] == "prepend"


def test_stream_delete():
    view = _make_view()
    consumer = FakeConsumer()
    view._ws_consumer = consumer
    asyncio.run(view.stream_delete("messages", "#msg-42"))
    op = consumer.sent_messages[0]["ops"][0]
    assert op == {"op": "delete", "target": "#msg-42"}


def test_stream_to_no_consumer():
    view = _make_view()
    asyncio.run(view.stream_to("messages", html="<p>test</p>"))  # no error


def test_extract_element_html_by_id():
    html = '<div><ul id="msgs"><li>Hello</li><li>World</li></ul></div>'
    assert StreamingMixin._extract_element_html(html, "#msgs") == "<li>Hello</li><li>World</li>"


def test_extract_element_html_by_attr():
    html = '<div dj-stream="chat"><p>Hi</p></div>'
    assert StreamingMixin._extract_element_html(html, "[dj-stream='chat']") == "<p>Hi</p>"


def test_stream_batching():
    view = _make_view()
    consumer = FakeConsumer()
    view._ws_consumer = consumer

    async def run():
        await view.stream_to("chat", html="<p>1</p>")
        assert len(consumer.sent_messages) == 1

        view._last_stream_time = time.monotonic()
        await view.stream_to("chat", html="<p>2</p>")
        await asyncio.sleep(MIN_STREAM_INTERVAL_S + 0.02)
        assert len(consumer.sent_messages) >= 2

    asyncio.run(run())
