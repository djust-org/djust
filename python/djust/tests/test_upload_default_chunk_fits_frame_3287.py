"""#3287 — the default upload ``chunk_size`` must fit the WebSocket frame limit.

``allow_upload()`` defaulted ``chunk_size`` to 64 KiB and sent that value to the
client, which prefers it over its own 63 KiB default. A chunk frame is
``FRAME_HEADER_SIZE + 4`` (21) bytes of header plus the chunk, so 65 536 + 21
bytes exceeded the default ``max_message_size`` (65 536) enforced in
``LiveViewConsumer.receive``. Every multi-chunk upload failed with "Message too
large" under default settings; small test files never hit it.

These tests drive the chunk size the server tells the client to use through the
real ``receive`` size gate and the real ``_handle_upload_frame`` path, with the
default config (no ``max_message_size`` override).
"""

from __future__ import annotations

import struct
import uuid
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("channels")

from django.test import override_settings  # noqa: E402

from djust import LiveView  # noqa: E402
from djust.config import config  # noqa: E402
from djust.rate_limit import ConnectionRateLimiter  # noqa: E402
from djust.uploads import (  # noqa: E402
    DEFAULT_CHUNK_SIZE,
    FRAME_CHUNK,
    FRAME_COMPLETE,
    UPLOAD_CHUNK_FRAME_OVERHEAD,
    UploadMixin,
)


class _UploadView(UploadMixin, LiveView):
    template = "<div>up</div>"

    def mount(self, request, **kwargs):
        # Defaults on purpose: this is the downstream failure shape.
        self.allow_upload("doc", accept=".txt")


def _chunk_frame(ref: str, index: int, payload: bytes) -> bytes:
    return bytes([FRAME_CHUNK]) + uuid.UUID(ref).bytes + struct.pack(">I", index) + payload


def _complete_frame(ref: str) -> bytes:
    return bytes([FRAME_COMPLETE]) + uuid.UUID(ref).bytes


def _make_consumer(view):
    from djust.websocket import LiveViewConsumer

    consumer = LiveViewConsumer()
    consumer.view_instance = view
    consumer.use_binary = False
    consumer.send = AsyncMock()
    consumer.send_error = AsyncMock()
    consumer.send_json = AsyncMock()
    consumer._rate_limiter = ConnectionRateLimiter()
    return consumer


def _mounted_view():
    view = _UploadView()
    view.mount(None)
    return view


async def _upload(consumer, view, size: int):
    """Upload ``size`` bytes the way the client does: chunk by the size the
    server advertised in the slot config, one frame per chunk."""
    cfg = view._upload_manager.get_upload_state()["doc"]["config"]
    chunk_size = cfg["chunk_size"]
    ref = str(uuid.uuid4())
    entry = view._upload_manager.register_entry("doc", ref, "big.txt", "text/plain", size)
    assert entry is not None
    data = b"x" * size
    frames = [
        _chunk_frame(ref, i, data[off : off + chunk_size])
        for i, off in enumerate(range(0, size, chunk_size))
    ]
    assert len(frames) > 1, "test must be multi-chunk to reproduce #3287"
    for frame in frames:
        await consumer.receive(bytes_data=frame)
    await consumer.receive(bytes_data=_complete_frame(ref))
    return ref, chunk_size


def _statuses(consumer):
    return [
        call.args[0].get("status")
        for call in consumer.send_json.await_args_list
        if call.args and isinstance(call.args[0], dict)
    ]


@pytest.mark.asyncio
async def test_default_chunk_frame_fits_default_max_message_size():
    config.reset()
    view = _mounted_view()
    cfg = view._upload_manager.get_upload_state()["doc"]["config"]
    assert cfg["chunk_size"] == DEFAULT_CHUNK_SIZE
    assert cfg["chunk_size"] + UPLOAD_CHUNK_FRAME_OVERHEAD <= config.get("max_message_size")


@pytest.mark.asyncio
async def test_multi_chunk_upload_succeeds_with_default_settings():
    """Reproduction: 200 KiB through the real receive gate, default config."""
    config.reset()
    view = _mounted_view()
    consumer = _make_consumer(view)

    ref, chunk_size = await _upload(consumer, view, 200 * 1024)

    consumer.send_error.assert_not_awaited()
    assert "error" not in _statuses(consumer)
    assert "complete" in _statuses(consumer)
    assert view._upload_manager._entries[ref].complete
    assert chunk_size + UPLOAD_CHUNK_FRAME_OVERHEAD <= 65536


@pytest.mark.asyncio
@override_settings(LIVEVIEW_CONFIG={"max_message_size": 16384})
async def test_chunk_size_is_clamped_to_a_smaller_max_message_size():
    """A deployment that lowers ``max_message_size`` gets a chunk size that
    still fits; the explicit 64 KiB (the old default) is clamped too."""
    config.reset()
    try:
        view = _UploadView()
        view.allow_upload("doc", accept=".txt")
        view.allow_upload("explicit", accept=".txt", chunk_size=64 * 1024)
        state = view._upload_manager.get_upload_state()
        for slot in ("doc", "explicit"):
            assert state[slot]["config"]["chunk_size"] == 16384 - UPLOAD_CHUNK_FRAME_OVERHEAD

        consumer = _make_consumer(view)
        ref, chunk_size = await _upload(consumer, view, 100 * 1024)
        consumer.send_error.assert_not_awaited()
        assert view._upload_manager._entries[ref].complete
    finally:
        config.reset()


@pytest.mark.asyncio
@override_settings(LIVEVIEW_CONFIG={"max_message_size": 0})
async def test_no_clamp_when_message_size_is_unlimited():
    config.reset()
    try:
        view = _UploadView()
        view.allow_upload("doc", chunk_size=256 * 1024)
        assert view._upload_manager.get_upload_state()["doc"]["config"]["chunk_size"] == (
            256 * 1024
        )
    finally:
        config.reset()


def test_client_default_matches_server_default():
    """The client's own fallback must equal the server default (#1993)."""
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[1] / "static" / "djust" / "src" / "15-uploads.js"
    ).read_text()
    assert f"DEFAULT_CHUNK_SIZE = {DEFAULT_CHUNK_SIZE // 1024} * 1024" in src
