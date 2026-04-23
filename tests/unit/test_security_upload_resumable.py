"""Unit tests for the resumable-uploads subsystem (ADR-010, issue #821).

Covers ``djust.uploads.storage``, ``djust.uploads.resumable``, and
``djust.uploads.views``. These modules are security-adjacent (session
isolation on resume, state-size enforcement, UUID validation) and the
CI security job measures coverage across them.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional
from unittest.mock import MagicMock

import pytest
from django.test import RequestFactory

from djust.uploads import UploadWriter
from djust.uploads.resumable import (
    ResumableUploadWriter,
    bytes_received_from_ranges,
    compact_chunks,
    expand_ranges,
    resolve_resume_request,
)
from djust.uploads.storage import (
    MAX_STATE_SIZE_BYTES,
    InMemoryUploadState,
    RedisUploadState,
    UploadStateStore,
    UploadStateTooLarge,
    _reset_default_store_for_tests,
    get_default_store,
    set_default_store,
)
from djust.uploads.views import UploadStatusView, upload_status_urlpatterns


# ===========================================================================
# Helpers
# ===========================================================================


class RecordingInnerWriter(UploadWriter):
    """Minimal UploadWriter that just records calls — no I/O."""

    def __init__(
        self,
        upload_id: str,
        filename: str,
        content_type: str,
        expected_size: Optional[int] = None,
    ):
        super().__init__(upload_id, filename, content_type, expected_size)
        self.chunks: list = []
        self.opened = False
        self.closed = False
        self.aborted_with: Optional[BaseException] = None
        self.close_result: Any = {"ok": True}

    def open(self) -> None:
        self.opened = True

    def write_chunk(self, chunk: bytes, chunk_index: int = 0) -> None:
        self.chunks.append((chunk_index, chunk))

    def close(self) -> Any:
        self.closed = True
        return self.close_result

    def abort(self, error: BaseException) -> None:
        self.aborted_with = error


# ===========================================================================
# compact_chunks / expand_ranges / bytes_received_from_ranges
# ===========================================================================


class TestCompaction:
    def test_compact_empty(self):
        assert compact_chunks([]) == []

    def test_compact_contiguous(self):
        assert compact_chunks([0, 1, 2, 3]) == [(0, 3)]

    def test_compact_with_gaps(self):
        assert compact_chunks([0, 1, 2, 5, 6, 10]) == [(0, 2), (5, 6), (10, 10)]

    def test_compact_dedupes_and_sorts(self):
        assert compact_chunks([2, 0, 1, 1, 3, 3]) == [(0, 3)]

    def test_expand_empty_and_none(self):
        assert expand_ranges([]) == []
        assert expand_ranges(None) == []  # defensive None handling

    def test_expand_ignores_malformed_entries(self):
        # len != 2 should be skipped silently.
        assert expand_ranges([[0, 2], "bad", [5], [8, 9, 10]]) == [0, 1, 2]

    def test_expand_handles_tuples_and_lists(self):
        assert expand_ranges([(0, 1), [3, 4]]) == [0, 1, 3, 4]

    def test_bytes_received_from_ranges(self):
        # 3 chunks (indices 0,1,2) × 1024 bytes = 3072 bytes
        assert bytes_received_from_ranges([[0, 2]], chunk_size=1024) == 3072

    def test_bytes_received_ignores_malformed(self):
        assert bytes_received_from_ranges([[0, 1], "bad", [3]], chunk_size=100) == 200

    def test_bytes_received_empty(self):
        assert bytes_received_from_ranges([], chunk_size=1024) == 0
        assert bytes_received_from_ranges(None, chunk_size=1024) == 0


# ===========================================================================
# InMemoryUploadState
# ===========================================================================


class TestInMemoryUploadState:
    def test_get_missing_returns_none(self):
        store = InMemoryUploadState()
        assert store.get("nonexistent") is None

    def test_set_and_get(self):
        store = InMemoryUploadState()
        store.set("u1", {"hello": "world"}, ttl=60)
        got = store.get("u1")
        assert got == {"hello": "world"}
        # Must be a *copy* — mutating caller's dict must not leak.
        got["hello"] = "mutated"
        assert store.get("u1") == {"hello": "world"}

    def test_update_merges(self):
        store = InMemoryUploadState()
        store.set("u1", {"a": 1, "b": 2}, ttl=60)
        merged = store.update("u1", {"b": 20, "c": 3})
        assert merged == {"a": 1, "b": 20, "c": 3}
        assert store.get("u1") == {"a": 1, "b": 20, "c": 3}

    def test_update_missing_returns_none(self):
        store = InMemoryUploadState()
        assert store.update("u1", {"x": 1}) is None

    def test_delete_removes_entry(self):
        store = InMemoryUploadState()
        store.set("u1", {"a": 1}, ttl=60)
        store.delete("u1")
        assert store.get("u1") is None
        # delete is idempotent.
        store.delete("u1")

    def test_ttl_expiry(self):
        store = InMemoryUploadState()
        store.set("u1", {"a": 1}, ttl=60)
        store._force_expire("u1")
        assert store.get("u1") is None

    def test_size_reporting(self):
        store = InMemoryUploadState()
        assert store._size() == 0
        store.set("u1", {"a": 1}, ttl=60)
        store.set("u2", {"b": 2}, ttl=60)
        assert store._size() == 2
        store._force_expire("u1")
        # _size() filters out expired entries.
        assert store._size() == 1

    def test_state_too_large_on_set(self):
        store = InMemoryUploadState()
        # Build a payload guaranteed to exceed MAX_STATE_SIZE_BYTES.
        huge = {"data": "x" * (MAX_STATE_SIZE_BYTES + 100)}
        with pytest.raises(UploadStateTooLarge):
            store.set("u1", huge, ttl=60)

    def test_state_too_large_on_update(self):
        store = InMemoryUploadState()
        store.set("u1", {"tiny": 1}, ttl=60)
        huge = {"data": "x" * (MAX_STATE_SIZE_BYTES + 100)}
        with pytest.raises(UploadStateTooLarge):
            store.update("u1", huge)


# ===========================================================================
# RedisUploadState (with fake client)
# ===========================================================================


class FakeRedisClient:
    """A minimal redis-py-compatible mock (no pipeline)."""

    def __init__(self):
        self.data: Dict[str, bytes] = {}
        self.ttls: Dict[str, int] = {}

    def get(self, key: str):
        return self.data.get(key)

    def set(self, key, value, ex=None):
        self.data[key] = value if isinstance(value, bytes) else str(value).encode()
        if ex is not None:
            self.ttls[key] = ex

    def setex(self, key, ttl, value):
        self.data[key] = value if isinstance(value, bytes) else str(value).encode()
        self.ttls[key] = ttl

    def delete(self, key):
        self.data.pop(key, None)
        self.ttls.pop(key, None)

    def ttl(self, key):
        return self.ttls.get(key, -1)


class TestRedisUploadStateNoPipeline:
    """Redis store exercising the non-atomic fallback path (no pipeline)."""

    def test_set_and_get(self):
        client = FakeRedisClient()
        store = RedisUploadState(client)
        store.set("u1", {"a": 1}, ttl=60)
        assert store.get("u1") == {"a": 1}

    def test_get_missing_returns_none(self):
        client = FakeRedisClient()
        store = RedisUploadState(client)
        assert store.get("nonexistent") is None

    def test_get_corrupt_json_discards_entry(self):
        client = FakeRedisClient()
        client.data["djust:upload:u1"] = b"not-json{"
        store = RedisUploadState(client)
        assert store.get("u1") is None
        # Corrupt entry should be deleted after discovery.
        assert "djust:upload:u1" not in client.data

    def test_setex_fallback_for_older_redis_py(self):
        """Older redis-py raises TypeError on ``ex=`` kwarg — store falls
        back to setex positional form."""

        class OldRedis(FakeRedisClient):
            def set(self, key, value, ex=None):
                if ex is not None:
                    raise TypeError("old redis doesn't support ex kwarg")
                super().set(key, value, ex=None)

        client = OldRedis()
        store = RedisUploadState(client)
        store.set("u1", {"a": 1}, ttl=30)
        assert client.ttls["djust:upload:u1"] == 30

    def test_update_no_pipeline_reads_and_rewrites(self):
        client = FakeRedisClient()
        store = RedisUploadState(client)
        store.set("u1", {"a": 1}, ttl=60)
        merged = store.update("u1", {"b": 2})
        assert merged == {"a": 1, "b": 2}
        assert store.get("u1") == {"a": 1, "b": 2}

    def test_update_no_pipeline_missing_entry_returns_none(self):
        client = FakeRedisClient()
        store = RedisUploadState(client)
        assert store.update("missing", {"x": 1}) is None

    def test_set_too_large_raises(self):
        client = FakeRedisClient()
        store = RedisUploadState(client)
        huge = {"data": "x" * (MAX_STATE_SIZE_BYTES + 100)}
        with pytest.raises(UploadStateTooLarge):
            store.set("u1", huge, ttl=60)

    def test_delete_catches_redis_errors(self, caplog):
        class BrokenRedis(FakeRedisClient):
            def delete(self, key):
                raise RuntimeError("redis down")

        store = RedisUploadState(BrokenRedis())
        # Should not propagate — logs a warning instead.
        store.delete("u1")

    def test_custom_key_prefix(self):
        client = FakeRedisClient()
        store = RedisUploadState(client, key_prefix="myapp:ups:")
        store.set("u1", {"a": 1}, ttl=60)
        assert "myapp:ups:u1" in client.data


class TestRedisUploadStateWithPipeline:
    """Exercise the ``WATCH``/``MULTI`` atomic path and its fallbacks."""

    def _make_client_with_pipeline(self, *, ttl_remaining: int = 60):
        """Build a mock with enough pipeline surface for one happy-path call."""
        client = MagicMock()
        client.get = MagicMock(return_value=json.dumps({"a": 1}).encode())
        client.ttl = MagicMock(return_value=ttl_remaining)

        pipe = MagicMock()
        pipe.__enter__ = MagicMock(return_value=pipe)
        pipe.__exit__ = MagicMock(return_value=False)
        pipe.watch = MagicMock()
        pipe.get = MagicMock(return_value=json.dumps({"a": 1}).encode())
        pipe.unwatch = MagicMock()
        pipe.multi = MagicMock()
        pipe.set = MagicMock()
        pipe.execute = MagicMock(return_value=[True])

        client.pipeline = MagicMock(return_value=pipe)
        return client, pipe

    def test_update_pipeline_happy_path(self):
        client, pipe = self._make_client_with_pipeline(ttl_remaining=120)
        store = RedisUploadState(client)
        merged = store.update("u1", {"b": 2})
        assert merged == {"a": 1, "b": 2}
        pipe.multi.assert_called()
        pipe.execute.assert_called()

    def test_update_pipeline_entry_missing(self):
        client, pipe = self._make_client_with_pipeline()
        pipe.get.return_value = None
        store = RedisUploadState(client)
        assert store.update("missing", {"x": 1}) is None
        pipe.unwatch.assert_called()

    def test_update_pipeline_watcherror_retries(self):
        """WatchError should trigger retry, not bubble up."""
        client, pipe = self._make_client_with_pipeline()

        class WatchError(Exception):
            pass

        # First execute raises WatchError → retry; second succeeds.
        pipe.execute.side_effect = [WatchError("conflict"), [True]]
        store = RedisUploadState(client)
        merged = store.update("u1", {"b": 2})
        assert merged == {"a": 1, "b": 2}

    def test_update_pipeline_too_large_propagates(self):
        client, pipe = self._make_client_with_pipeline()
        store = RedisUploadState(client)
        huge = {"data": "x" * (MAX_STATE_SIZE_BYTES + 100)}
        with pytest.raises(UploadStateTooLarge):
            store.update("u1", huge)

    def test_update_pipeline_generic_error_falls_back(self):
        """Non-WatchError exception inside pipeline → fallback non-atomic path."""
        client, pipe = self._make_client_with_pipeline()
        # Make pipe.watch throw a generic error; fallback path uses
        # client.get which we've stubbed to return the JSON dict.
        pipe.watch.side_effect = RuntimeError("redis link flaky")
        # Fallback path calls self.get() which uses client.get().
        store = RedisUploadState(client)
        merged = store.update("u1", {"b": 2})
        assert merged == {"a": 1, "b": 2}


# ===========================================================================
# Default-store resolution
# ===========================================================================


class TestDefaultStoreResolution:
    def teardown_method(self):
        _reset_default_store_for_tests()

    def test_default_store_is_in_memory(self):
        _reset_default_store_for_tests()
        store = get_default_store()
        assert isinstance(store, InMemoryUploadState)
        # Idempotent: same instance on second call.
        assert get_default_store() is store

    def test_set_default_store_rejects_invalid_types(self):
        with pytest.raises(TypeError):
            set_default_store("not a store")  # type: ignore[arg-type]

    def test_set_default_store_swaps(self):
        _reset_default_store_for_tests()
        new_store = InMemoryUploadState()
        set_default_store(new_store)
        assert get_default_store() is new_store


# ===========================================================================
# ResumableUploadWriter
# ===========================================================================


class TestResumableUploadWriterConstruction:
    def test_requires_inner_writer_cls(self):
        store = InMemoryUploadState()
        with pytest.raises(RuntimeError, match="with_inner"):
            ResumableUploadWriter(
                upload_id="u1",
                filename="x.bin",
                content_type="application/octet-stream",
                state_store=store,
            )

    def test_with_inner_binds_class(self):
        store = InMemoryUploadState()
        Bound = ResumableUploadWriter.with_inner(
            RecordingInnerWriter, state_store=store, ttl_hours=2.0
        )
        w = Bound(
            upload_id="u1",
            filename="x.bin",
            content_type="application/octet-stream",
        )
        assert isinstance(w, ResumableUploadWriter)
        assert w.state_store is store
        # ttl_hours=2 → 7200s
        assert w.ttl == 7200
        assert Bound.__name__ == "ResumableRecordingInnerWriter"

    def test_with_inner_per_upload_overrides(self):
        default_store = InMemoryUploadState()
        override_store = InMemoryUploadState()
        Bound = ResumableUploadWriter.with_inner(
            RecordingInnerWriter, state_store=default_store, ttl_hours=1.0
        )
        w = Bound(
            upload_id="u1",
            filename="x.bin",
            content_type="application/octet-stream",
            state_store=override_store,
            ttl_hours=0.5,
        )
        assert w.state_store is override_store
        assert w.ttl == 1800

    def test_probe_failure_disables_resume_gracefully(self, caplog):
        """If the state-store probe throws, writer degrades to non-resumable."""

        class BrokenStore:
            def get(self, upload_id):
                raise RuntimeError("redis down")

            def set(self, *a, **k):
                raise RuntimeError("unreachable")

            def update(self, *a, **k):
                raise RuntimeError("unreachable")

            def delete(self, *a, **k):
                pass

        Bound = ResumableUploadWriter.with_inner(RecordingInnerWriter)
        w = Bound(
            upload_id="u1",
            filename="x.bin",
            content_type="application/octet-stream",
            state_store=BrokenStore(),
        )
        assert w._store_available is False


class TestResumableUploadWriterLifecycle:
    def _build(self, store=None):
        store = store or InMemoryUploadState()
        Bound = ResumableUploadWriter.with_inner(RecordingInnerWriter, state_store=store)
        w = Bound(
            upload_id="u1",
            filename="vid.mp4",
            content_type="video/mp4",
            expected_size=1024,
        )
        return w, store

    def test_open_persists_initial_state(self):
        w, store = self._build()
        w.open()
        state = store.get("u1")
        assert state is not None
        assert state["upload_id"] == "u1"
        assert state["filename"] == "vid.mp4"
        assert state["chunks_received_ranges"] == []

    def test_write_chunk_requires_open(self):
        w, _ = self._build()
        with pytest.raises(RuntimeError, match="before open"):
            w.write_chunk(b"x", chunk_index=0)

    def test_write_chunk_persists_progress(self):
        w, store = self._build()
        w.open()
        w.write_chunk(b"a" * 64, chunk_index=0)
        w.write_chunk(b"b" * 64, chunk_index=1)
        state = store.get("u1")
        assert state["chunks_received_ranges"] == [[0, 1]]
        # Inner writer saw both chunks.
        assert len(w._inner.chunks) == 2

    def test_write_chunk_idempotent_replay(self):
        w, store = self._build()
        w.open()
        w.write_chunk(b"a" * 64, chunk_index=0)
        # Replay same index — inner writer should NOT see it twice.
        w.write_chunk(b"a" * 64, chunk_index=0)
        assert len(w._inner.chunks) == 1
        state = store.get("u1")
        assert state["chunks_received_ranges"] == [[0, 0]]

    def test_write_chunk_inner_failure_does_not_mark_received(self):
        w, store = self._build()
        w.open()

        def fail(chunk, chunk_index=0):
            raise IOError("disk full")

        w._inner.write_chunk = fail
        with pytest.raises(IOError):
            w.write_chunk(b"a" * 64, chunk_index=0)
        # chunk_index 0 should NOT be marked received.
        state = store.get("u1")
        assert state["chunks_received_ranges"] == []

    def test_close_deletes_state_and_returns_inner_result(self):
        w, store = self._build()
        w.open()
        w.write_chunk(b"a" * 64, chunk_index=0)
        result = w.close()
        assert result == {"ok": True}
        assert store.get("u1") is None
        assert w._inner.closed

    def test_abort_deletes_state(self):
        w, store = self._build()
        w.open()
        w.write_chunk(b"a" * 64, chunk_index=0)
        w.abort(ValueError("cancelled"))
        assert store.get("u1") is None
        assert isinstance(w._inner.aborted_with, ValueError)

    def test_snapshot_for_resume(self):
        w, _ = self._build()
        w.open()
        w.write_chunk(b"x" * 64, chunk_index=0)
        snap = w.snapshot_for_resume()
        assert snap["upload_id"] == "u1"
        assert snap["chunks_received_ranges"] == [[0, 0]]

    def test_snapshot_returns_empty_when_store_unavailable(self):
        w, _ = self._build()
        w._store_available = False
        assert w.snapshot_for_resume() == {}

    def test_persist_handles_too_large_entry(self, caplog):
        """State store raising UploadStateTooLarge disables resume, doesn't crash."""
        store = MagicMock(spec=UploadStateStore)
        store.get.return_value = None
        store.set.side_effect = UploadStateTooLarge("too big")
        Bound = ResumableUploadWriter.with_inner(RecordingInnerWriter, state_store=store)
        w = Bound(
            upload_id="u1",
            filename="x",
            content_type="application/octet-stream",
        )
        # Open triggers _persist_initial_state which hits the set()
        # exception path — must not propagate.
        w.open()
        assert w._store_available is False

    def test_persist_chunk_handles_too_large(self):
        store = InMemoryUploadState()
        Bound = ResumableUploadWriter.with_inner(RecordingInnerWriter, state_store=store)
        w = Bound(
            upload_id="u1",
            filename="x",
            content_type="application/octet-stream",
        )
        w.open()
        # Patch update to blow up on second call.
        real_update = store.update

        def fake_update(uid, partial):
            raise UploadStateTooLarge("simulated")

        store.update = fake_update  # type: ignore[method-assign]
        w.write_chunk(b"x" * 64, chunk_index=0)
        assert w._store_available is False
        # restore so teardown works
        store.update = real_update  # type: ignore[method-assign]

    def test_persist_chunk_reseeds_when_entry_vanished(self):
        store = InMemoryUploadState()
        Bound = ResumableUploadWriter.with_inner(RecordingInnerWriter, state_store=store)
        w = Bound(
            upload_id="u1",
            filename="x",
            content_type="application/octet-stream",
        )
        w.open()
        # Simulate TTL expiry mid-upload.
        store.delete("u1")
        w.write_chunk(b"x" * 64, chunk_index=0)
        # Writer should have re-seeded state.
        assert store.get("u1") is not None

    def test_persist_chunk_handles_generic_error(self, caplog):
        store = MagicMock(spec=UploadStateStore)
        store.get.return_value = None
        store.set.return_value = None
        store.update.side_effect = RuntimeError("redis blew up")
        Bound = ResumableUploadWriter.with_inner(RecordingInnerWriter, state_store=store)
        w = Bound(
            upload_id="u1",
            filename="x",
            content_type="application/octet-stream",
        )
        w.open()
        w.write_chunk(b"x" * 64, chunk_index=0)
        assert w._store_available is False

    def test_delete_state_entry_swallows_errors(self, caplog):
        store = MagicMock(spec=UploadStateStore)
        store.get.return_value = None
        store.set.return_value = None
        store.delete.side_effect = RuntimeError("redis gone")
        Bound = ResumableUploadWriter.with_inner(RecordingInnerWriter, state_store=store)
        w = Bound(
            upload_id="u1",
            filename="x",
            content_type="application/octet-stream",
        )
        w.open()
        # Should not raise.
        w._delete_state_entry()


# ===========================================================================
# resolve_resume_request
# ===========================================================================


class TestResolveResumeRequest:
    def test_not_found_when_entry_missing(self):
        store = InMemoryUploadState()
        result = resolve_resume_request("u-missing", session_key="s1", store=store)
        assert result["status"] == "not_found"
        assert result["ref"] == "u-missing"

    def test_session_mismatch_returns_not_found(self):
        store = InMemoryUploadState()
        store.set("u1", {"session_key": "owner", "bytes_received": 100}, ttl=60)
        result = resolve_resume_request("u1", session_key="stranger", store=store)
        assert result["status"] == "not_found"

    def test_session_match_returns_resumed(self):
        store = InMemoryUploadState()
        store.set(
            "u1",
            {
                "session_key": "owner",
                "bytes_received": 128,
                "chunks_received_ranges": [[0, 1]],
            },
            ttl=60,
        )
        result = resolve_resume_request("u1", session_key="owner", store=store)
        assert result["status"] == "resumed"
        assert result["bytes_received"] == 128
        assert result["chunks_received"] == [0, 1]

    def test_active_refs_locks_concurrent_session(self):
        store = InMemoryUploadState()
        store.set(
            "u1",
            {
                "session_key": "owner",
                "bytes_received": 128,
                "chunks_received_ranges": [[0, 1]],
            },
            ttl=60,
        )
        result = resolve_resume_request(
            "u1", session_key="owner", store=store, active_refs=lambda _uid: True
        )
        assert result["status"] == "locked"

    def test_store_read_failure_returns_not_found(self, caplog):
        broken = MagicMock(spec=UploadStateStore)
        broken.get.side_effect = RuntimeError("store offline")
        result = resolve_resume_request("u1", session_key="owner", store=broken)
        assert result["status"] == "not_found"

    def test_no_session_key_stored_accepts_any_session(self):
        store = InMemoryUploadState()
        store.set(
            "u1",
            {
                "session_key": None,
                "bytes_received": 64,
                "chunks_received_ranges": [[0, 0]],
            },
            ttl=60,
        )
        result = resolve_resume_request("u1", session_key="whoever", store=store)
        assert result["status"] == "resumed"

    def test_default_store_used_when_none(self):
        _reset_default_store_for_tests()
        # Seed the default store directly.
        get_default_store().set(
            "u1",
            {"session_key": "s", "bytes_received": 0, "chunks_received_ranges": []},
            ttl=60,
        )
        result = resolve_resume_request("u1", session_key="s")
        assert result["status"] == "resumed"
        _reset_default_store_for_tests()


# ===========================================================================
# UploadStatusView
# ===========================================================================


class _FakeSession:
    """Lightweight session stand-in that never hits the DB."""

    def __init__(self, session_key):
        self.session_key = session_key


class TestUploadStatusView:
    def _make_request(self, with_session=True, session_key="s-abc"):
        rf = RequestFactory()
        req = rf.get("/djust/uploads/x/status")
        if with_session:
            # Avoid Django's DB-backed session machinery; the view only
            # reads ``request.session.session_key``.
            req.session = _FakeSession(session_key)  # type: ignore[attr-defined]
        return req

    def test_malformed_uuid_returns_404(self):
        view = UploadStatusView.as_view()
        req = self._make_request()
        resp = view(req, upload_id="not-a-uuid")
        assert resp.status_code == 404
        assert json.loads(resp.content)["status"] == "not_found"

    def test_anonymous_client_returns_404(self):
        _reset_default_store_for_tests()
        view = UploadStatusView.as_view()
        # Request with no session attribute.
        rf = RequestFactory()
        req = rf.get("/djust/uploads/x/status")
        # Stripped-session request — no .session attribute.
        resp = view(req, upload_id="12345678-1234-1234-1234-123456789abc")
        assert resp.status_code == 404

    def test_missing_session_key_returns_404(self):
        view = UploadStatusView.as_view()
        req = self._make_request(session_key=None)
        resp = view(req, upload_id="12345678-1234-1234-1234-123456789abc")
        assert resp.status_code == 404

    def test_entry_missing_returns_404(self):
        _reset_default_store_for_tests()
        view = UploadStatusView.as_view()
        req = self._make_request(session_key="s1")
        resp = view(req, upload_id="12345678-1234-1234-1234-123456789abc")
        assert resp.status_code == 404

    def test_session_mismatch_returns_404(self):
        _reset_default_store_for_tests()
        store = get_default_store()
        uid = "12345678-1234-1234-1234-123456789abc"
        store.set(uid, {"upload_id": uid, "session_key": "owner"}, ttl=60)
        view = UploadStatusView.as_view()
        req = self._make_request(session_key="stranger")
        resp = view(req, upload_id=uid)
        assert resp.status_code == 404

    def test_happy_path_returns_status(self):
        _reset_default_store_for_tests()
        store = get_default_store()
        uid = "12345678-1234-1234-1234-123456789abc"
        store.set(
            uid,
            {
                "upload_id": uid,
                "session_key": "s1",
                "bytes_received": 512,
                "chunks_received_ranges": [[0, 1]],
                "filename": "video.mp4",
                "expected_size": 2048,
            },
            ttl=60,
        )
        view = UploadStatusView.as_view()
        req = self._make_request(session_key="s1")
        resp = view(req, upload_id=uid)
        assert resp.status_code == 200
        payload = json.loads(resp.content)
        assert payload["upload_id"] == uid
        assert payload["status"] == "uploading"
        assert payload["bytes_received"] == 512
        assert payload["chunks_received"] == [0, 1]
        assert payload["filename"] == "video.mp4"
        assert payload["expected_size"] == 2048
        _reset_default_store_for_tests()

    def test_store_read_failure_returns_404_and_logs(self, caplog):
        view_inst = UploadStatusView()
        broken = MagicMock(spec=UploadStateStore)
        broken.get.side_effect = RuntimeError("store offline")
        view_inst.state_store = broken
        req = self._make_request(session_key="s1")
        resp = view_inst.get(req, upload_id="12345678-1234-1234-1234-123456789abc")
        assert resp.status_code == 404

    def test_urlpatterns_helper(self):
        patterns = upload_status_urlpatterns()
        assert len(patterns) == 1
        # With custom prefix.
        custom = upload_status_urlpatterns(prefix="api/ups/")
        assert len(custom) == 1

    def test_custom_store_on_view_subclass(self):
        uid = "12345678-1234-1234-1234-123456789abc"
        store = InMemoryUploadState()
        store.set(uid, {"upload_id": uid, "session_key": "s1"}, ttl=60)

        class CustomView(UploadStatusView):
            state_store = store

        req = self._make_request(session_key="s1")
        resp = CustomView.as_view()(req, upload_id=uid)
        assert resp.status_code == 200
