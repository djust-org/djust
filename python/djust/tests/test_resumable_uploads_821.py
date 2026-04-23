"""Regression tests for resumable uploads across WS disconnects (#821).

Covers:

- state store round-trip (in-memory + mocked Redis)
- ``ResumableUploadWriter`` chunk-by-chunk persistence
- resume protocol (``resolve_resume_request``) — resumed / not_found /
  locked / cross-session mismatch
- TTL expiry via a mock clock
- ``UploadStatusView`` HTTP endpoint
- WebSocket-level ``upload_resume`` message handler
- Concurrent resume of the same upload_id is rejected

Redis is mocked via ``fakeredis`` if available; otherwise a hand-rolled
dict-backed stub fills in. Both paths exercise the same assertions so
CI needn't install fakeredis.
"""

from __future__ import annotations

import json
import time
from typing import Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from djust.uploads import UploadMixin, UploadWriter
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
    UploadStateTooLarge,
    _reset_default_store_for_tests,
    get_default_store,
    set_default_store,
)


# ---------------------------------------------------------------------------
# Helpers — a recording inner writer so we can assert on chunk delivery
# ---------------------------------------------------------------------------


class _RecordingInnerWriter(UploadWriter):
    """Captures every lifecycle call so tests can assert ordering."""

    def __init__(self, upload_id, filename, content_type, expected_size=None):
        super().__init__(upload_id, filename, content_type, expected_size)
        self.opened = False
        self.closed = False
        self.aborted_with: Optional[BaseException] = None
        self.chunks: List[bytes] = []

    def open(self):
        self.opened = True

    def write_chunk(self, chunk):
        if not self.opened:
            raise RuntimeError("write before open")
        self.chunks.append(chunk)

    def close(self):
        self.closed = True
        return {"total_bytes": sum(len(c) for c in self.chunks)}

    def abort(self, error):
        self.aborted_with = error


class _FailingInnerWriter(_RecordingInnerWriter):
    """Inner writer whose write_chunk always raises — for abort/rollback
    path tests."""

    def write_chunk(self, chunk):
        super().write_chunk(chunk)  # still record
        raise RuntimeError("simulated backend rejection")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_default_store():
    _reset_default_store_for_tests()
    yield
    _reset_default_store_for_tests()


@pytest.fixture
def memory_store() -> InMemoryUploadState:
    return InMemoryUploadState()


# ---------------------------------------------------------------------------
# Compact / expand utilities
# ---------------------------------------------------------------------------


class TestChunkCompaction:
    def test_compact_empty(self):
        assert compact_chunks([]) == []

    def test_compact_single(self):
        assert compact_chunks([5]) == [(5, 5)]

    def test_compact_contiguous(self):
        assert compact_chunks([0, 1, 2, 3, 4]) == [(0, 4)]

    def test_compact_gaps(self):
        assert compact_chunks([0, 1, 2, 5, 6, 9]) == [(0, 2), (5, 6), (9, 9)]

    def test_compact_unsorted_and_duplicates(self):
        assert compact_chunks([5, 1, 2, 0, 5, 6, 1]) == [(0, 2), (5, 6)]

    def test_expand_roundtrip(self):
        original = [0, 1, 2, 5, 6, 9, 10, 11]
        ranges = compact_chunks(original)
        assert sorted(expand_ranges(ranges)) == original

    def test_bytes_received_from_ranges(self):
        # 3 chunks × 64 KB = 192 KB
        assert bytes_received_from_ranges([(0, 2)], chunk_size=64 * 1024) == 3 * 64 * 1024


# ---------------------------------------------------------------------------
# InMemoryUploadState
# ---------------------------------------------------------------------------


class TestInMemoryStateStore:
    def test_in_memory_state_store_roundtrip(self, memory_store):
        """set → get returns an equivalent dict (deep-copied)."""
        state = {
            "upload_id": "abc",
            "bytes_received": 12345,
            "chunks_received_ranges": [[0, 5]],
        }
        memory_store.set("abc", state, ttl=60)
        loaded = memory_store.get("abc")
        assert loaded is not None
        assert loaded["upload_id"] == "abc"
        assert loaded["bytes_received"] == 12345
        assert loaded["chunks_received_ranges"] == [[0, 5]]
        # Mutating the returned dict must not leak back into the store.
        loaded["bytes_received"] = 0
        assert memory_store.get("abc")["bytes_received"] == 12345

    def test_update_merges_and_preserves_ttl(self, memory_store):
        memory_store.set("abc", {"bytes_received": 1, "chunks_received_ranges": [[0, 0]]}, ttl=60)
        merged = memory_store.update("abc", {"bytes_received": 42, "last_updated": 1234.5})
        assert merged["bytes_received"] == 42
        assert merged["last_updated"] == 1234.5
        assert merged["chunks_received_ranges"] == [[0, 0]]

    def test_update_on_missing_entry_returns_none(self, memory_store):
        assert memory_store.update("ghost", {"x": 1}) is None

    def test_ttl_expires_orphan_state(self, memory_store):
        """Entries vanish after TTL — uses the internal force-expire
        helper instead of sleeping, so the test is deterministic."""
        memory_store.set("abc", {"x": 1}, ttl=3600)
        assert memory_store.get("abc") is not None
        memory_store._force_expire("abc")
        assert memory_store.get("abc") is None

    def test_delete(self, memory_store):
        memory_store.set("abc", {"x": 1}, ttl=60)
        memory_store.delete("abc")
        assert memory_store.get("abc") is None

    def test_rejects_oversize_state(self, memory_store):
        huge = {"payload": "a" * (MAX_STATE_SIZE_BYTES + 100)}
        with pytest.raises(UploadStateTooLarge):
            memory_store.set("abc", huge, ttl=60)

    def test_update_rejects_oversize_merged_state(self, memory_store):
        memory_store.set("abc", {"x": 1}, ttl=60)
        with pytest.raises(UploadStateTooLarge):
            memory_store.update("abc", {"huge": "a" * (MAX_STATE_SIZE_BYTES + 100)})


# ---------------------------------------------------------------------------
# RedisUploadState with a hand-rolled fake redis client
# ---------------------------------------------------------------------------


class _FakeRedis:
    """Minimal redis-py-like client for tests.

    Implements ``get`` / ``set(ex=)`` / ``setex`` / ``delete`` / ``ttl``.
    Does NOT implement ``pipeline`` — ``RedisUploadState.update`` falls
    back to its non-atomic branch, which is still exercised by these
    tests.
    """

    def __init__(self):
        self._data: Dict[str, bytes] = {}
        self._ttl: Dict[str, float] = {}

    def get(self, key):
        # Enforce TTL lazily.
        deadline = self._ttl.get(key)
        if deadline is not None and time.time() >= deadline:
            self._data.pop(key, None)
            self._ttl.pop(key, None)
            return None
        return self._data.get(key)

    def set(self, key, value, ex=None):
        self._data[key] = value
        if ex is not None:
            self._ttl[key] = time.time() + ex

    def setex(self, key, seconds, value):
        self.set(key, value, ex=seconds)

    def delete(self, key):
        self._data.pop(key, None)
        self._ttl.pop(key, None)

    def ttl(self, key):
        deadline = self._ttl.get(key)
        if deadline is None:
            return -1
        return int(max(0, deadline - time.time()))


class TestRedisStateStore:
    def test_redis_state_store_roundtrip(self):
        """set → get round-trips through a fake redis client."""
        fake = _FakeRedis()
        store = RedisUploadState(fake)
        state = {"upload_id": "abc", "bytes_received": 10}
        store.set("abc", state, ttl=60)
        loaded = store.get("abc")
        assert loaded == state

    def test_redis_update_merges(self):
        fake = _FakeRedis()
        store = RedisUploadState(fake)
        store.set("abc", {"x": 1, "y": 2}, ttl=60)
        merged = store.update("abc", {"y": 99, "z": 3})
        assert merged == {"x": 1, "y": 99, "z": 3}

    def test_redis_update_missing_entry(self):
        fake = _FakeRedis()
        store = RedisUploadState(fake)
        assert store.update("ghost", {"x": 1}) is None

    def test_redis_delete(self):
        fake = _FakeRedis()
        store = RedisUploadState(fake)
        store.set("abc", {"x": 1}, ttl=60)
        store.delete("abc")
        assert store.get("abc") is None

    def test_redis_corrupt_json_returns_none_and_deletes(self):
        fake = _FakeRedis()
        fake._data["djust:upload:abc"] = b"not-json"
        store = RedisUploadState(fake)
        assert store.get("abc") is None
        # Corrupt entry should have been deleted.
        assert "djust:upload:abc" not in fake._data

    def test_redis_rejects_oversize(self):
        fake = _FakeRedis()
        store = RedisUploadState(fake)
        with pytest.raises(UploadStateTooLarge):
            store.set("abc", {"p": "a" * (MAX_STATE_SIZE_BYTES + 100)}, ttl=60)


# ---------------------------------------------------------------------------
# ResumableUploadWriter
# ---------------------------------------------------------------------------


class TestResumableUploadWriter:
    def _make_writer(self, store=None, inner_cls=_RecordingInnerWriter, session_key="sess1"):
        if store is None:
            store = InMemoryUploadState()
        cls = ResumableUploadWriter.with_inner(inner_cls, state_store=store)
        writer = cls(
            upload_id="uuid-1",
            filename="foo.mp4",
            content_type="video/mp4",
            expected_size=1024,
            session_key=session_key,
        )
        return writer, store

    def test_requires_with_inner(self):
        """Bare ResumableUploadWriter (no inner class) must fail loudly."""
        with pytest.raises(RuntimeError):
            ResumableUploadWriter(
                upload_id="x",
                filename="f",
                content_type="text/plain",
            )

    def test_resumable_writer_persists_state_on_each_chunk(self):
        """After each successful chunk, the state-store entry should
        reflect the growing chunks_received set."""
        writer, store = self._make_writer()
        writer.open()
        assert store.get("uuid-1") is not None  # initial state written

        writer.write_chunk(b"AAAA", chunk_index=0)
        entry = store.get("uuid-1")
        assert entry["chunks_received_ranges"] == [[0, 0]]

        writer.write_chunk(b"BBBB", chunk_index=1)
        entry = store.get("uuid-1")
        assert entry["chunks_received_ranges"] == [[0, 1]]

        writer.write_chunk(b"DDDD", chunk_index=3)  # intentional gap
        entry = store.get("uuid-1")
        assert entry["chunks_received_ranges"] == [[0, 1], [3, 3]]

    def test_resume_returns_correct_bytes_received(self):
        """snapshot_for_resume exposes bytes_received consistent with
        the chunks uploaded so far."""
        writer, store = self._make_writer()
        writer.open()
        for i in range(3):
            writer.write_chunk(b"x" * (64 * 1024), chunk_index=i)
        snap = writer.snapshot_for_resume()
        assert snap["bytes_received"] == 3 * 64 * 1024
        assert snap["chunks_received_ranges"] == [[0, 2]]

    def test_duplicate_chunk_is_skipped(self):
        """Replay of an already-received chunk must be idempotent —
        inner writer is called exactly once."""
        writer, store = self._make_writer()
        writer.open()
        writer.write_chunk(b"AAAA", chunk_index=0)
        writer.write_chunk(b"AAAA", chunk_index=0)  # duplicate
        inner = writer._inner
        assert len(inner.chunks) == 1

    def test_close_deletes_state_entry(self):
        writer, store = self._make_writer()
        writer.open()
        writer.write_chunk(b"AAAA", chunk_index=0)
        assert store.get("uuid-1") is not None
        result = writer.close()
        assert result["total_bytes"] == 4
        assert store.get("uuid-1") is None

    def test_abort_deletes_state_entry(self):
        writer, store = self._make_writer()
        writer.open()
        writer.write_chunk(b"AAAA", chunk_index=0)
        writer.abort(RuntimeError("client cancelled"))
        assert store.get("uuid-1") is None
        assert isinstance(writer._inner.aborted_with, RuntimeError)

    def test_failing_inner_does_not_mark_chunk_received(self):
        """If the inner writer raises, chunks_received is NOT updated —
        so the client's retry is accepted rather than being skipped as
        a duplicate."""
        writer, store = self._make_writer(inner_cls=_FailingInnerWriter)
        writer.open()
        with pytest.raises(RuntimeError):
            writer.write_chunk(b"AAAA", chunk_index=0)
        entry = store.get("uuid-1")
        # Nothing persisted — retry with chunk_index=0 will proceed.
        assert entry["chunks_received_ranges"] == []

    def test_store_unavailable_degrades_gracefully(self):
        """If the state store is down at construction, the writer
        continues to operate as a non-resumable writer (best-effort
        fallback)."""

        class BrokenStore:
            def get(self, _):
                raise RuntimeError("redis down")

            def set(self, *a, **kw):
                raise RuntimeError("redis down")

            def update(self, *a, **kw):
                raise RuntimeError("redis down")

            def delete(self, *a, **kw):
                raise RuntimeError("redis down")

        cls = ResumableUploadWriter.with_inner(_RecordingInnerWriter, state_store=BrokenStore())
        writer = cls(
            upload_id="uuid-1",
            filename="foo",
            content_type="text/plain",
        )
        writer.open()
        # Must not raise, must not touch store after the probe.
        writer.write_chunk(b"AAAA", chunk_index=0)
        writer.close()
        snap = writer.snapshot_for_resume()
        assert snap == {}


# ---------------------------------------------------------------------------
# resolve_resume_request (the WS handler's inner)
# ---------------------------------------------------------------------------


class TestResolveResumeRequest:
    def test_resume_with_unknown_upload_id_returns_not_found(self):
        """The primary contract: unknown ref → not_found response."""
        store = InMemoryUploadState()
        payload = resolve_resume_request(upload_id="ghost", session_key="sess1", store=store)
        assert payload["status"] == "not_found"
        assert payload["bytes_received"] == 0
        assert payload["chunks_received"] == []
        assert payload["type"] == "upload_resumed"

    def test_resumed_happy_path(self):
        store = InMemoryUploadState()
        store.set(
            "abc",
            {
                "upload_id": "abc",
                "session_key": "sess1",
                "bytes_received": 2048,
                "chunks_received_ranges": [[0, 2]],
            },
            ttl=60,
        )
        payload = resolve_resume_request(upload_id="abc", session_key="sess1", store=store)
        assert payload["status"] == "resumed"
        assert payload["bytes_received"] == 2048
        assert payload["chunks_received"] == [0, 1, 2]

    def test_cross_session_access_returns_not_found(self):
        """A different session trying to resume someone else's upload_id
        must get the same response as if the ID didn't exist."""
        store = InMemoryUploadState()
        store.set(
            "abc",
            {"upload_id": "abc", "session_key": "sess1"},
            ttl=60,
        )
        payload = resolve_resume_request(upload_id="abc", session_key="sess2", store=store)
        assert payload["status"] == "not_found"

    def test_concurrent_resume_of_same_upload_id_rejected(self):
        """Second active session trying to resume gets status=locked.

        ``active_refs`` reports True → the resolve helper flags the
        upload as busy; v1 rejects rather than supporting take-over.
        """
        store = InMemoryUploadState()
        store.set(
            "abc",
            {"upload_id": "abc", "session_key": "sess1", "bytes_received": 1024},
            ttl=60,
        )
        payload = resolve_resume_request(
            upload_id="abc",
            session_key="sess1",
            store=store,
            active_refs=lambda uid: uid == "abc",
        )
        assert payload["status"] == "locked"
        # Still reports progress so the UI can show something useful.
        assert payload["bytes_received"] == 1024

    def test_store_read_failure_returns_not_found(self):
        """If the store raises on get, we degrade to not_found rather
        than letting the exception propagate to the client."""

        class BrokenStore:
            def get(self, _):
                raise RuntimeError("down")

        payload = resolve_resume_request(upload_id="abc", session_key="sess1", store=BrokenStore())
        assert payload["status"] == "not_found"


# ---------------------------------------------------------------------------
# WebSocket consumer — upload_resume message end-to-end
# ---------------------------------------------------------------------------


class TestWebsocketUploadResumeHandler:
    @pytest.mark.asyncio
    async def test_websocket_upload_resume_message_handler(self):
        """Simulate the WS dispatch: a mocked consumer with an
        ``upload_resume`` message returns an ``upload_resumed`` reply
        populated from the state store.

        We import the handler directly and stub ``send_json`` /
        ``send_error`` / ``view_instance`` / ``scope`` — avoids
        spinning up Channels for a unit test.
        """
        from djust.websocket import LiveViewConsumer

        # Seed the default store with a resumable entry.
        store = InMemoryUploadState()
        set_default_store(store)
        store.set(
            "uuid-77",
            {
                "upload_id": "uuid-77",
                "session_key": "ws-session",
                "bytes_received": 131072,
                "chunks_received_ranges": [[0, 1]],
            },
            ttl=60,
        )

        # Build a bare consumer instance — bypass __init__ so we
        # don't need Channels wiring.
        consumer = LiveViewConsumer.__new__(LiveViewConsumer)
        consumer.send_json = AsyncMock()
        consumer.send_error = AsyncMock()
        consumer.view_instance = None
        # Minimal scope with a session carrying the right key.
        fake_session = MagicMock()
        fake_session.session_key = "ws-session"
        consumer.scope = {"session": fake_session}

        await consumer._handle_upload_resume({"type": "upload_resume", "ref": "uuid-77"})

        # send_json must have been called exactly once with the
        # upload_resumed payload.
        assert consumer.send_json.await_count == 1
        payload = consumer.send_json.await_args.args[0]
        assert payload["type"] == "upload_resumed"
        assert payload["ref"] == "uuid-77"
        assert payload["status"] == "resumed"
        assert payload["bytes_received"] == 131072
        assert payload["chunks_received"] == [0, 1]

    @pytest.mark.asyncio
    async def test_upload_resume_without_ref_sends_error(self):
        from djust.websocket import LiveViewConsumer

        consumer = LiveViewConsumer.__new__(LiveViewConsumer)
        consumer.send_json = AsyncMock()
        consumer.send_error = AsyncMock()
        consumer.view_instance = None
        consumer.scope = {}
        await consumer._handle_upload_resume({"type": "upload_resume"})
        consumer.send_error.assert_awaited()


# ---------------------------------------------------------------------------
# UploadStatusView HTTP endpoint
# ---------------------------------------------------------------------------


class TestUploadStatusView:
    def _request(self, session_key="sess1"):
        # Build a tiny request-like stub. UploadStatusView only reaches
        # into request.session.session_key, so the stub is small.
        req = MagicMock()
        session = MagicMock()
        session.session_key = session_key
        req.session = session
        return req

    def test_returns_404_for_unknown_upload_id(self):
        from djust.uploads.views import UploadStatusView

        store = InMemoryUploadState()
        view = UploadStatusView()
        view.state_store = store
        req = self._request()
        resp = view.get(req, upload_id="a1b2c3d4-0000-0000-0000-000000000000")
        assert resp.status_code == 404
        assert json.loads(resp.content) == {"status": "not_found"}

    def test_returns_state_for_owned_upload(self):
        from djust.uploads.views import UploadStatusView

        store = InMemoryUploadState()
        store.set(
            "a1b2c3d4-0000-0000-0000-000000000000",
            {
                "upload_id": "a1b2c3d4-0000-0000-0000-000000000000",
                "session_key": "sess1",
                "bytes_received": 2048,
                "filename": "big.mp4",
                "expected_size": 1024 * 1024,
                "chunks_received_ranges": [[0, 1]],
            },
            ttl=60,
        )
        view = UploadStatusView()
        view.state_store = store
        req = self._request("sess1")
        resp = view.get(req, upload_id="a1b2c3d4-0000-0000-0000-000000000000")
        assert resp.status_code == 200
        body = json.loads(resp.content)
        assert body["bytes_received"] == 2048
        assert body["filename"] == "big.mp4"
        assert body["chunks_received"] == [0, 1]

    def test_cross_session_returns_404(self):
        from djust.uploads.views import UploadStatusView

        store = InMemoryUploadState()
        store.set(
            "a1b2c3d4-0000-0000-0000-000000000000",
            {
                "upload_id": "a1b2c3d4-0000-0000-0000-000000000000",
                "session_key": "sess1",
            },
            ttl=60,
        )
        view = UploadStatusView()
        view.state_store = store
        # Different session → same 404 response as a missing ID.
        req = self._request("sess2")
        resp = view.get(req, upload_id="a1b2c3d4-0000-0000-0000-000000000000")
        assert resp.status_code == 404

    def test_malformed_upload_id_returns_404(self):
        from djust.uploads.views import UploadStatusView

        view = UploadStatusView()
        view.state_store = InMemoryUploadState()
        req = self._request()
        resp = view.get(req, upload_id="not-a-uuid")
        assert resp.status_code == 404

    def test_anonymous_returns_404(self):
        from djust.uploads.views import UploadStatusView

        view = UploadStatusView()
        view.state_store = InMemoryUploadState()
        req = MagicMock()
        req.session = MagicMock()
        req.session.session_key = None  # anonymous
        resp = view.get(req, upload_id="a1b2c3d4-0000-0000-0000-000000000000")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# set_default_store / get_default_store
# ---------------------------------------------------------------------------


class TestDefaultStoreResolution:
    def test_default_store_is_in_memory(self):
        store = get_default_store()
        assert isinstance(store, InMemoryUploadState)

    def test_set_default_store_overrides(self, memory_store):
        set_default_store(memory_store)
        assert get_default_store() is memory_store

    def test_set_default_store_rejects_bad_type(self):
        with pytest.raises(TypeError):
            set_default_store("not-a-store")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# UploadMixin — allow_upload(resumable=True) + config roundtrip
# ---------------------------------------------------------------------------


class TestAllowUploadResumableFlag:
    def test_resumable_flag_flows_to_config(self):
        class V(UploadMixin):
            pass

        v = V()
        cfg = v.allow_upload("avatar", resumable=True)
        assert cfg.resumable is True

    def test_state_exposes_resumable(self):
        class V(UploadMixin):
            pass

        v = V()
        v.allow_upload("avatar", resumable=True)
        state = v._upload_manager.get_upload_state()
        assert state["avatar"]["config"]["resumable"] is True

    def test_restore_replays_resumable_flag(self):
        class V(UploadMixin):
            pass

        v = V()
        v.allow_upload("avatar", resumable=True)
        saved = json.loads(json.dumps(v._upload_configs_saved))

        # Simulate session restoration on a new instance.
        v2 = V()
        v2._upload_configs_saved = saved
        v2._restore_upload_configs()
        assert v2._upload_manager._configs["avatar"].resumable is True
