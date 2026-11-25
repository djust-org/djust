"""Stream ops reach an SSE client with the same frames as a WebSocket client (#4232).

``StreamingMixin`` (``stream_start`` / ``stream_to`` / ``stream_done``) sends a
``{"type": "stream", "stream": ..., "ops": [...]}`` frame through
``view._ws_consumer``. An SSE-mounted view has no consumer, so every op
returned early and an SSE client never saw the stream: its
``dj-update="ignore"`` target kept its first render.

The demo's ``/demos/markdown-stream/`` drives the stream over both transports
with the same short reply; the ``stream`` frames each client receives, reduced
to ``(stream, [op kinds])``, must be the same.
"""

from __future__ import annotations

import asyncio
import json

import pytest

pytestmark = pytest.mark.django_db(transaction=True)

REPLY = "**p4232**"


def _stream_frames(frames):
    """The ``stream`` frames, as ``(stream name, op kinds, last html)``."""
    out = []
    for frame in frames:
        if isinstance(frame, dict) and frame.get("type") == "stream":
            ops = frame.get("ops") or []
            html = [op.get("html") for op in ops if "html" in op]
            out.append((frame.get("stream"), [op.get("op") for op in ops], html[-1:] or None))
    return out


def _collect(transport, monkeypatch):
    from djust.testing import close_live_pages, open_live_page
    from djust_demos.views import markdown_stream_demo

    monkeypatch.setattr(markdown_stream_demo, "_SAMPLE_REPLY", REPLY)
    page = open_live_page("/demos/markdown-stream/")

    async def run():
        connect = page.connect if transport == "ws" else page.connect_sse
        connection = await connect()
        try:
            answer = await connection.send_event("start_stream", {})
            assert answer.get("type") != "error", answer
            for _ in range(100):
                try:
                    await connection.receive(timeout=0.1)
                except asyncio.TimeoutError:
                    pass
                kinds = [k for _, ops, _ in _stream_frames(connection.frames) for k in ops]
                if "done" in kinds:
                    break
            return list(connection.frames)
        finally:
            await connection.close()

    try:
        return asyncio.run(run())
    finally:
        close_live_pages()


def _summary(frames):
    """Op kinds in order (rate-window batching may merge replaces, so
    consecutive replaces collapse) and the settled HTML of the last replace."""
    kinds, last_html = [], None
    for _, ops, html in _stream_frames(frames):
        for kind in ops:
            if not (kind == "replace" and kinds and kinds[-1] == "replace"):
                kinds.append(kind)
        if html:
            last_html = html[-1]
    return kinds, last_html


@pytest.mark.parametrize("transport", ["ws", "sse"])
def test_stream_ops_reach_the_client(transport, monkeypatch):
    frames = _collect(transport, monkeypatch)
    kinds, last_html = _summary(frames)
    assert kinds == ["start", "replace", "done"], (transport, json.dumps(frames)[-800:])
    assert last_html and "<strong>p4232</strong>" in last_html, (transport, last_html)


def test_sse_stream_frames_match_websocket(monkeypatch):
    """Parity: the SSE client's stream frames equal the WebSocket client's."""
    ws = _summary(_collect("ws", monkeypatch))
    sse = _summary(_collect("sse", monkeypatch))
    assert ws[0] == ["start", "replace", "done"], ws
    assert sse == ws, {"ws": ws, "sse": sse}
