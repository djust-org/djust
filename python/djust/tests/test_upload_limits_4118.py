"""#4118 (slice 1): upload limits the browser can hear about.

* ``max_entries`` counts every entry the slot holds, finished ones too, as
  documented (a slot with ``max_entries=5`` used to accept 20 finished uploads).
* An ``UploadRefused`` a writer raises is a refusal: logged at WARNING without
  a traceback, and its own message (only that) reaches the browser. Any other
  writer exception, the contrib backends' ``UploadError`` faults included,
  keeps the fixed text and the logged traceback.
* The view's ``cancel_upload()`` tells the browser (status ``cancelled``).
* ``allow_upload(timeout=)`` cancels an upload still unfinished after that many
  seconds, aborting its writer and telling the browser why.
"""

import asyncio
import logging
import struct
import types
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from asgiref.sync import sync_to_async
from django.test import override_settings
from django.urls import path

from djust import LiveView, event_handler
from djust.testing import open_live_page
from djust.uploads import (
    FRAME_CHUNK,
    FRAME_COMPLETE,
    SERVER_CANCEL_MESSAGE,
    UPLOAD_PROGRESS_PUSH_EVENT,
    UploadManager,
    UploadMixin,
    UploadRefused,
    UploadWriter,
)

SECRET = "arn:aws:iam::123456789012:role/uploader bucket=private-bucket"

#: What the test writers saw, by upload ref: "open", chunks, "close", aborts.
EVENTS: dict = {}


class RecordingWriter(UploadWriter):
    def open(self):
        EVENTS.setdefault(self.upload_id, []).append("open")

    def write_chunk(self, chunk, chunk_index=0):
        EVENTS.setdefault(self.upload_id, []).append(bytes(chunk))

    def close(self):
        EVENTS.setdefault(self.upload_id, []).append("close")
        return {"ok": True}

    def abort(self, error):
        EVENTS.setdefault(self.upload_id, []).append(("abort", type(error).__name__))


class RefusingWriter(RecordingWriter):
    """Refuses every chunk the way a size or content policy would."""

    def write_chunk(self, chunk, chunk_index=0):
        raise UploadRefused(
            "Images must be under 2 MB.", sdk_exc=RuntimeError("quota on %s" % SECRET)
        )


class RefusingOpenWriter(RecordingWriter):
    def open(self):
        raise UploadRefused("Uploads are closed for today.")


class RefusingCloseWriter(RecordingWriter):
    def close(self):
        raise UploadRefused("The image could not be decoded.")


class FaultyWriter(RecordingWriter):
    def write_chunk(self, chunk, chunk_index=0):
        raise RuntimeError("connect to %s failed" % SECRET)


@pytest.fixture(autouse=True)
def _clear_events():
    EVENTS.clear()
    yield
    EVENTS.clear()


def _register(mgr, ref, name="doc", size=10):
    return mgr.register_entry(name, ref, "a.txt", "text/plain", size)


def _finish(mgr, ref, data=b"0123456789"):
    assert mgr.add_chunk(ref, 0, data) is not None
    assert mgr.complete_upload(ref) is not None


# ---------------------------------------------------------------------------
# Item 4: max_entries counts every held entry
# ---------------------------------------------------------------------------


class TestMaxEntriesCountsHeldEntries:
    def test_finished_entries_hold_their_places(self):
        mgr = UploadManager()
        mgr.configure("doc", accept=".txt", max_entries=2)
        for ref in ("r1", "r2"):
            assert _register(mgr, ref) is not None
            _finish(mgr, ref)
        # Both finished, neither consumed: the slot is full.
        assert _register(mgr, "r3") is None
        assert [e.ref for e in mgr.get_entries("doc")] == ["r1", "r2"]

    def test_the_issue_case_twenty_finished_uploads_on_five_places(self):
        mgr = UploadManager()
        mgr.configure("doc", accept=".txt", max_entries=5)
        accepted = 0
        for i in range(20):
            if _register(mgr, "r%d" % i) is not None:
                _finish(mgr, "r%d" % i)
                accepted += 1
        assert accepted == 5

    def test_consuming_or_cancelling_frees_a_place(self):
        mgr = UploadManager()
        mgr.configure("doc", accept=".txt", max_entries=1)
        assert _register(mgr, "r1") is not None
        _finish(mgr, "r1")
        assert _register(mgr, "r2") is None
        assert [e.ref for e in mgr.consume_entries("doc")] == ["r1"]
        assert _register(mgr, "r2") is not None
        mgr.cancel_upload("r2")
        assert _register(mgr, "r3") is not None

    def test_a_failed_entry_does_not_hold_a_place(self):
        mgr = UploadManager()
        mgr.configure("doc", accept=".txt", max_entries=1, max_file_size=4)
        assert _register(mgr, "r1", size=4) is not None
        assert mgr.add_chunk("r1", 0, b"too long") is None  # over max_file_size
        assert mgr.get_entries("doc")[0].error
        # The user can try again without the view clearing the failure first.
        assert _register(mgr, "r2", size=4) is not None


# ---------------------------------------------------------------------------
# Item 3: an UploadError refusal reaches the browser, logged at WARNING
# ---------------------------------------------------------------------------


def _assert_refusal_logged(caplog, message):
    records = [r for r in caplog.records if r.name == "djust.uploads"]
    assert records, "the refusal was not logged"
    assert all(r.levelno == logging.WARNING for r in records), [
        (r.levelname, r.getMessage()) for r in records
    ]
    assert all(r.exc_info is None for r in records), "a refusal is logged without a traceback"
    assert any(message in r.getMessage() for r in records)
    assert not any(SECRET in r.getMessage() for r in records)


class TestWriterRefusal:
    def test_write_chunk_refusal_message_is_the_entry_error(self, caplog):
        mgr = UploadManager()
        mgr.configure("doc", accept=".txt", writer=RefusingWriter)
        _register(mgr, "r1")
        with caplog.at_level(logging.DEBUG, logger="djust.uploads"):
            assert mgr.add_chunk("r1", 0, b"0123") is None
        entry = mgr.get_entries("doc")[0]
        assert entry.error == "Images must be under 2 MB."
        assert SECRET not in entry.error
        assert EVENTS["r1"] == ["open", ("abort", "UploadRefused")]
        _assert_refusal_logged(caplog, "Images must be under 2 MB.")

    def test_open_refusal(self, caplog):
        mgr = UploadManager()
        mgr.configure("doc", accept=".txt", writer=RefusingOpenWriter)
        _register(mgr, "r1")
        with caplog.at_level(logging.DEBUG, logger="djust.uploads"):
            assert mgr.add_chunk("r1", 0, b"0123") is None
        assert mgr.get_entries("doc")[0].error == "Uploads are closed for today."
        _assert_refusal_logged(caplog, "Uploads are closed for today.")

    def test_close_refusal(self, caplog):
        mgr = UploadManager()
        mgr.configure("doc", accept=".txt", writer=RefusingCloseWriter)
        _register(mgr, "r1")
        assert mgr.add_chunk("r1", 0, b"0123") is not None
        with caplog.at_level(logging.WARNING, logger="djust.uploads"):
            assert mgr.complete_upload("r1") is None
        assert mgr.get_entries("doc")[0].error == "The image could not be decoded."
        assert all(r.exc_info is None for r in caplog.records if r.name == "djust.uploads")

    def test_any_other_exception_keeps_the_fixed_text_and_the_traceback(self, caplog):
        mgr = UploadManager()
        mgr.configure("doc", accept=".txt", writer=FaultyWriter)
        _register(mgr, "r1")
        with caplog.at_level(logging.DEBUG, logger="djust.uploads"):
            assert mgr.add_chunk("r1", 0, b"0123") is None
        assert mgr.get_entries("doc")[0].error == "Upload writer rejected chunk"
        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert errors and errors[0].exc_info is not None

    @pytest.mark.parametrize(
        "sdk_exc",
        [
            type("ClientAuthenticationError", (Exception,), {})("token for %s" % SECRET),
            type("HttpResponseError", (Exception,), {"status_code": 403})("denied"),
            type("ServiceRequestError", (Exception,), {})("dns for %s" % SECRET),
        ],
        ids=["credentials", "auth-403", "network"],
    )
    def test_a_contrib_backend_fault_is_not_a_refusal(self, caplog, sdk_exc):
        """The contrib backends raise their infrastructure faults as UploadError
        subclasses ("Azure credentials rejected", ...): those name the vendor
        and stay on the server, logged at ERROR with the traceback."""
        from djust.contrib.uploads.azure import AzureBlockBlobWriter

        fault = AzureBlockBlobWriter._translate_sdk_exc(sdk_exc)

        class BackendWriter(RecordingWriter):
            def write_chunk(self, chunk, chunk_index=0):
                raise fault

        mgr = UploadManager()
        mgr.configure("doc", accept=".txt", writer=BackendWriter)
        _register(mgr, "r1")
        with caplog.at_level(logging.DEBUG, logger="djust.uploads"):
            assert mgr.add_chunk("r1", 0, b"0123") is None
        error = mgr.get_entries("doc")[0].error
        assert error == "Upload writer rejected chunk"
        assert "Azure" not in error and SECRET not in error
        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert errors and errors[0].exc_info is not None
        assert not [r for r in caplog.records if r.levelno == logging.WARNING]

    def test_the_message_is_one_trimmed_line(self):
        class LongRefusal(RecordingWriter):
            def write_chunk(self, chunk, chunk_index=0):
                raise UploadRefused("line one\nline two\r\n" + "x" * 500)

        mgr = UploadManager()
        mgr.configure("doc", accept=".txt", writer=LongRefusal)
        _register(mgr, "r1")
        mgr.add_chunk("r1", 0, b"0123")
        error = mgr.get_entries("doc")[0].error
        assert error.startswith("line one line two xxx")
        assert "\n" not in error and len(error) == 200

    def test_an_empty_message_is_a_fault(self):
        class EmptyRefusal(RecordingWriter):
            def write_chunk(self, chunk, chunk_index=0):
                raise UploadRefused("   ")

        mgr = UploadManager()
        mgr.configure("doc", accept=".txt", writer=EmptyRefusal)
        _register(mgr, "r1")
        mgr.add_chunk("r1", 0, b"0123")
        assert mgr.get_entries("doc")[0].error == "Upload writer rejected chunk"


# ---------------------------------------------------------------------------
# Item 2: allow_upload(timeout=)
# ---------------------------------------------------------------------------


class TestTimeoutManager:
    @pytest.mark.parametrize("bad", [0, -1, "5", True])
    def test_timeout_must_be_a_positive_number(self, bad):
        with pytest.raises(ValueError):
            UploadManager().configure("doc", timeout=bad)

    def test_an_overdue_upload_is_cancelled_and_its_writer_aborted(self):
        mgr = UploadManager()
        mgr.configure("doc", accept=".txt", writer=RecordingWriter, timeout=30)
        entry = _register(mgr, "r1")
        mgr.add_chunk("r1", 0, b"0123")
        assert mgr.expire_overdue(now=entry._started + 29) == []
        assert mgr.expire_overdue(now=entry._started + 30) == [
            ("r1", "Upload timed out after 30 seconds.")
        ]
        assert mgr.get_entries("doc") == []
        assert EVENTS["r1"][-1] == ("abort", "TimeoutError")

    def test_finished_uploads_and_slots_without_timeout_never_expire(self):
        mgr = UploadManager()
        mgr.configure("doc", accept=".txt", timeout=5, max_entries=3)
        mgr.configure("free", accept=".txt", max_entries=3)
        done = _register(mgr, "r1")
        _finish(mgr, "r1")
        _register(mgr, "r2", name="free")
        assert mgr.expire_overdue(now=done._started + 3600) == []

    def test_an_overdue_upload_frees_its_place(self):
        mgr = UploadManager()
        mgr.configure("doc", accept=".txt", max_entries=1, timeout=5)
        entry = _register(mgr, "r1")
        assert _register(mgr, "r2") is None
        entry._started -= 10
        assert _register(mgr, "r2") is not None

    def test_a_wall_clock_step_does_not_move_the_deadline(self, monkeypatch):
        """The deadline runs on the monotonic clock, as the loop's timers do: a
        wall-clock step neither expires an upload early nor keeps it alive."""
        import time as _time

        mgr = UploadManager()
        mgr.configure("doc", accept=".txt", timeout=30)
        entry = _register(mgr, "r1")
        real = _time.time()
        monkeypatch.setattr(_time, "time", lambda: real + 3600)  # set forward
        assert not mgr.is_overdue("r1")
        monkeypatch.setattr(_time, "time", lambda: real - 3600)  # set back
        entry._started -= 31  # 31 s have really passed
        assert mgr.is_overdue("r1")

    def test_a_failed_upload_drops_its_timer(self):
        mgr = UploadManager()
        mgr.configure("doc", accept=".txt", max_file_size=4, timeout=5)
        _register(mgr, "r1", size=4)
        handle = MagicMock()
        mgr.set_deadline_handle("r1", handle)
        assert mgr.add_chunk("r1", 0, b"too long") is None
        handle.cancel.assert_called_once_with()
        assert mgr._deadline_handles == {}
        assert mgr.remaining("r1") is None

    def test_cleanup_and_completion_cancel_the_timer(self):
        mgr = UploadManager()
        mgr.configure("doc", accept=".txt", timeout=5, max_entries=2)
        _register(mgr, "r1")
        _register(mgr, "r2")
        h1, h2 = MagicMock(), MagicMock()
        mgr.set_deadline_handle("r1", h1)
        mgr.set_deadline_handle("r2", h2)
        _finish(mgr, "r1")
        h1.cancel.assert_called_once_with()
        mgr.cleanup()
        h2.cancel.assert_called_once_with()
        assert mgr._deadline_handles == {}


class _Page(UploadMixin, LiveView):
    template = "<div dj-root>up</div>"


def _consumer(view):
    from djust.websocket import LiveViewConsumer

    consumer = LiveViewConsumer.__new__(LiveViewConsumer)
    consumer.send_json = AsyncMock()
    consumer.send_error = AsyncMock()
    consumer.view_instance = view
    session = MagicMock()
    session.session_key = "ws-session"
    consumer.scope = {"session": session}
    return consumer


async def test_a_frame_for_an_overdue_upload_is_not_applied_and_the_browser_is_told():
    view = _Page()
    view.allow_upload("doc", accept=".txt", writer=RecordingWriter, timeout=5)
    consumer = _consumer(view)
    await consumer._handle_upload_register(
        {
            "upload_name": "doc",
            "ref": "r1",
            "client_name": "a.txt",
            "client_type": "text/plain",
            "client_size": 10,
        }
    )
    mgr = view._upload_manager
    assert "r1" in mgr._deadline_handles
    mgr._entries["r1"]._started -= 10
    await consumer._handle_upload_frame_for_view(
        {"type": "chunk", "ref": "r1", "chunk_index": 0, "data": b"0123"}
    )
    assert consumer.send_json.await_args.args[0] == {
        "type": "upload_progress",
        "ref": "r1",
        "progress": 0,
        "status": "cancelled",
        "error": "Upload timed out after 5 seconds.",
    }
    assert "r1" not in mgr._entries and "r1" not in EVENTS  # the chunk never reached the writer
    assert mgr._deadline_handles == {}


def test_consume_entries_skips_nothing_when_an_entry_goes_meanwhile():
    mgr = UploadManager()
    mgr.configure("doc", accept=".txt", max_entries=3)
    for ref in ("r1", "r2", "r3"):
        _register(mgr, ref)
        _finish(mgr, ref)
    seen = []
    for entry in mgr.consume_entries("doc"):
        seen.append(entry.ref)
        if entry.ref == "r1":
            mgr.cancel_upload("r1")  # e.g. a deadline turn, or the view itself
    assert seen == ["r1", "r2", "r3"]
    assert mgr.get_entries("doc") == []


async def test_the_deadline_turn_waits_for_the_render_lock_and_re_arms_when_early():
    view = _Page()
    view.allow_upload("doc", accept=".txt", writer=RecordingWriter, timeout=5)
    consumer = _consumer(view)
    consumer._render_lock = asyncio.Lock()
    mgr = view._upload_manager
    _register(mgr, "r1")
    item = {"mgr": mgr, "ref": "r1"}

    # Early (time left): nothing expires, a new timer is armed for the rest.
    await consumer._upload_deadline_turn(item)
    assert "r1" in mgr._entries and "r1" in mgr._deadline_handles

    # Overdue, but a handler holds the render lock: the turn waits for it.
    mgr._entries["r1"]._started -= 10
    await consumer._render_lock.acquire()
    turn = asyncio.ensure_future(consumer._upload_deadline_turn(item))
    await asyncio.sleep(0.01)
    assert not turn.done() and "r1" in mgr._entries
    consumer._render_lock.release()
    await turn
    assert "r1" not in mgr._entries
    assert consumer.send_json.await_args.args[0]["status"] == "cancelled"
    mgr.cleanup()


# ---------------------------------------------------------------------------
# Item 5: the view's cancel_upload() tells the browser
# ---------------------------------------------------------------------------


def test_cancel_upload_queues_a_cancelled_progress_push():
    view = _Page()
    view.allow_upload("doc", accept=".txt", writer=RecordingWriter)
    mgr = view._upload_manager
    _register(mgr, "r1")
    mgr.add_chunk("r1", 0, b"0123")
    view.cancel_upload("doc", "r1")
    assert mgr.get_entries("doc") == []
    assert EVENTS["r1"][-1] == ("abort", "ConnectionAbortedError")
    assert view._drain_push_events() == [
        (
            UPLOAD_PROGRESS_PUSH_EVENT,
            {
                "type": "upload_progress",
                "ref": "r1",
                "progress": 0,
                "status": "cancelled",
                "error": SERVER_CANCEL_MESSAGE,
            },
        )
    ]
    # An unknown ref changes nothing and tells nobody.
    view.cancel_upload("doc", "nope")
    assert view._drain_push_events() == []


# ---------------------------------------------------------------------------
# Over a real socket
# ---------------------------------------------------------------------------


class LimitsPage(UploadMixin, LiveView):
    template = "<div dj-root>up</div>"

    def mount(self, request, **kwargs):
        self.allow_upload("doc", accept=".txt", writer=RecordingWriter, timeout=0.3)
        self.allow_upload("slow", accept=".txt", writer=RecordingWriter, timeout=600)
        self.allow_upload("img", accept=".txt", writer=RefusingWriter)

    @event_handler
    def drop(self, ref: str = ""):
        self.cancel_upload("doc", ref)


LIMITS_URLCONF = types.ModuleType("upload_limits_4118_urls")
LIMITS_URLCONF.urlpatterns = [path("t/limits/", LimitsPage.as_view())]  # type: ignore[attr-defined]


@pytest.fixture
def limits_site():
    with override_settings(
        ROOT_URLCONF=LIMITS_URLCONF,
        ALLOWED_HOSTS=["*"],
        DEBUG=False,
        LIVEVIEW_ALLOWED_MODULES=["djust.tests"],
    ):
        yield


def _chunk(ref, i, data):
    return bytes([FRAME_CHUNK]) + uuid.UUID(ref).bytes + struct.pack(">I", i) + data


def _complete(ref):
    return bytes([FRAME_COMPLETE]) + uuid.UUID(ref).bytes


async def _start(socket, slot):
    ref = str(uuid.uuid4())
    await socket.send(
        {
            "type": "upload_register",
            "upload_name": slot,
            "ref": ref,
            "client_name": "a.txt",
            "client_type": "text/plain",
            "client_size": 40,
        }
    )
    registered = await socket.receive(("upload_registered", "error"))
    assert registered["type"] == "upload_registered", registered
    return ref


@pytest.mark.django_db(transaction=True)
async def test_socket_refusal_message_reaches_the_browser(limits_site):
    page = await sync_to_async(open_live_page)("/t/limits/")
    socket = await page.connect()
    ref = await _start(socket, "img")
    await socket.communicator.send_to(bytes_data=_chunk(ref, 0, b"0123"))
    answer = await socket.receive(("upload_progress",))
    assert answer["status"] == "error"
    assert answer["error"] == "Images must be under 2 MB."
    await socket.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_socket_view_cancel_is_pushed_to_the_browser(limits_site):
    page = await sync_to_async(open_live_page)("/t/limits/")
    socket = await page.connect()
    ref = await _start(socket, "doc")
    await socket.communicator.send_to(bytes_data=_chunk(ref, 0, b"0123"))
    await socket.send_event("drop", {"ref": ref})
    pushed = await socket.receive(("push_event",))
    assert pushed["event"] == UPLOAD_PROGRESS_PUSH_EVENT
    assert pushed["payload"]["ref"] == ref
    assert pushed["payload"]["status"] == "cancelled"
    assert pushed["payload"]["error"] == SERVER_CANCEL_MESSAGE
    assert EVENTS[ref][-1] == ("abort", "ConnectionAbortedError")
    await socket.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_socket_stalled_upload_times_out(limits_site):
    page = await sync_to_async(open_live_page)("/t/limits/")
    socket = await page.connect()
    ref = await _start(socket, "doc")
    await socket.communicator.send_to(bytes_data=_chunk(ref, 0, b"0123"))
    # The browser stalls: no further frame. The timer stops the upload.
    answer = await socket.receive(("upload_progress",))
    while answer.get("status") == "uploading":
        answer = await socket.receive(("upload_progress",))
    assert answer == {
        "type": "upload_progress",
        "ref": ref,
        "progress": 0,
        "status": "cancelled",
        "error": "Upload timed out after 0.3 seconds.",
    }
    assert EVENTS[ref][-1] == ("abort", "TimeoutError")
    await socket.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_socket_upload_finished_in_time_is_not_cancelled(limits_site):
    page = await sync_to_async(open_live_page)("/t/limits/")
    socket = await page.connect()
    # A long timeout: what this pins is that finishing is not cancelled, not
    # the timer's precision (the stall test covers the timer).
    ref = await _start(socket, "slow")
    await socket.communicator.send_to(bytes_data=_chunk(ref, 0, b"0" * 40))
    await socket.communicator.send_to(bytes_data=_complete(ref))
    answer = await socket.receive(("upload_progress",))
    while answer.get("status") == "uploading":
        answer = await socket.receive(("upload_progress",))
    assert answer["status"] == "complete"
    await socket.receive_nothing(("upload_progress",), within=0.1)
    assert EVENTS[ref][-1] == "close"
    await socket.disconnect()
