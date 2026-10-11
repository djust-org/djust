"""Resumable uploads record and enforce their owning session.

The WebSocket ``upload_register`` handler passes the connection's Django
session key to ``UploadManager.register_entry``; the resumable writer stores
it in the upload state. ``upload_resume`` (WebSocket) and ``UploadStatusView``
(HTTP) then answer only for that session, and both treat a state entry with no
recorded session as ``not_found``.

These tests drive the real consumer handlers, the real ``UploadManager`` and
the real ``UploadStatusView`` so the stored value is the one the framework
writes, not a hand-seeded one.
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from djust.uploads import UploadManager, UploadWriter
from djust.uploads.resumable import ResumableUploadWriter, resolve_resume_request
from djust.uploads.storage import (
    InMemoryUploadState,
    _reset_default_store_for_tests,
    get_default_store,
    set_default_store,
)


class _SinkWriter(UploadWriter):
    """Inner writer that accepts and discards chunks."""

    def open(self):
        pass

    def write_chunk(self, chunk):
        pass

    def close(self):
        return {}

    def abort(self, error):
        pass


class _PlainWriter(_SinkWriter):
    """Non-resumable writer with the base constructor signature."""

    constructed_with: dict = {}

    def __init__(self, upload_id, filename, content_type, expected_size=None):
        super().__init__(upload_id, filename, content_type, expected_size)
        type(self).constructed_with = {"upload_id": upload_id}


@pytest.fixture(autouse=True)
def _fresh_store():
    _reset_default_store_for_tests()
    set_default_store(InMemoryUploadState())
    yield
    _reset_default_store_for_tests()


def _consumer(session_key, manager):
    from djust.websocket import LiveViewConsumer

    consumer = LiveViewConsumer.__new__(LiveViewConsumer)
    consumer.send_json = AsyncMock()
    consumer.send_error = AsyncMock()
    view = MagicMock()
    view._upload_manager = manager
    consumer.view_instance = view
    session = MagicMock()
    session.session_key = session_key
    consumer.scope = {"session": session}
    return consumer


def _resumable_manager():
    mgr = UploadManager()
    mgr.configure("doc", writer=ResumableUploadWriter.with_inner(_SinkWriter), resumable=True)
    return mgr


async def _register_and_write(consumer, mgr, ref):
    await consumer._handle_upload_register(
        {
            "type": "upload_register",
            "upload_name": "doc",
            "ref": ref,
            "client_name": "report.pdf",
            "client_type": "application/pdf",
            "client_size": 1_000_000,
        }
    )
    assert consumer.send_json.await_args.args[0]["type"] == "upload_registered"
    mgr.add_chunk(ref, 0, b"A" * 65536)
    mgr.add_chunk(ref, 1, b"B" * 65536)


async def _resume(consumer, ref):
    consumer.send_json.reset_mock()
    await consumer._handle_upload_resume({"type": "upload_resume", "ref": ref})
    return consumer.send_json.await_args.args[0]


def _status_request(session_key):
    req = MagicMock()
    req.session = MagicMock()
    req.session.session_key = session_key
    return req


# ---------------------------------------------------------------------------
# Registration records the owner
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_registration_stores_connection_session_key():
    mgr = _resumable_manager()
    ref = str(uuid.uuid4())
    await _register_and_write(_consumer("owner-sess", mgr), mgr, ref)
    assert get_default_store().get(ref)["session_key"] == "owner-sess"


@pytest.mark.asyncio
async def test_non_resumable_writer_is_built_without_session_key():
    mgr = UploadManager()
    mgr.configure("doc", writer=_PlainWriter)
    ref = str(uuid.uuid4())
    await _register_and_write(_consumer("owner-sess", mgr), mgr, ref)
    assert mgr._entries[ref]._error is None
    assert _PlainWriter.constructed_with == {"upload_id": ref}


# ---------------------------------------------------------------------------
# WebSocket upload_resume
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_owner_can_resume_over_websocket():
    mgr = _resumable_manager()
    ref = str(uuid.uuid4())
    await _register_and_write(_consumer("owner-sess", mgr), mgr, ref)

    # A fresh connection of the same session (after a reconnect) resumes.
    payload = await _resume(_consumer("owner-sess", UploadManager()), ref)
    assert payload["status"] == "resumed"
    assert payload["chunks_received"] == [0, 1]
    assert payload["bytes_received"] == 131072


@pytest.mark.asyncio
@pytest.mark.parametrize("other_session", ["other-sess", None])
async def test_other_session_gets_not_found_over_websocket(other_session):
    mgr = _resumable_manager()
    ref = str(uuid.uuid4())
    await _register_and_write(_consumer("owner-sess", mgr), mgr, ref)

    payload = await _resume(_consumer(other_session, UploadManager()), ref)
    assert payload == {
        "type": "upload_resumed",
        "ref": ref,
        "status": "not_found",
        "bytes_received": 0,
        "chunks_received": [],
    }


@pytest.mark.parametrize("caller", ["any-sess", None])
def test_entry_without_recorded_session_is_not_found(caller):
    store = InMemoryUploadState()
    store.set(
        "u1",
        {"upload_id": "u1", "session_key": None, "chunks_received_ranges": [[0, 3]]},
        ttl=60,
    )
    assert resolve_resume_request("u1", caller, store=store)["status"] == "not_found"


@pytest.mark.asyncio
async def test_upload_registered_without_session_is_not_resumable():
    mgr = _resumable_manager()
    ref = str(uuid.uuid4())
    await _register_and_write(_consumer(None, mgr), mgr, ref)
    assert get_default_store().get(ref)["session_key"] is None
    payload = await _resume(_consumer(None, UploadManager()), ref)
    assert payload["status"] == "not_found"


# ---------------------------------------------------------------------------
# HTTP UploadStatusView
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_owner_can_read_status_over_http():
    from djust.uploads.views import UploadStatusView

    mgr = _resumable_manager()
    ref = str(uuid.uuid4())
    await _register_and_write(_consumer("owner-sess", mgr), mgr, ref)

    resp = UploadStatusView().get(_status_request("owner-sess"), upload_id=ref)
    assert resp.status_code == 200
    body = json.loads(resp.content)
    assert body["upload_id"] == ref
    assert body["chunks_received"] == [0, 1]
    assert body["filename"] == "report.pdf"


@pytest.mark.asyncio
async def test_other_session_gets_404_over_http():
    from djust.uploads.views import UploadStatusView

    mgr = _resumable_manager()
    ref = str(uuid.uuid4())
    await _register_and_write(_consumer("owner-sess", mgr), mgr, ref)

    resp = UploadStatusView().get(_status_request("other-sess"), upload_id=ref)
    assert resp.status_code == 404
    assert json.loads(resp.content) == {"status": "not_found"}
