"""No state-backend log line prints a cache key (#3632, review of #3640).

The cache key is ``{tenant}{session_key}_{view_key}_t<hash>``: the Django session
key is a bearer credential and the view key can carry a request path with ids.
The large-state warning already names the state by ``state_key_label``; these
tests pin the older ERROR/DEBUG lines in the backends to the same rule. Each
test drives one log line through the real code path and asserts that the
captured text (message and traceback) holds neither the session key nor the
path, and does hold the label, so a line that stopped logging at all fails too.
"""

from __future__ import annotations

import logging
import time
from unittest.mock import MagicMock

import pytest

from djust._rust import RustLiveView
from djust.state_backends.base import state_key_label
from djust.state_backends.memory import InMemoryStateBackend
from djust.state_backends.redis import RedisStateBackend

SESSION = "s3cr3tSESSIONkey0123456789abcdef"
KEY = f"tenant:7:{SESSION}_/orders/42/_tdeadbeef"
SECRETS = (SESSION, "/orders/42", "tenant:7", KEY)


def _view() -> RustLiveView:
    view = RustLiveView("<div dj-root>{{ x }}</div>", [])
    view.update_state({"x": 1})
    view.render_with_diff()
    return view


class _SerializeFails:
    def serialize_msgpack(self):
        raise RuntimeError("boom")


class _SizeFails:
    """Serialises fine, but its size estimate raises (the DEBUG line in ``set``)."""

    def __init__(self, inner):
        self._inner = inner

    def serialize_msgpack(self):
        return self._inner.serialize_msgpack()

    def get_state_size(self):
        raise RuntimeError("no size")


def _assert_clean(caplog, *, needle: str):
    text = caplog.text
    assert needle in text, f"the line did not log, so nothing was measured: {text!r}"
    for secret in SECRETS:
        assert secret not in text, f"{secret!r} reached the log: {text!r}"
    assert state_key_label(KEY) in text


class TestInMemoryBackend:
    def test_get_with_a_corrupt_snapshot_logs_the_label(self, caplog):
        backend = InMemoryStateBackend()
        backend._cache[KEY] = (b"\xc1not-msgpack", time.time())
        with caplog.at_level(logging.ERROR, logger="djust.state_backends.memory"):
            assert backend.get(KEY) is None
        _assert_clean(caplog, needle="round-trip failed")
        assert KEY not in backend._cache, "the corrupt entry is still discarded"

    def test_set_with_an_unserialisable_view_logs_the_label(self, caplog):
        backend = InMemoryStateBackend()
        with caplog.at_level(logging.ERROR, logger="djust.state_backends.memory"):
            backend.set(KEY, _SerializeFails())
        _assert_clean(caplog, needle="could not snapshot")

    def test_refresh_with_an_unserialisable_view_logs_the_label(self, caplog):
        backend = InMemoryStateBackend()
        backend.set(KEY, _view())
        with caplog.at_level(logging.ERROR, logger="djust.state_backends.memory"):
            backend.refresh(KEY, _SerializeFails())
        _assert_clean(caplog, needle="could not snapshot")

    def test_a_failed_size_estimate_logs_the_label(self, caplog):
        backend = InMemoryStateBackend()
        with caplog.at_level(logging.DEBUG, logger="djust.state_backends.memory"):
            backend.set(KEY, _SizeFails(_view()))
        _assert_clean(caplog, needle="Failed to estimate state size")

    def test_nothing_the_backend_logs_at_any_level_carries_the_key(self, caplog):
        backend = InMemoryStateBackend(state_size_warning_kb=1)
        with caplog.at_level(logging.DEBUG, logger="djust.state_backends"):
            backend._cache[KEY] = (b"\xc1", time.time())
            backend.get(KEY)
            backend.set(KEY, _SerializeFails())
            backend.set(KEY, _SizeFails(_view()))
            backend.refresh(KEY, _SerializeFails())
            backend.delete(KEY)
            backend.cleanup_expired(ttl=1)
            backend.delete_all()
        assert caplog.records, "nothing was logged, so nothing was measured"
        for secret in SECRETS:
            assert secret not in caplog.text, secret


def _redis_backend():
    """A RedisStateBackend with Redis I/O mocked (``__init__`` needs a live server)."""
    backend = RedisStateBackend.__new__(RedisStateBackend)
    backend._client = MagicMock()
    backend._key_prefix = "djust:"
    backend._default_ttl = 3600
    backend._compression_enabled = False
    backend._compression_threshold = 10240
    backend._compression_level = 3
    backend._compressor = None
    backend._decompressor = None
    backend._stats = {"compressed_count": 0, "uncompressed_count": 0, "total_bytes_saved": 0}
    return backend


class TestRedisBackend:
    def test_get_with_a_corrupt_payload_logs_the_label(self, caplog):
        backend = _redis_backend()
        backend._client.get.return_value = b"\x00not-msgpack"
        with caplog.at_level(logging.ERROR, logger="djust.state_backends.redis"):
            assert backend.get(KEY) is None
        _assert_clean(caplog, needle="Failed to deserialize from Redis")

    def test_set_with_an_unserialisable_view_logs_the_label_and_still_raises(self, caplog):
        backend = _redis_backend()
        with caplog.at_level(logging.ERROR, logger="djust.state_backends.redis"):
            with pytest.raises(RuntimeError, match="boom"):
                backend.set(KEY, _SerializeFails())
        _assert_clean(caplog, needle="Failed to serialize to Redis")
