"""``stream_text(mode="append")`` must not lose chunks inside the rate window.

``stream_to``/``stream_text`` rate-limit to ``MIN_STREAM_INTERVAL_S``: an op
inside the window is queued and flushed later. The queue used to keep only the
stream's latest op ("only latest matters"), which is right for a replace but
drops text for an append: five tight appends of ``"a"``..``"e"`` reached the
client as ``"ae"``.

The client is modelled on ``17-streaming.js`` (``_applyTextOp`` and the
``replace`` op), so each test asserts what the user would see, not the queue's
internal shape. "Inside the window" is forced as state, as in
``test_stream_terminal_flush_3227.py``, never as a race against the timer.
"""

from __future__ import annotations

import time

import pytest

from djust import LiveView
from djust.streaming import StreamingMixin


class _Consumer:
    def __init__(self):
        self.frames = []

    async def send_json(self, data):
        self.frames.append(data)

    def ops(self):
        return [op for f in self.frames for op in f["ops"]]

    def content(self, target):
        """What ``target`` shows after the client applied every frame, in order."""
        text = ""
        for op in self.ops():
            if op["target"] != target:
                continue
            if op["op"] == "replace":
                text = op.get("html") or ""
            elif op["op"] == "text":
                mode = op.get("mode") or "append"
                if mode == "replace":
                    text = op["text"]
                elif mode == "prepend":
                    text = op["text"] + text
                else:
                    text += op["text"]
        return text


class _View(LiveView):
    template = '<div dj-root><p dj-stream="a"></p><p id="b"></p></div>'


_A = "[dj-stream='a']"


def _view():
    view = _View()
    view._ws_consumer = _Consumer()
    return view


def _inside_window(view):
    """Queue every following op; the flush runs without its delay when awaited."""
    view._last_stream_time = time.monotonic() + 60.0
    view._flush_stream_batch = lambda delay: StreamingMixin._flush_stream_batch(view, 0)


async def _flush(view):
    task = view._stream_flush_task
    if task is not None:
        await task


@pytest.mark.asyncio
async def test_tight_appends_keep_every_chunk():
    view = _view()
    await view.stream_text("a", "a")  # sent at once
    _inside_window(view)
    for chunk in "bcde":
        await view.stream_text("a", chunk)
    await _flush(view)

    assert view._ws_consumer.content(_A) == "abcde"
    # The queued appends travel as one op, not four.
    assert len(view._ws_consumer.ops()) == 2


@pytest.mark.asyncio
async def test_append_after_a_queued_replace_keeps_the_order():
    view = _view()
    await view.stream_text("a", "old")
    _inside_window(view)
    await view.stream_text("a", "x")
    await view.stream_text("a", "NEW", mode="replace")
    await view.stream_text("a", "1")
    await view.stream_text("a", "2")
    await _flush(view)

    assert view._ws_consumer.content(_A) == "NEW12"


@pytest.mark.asyncio
async def test_a_replace_supersedes_queued_appends():
    view = _view()
    await view.stream_text("a", "old")
    _inside_window(view)
    await view.stream_text("a", "x")
    await view.stream_text("a", "y")
    await view.stream_text("a", "FINAL", mode="replace")
    await _flush(view)

    assert view._ws_consumer.content(_A) == "FINAL"
    assert view._ws_consumer.ops()[-1] == {
        "op": "text",
        "target": _A,
        "text": "FINAL",
        "mode": "replace",
    }
    assert len(view._ws_consumer.ops()) == 2, "the superseded appends are not sent"


@pytest.mark.asyncio
async def test_tight_prepends_keep_every_chunk():
    view = _view()
    await view.stream_text("a", "end", mode="replace")
    _inside_window(view)
    await view.stream_text("a", "2", mode="prepend")
    await view.stream_text("a", "1", mode="prepend")
    await _flush(view)

    assert view._ws_consumer.content(_A) == "12end"


@pytest.mark.asyncio
async def test_appends_to_two_targets_of_one_stream_are_both_kept():
    view = _view()
    _inside_window(view)
    await view.stream_text("a", "a1")
    await view.stream_text("a", "b1", target="#b")
    await view.stream_text("a", "a2")
    await _flush(view)

    assert view._ws_consumer.content(_A) == "a1a2"
    assert view._ws_consumer.content("#b") == "b1"


@pytest.mark.asyncio
async def test_html_replace_still_keeps_only_the_latest():
    view = _view()
    await view.stream_to("a", html="<p>1</p>")
    _inside_window(view)
    await view.stream_to("a", html="<p>12</p>")
    await view.stream_to("a", html="<p>123</p>")
    await _flush(view)

    assert [op.get("html") for op in view._ws_consumer.ops()] == ["<p>1</p>", "<p>123</p>"]


@pytest.mark.asyncio
async def test_append_after_a_queued_html_replace_is_kept_after_it():
    view = _view()
    await view.stream_to("a", html="<p>1</p>")
    _inside_window(view)
    await view.stream_to("a", html="base")
    await view.stream_text("a", "+tail")
    await _flush(view)

    assert view._ws_consumer.content(_A) == "base+tail"


@pytest.mark.asyncio
async def test_queued_appends_go_out_before_done():
    view = _view()
    await view.stream_text("a", "a")
    _inside_window(view)
    await view.stream_text("a", "b")
    await view.stream_text("a", "c")
    await view.stream_done("a")

    ops = view._ws_consumer.ops()
    assert ops[-1]["op"] == "done"
    assert view._ws_consumer.content(_A) == "abc"
