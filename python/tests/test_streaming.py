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
#
# These assert ORDERING invariants, never a count sampled after a wall-clock
# margin (#2625, the #1795 family): a second op inside the interval is queued
# and reaches the consumer only once the flush task itself completes, and an
# op outside the interval is sent inline with no flush task at all. The test
# owns the timing decision (``_last_stream_time``) and the flush delay, so no
# scheduler jitter can flip either branch.


def _force_inside_interval(view):
    """Make the NEXT op batch, deterministically, and run its flush at once.

    A ``_last_stream_time`` a minute in the future keeps
    ``elapsed < MIN_STREAM_INTERVAL_S`` true under any load; the flush wrapper
    keeps the real ``_flush_stream_batch`` body but drops its sleep so the
    test drives the flush by awaiting the task, not by out-waiting it.
    Returns the list of delays the mixin asked for.
    """
    view._last_stream_time = time.monotonic() + 60.0
    delays = []

    def _flush_now(delay):
        delays.append(delay)  # recorded synchronously, before the task is scheduled
        return StreamingMixin._flush_stream_batch(view, 0)

    view._flush_stream_batch = _flush_now
    return delays


def _force_interval_elapsed(view):
    """Make the NEXT op send inline: the interval has (long) passed."""
    view._last_stream_time = 0.0


def test_stream_batching():
    view = _make_view()
    consumer = FakeConsumer()
    view._ws_consumer = consumer

    async def run():
        await view.stream_to("chat", html="<p>1</p>")
        assert len(consumer.sent_messages) == 1

        delays = _force_inside_interval(view)
        await view.stream_to("chat", html="<p>2</p>")
        # Queued, not sent: the op is in the batch and a flush task is pending.
        assert len(consumer.sent_messages) == 1
        assert view._stream_batch == {
            "chat": [{"op": "replace", "target": "[dj-stream='chat']", "html": "<p>2</p>"}]
        }
        task = view._stream_flush_task
        assert task is not None and not task.done()
        assert delays and delays[0] > MIN_STREAM_INTERVAL_S

        await task  # the flush's own completion is the signal, not a sleep
        assert len(consumer.sent_messages) == 2
        assert consumer.sent_messages[1]["ops"] == [
            {"op": "replace", "target": "[dj-stream='chat']", "html": "<p>2</p>"}
        ]
        assert view._stream_batch == {}

    asyncio.run(run())


def test_stream_batching_gate_off_outside_interval_sends_inline():
    """Gate-off sibling (#1468): the same two ops with the interval elapsed are
    NOT batched — sent inline, no batch entry, no flush task — so the
    batched-path assertions above can tell batched from unbatched."""
    view = _make_view()
    consumer = FakeConsumer()
    view._ws_consumer = consumer

    async def run():
        await view.stream_to("chat", html="<p>1</p>")
        _force_interval_elapsed(view)
        await view.stream_to("chat", html="<p>2</p>")
        assert len(consumer.sent_messages) == 2
        assert consumer.sent_messages[1]["ops"][0]["html"] == "<p>2</p>"
        assert view._stream_batch == {}
        assert view._stream_flush_task is None

    asyncio.run(run())


def test_stream_text_batching():
    """Text ops batch the same way: queued inside the interval, delivered by
    the flush task, latest-wins within the batch."""
    view = _make_view()
    consumer = FakeConsumer()
    view._ws_consumer = consumer

    async def run():
        await view.stream_text("out", "first")
        assert len(consumer.sent_messages) == 1

        _force_inside_interval(view)
        await view.stream_text("out", "second")
        assert len(consumer.sent_messages) == 1
        assert view._stream_batch["out"][0]["text"] == "second"
        task = view._stream_flush_task
        assert task is not None and not task.done()

        await task
        assert len(consumer.sent_messages) == 2
        assert consumer.sent_messages[1]["ops"][0] == {
            "op": "text",
            "target": "[dj-stream='out']",
            "text": "second",
            "mode": "append",
        }
        assert view._stream_batch == {}

    asyncio.run(run())


def test_stream_text_batching_gate_off_outside_interval_sends_inline():
    view = _make_view()
    consumer = FakeConsumer()
    view._ws_consumer = consumer

    async def run():
        await view.stream_text("out", "first")
        _force_interval_elapsed(view)
        await view.stream_text("out", "second")
        assert len(consumer.sent_messages) == 2
        assert consumer.sent_messages[1]["ops"][0]["text"] == "second"
        assert view._stream_flush_task is None

    asyncio.run(run())


# ── Full lifecycle test ──────────────────────────────────────────────


def test_stream_lifecycle():
    """Test start → text → text → done sequence.

    "The batching interval has passed" between the two text ops is expressed
    as state (``_last_stream_time = 0``), not as a sleep, so the exact
    four-message sequence cannot fail in either direction under load.
    """
    view = _make_view()
    consumer = FakeConsumer()
    view._ws_consumer = consumer

    async def run():
        await view.stream_start("gen")
        await view.stream_text("gen", "Hello ")
        _force_interval_elapsed(view)
        await view.stream_text("gen", "world!")
        await view.stream_done("gen")
        assert view._stream_flush_task is None, "nothing was batched in this sequence"

    asyncio.run(run())

    assert [m["ops"][0]["op"] for m in consumer.sent_messages] == ["start", "text", "text", "done"]
    assert consumer.sent_messages[1]["ops"][0]["text"] == "Hello "
    assert consumer.sent_messages[2]["ops"][0]["text"] == "world!"


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
