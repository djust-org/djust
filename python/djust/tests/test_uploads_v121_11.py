"""v1.2.1-11 — offline sync, uploads, PWA command.

* #2957 — handlers registered with ``@register_sync_handler`` (and the
  instance API ``SyncManager.register_sync_handler``) are actually called.
* #2972 — a resumable upload survives a WebSocket drop: the disconnect
  suspends it instead of aborting it, and the reconnect's ``upload_resume``
  re-attaches the live writer so the remaining chunks continue it.
* #2967 — ``manage.py generate_sw`` runs; its version option is
  ``--sw-version``.
"""

from __future__ import annotations

import json
from typing import List, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest
from django.test import RequestFactory

from djust.pwa import sync as pwa_sync
from djust.uploads import UploadManager, UploadWriter
from djust.uploads import resumable
from djust.uploads.resumable import ResumableUploadWriter
from djust.uploads.storage import (
    InMemoryUploadState,
    _reset_default_store_for_tests,
    set_default_store,
)

# ---------------------------------------------------------------------------
# #2957 — PWA sync handlers
# ---------------------------------------------------------------------------


class _User:
    is_authenticated = True


@pytest.fixture
def global_handlers(monkeypatch):
    registry: dict = {}
    monkeypatch.setattr(pwa_sync, "_sync_handlers", registry)
    return registry


def _post(actions):
    request = RequestFactory().post(
        "/sync/", data=json.dumps({"actions": actions}), content_type="application/json"
    )
    request.user = _User()
    return request


def _action(action_type: str, model: str, i: int):
    return {"id": str(i), "type": action_type, "model": model, "data": {"n": i}, "timestamp": 1.0}


class TestRegisteredSyncHandlersRun:
    def test_endpoint_calls_each_registered_handler(self, global_handlers):
        calls: List[tuple] = []

        @pwa_sync.register_sync_handler("Note", "create")
        def create_note(batch):
            calls.append(("create", [a.id for a in batch]))
            return {"processed": len(batch), "failed": 0}

        @pwa_sync.register_sync_handler("Note", "update")
        def update_note(batch):
            calls.append(("update", [a.id for a in batch]))
            return {"processed": len(batch), "failed": 0}

        response = pwa_sync.sync_endpoint_view(
            _post([_action("create", "Note", 1), _action("update", "Note", 2)])
        )
        body = json.loads(response.content)
        assert sorted(calls) == [("create", ["1"]), ("update", ["2"])]
        assert body["success"] is True
        assert body["processed_count"] == 2 and body["failed_count"] == 0

    def test_model_wide_instance_handler_is_used(self):
        manager = pwa_sync.SyncManager()
        seen = []
        manager.register_sync_handler(
            "Task", lambda batch: seen.append(len(batch)) or {"processed": len(batch), "failed": 0}
        )
        result = manager.sync_actions(
            [pwa_sync.OfflineAction(**{**_action("delete", "Task", 3), "status": "pending"})]
        )
        assert seen == [1]
        assert result.success is True

    def test_a_client_model_name_cannot_reach_an_action_handler(self, global_handlers):
        """The model name is client input: ``model="create_Task"`` with
        ``type="delete"`` must not fall back to the ``create_Task`` handler."""
        calls = []

        @pwa_sync.register_sync_handler("Task", "create")
        def create_task(batch):
            calls.append(batch)
            return {"processed": len(batch), "failed": 0}

        pwa_sync.sync_endpoint_view(_post([_action("delete", "create_Task", 5)]))
        assert calls == []

    def test_handler_exception_text_stays_in_the_log(self, global_handlers):
        @pwa_sync.register_sync_handler("Note", "create")
        def boom(batch):
            raise RuntimeError("duplicate key value violates unique constraint secret_idx")

        body = json.loads(
            pwa_sync.sync_endpoint_view(_post([_action("create", "Note", 6)])).content
        )
        assert body["failed_count"] == 1
        assert "secret_idx" not in json.dumps(body)

    def test_instance_api_with_a_full_key_still_serves_that_action(self):
        """``register_sync_handler("create_Task", fn)`` matched on main; it
        must keep working, and must not become a model-wide fallback."""
        manager = pwa_sync.SyncManager()
        seen = []
        manager.register_sync_handler(
            "create_Task",
            lambda b: (
                seen.extend((a.type, a.model) for a in b) or {"processed": len(b), "failed": 0}
            ),
        )
        manager.sync_actions(
            [pwa_sync.OfflineAction(**{**_action("create", "Task", 7), "status": "pending"})]
        )
        manager.sync_actions(
            [pwa_sync.OfflineAction(**{**_action("delete", "create_Task", 8), "status": "pending"})]
        )
        assert seen == [("create", "Task")]

    def test_action_specific_handler_wins_over_model_wide(self):
        manager = pwa_sync.SyncManager()
        manager.register_sync_handler("Task", lambda b: {"processed": 0, "failed": len(b)})
        manager._sync_handlers["create_Task"] = lambda b: {"processed": len(b), "failed": 0}
        result = manager.sync_actions(
            [pwa_sync.OfflineAction(**{**_action("create", "Task", 4), "status": "pending"})]
        )
        assert result.success is True


# ---------------------------------------------------------------------------
# #2972 — resumable uploads across a WebSocket drop
# ---------------------------------------------------------------------------


class _Inner(UploadWriter):
    def __init__(self, upload_id, filename, content_type, expected_size=None):
        super().__init__(upload_id, filename, content_type, expected_size)
        self.chunks: List[bytes] = []
        self.aborted_with: Optional[BaseException] = None
        self.closed = False

    def open(self):
        pass

    def write_chunk(self, chunk):
        self.chunks.append(chunk)

    def close(self):
        self.closed = True
        return {"bytes": sum(len(c) for c in self.chunks)}

    def abort(self, error):
        self.aborted_with = error


@pytest.fixture
def store():
    _reset_default_store_for_tests()
    resumable._reset_suspended_uploads()
    s = InMemoryUploadState()
    set_default_store(s)
    yield s
    resumable._reset_suspended_uploads()
    _reset_default_store_for_tests()


_RESUMABLE = ResumableUploadWriter.with_inner(_Inner)


def _manager() -> UploadManager:
    mgr = UploadManager()
    mgr.configure(
        name="video", max_file_size=1_000_000, writer=_RESUMABLE, resumable=True, accept=".mp4"
    )
    return mgr


def _start(mgr: UploadManager, ref: str = "11111111-2222-3333-4444-555555555555", key="sess-A"):
    entry = mgr.register_entry(
        upload_name="video",
        ref=ref,
        client_name="clip.mp4",
        client_type="video/mp4",
        client_size=12,
        session_key=key,
    )
    assert entry is not None
    assert mgr.add_chunk(ref, 0, b"AAAA") is not None
    assert mgr.add_chunk(ref, 1, b"BBBB") is not None
    return entry


class TestDisconnectSuspendsResumableUploads:
    def test_disconnect_keeps_the_state_and_the_inner_writer(self, store):
        mgr = _manager()
        entry = _start(mgr)
        inner = entry.writer_instance._inner
        mgr.cleanup()  # the WebSocket closed
        state = store.get(entry.ref)
        assert state is not None and state["chunks_received_ranges"] == [[0, 1]]
        assert inner.aborted_with is None

    def test_reconnect_resumes_the_same_writer_to_completion(self, store):
        entry = _start(_old := _manager())
        writer = entry.writer_instance
        _old.cleanup()

        new = _manager()  # the view mounted again on the new connection
        resumed = new.resume_entry(entry.ref, "sess-A")
        assert resumed is entry and resumed.writer_instance is writer
        assert new.add_chunk(entry.ref, 1, b"BBBB") is not None  # replayed: skipped
        assert new.add_chunk(entry.ref, 2, b"CCCC") is not None
        done = new.complete_upload(entry.ref)
        assert done is not None and done.writer_result == {"bytes": 12}
        assert writer._inner.chunks == [b"AAAA", b"BBBB", b"CCCC"]
        assert store.get(entry.ref) is None  # close() deleted the state

    def test_a_view_that_builds_its_writer_in_mount_can_resume(self, store):
        """``with_inner()`` returns a new class per call; a view calling it in
        mount() has a different wrapper class after the reconnect."""
        old = _manager()
        entry = _start(old)
        old.cleanup()
        new = UploadManager()
        new.configure(
            name="video",
            max_file_size=1_000_000,
            writer=ResumableUploadWriter.with_inner(_Inner),
            resumable=True,
            accept=".mp4",
        )
        assert new.resume_entry(entry.ref, "sess-A") is entry

    def test_a_slot_with_another_writer_leaves_it_parked(self, store):
        old = _manager()
        entry = _start(old)
        old.cleanup()

        class _Other(_Inner):
            pass

        other = UploadManager()
        other.configure(
            name="video", max_file_size=1_000_000, writer=ResumableUploadWriter.with_inner(_Other)
        )
        assert other.resume_entry(entry.ref, "sess-A") is None
        assert _manager().resume_entry(entry.ref, "sess-A") is entry

    def test_another_session_cannot_claim_it(self, store):
        mgr = _manager()
        entry = _start(mgr)
        mgr.cleanup()
        assert _manager().resume_entry(entry.ref, "sess-B") is None
        assert _manager().resume_entry(entry.ref, "sess-A") is entry

    def test_another_session_reusing_the_ref_cannot_displace_it(self, store):
        """The ref comes from the client; a second session registering the
        same ref and disconnecting must not abort the owner's parked upload."""
        mine = _manager()
        entry = _start(mine)
        mine.cleanup()
        theirs = _manager()
        _start(theirs, key="sess-B")
        theirs.cleanup()
        assert entry.writer_instance._inner.aborted_with is None
        assert _manager().resume_entry(entry.ref, "sess-A") is entry

    def test_cleanup_sweeps_expired_uploads(self, store, monkeypatch):
        mgr = _manager()
        entry = _start(mgr)
        mgr.cleanup()
        with resumable._suspended_lock:
            key = next(iter(resumable._suspended))
            resumable._suspended[key] = (entry, 0.0)  # window already over
        other = _manager()
        _start(other, ref="dddddddd-2222-3333-4444-555555555555", key=None)
        other.cleanup()  # an unrelated disconnect releases it
        assert isinstance(entry.writer_instance._inner.aborted_with, ConnectionAbortedError)

    def test_explicit_cancel_still_aborts_and_deletes(self, store):
        mgr = _manager()
        entry = _start(mgr)
        inner = entry.writer_instance._inner
        mgr.cancel_upload(entry.ref)
        assert isinstance(inner.aborted_with, ConnectionAbortedError)
        assert store.get(entry.ref) is None

    def test_an_upload_without_an_owner_is_aborted_as_before(self, store):
        mgr = _manager()
        entry = _start(mgr, key=None)
        inner = entry.writer_instance._inner
        mgr.cleanup()
        assert isinstance(inner.aborted_with, ConnectionAbortedError)

    def test_the_cap_aborts_the_oldest(self, store, monkeypatch):
        monkeypatch.setattr(resumable, "MAX_SUSPENDED_UPLOADS", 1)
        first = _manager()
        a = _start(first, ref="aaaaaaaa-2222-3333-4444-555555555555")
        first.cleanup()
        second = _manager()
        b = _start(second, ref="bbbbbbbb-2222-3333-4444-555555555555")
        second.cleanup()
        assert isinstance(a.writer_instance._inner.aborted_with, ConnectionAbortedError)
        assert store.get(a.ref) is None
        assert b.writer_instance._inner.aborted_with is None

    def test_an_expired_window_aborts(self, store, monkeypatch):
        mgr = _manager()
        entry = _start(mgr)
        monkeypatch.setattr(resumable, "SUSPENDED_UPLOAD_WINDOW_SECONDS", 0)
        mgr.cleanup()
        assert _manager().resume_entry(entry.ref, "sess-A") is None
        assert isinstance(entry.writer_instance._inner.aborted_with, ConnectionAbortedError)


class TestUploadResumeMessage:
    async def _resume(self, view, key: str, ref: str):
        from djust.websocket import LiveViewConsumer

        consumer = LiveViewConsumer.__new__(LiveViewConsumer)
        consumer.send_json = AsyncMock()
        consumer.send_error = AsyncMock()
        consumer.view_instance = view
        session = MagicMock()
        session.session_key = key
        consumer.scope = {"session": session}
        await consumer._handle_upload_resume({"type": "upload_resume", "ref": ref})
        return consumer.send_json.await_args.args[0]

    @pytest.mark.asyncio
    async def test_resume_after_a_drop_reattaches(self, store):
        old = _manager()
        entry = _start(old)
        old.cleanup()
        view = MagicMock()
        view.exposure_policy = "legacy"  # a bare MagicMock reads as nonlegacy (ADR-038)
        view._upload_manager = _manager()
        payload = await self._resume(view, "sess-A", entry.ref)
        assert payload["status"] == "resumed", payload
        assert payload["chunks_received"] == [0, 1]
        assert entry.ref in view._upload_manager._entries

    @pytest.mark.asyncio
    async def test_state_without_a_live_writer_is_not_found(self, store):
        store.set(
            "cccccccc-2222-3333-4444-555555555555",
            {"session_key": "sess-A", "bytes_received": 8, "chunks_received_ranges": [[0, 1]]},
            ttl=60,
        )
        view = MagicMock()
        view.exposure_policy = "legacy"  # a bare MagicMock reads as nonlegacy (ADR-038)
        view._upload_manager = _manager()
        payload = await self._resume(view, "sess-A", "cccccccc-2222-3333-4444-555555555555")
        assert payload["status"] == "not_found"


# ---------------------------------------------------------------------------
# #2967 — generate_sw
# ---------------------------------------------------------------------------


class TestGenerateSw:
    def test_runs_with_sw_version(self, tmp_path, settings):
        from django.core.management import call_command

        settings.STATIC_ROOT = str(tmp_path)
        out = tmp_path / "sw.js"
        call_command("generate_sw", output=str(out), sw_version="2.1.0")
        assert "djust-cache-v2.1.0" in out.read_text()

    def test_parser_builds_and_takes_the_flag(self):
        from djust.management.commands.generate_sw import Command

        parser = Command().create_parser("manage.py", "generate_sw")
        options = parser.parse_args(["--sw-version", "3.0"])
        assert options.sw_version == "3.0"
