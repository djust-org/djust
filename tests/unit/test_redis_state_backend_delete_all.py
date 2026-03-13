"""
Tests for RedisStateBackend.delete_all() — issue #428.

Covers three scenarios:
1. Normal path — keys found and deleted via pipeline, correct count returned.
2. Scan error path — scan_iter raises mid-iteration, returns 0 (not a partial count).
3. Empty keyspace — no keys, returns 0 without calling pipeline.execute().
"""

from unittest.mock import MagicMock, patch

from djust.state_backends.redis import RedisStateBackend


def _make_backend(keys=None, scan_error=None):
    """
    Build a RedisStateBackend with all Redis I/O mocked.

    Bypasses __init__ (which requires a live Redis connection) by using
    __new__ and directly injecting a mock client.

    Args:
        keys: list of byte-string keys scan_iter should yield (default: [])
        scan_error: if set, scan_iter raises this exception after yielding keys
    """
    backend = RedisStateBackend.__new__(RedisStateBackend)

    mock_client = MagicMock()
    backend._client = mock_client
    backend._key_prefix = "djust:"
    backend._default_ttl = 3600
    backend._compression_enabled = False
    backend._compression_threshold = 10240
    backend._compression_level = 3
    backend._compressor = None
    backend._decompressor = None
    backend._stats = {
        "compressed_count": 0,
        "uncompressed_count": 0,
        "total_bytes_saved": 0,
    }

    # Configure scan_iter behavior
    if scan_error is not None:
        def _iter_with_error(*args, **kwargs):
            for k in keys or []:
                yield k
            raise scan_error

        mock_client.scan_iter.side_effect = _iter_with_error
    else:
        mock_client.scan_iter.return_value = iter(keys or [])

    # pipeline() returns a mock pipeline
    mock_pipeline = MagicMock()
    mock_client.pipeline.return_value = mock_pipeline

    return backend, mock_client, mock_pipeline


class TestRedisDeleteAllNormalPath:
    def test_returns_correct_count_when_keys_exist(self):
        keys = [b"djust:sess1", b"djust:sess2", b"djust:sess3"]
        backend, client, pipeline = _make_backend(keys=keys)

        result = backend.delete_all()

        assert result == 3

    def test_pipeline_delete_called_for_each_key(self):
        keys = [b"djust:sess1", b"djust:sess2"]
        backend, client, pipeline = _make_backend(keys=keys)

        backend.delete_all()

        assert pipeline.delete.call_count == 2
        pipeline.delete.assert_any_call(b"djust:sess1")
        pipeline.delete.assert_any_call(b"djust:sess2")

    def test_pipeline_execute_called_when_keys_exist(self):
        keys = [b"djust:sess1"]
        backend, client, pipeline = _make_backend(keys=keys)

        backend.delete_all()

        pipeline.execute.assert_called_once()

    def test_scan_iter_uses_key_prefix_pattern(self):
        backend, client, pipeline = _make_backend(keys=[])

        backend.delete_all()

        client.scan_iter.assert_called_once_with(match="djust:*", count=100)


class TestRedisDeleteAllEmptyKeyspace:
    def test_returns_zero_when_no_keys(self):
        backend, client, pipeline = _make_backend(keys=[])

        result = backend.delete_all()

        assert result == 0

    def test_pipeline_execute_not_called_when_no_keys(self):
        backend, client, pipeline = _make_backend(keys=[])

        backend.delete_all()

        pipeline.execute.assert_not_called()


class TestRedisDeleteAllScanError:
    def test_returns_zero_on_scan_error(self):
        """scan_iter raises — must return 0, not a partial queued count."""
        backend, client, pipeline = _make_backend(
            keys=[b"djust:sess1", b"djust:sess2"],
            scan_error=Exception("Redis connection lost"),
        )

        result = backend.delete_all()

        assert result == 0

    def test_pipeline_execute_not_called_on_scan_error(self):
        """If scan_iter raises, the pipeline was never fully built — do not execute."""
        backend, client, pipeline = _make_backend(
            keys=[b"djust:sess1"],
            scan_error=RuntimeError("scan failed"),
        )

        backend.delete_all()

        pipeline.execute.assert_not_called()

    def test_logs_exception_on_error(self):
        backend, client, pipeline = _make_backend(
            keys=[],
            scan_error=OSError("timeout"),
        )

        with patch("djust.state_backends.redis.logger") as mock_logger:
            backend.delete_all()
            mock_logger.exception.assert_called_once()
            assert "delete_all" in mock_logger.exception.call_args[0][0]
