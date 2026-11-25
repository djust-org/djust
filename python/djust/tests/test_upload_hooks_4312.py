"""#4312: the view hears its uploads.

Optional view hooks, each run as a turn of the view with a render:

* ``handle_upload_started(name, entry)``: after a register, and again after a
  resume.
* ``handle_upload_progress(name, entry)``: opt-in per slot
  (``allow_upload(..., progress_hook=True)``), at the 10% steps the browser is
  sent.
* ``handle_upload_finished(name, entry)``: after the upload completes.
* ``handle_upload_failed(name, entry, reason)``: ``refused``, ``too_large``,
  ``timeout``, ``cancelled`` or ``error``.

Everything here goes over a real socket, through the real consumer.
"""

import json
import struct
import types
import uuid

import pytest
from asgiref.sync import sync_to_async
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings
from django.urls import path

from djust import LiveView, event_handler, state
from djust.testing import open_live_page
from djust.uploads import (
    FRAME_CANCEL,
    FRAME_CHUNK,
    FRAME_COMPLETE,
    UploadManager,
    UploadMixin,
    UploadRefused,
    UploadWriter,
    upload_failure_reason,
)
from djust.uploads.resumable import ResumableUploadWriter
from djust.uploads.storage import (
    InMemoryUploadState,
    _reset_default_store_for_tests,
    set_default_store,
)

#: Every hook call, in order: (hook, slot, ref, extra...).
CALLS: list = []


class SinkWriter(UploadWriter):
    def open(self):
        pass

    def write_chunk(self, chunk, chunk_index=0):
        pass

    def close(self):
        return {"ok": True}

    def abort(self, error):
        pass


class RefusingWriter(SinkWriter):
    def write_chunk(self, chunk, chunk_index=0):
        raise UploadRefused("Images must be under 2 MB.")


class HookPage(UploadMixin, LiveView):
    template = "<div dj-root><p>{{ status }}</p></div>"

    status = state("idle")

    def mount(self, request, **kwargs):
        self.allow_upload("doc", accept=".txt", writer=SinkWriter, max_file_size=40)
        self.allow_upload("prog", accept=".txt", writer=SinkWriter, progress_hook=True)
        self.allow_upload("img", accept=".txt", writer=RefusingWriter)
        self.allow_upload("quick", accept=".txt", writer=SinkWriter, timeout=0.3)
        self.allow_upload("boom", accept=".txt", writer=SinkWriter, max_entries=3)
        self.allow_upload(
            "res",
            accept=".txt",
            writer=ResumableUploadWriter.with_inner(SinkWriter),
            resumable=True,
        )

    def handle_upload_started(self, name, entry):
        CALLS.append(("started", name, entry.ref, entry.progress))
        if name == "boom":
            raise RuntimeError("hook failed")
        self.status = "started %s %d" % (name, entry.progress)

    def handle_upload_progress(self, name, entry):
        CALLS.append(("progress", name, entry.ref, entry.progress))
        self.status = "progress %s %d" % (name, entry.progress)

    def handle_upload_finished(self, name, entry):
        CALLS.append(("finished", name, entry.ref))
        if name == "boom":
            raise RuntimeError("hook failed")
        self.status = "finished %s" % name

    def handle_upload_failed(self, name, entry, reason):
        CALLS.append(("failed", name, entry.ref, reason, entry.error))
        self.status = "failed %s %s" % (name, reason)

    @event_handler
    def drop(self, name: str = "", ref: str = ""):
        self.cancel_upload(name, ref)


class SilentPage(UploadMixin, LiveView):
    """No hooks: uploads behave exactly as before."""

    template = "<div dj-root>up</div>"

    def mount(self, request, **kwargs):
        self.allow_upload("doc", accept=".txt", writer=SinkWriter)


URLCONF = types.ModuleType("upload_hooks_4312_urls")
URLCONF.urlpatterns = [  # type: ignore[attr-defined]
    path("t/hooks/", HookPage.as_view()),
    path("t/silent/", SilentPage.as_view()),
]


@pytest.fixture(autouse=True)
def _clear():
    CALLS.clear()
    _reset_default_store_for_tests()
    set_default_store(InMemoryUploadState())
    yield
    CALLS.clear()
    _reset_default_store_for_tests()


@pytest.fixture
def site():
    with override_settings(
        ROOT_URLCONF=URLCONF,
        ALLOWED_HOSTS=["*"],
        DEBUG=False,
        LIVEVIEW_ALLOWED_MODULES=["djust.tests"],
    ):
        yield


def _chunk(ref, i, data):
    return bytes([FRAME_CHUNK]) + uuid.UUID(ref).bytes + struct.pack(">I", i) + data


def _complete(ref):
    return bytes([FRAME_COMPLETE]) + uuid.UUID(ref).bytes


def _cancel(ref):
    return bytes([FRAME_CANCEL]) + uuid.UUID(ref).bytes


async def _start(socket, slot, size=40, ref=None):
    ref = ref or str(uuid.uuid4())
    await socket.send(
        {
            "type": "upload_register",
            "upload_name": slot,
            "ref": ref,
            "client_name": "a.txt",
            "client_type": "text/plain",
            "client_size": size,
        }
    )
    registered = await socket.receive(("upload_registered", "error"))
    assert registered["type"] == "upload_registered", registered
    return ref


async def _until(socket, predicate, limit=40):
    """Frames up to and including the first one ``predicate`` accepts."""
    seen = []
    for _ in range(limit):
        frame = await socket.receive()
        seen.append(frame)
        if predicate(frame):
            return seen
    raise AssertionError("no matching frame in %r" % seen)


def _renders(frame, text):
    return frame.get("type") in ("patch", "html_update") and text in json.dumps(frame)


async def _open(url="/t/hooks/"):
    page = await sync_to_async(open_live_page)(url)
    return page, await page.connect()


# ---------------------------------------------------------------------------
# Each hook once, in order, with a render after it
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
async def test_started_then_finished_each_once_and_each_renders(site):
    _, socket = await _open()
    ref = await _start(socket, "doc")
    frames = await _until(socket, lambda f: _renders(f, "started doc"))
    assert [f["type"] for f in frames] == ["patch"]

    await socket.communicator.send_to(bytes_data=_chunk(ref, 0, b"0" * 40))
    await socket.communicator.send_to(bytes_data=_complete(ref))
    frames = await _until(socket, lambda f: _renders(f, "finished doc"))
    statuses = [f.get("status") for f in frames if f["type"] == "upload_progress"]
    # The browser hears the completion before the hook's render.
    assert statuses == ["uploading", "complete"]
    await socket.receive_nothing(("patch", "html_update"), within=0.2)

    # Progress is off by default: the view defines the hook, the slot did not
    # ask for it.
    assert CALLS == [("started", "doc", ref, 0), ("finished", "doc", ref)]
    await socket.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_a_raising_hook_is_logged_and_the_upload_goes_on(site, caplog):
    _, socket = await _open()
    ref = await _start(socket, "boom")
    # No render for the raising started hook.
    await socket.receive_nothing(("patch", "html_update"), within=0.2)
    await socket.communicator.send_to(bytes_data=_chunk(ref, 0, b"0" * 40))
    await socket.communicator.send_to(bytes_data=_complete(ref))
    frames = await _until(socket, lambda f: f.get("status") == "complete")
    assert frames[-1]["ref"] == ref
    await socket.receive_nothing(("patch", "html_update"), within=0.2)
    assert [c[0] for c in CALLS] == ["started", "finished"]
    # Logged once per raising hook (value-free for an explicit view, ADR-038).
    errors = [r for r in caplog.records if r.name == "djust.websocket" and r.levelname == "ERROR"]
    assert len(errors) == 2

    # The view still works: its next upload's hooks run and render.
    other = await _start(socket, "doc")
    await _until(socket, lambda f: _renders(f, "started doc"))
    assert CALLS[-1] == ("started", "doc", other, 0)
    await socket.disconnect()


# ---------------------------------------------------------------------------
# Progress: opt-in, throttled to the browser's 10% steps
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
async def test_progress_hook_runs_at_the_steps_the_browser_gets(site):
    _, socket = await _open()
    ref = await _start(socket, "prog")
    await _until(socket, lambda f: _renders(f, "started prog"))
    # 14 chunks: 3 bytes each and one of 1 (40 bytes). Progress runs 7, 15,
    # 22, 30, ... 97, 100; the browser hears 30, 60, 90 and 100.
    data = b"x" * 40
    for i, start in enumerate(range(0, 40, 3)):
        await socket.communicator.send_to(bytes_data=_chunk(ref, i, data[start : start + 3]))
    frames = await _until(socket, lambda f: _renders(f, "progress prog 100"))
    sent = [f["progress"] for f in frames if f["type"] == "upload_progress"]
    assert sent == [30, 60, 90, 100]
    progress_calls = [c for c in CALLS if c[0] == "progress"]
    assert [c[3] for c in progress_calls] == [30, 60, 90, 100]
    # Each step's render follows its own progress frame.
    order = [
        ("p", f["progress"]) if f["type"] == "upload_progress" else ("r",)
        for f in frames
        if f["type"] in ("upload_progress", "patch")
    ]
    assert order == [("p", 30), ("r",), ("p", 60), ("r",), ("p", 90), ("r",), ("p", 100), ("r",)]
    await socket.disconnect()


def test_progress_hook_without_the_method_is_refused():
    class NoProgress(UploadMixin, LiveView):
        template = "<div dj-root></div>"

    with pytest.raises(ImproperlyConfigured, match="handle_upload_progress"):
        NoProgress().allow_upload("doc", progress_hook=True)


def test_progress_hook_must_be_a_bool():
    with pytest.raises(ValueError):
        UploadManager().configure("doc", progress_hook="yes")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Failures and their reasons
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
async def test_a_writer_refusal_is_failed_refused_with_the_message(site):
    _, socket = await _open()
    ref = await _start(socket, "img")
    await socket.communicator.send_to(bytes_data=_chunk(ref, 0, b"0123"))
    # A trailing chunk of the failed upload does not fail it again.
    await socket.communicator.send_to(bytes_data=_chunk(ref, 1, b"4567"))
    await _until(socket, lambda f: _renders(f, "failed img refused"))
    await socket.receive_nothing(("patch", "html_update"), within=0.2)
    assert CALLS[1:] == [("failed", "img", ref, "refused", "Images must be under 2 MB.")]
    await socket.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_over_the_size_limit_is_failed_too_large(site):
    _, socket = await _open()
    ref = await _start(socket, "doc")
    await socket.communicator.send_to(bytes_data=_chunk(ref, 0, b"0" * 50))
    await _until(socket, lambda f: _renders(f, "failed doc too_large"))
    assert CALLS[-1][:4] == ("failed", "doc", ref, "too_large")
    await socket.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_a_stalled_upload_is_failed_timeout(site):
    _, socket = await _open()
    ref = await _start(socket, "quick")
    await socket.communicator.send_to(bytes_data=_chunk(ref, 0, b"0123"))
    frames = await _until(socket, lambda f: _renders(f, "failed quick timeout"))
    cancelled = [f for f in frames if f.get("status") == "cancelled"]
    assert cancelled and cancelled[0]["ref"] == ref
    assert CALLS[-1][:4] == ("failed", "quick", ref, "timeout")
    assert [c[0] for c in CALLS] == ["started", "failed"]
    await socket.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_a_cancel_by_the_browser_is_failed_cancelled(site):
    _, socket = await _open()
    ref = await _start(socket, "doc")
    await socket.communicator.send_to(bytes_data=_chunk(ref, 0, b"0123"))
    await socket.communicator.send_to(bytes_data=_cancel(ref))
    await _until(socket, lambda f: _renders(f, "failed doc cancelled"))
    assert CALLS[-1] == ("failed", "doc", ref, "cancelled", None)
    # A second cancel of the gone upload calls nothing.
    await socket.communicator.send_to(bytes_data=_cancel(ref))
    await socket.receive(("upload_progress",))
    await socket.receive_nothing(("patch", "html_update"), within=0.2)
    assert [c[0] for c in CALLS] == ["started", "failed"]
    await socket.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_no_hook_runs_for_an_upload_the_view_cancelled(site):
    _, socket = await _open()
    ref = await _start(socket, "doc")
    await socket.communicator.send_to(bytes_data=_chunk(ref, 0, b"0123"))
    await socket.send_event("drop", {"name": "doc", "ref": ref})
    await socket.communicator.send_to(bytes_data=_chunk(ref, 1, b"4567"))
    await socket.communicator.send_to(bytes_data=_complete(ref))
    await socket.receive_nothing(("never",), within=0.3)
    # The view's own cancel is not a failure it is told about, and the frames
    # after it run no hook.
    assert [c[0] for c in CALLS] == ["started"]
    await socket.disconnect()


# ---------------------------------------------------------------------------
# Resume
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
async def test_a_resume_calls_started_again(site):
    page, socket = await _open()
    ref = await _start(socket, "res", size=40)
    await socket.communicator.send_to(bytes_data=_chunk(ref, 0, b"0" * 20))
    await _until(socket, lambda f: f.get("type") == "upload_progress")
    await socket.disconnect()

    socket = await page.connect()
    await socket.send({"type": "upload_resume", "ref": ref})
    answer = await socket.receive(("upload_resumed",))
    assert answer["status"] == "resumed", answer
    # The resumed view restored "started res 0"; the hook's render shows 50.
    await _until(socket, lambda f: _renders(f, "started res 50"))
    started = [c for c in CALLS if c[0] == "started"]
    # Once at the register, once at the resume (with the bytes it has).
    assert started == [("started", "res", ref, 0), ("started", "res", ref, 50)]
    await socket.communicator.send_to(bytes_data=_chunk(ref, 1, b"1" * 20))
    await socket.communicator.send_to(bytes_data=_complete(ref))
    await _until(socket, lambda f: _renders(f, "finished res"))
    assert CALLS[-1] == ("finished", "res", ref)
    await socket.disconnect()


# ---------------------------------------------------------------------------
# A view without hooks pays nothing
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
async def test_a_view_without_hooks_renders_nothing_for_its_uploads(site):
    _, socket = await _open("/t/silent/")
    ref = await _start(socket, "doc")
    await socket.communicator.send_to(bytes_data=_chunk(ref, 0, b"0" * 40))
    await socket.communicator.send_to(bytes_data=_complete(ref))
    await _until(socket, lambda f: f.get("status") == "complete")
    await socket.receive_nothing(("patch", "html_update"), within=0.2)
    await socket.disconnect()


def test_failure_reason_mapping():
    mgr = UploadManager()
    mgr.configure("doc", accept=".txt", max_file_size=4)
    entry = mgr.register_entry("doc", "r1", "a.txt", "text/plain", 4)
    assert mgr.add_chunk("r1", 0, b"too long") is None
    assert upload_failure_reason(entry) == "too_large"
    entry._error, entry._refused = "Nope.", True
    assert upload_failure_reason(entry) == "refused"
    entry._error, entry._refused = "Upload writer rejected chunk", False
    assert upload_failure_reason(entry) == "error"
