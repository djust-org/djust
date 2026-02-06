"""
Tests for the StreamingMixin — real-time partial DOM updates.
"""

import asyncio
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
    for name in (
        "stream_to",
        "stream_insert",
        "stream_delete",
        "stream_text",
        "stream_error",
        "stream_start",
        "stream_done",
        "_send_stream_ops",
        "_flush_stream_batch",
    ):
        setattr(view, name, getattr(StreamingMixin, name).__get__(view))
    return view


# ── Basic stream_to tests ──────────────────────────────────────────────


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


def test_stream_to_no_consumer():
    view = _make_view()
    asyncio.run(view.stream_to("messages", html="<p>test</p>"))  # no error


# ── stream_insert tests ───────────────────────────────────────────────


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


def test_stream_insert_default_target():
    view = _make_view()
    consumer = FakeConsumer()
    view._ws_consumer = consumer
    asyncio.run(view.stream_insert("notifications", "<div>Alert</div>"))
    op = consumer.sent_messages[0]["ops"][0]
    assert op["target"] == "[dj-stream='notifications']"
    assert op["html"] == "<div>Alert</div>"


def test_stream_insert_custom_target():
    view = _make_view()
    consumer = FakeConsumer()
    view._ws_consumer = consumer
    asyncio.run(view.stream_insert("feed", "<p>Item</p>", target="#custom-list"))
    assert consumer.sent_messages[0]["ops"][0]["target"] == "#custom-list"


# ── stream_delete tests ──────────────────────────────────────────────


def test_stream_delete():
    view = _make_view()
    consumer = FakeConsumer()
    view._ws_consumer = consumer
    asyncio.run(view.stream_delete("messages", "#msg-42"))
    op = consumer.sent_messages[0]["ops"][0]
    assert op == {"op": "delete", "target": "#msg-42"}


def test_stream_delete_no_consumer():
    view = _make_view()
    asyncio.run(view.stream_delete("messages", "#msg-1"))  # no error


# ── stream_text tests ────────────────────────────────────────────────


def test_stream_text_append():
    view = _make_view()
    consumer = FakeConsumer()
    view._ws_consumer = consumer
    asyncio.run(view.stream_text("output", "Hello "))
    msg = consumer.sent_messages[0]
    assert msg["type"] == "stream"
    op = msg["ops"][0]
    assert op["op"] == "text"
    assert op["text"] == "Hello "
    assert op["mode"] == "append"


def test_stream_text_replace():
    view = _make_view()
    consumer = FakeConsumer()
    view._ws_consumer = consumer
    asyncio.run(view.stream_text("output", "Full replace", mode="replace"))
    op = consumer.sent_messages[0]["ops"][0]
    assert op["mode"] == "replace"
    assert op["text"] == "Full replace"


def test_stream_text_prepend():
    view = _make_view()
    consumer = FakeConsumer()
    view._ws_consumer = consumer
    asyncio.run(view.stream_text("output", "prefix: ", mode="prepend"))
    op = consumer.sent_messages[0]["ops"][0]
    assert op["mode"] == "prepend"


def test_stream_text_default_target():
    view = _make_view()
    consumer = FakeConsumer()
    view._ws_consumer = consumer
    asyncio.run(view.stream_text("response", "token"))
    assert consumer.sent_messages[0]["ops"][0]["target"] == "[dj-stream='response']"


def test_stream_text_custom_target():
    view = _make_view()
    consumer = FakeConsumer()
    view._ws_consumer = consumer
    asyncio.run(view.stream_text("response", "token", target="#my-output"))
    assert consumer.sent_messages[0]["ops"][0]["target"] == "#my-output"


def test_stream_text_no_consumer():
    view = _make_view()
    asyncio.run(view.stream_text("output", "test"))  # no error


# ── stream_error tests ───────────────────────────────────────────────


def test_stream_error_sends_error_op():
    view = _make_view()
    consumer = FakeConsumer()
    view._ws_consumer = consumer
    asyncio.run(view.stream_error("chat", "Connection lost"))
    msg = consumer.sent_messages[0]
    assert msg["type"] == "stream"
    op = msg["ops"][0]
    assert op["op"] == "error"
    assert op["error"] == "Connection lost"
    assert op["target"] == "[dj-stream='chat']"


def test_stream_error_custom_target():
    view = _make_view()
    consumer = FakeConsumer()
    view._ws_consumer = consumer
    asyncio.run(view.stream_error("chat", "Timeout", target="#error-zone"))
    assert consumer.sent_messages[0]["ops"][0]["target"] == "#error-zone"


def test_stream_error_no_consumer():
    view = _make_view()
    asyncio.run(view.stream_error("chat", "fail"))  # no error


# ── stream_start / stream_done tests ────────────────────────────────


def test_stream_start():
    view = _make_view()
    consumer = FakeConsumer()
    view._ws_consumer = consumer
    asyncio.run(view.stream_start("generation"))
    op = consumer.sent_messages[0]["ops"][0]
    assert op["op"] == "start"
    assert op["target"] == "[dj-stream='generation']"


def test_stream_done():
    view = _make_view()
    consumer = FakeConsumer()
    view._ws_consumer = consumer
    asyncio.run(view.stream_done("generation"))
    op = consumer.sent_messages[0]["ops"][0]
    assert op["op"] == "done"
    assert op["target"] == "[dj-stream='generation']"


def test_stream_start_no_consumer():
    view = _make_view()
    asyncio.run(view.stream_start("x"))  # no error


def test_stream_done_no_consumer():
    view = _make_view()
    asyncio.run(view.stream_done("x"))  # no error


# ── extract_element_html tests ───────────────────────────────────────


def test_extract_element_html_by_id():
    html = '<div><ul id="msgs"><li>Hello</li><li>World</li></ul></div>'
    assert StreamingMixin._extract_element_html(html, "#msgs") == "<li>Hello</li><li>World</li>"


def test_extract_element_html_by_attr():
    html = '<div dj-stream="chat"><p>Hi</p></div>'
    assert StreamingMixin._extract_element_html(html, "[dj-stream='chat']") == "<p>Hi</p>"


def test_extract_element_html_fallback():
    html = "<div>No match here</div>"
    result = StreamingMixin._extract_element_html(html, "#nonexistent")
    assert result == html  # fallback returns full HTML


# ── Batching tests ───────────────────────────────────────────────────


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


def test_stream_text_batching():
    """Text ops also batch when sent rapidly."""
    view = _make_view()
    consumer = FakeConsumer()
    view._ws_consumer = consumer

    async def run():
        await view.stream_text("out", "first")
        assert len(consumer.sent_messages) == 1

        view._last_stream_time = time.monotonic()
        await view.stream_text("out", "second")
        # Should be batched, not sent yet
        await asyncio.sleep(MIN_STREAM_INTERVAL_S + 0.02)
        assert len(consumer.sent_messages) >= 2

    asyncio.run(run())


# ── Full lifecycle test ──────────────────────────────────────────────


def test_stream_lifecycle():
    """Test start → text → text → done sequence."""
    view = _make_view()
    consumer = FakeConsumer()
    view._ws_consumer = consumer

    async def run():
        await view.stream_start("gen")
        await view.stream_text("gen", "Hello ")
        # Wait for batching interval to pass
        await asyncio.sleep(MIN_STREAM_INTERVAL_S + 0.01)
        await view.stream_text("gen", "world!")
        await view.stream_done("gen")

    asyncio.run(run())

    assert len(consumer.sent_messages) == 4
    assert consumer.sent_messages[0]["ops"][0]["op"] == "start"
    assert consumer.sent_messages[1]["ops"][0]["op"] == "text"
    assert consumer.sent_messages[1]["ops"][0]["text"] == "Hello "
    assert consumer.sent_messages[2]["ops"][0]["text"] == "world!"
    assert consumer.sent_messages[3]["ops"][0]["op"] == "done"


def test_stream_error_preserves_partial():
    """Error after partial text keeps the text ops already sent."""
    view = _make_view()
    consumer = FakeConsumer()
    view._ws_consumer = consumer

    async def run():
        await view.stream_text("gen", "Partial content...")
        await view.stream_error("gen", "LLM provider error")

    asyncio.run(run())

    assert len(consumer.sent_messages) == 2
    assert consumer.sent_messages[0]["ops"][0]["op"] == "text"
    assert consumer.sent_messages[1]["ops"][0]["op"] == "error"
    assert consumer.sent_messages[1]["ops"][0]["error"] == "LLM provider error"
