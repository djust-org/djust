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

import asyncio
import json
import struct
import threading
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
# Progress: opt-in, once per 10% step, the steps the browser gets
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
async def test_progress_hook_runs_once_per_step_the_browser_gets(site):
    _, socket = await _open()
    ref = await _start(socket, "prog")
    await _until(socket, lambda f: _renders(f, "started prog"))
    # 14 chunks: 3 bytes each and one of 1 (40 bytes). Progress runs 7, 15,
    # 22, 30, 37, 45, ... 97, 100: each 10% step is reported at the first
    # chunk that reaches it, and once (37 is still step 3, 97 step 9).
    data = b"x" * 40
    for i, start in enumerate(range(0, 40, 3)):
        await socket.communicator.send_to(bytes_data=_chunk(ref, i, data[start : start + 3]))
    frames = await _until(socket, lambda f: _renders(f, "progress prog 100"))
    steps = [15, 22, 30, 45, 52, 60, 75, 82, 90, 100]
    sent = [f["progress"] for f in frames if f["type"] == "upload_progress"]
    assert sent == steps
    progress_calls = [c for c in CALLS if c[0] == "progress"]
    assert [c[3] for c in progress_calls] == steps
    # Each step's render follows its own progress frame.
    order = [
        ("p", f["progress"]) if f["type"] == "upload_progress" else ("r",)
        for f in frames
        if f["type"] in ("upload_progress", "patch")
    ]
    assert order == [item for step in steps for item in (("p", step), ("r",))]
    await socket.disconnect()


async def _send_chunks_then_ping(socket, ref, count):
    for i in range(count):
        await socket.communicator.send_to(bytes_data=_chunk(ref, i, b"x"))
    # Frames run in order: the pong comes once every chunk has been handled.
    await socket.send({"type": "ping"})
    return await _until(socket, lambda f: f.get("type") == "pong")


@pytest.mark.django_db(transaction=True)
async def test_tiny_chunks_of_a_large_file_report_no_step_until_one_is_reached(site):
    _, socket = await _open()
    ref = await _start(socket, "prog", size=10_000_000)
    await _until(socket, lambda f: _renders(f, "started prog"))
    # 30 one-byte chunks of a 10 MB file: progress stays 0, no step is reached.
    frames = await _send_chunks_then_ping(socket, ref, 30)
    assert [f for f in frames if f["type"] in ("upload_progress", "patch")] == []
    await socket.receive_nothing(("upload_progress", "patch", "html_update"), within=0.2)
    assert [c for c in CALLS if c[0] == "progress"] == []
    await socket.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_chunks_past_the_announced_size_report_100_once(site):
    _, socket = await _open()
    ref = await _start(socket, "prog", size=1)
    await _until(socket, lambda f: _renders(f, "started prog"))
    # Every chunk after the first keeps the upload at 100%.
    frames = await _send_chunks_then_ping(socket, ref, 20)
    assert [f["progress"] for f in frames if f["type"] == "upload_progress"] == [100]
    assert [f for f in frames if f["type"] == "patch"] and _renders(
        [f for f in frames if f["type"] == "patch"][-1], "progress prog 100"
    )
    await socket.receive_nothing(("upload_progress", "patch", "html_update"), within=0.2)
    assert [c[3] for c in CALLS if c[0] == "progress"] == [100]
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


@pytest.mark.django_db(transaction=True)
async def test_a_second_register_of_a_started_ref_fails_the_first(site):
    _, socket = await _open()
    ref = await _start(socket, "boom")
    await _start(socket, "boom", ref=ref)
    await socket.receive_nothing(("never",), within=0.3)
    # The replaced upload ends (failed, error) before its replacement starts.
    assert [c[:4] if c[0] == "failed" else c[0] for c in CALLS] == [
        "started",
        ("failed", "boom", ref, "error"),
        "started",
    ]
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


# ---------------------------------------------------------------------------
# A socket that hosts several views: hooks run on the view's lane (#3252)
# ---------------------------------------------------------------------------

MOD = __name__
#: Set when a blocking hook or handler is running; the test lets go with RELEASE.
HELD = threading.Event()
RELEASE = threading.Event()
PATIENCE = 20.0


async def _block():
    HELD.set()
    await asyncio.get_running_loop().run_in_executor(None, RELEASE.wait, PATIENCE)


class LanePage(LiveView):
    template = '<div dj-root dj-view="' + MOD + '.LanePage"><b>page</b></div>'

    @event_handler()
    async def quick(self, **kwargs):
        CALLS.append(("quick",))


class SlowUploader(UploadMixin, LiveView):
    template = '<div dj-view="' + MOD + '.SlowUploader"><b>up</b></div>'

    def mount(self, request, **kwargs):
        self.allow_upload("doc", accept=".txt", writer=SinkWriter, max_file_size=1000)

    async def handle_upload_started(self, name, entry):
        CALLS.append(("started", name, entry.ref))

    async def handle_upload_finished(self, name, entry):
        CALLS.append(("finished-start", name, entry.ref))
        await _block()
        CALLS.append(("finished-end", name, entry.ref))

    def handle_upload_failed(self, name, entry, reason):
        CALLS.append(("failed", name, entry.ref, reason))

    @event_handler()
    async def hold(self, **kwargs):
        await _block()

    @event_handler()
    def bump(self, **kwargs):
        CALLS.append(("bump",))


@pytest.fixture
def lanes():
    HELD.clear()
    RELEASE.clear()
    with override_settings(LIVEVIEW_ALLOWED_MODULES=["djust.tests"], DEBUG=False):
        yield
    RELEASE.set()


async def _socket():
    from channels.testing import WebsocketCommunicator
    from django.contrib.auth.models import AnonymousUser
    from django.contrib.sessions.backends.db import SessionStore

    from djust.websocket import LiveViewConsumer

    consumers = []

    class _Recorded(LiveViewConsumer):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            consumers.append(self)

    store = SessionStore()
    await sync_to_async(store.create)()
    communicator = WebsocketCommunicator(_Recorded.as_asgi(), "/ws/")
    communicator.scope["session"] = store
    communicator.scope["user"] = AnonymousUser()
    assert (await communicator.connect())[0]
    await communicator.receive_json_from()
    for cls, target in ((LanePage, None), (SlowUploader, "up")):
        frame = {"type": "mount", "view": MOD + "." + cls.__name__, "url": "/p/"}
        if target:
            frame["target_id"] = target
        await communicator.send_json_to(frame)
        await _frames(communicator, lambda f: f.get("type") == "mount")
    consumer = consumers[-1]
    return communicator, consumer, consumer._slot_map()["up"].facade


async def _frames(communicator, predicate, timeout=PATIENCE):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    seen = []
    while True:
        seen.append(await communicator.receive_json_from(timeout=deadline - loop.time()))
        if predicate(seen[-1]):
            return seen


async def _spin(predicate, what):
    for _ in range(4000):
        if predicate():
            return
        await asyncio.sleep(0.001)
    raise AssertionError("timed out waiting for " + what)


async def _wait_held():
    assert await asyncio.get_running_loop().run_in_executor(None, HELD.wait, PATIENCE)


def _register(ref, size):
    return {
        "type": "upload_register",
        "target_id": "up",
        "upload_name": "doc",
        "ref": ref,
        "client_name": "a.txt",
        "client_type": "text/plain",
        "client_size": size,
    }


def _event(name, ref, target=None):
    frame = {"type": "event", "event": name, "params": {}, "ref": ref}
    if target:
        frame["target_id"] = target
    return frame


def _reply_to(ref):
    return lambda f: (
        f.get("type") in ("patch", "html_update", "noop", "error") and f.get("ref") == ref
    )


@pytest.mark.django_db(transaction=True)
async def test_a_slow_hook_does_not_delay_another_view_and_keeps_its_views_order(lanes):
    communicator, _, facade = await _socket()
    try:
        ref = str(uuid.uuid4())
        await communicator.send_json_to(_register(ref, 3))
        await _frames(communicator, lambda f: f.get("type") == "upload_registered")
        await _spin(lambda: not facade._lane.busy(), "the register to drain")
        # The view is idle, so its frames are handled in the receive loop; the
        # finished hook they call for is queued on its lane and blocks there.
        await communicator.send_to(bytes_data=_chunk(ref, 0, b"abc"))
        await communicator.send_to(bytes_data=_complete(ref))
        await _wait_held()
        # Another view's event is answered while the hook is still blocked.
        await communicator.send_json_to(_event("quick", 7))
        await _frames(communicator, _reply_to(7), timeout=5.0)
        # The uploading view's own next event waits for its hook.
        await communicator.send_json_to(_event("bump", 8, "up"))
        await communicator.send_json_to({"type": "ping"})
        await _frames(communicator, lambda f: f.get("type") == "pong", timeout=5.0)
        assert ("finished-end", "doc", ref) not in CALLS and ("bump",) not in CALLS
        RELEASE.set()
        await _frames(communicator, _reply_to(8))
        assert CALLS == [
            ("started", "doc", ref),
            ("finished-start", "doc", ref),
            ("quick",),
            ("finished-end", "doc", ref),
            ("bump",),
        ]
    finally:
        RELEASE.set()
        await communicator.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_an_upload_stopped_while_its_register_waited_fails_with_error(lanes, monkeypatch):
    communicator, consumer, facade = await _socket()
    try:
        await communicator.send_json_to(_event("hold", 1, "up"))
        await _wait_held()
        monkeypatch.setattr(type(consumer), "_upload_queue_budget", lambda self: 1000)
        ref = str(uuid.uuid4())
        await communicator.send_json_to(_register(ref, 600))
        await communicator.send_to(bytes_data=_chunk(ref, 0, b"a" * 300))  # fits
        await _spin(lambda: facade._lane.queued() == 2, "the register and the first chunk")
        await communicator.send_to(bytes_data=_chunk(ref, 1, b"b" * 300))  # does not
        stopped = await _frames(communicator, lambda f: f.get("type") == "upload_progress")
        assert stopped[-1]["status"] == "error" and "busy" in stopped[-1]["error"]
        RELEASE.set()
        await _frames(communicator, _reply_to(1))
        await _spin(lambda: not facade._lane.busy(), "the lane to drain")
        # The queued register ran (started), then its upload was cancelled.
        assert CALLS == [("started", "doc", ref), ("failed", "doc", ref, "error")]
    finally:
        RELEASE.set()
        await communicator.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_a_started_upload_stopped_by_a_full_lane_fails_with_error(lanes, monkeypatch):
    communicator, consumer, facade = await _socket()
    try:
        ref = str(uuid.uuid4())
        await communicator.send_json_to(_register(ref, 600))
        await _frames(communicator, lambda f: f.get("type") == "upload_registered")
        await _spin(lambda: not facade._lane.busy(), "the register to drain")
        await communicator.send_json_to(_event("hold", 1, "up"))
        await _wait_held()
        monkeypatch.setattr(type(consumer), "_upload_queue_budget", lambda self: 1000)
        await communicator.send_to(bytes_data=_chunk(ref, 0, b"a" * 300))  # fits
        await _spin(lambda: facade._lane.queued() == 1, "the first chunk")
        await communicator.send_to(bytes_data=_chunk(ref, 1, b"b" * 300))  # does not
        stopped = await _frames(communicator, lambda f: f.get("type") == "upload_progress")
        assert stopped[-1]["status"] == "error" and "busy" in stopped[-1]["error"]
        RELEASE.set()
        await _frames(communicator, _reply_to(1))
        await _spin(lambda: not facade._lane.busy(), "the lane to drain")
        # Once, after the view's busy turn; the queued chunk of the stopped
        # upload fails nothing more.
        assert CALLS == [("started", "doc", ref), ("failed", "doc", ref, "error")]
    finally:
        RELEASE.set()
        await communicator.disconnect()
