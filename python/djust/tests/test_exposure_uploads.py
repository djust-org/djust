"""ADR-038 E2-6: uploads under the explicit exposure policy.

An explicit view renders upload progress from a registered ``djust.uploads``
provider, a render-only projection of ``UploadManager.get_upload_state()``
that drops the raw client-supplied ``client_name`` (templates get the
sanitized ``safe_client_name``) and the server-side ``writer_result``.

Uploads in flight resume across a reconnect (#4175, which
replaced ADR-038 D-g): a ``ResumableUploadWriter`` records the upload, bound
to the session and, for an explicit view, to the mount binding's principal and
view, and the ``upload_resume`` handshake re-attaches the parked writer. A
plain upload's entry is not preserved: the remount starts with an empty upload
manager and the client falls back to ``upload_register``, which still works.

Only the staged construction gate is bypassed. Runtime cases use the real ``ViewRuntime`` with
DB sessions.
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from asgiref.sync import sync_to_async
from django.test import override_settings

from djust import LiveView, event_handler
from djust._exposure import ExposureContract, ExposureError
from djust._exposure_sessions import server_state_adapter
from djust.decorators import state
from djust.runtime import ViewRuntime
from djust.tests.test_exposure_runtime import make_request
from djust.tests.test_runtime_state_save_tt_1894 import MockTransport
from djust.uploads import DEFAULT_CHUNK_SIZE, UploadMixin
from djust.uploads.storage import InMemoryUploadState, get_default_store, set_default_store

from .conftest import observability_request_factory

RAW_NAME = "RAWDIR_SENTINEL/../photo_SAFE.png"
SAFE_NAME = "photo_SAFE.png"
WRITER_SENTINEL = "WRITER_SENTINEL"
PERSISTENCE_SENTINELS = ("RAWDIR_SENTINEL", WRITER_SENTINEL, "photo_SAFE")

TEMPLATE = (
    "<div dj-root>{{ count }}"
    "{% for e in uploads.avatar.entries %}"
    "|entry:{{ e.safe_client_name }}{{ e.client_name }}{{ e.writer_result }}:{{ e.progress }}|"
    "{% endfor %}"
    "{% if uploads.avatar.config.accept %}|cfg{% endif %}</div>"
)


@pytest.fixture
def staged(monkeypatch):
    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)


def _register(view, ref="ref-1"):
    entry = view._upload_manager.register_entry("avatar", ref, RAW_NAME, "image/png", 100)
    assert entry is not None
    entry.writer_result = WRITER_SENTINEL
    return entry


class UploadPage(UploadMixin, LiveView):
    exposure_policy = "explicit"
    count = state(0, persist="server")
    navigation = state("NAV", persist="client", client=True)
    template = TEMPLATE

    def mount(self, request, **kwargs):
        self.allow_upload("avatar", accept=".png", max_entries=3, max_file_size=1000)

    def get_context_data(self, **kwargs):
        return super().get_context_data(count=self.count, **kwargs)

    @event_handler()
    def start(self):
        self.count += 1
        _register(self)


class MountRegisteredPage(UploadPage):
    """An entry already in flight when the mount frame is built."""

    count = state(0, persist="server")
    navigation = state("NAV", persist="client", client=True)

    def mount(self, request, **kwargs):
        super().mount(request, **kwargs)
        _register(self)


class LiveViewFirstPage(LiveView, UploadMixin):
    """The documented ``class MyView(LiveView, UploadMixin)`` ordering."""

    exposure_policy = "explicit"
    count = state(0, persist="server")
    navigation = state("NAV", persist="client", client=True)
    template = TEMPLATE

    def mount(self, request, **kwargs):
        self.allow_upload("avatar", accept=".png", max_entries=3, max_file_size=1000)

    def get_context_data(self, **kwargs):
        return super().get_context_data(count=self.count, **kwargs)

    @event_handler()
    def start(self):
        self.count += 1
        _register(self)


async def _mount(request, view_class):
    transport = MockTransport()
    transport.build_request = lambda: request

    async def fresh_event_request(view):
        return await sync_to_async(make_request)(request.session.session_key)

    transport.explicit_event_request = fresh_event_request
    runtime = ViewRuntime(transport)
    with override_settings(LIVEVIEW_ALLOWED_MODULES=["djust"]):
        await runtime.dispatch_mount(
            {"type": "mount", "view": __name__ + "." + view_class.__name__, "url": request.path}
        )
    return runtime, transport


def _mount_frame(transport):
    return next(frame for frame in transport.sent if frame.get("type") == "mount")


def _stored(request):
    from django.contrib.sessions.backends.db import SessionStore

    return json.dumps(dict(SessionStore(request.session.session_key).load()))


# ---------------------------------------------------------------------------
# The provider contract.
# ---------------------------------------------------------------------------


def test_upload_provider_is_registered_render_only():
    providers = {p.name: p for p in ExposureContract.from_view_class(UploadPage).providers}
    uploads = providers["djust.uploads"]
    assert uploads.rendered == {"uploads"}
    assert uploads.persisted == frozenset() and uploads.client == frozenset()


def test_uploads_as_application_kwarg(staged, monkeypatch):
    view = UploadPage()
    view.allow_upload("avatar", accept=".png")
    with pytest.raises(ExposureError, match="collision"):
        view.get_context_data(uploads="APP_SENTINEL")


# ---------------------------------------------------------------------------
# Rendered HTML.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("view_class", [UploadPage, LiveViewFirstPage])
async def test_upload_progress_renders_from_the_provider(staged, monkeypatch, view_class):
    request = await sync_to_async(make_request)()
    runtime, transport = await _mount(request, view_class)
    assert not transport.errors, transport.errors
    await runtime.dispatch_event({"type": "event", "event": "start", "params": {}})
    assert not transport.errors, transport.errors
    view = runtime.view_instance
    assert view.count == 1
    assert [e.ref for e in view._upload_manager.get_entries("avatar")] == ["ref-1"]
    frames = json.dumps(transport.sent)
    assert "RAWDIR_SENTINEL" not in frames
    assert WRITER_SENTINEL not in frames
    assert f"|entry:{SAFE_NAME}:0|" in frames
    assert "|cfg" in frames


# ---------------------------------------------------------------------------
# The mount frame carries configuration only.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_mount_frame_upload_configs_are_configuration_only(staged, monkeypatch):
    request = await sync_to_async(make_request)()
    runtime, transport = await _mount(request, MountRegisteredPage)
    assert not transport.errors, transport.errors
    assert runtime.view_instance._upload_manager.get_entries("avatar"), "an entry is in flight"
    frame = _mount_frame(transport)
    assert frame["upload_configs"] == {
        "avatar": {
            "name": "avatar",
            "accept": ".png",
            "max_entries": 3,
            "max_file_size": 1000,
            "chunk_size": DEFAULT_CHUNK_SIZE,
            "auto_upload": True,
            "resumable": False,
        }
    }
    configs = json.dumps(frame["upload_configs"])
    for sentinel in PERSISTENCE_SENTINELS + ("ref-1", '"entries"'):
        assert sentinel not in configs
    whole = json.dumps(frame)
    assert "RAWDIR_SENTINEL" not in whole and WRITER_SENTINEL not in whole
    # The only place the entry reaches the frame is the rendered page.
    assert f"|entry:{SAFE_NAME}:0|" in frame["html"]


# ---------------------------------------------------------------------------
# State destinations: server envelope, signed snapshot, debug.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_explicit_upload_entries_never_reach_state_destinations(staged, rf):
    from djust._exposure import explicit_debug_projection
    from djust._exposure_snapshots import snapshot_codec
    from djust.observability.registry import register_view, unregister_view
    from djust.observability.views import view_assigns

    request = await sync_to_async(make_request)()
    with override_settings(DEBUG=True):
        runtime, transport = await _mount(request, UploadPage)
        assert not transport.errors, transport.errors
        await runtime.dispatch_event({"type": "event", "event": "start", "params": {}})
    assert not transport.errors, transport.errors
    view = runtime.view_instance
    frames = json.dumps(transport.sent)
    assert f"|entry:{SAFE_NAME}:0|" in frames, "the provider must have rendered"

    persisted = await sync_to_async(make_request)(request.session.session_key)
    adapter = await sync_to_async(server_state_adapter)(view, persisted)
    assert await adapter.aload() == {"count": 1}
    stored = await sync_to_async(_stored)(request)
    token = next(
        frame["state_snapshot_signed"]
        for frame in reversed(transport.sent)
        if frame.get("state_snapshot_signed")
    )
    codec = await sync_to_async(snapshot_codec)(view, request)
    assert codec.restore(token) == {"navigation": "NAV"}
    debug = json.dumps(explicit_debug_projection(view))
    with override_settings(DEBUG=True):
        register_view("exposure-upload-test", view)
        try:
            response = await sync_to_async(view_assigns)(
                # The endpoint serves only token-bearing requests (b5ed46f2a).
                observability_request_factory().get(
                    "/debug/", {"session_id": "exposure-upload-test"}
                )
            )
        finally:
            unregister_view("exposure-upload-test")
    assert response.status_code == 200
    for sentinel in PERSISTENCE_SENTINELS + ("ref-1",):
        assert sentinel not in stored
        assert sentinel not in token
        assert sentinel not in debug
        assert sentinel not in response.content.decode()


# ---------------------------------------------------------------------------
# Reconnect: a plain upload starts over; a resumable one resumes (#4175).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_explicit_reconnect_drops_entries_in_flight(staged):
    request = await sync_to_async(make_request)()
    runtime, transport = await _mount(request, UploadPage)
    await runtime.dispatch_event({"type": "event", "event": "start", "params": {}})
    assert not transport.errors, transport.errors
    assert f"|entry:{SAFE_NAME}:0|" in json.dumps(transport.sent)

    again, again_transport = await _mount(
        await sync_to_async(make_request)(request.session.session_key), UploadPage
    )
    assert not again_transport.errors, again_transport.errors
    assert again.view_instance.count == 1, "declared server state must be restored"
    assert again.view_instance._upload_manager.get_entries("avatar") == []
    html = _mount_frame(again_transport)["html"]
    assert "|cfg" in html, "the upload provider must have rendered on remount"
    assert "|entry:" not in html


def _consumer(view, session_key="ws-session"):
    from djust.websocket import LiveViewConsumer

    consumer = LiveViewConsumer.__new__(LiveViewConsumer)
    consumer.send_json = AsyncMock()
    consumer.send_error = AsyncMock()
    consumer.view_instance = view
    session = MagicMock()
    session.session_key = session_key
    consumer.scope = {"session": session}
    return consumer


@pytest.fixture
def resumable_store():

    previous = get_default_store()
    store = InMemoryUploadState()
    set_default_store(store)
    store.set(
        "ref-resume",
        {
            "upload_id": "ref-resume",
            "session_key": "ws-session",
            "filename": RAW_NAME,
            "bytes_received": 65536,
            "chunks_received_ranges": [[0, 0]],
        },
        ttl=60,
    )
    yield store
    set_default_store(previous)


@pytest.mark.asyncio
async def test_upload_resume_then_reregister(staged, monkeypatch, resumable_store):
    view = UploadPage()
    view.allow_upload("avatar", accept=".png", max_entries=3, max_file_size=100000)
    consumer = _consumer(view)

    from djust.uploads import resumable

    consulted = []
    original = resumable.resolve_resume_request

    def spy(**kwargs):
        consulted.append(kwargs["upload_id"])
        return original(**kwargs)

    monkeypatch.setattr(resumable, "resolve_resume_request", spy)
    await consumer._handle_upload_resume({"type": "upload_resume", "ref": "ref-resume"})
    reply = consumer.send_json.await_args.args[0]
    assert reply["type"] == "upload_resumed" and reply["ref"] == "ref-resume"
    # The resume consults the resumable state store (#4175). Since #2972 a
    # resume also needs the upload's live writer parked in this process; a
    # bare state entry (no writer) answers not_found.
    assert consulted == ["ref-resume"]
    assert reply == {
        "type": "upload_resumed",
        "ref": "ref-resume",
        "status": "not_found",
        "bytes_received": 0,
        "chunks_received": [],
    }
    assert "RAWDIR_SENTINEL" not in json.dumps(reply)

    # The client's fallback: a fresh registration with a new ref.
    await consumer._handle_upload_register(
        {
            "type": "upload_register",
            "upload_name": "avatar",
            "ref": "ref-new",
            "client_name": "again.png",
            "client_type": "image/png",
            "client_size": 10,
        }
    )
    consumer.send_error.assert_not_awaited()
    assert consumer.send_json.await_args.args[0] == {
        "type": "upload_registered",
        "ref": "ref-new",
        "upload_name": "avatar",
    }
    assert [e.ref for e in view._upload_manager.get_entries("avatar")] == ["ref-new"]


def test_resumable_writers_record_resume_state(staged, monkeypatch):
    """#4175: an explicit view resumes uploads in flight, so its resumable
    writer records the upload in the resume store."""
    from djust.tests.test_resumable_uploads_821 import _RecordingInnerWriter
    from djust.uploads.resumable import ResumableUploadWriter

    store = InMemoryUploadState()
    writer = ResumableUploadWriter.with_inner(_RecordingInnerWriter, state_store=store)
    view = UploadPage()
    view.allow_upload("avatar", accept=".png", max_entries=3, max_file_size=100000, writer=writer)
    entry = view._upload_manager.register_entry("avatar", "ref-w", RAW_NAME, "image/png", 10)
    assert entry is not None
    view._upload_manager.add_chunk("ref-w", 0, b"0123456789")
    assert entry.writer_instance is not None, "the writer never received the chunk"
    assert store.get("ref-w") is not None


@pytest.mark.asyncio
async def test_upload_resume_with_a_parked_writer(staged, monkeypatch):
    """Since #2972 a resume needs the upload's live writer parked in this
    process; the view re-attaches it (#4175)."""
    from djust.uploads import resumable
    from djust.uploads.storage import (
        InMemoryUploadState,
        _reset_default_store_for_tests,
        set_default_store,
    )

    from .test_uploads_v121_11 import _manager, _start

    _reset_default_store_for_tests()
    resumable._reset_suspended_uploads()
    set_default_store(InMemoryUploadState())
    try:
        old = _manager()
        entry = _start(old, key="ws-session")
        old.cleanup()  # the socket dropped: the writer is parked

        view = UploadPage()
        view._upload_manager = _manager()
        consumer = _consumer(view)
        await consumer._handle_upload_resume({"type": "upload_resume", "ref": entry.ref})
        reply = consumer.send_json.await_args.args[0]
        assert reply["status"] == "resumed", reply
        assert reply["chunks_received"] == [0, 1]
        assert entry.ref in view._upload_manager._entries
    finally:
        resumable._reset_suspended_uploads()
        _reset_default_store_for_tests()


# ---------------------------------------------------------------------------
# #4175: an explicit view resumes an upload across a real WebSocket reconnect,
# for its owner only.
# ---------------------------------------------------------------------------

import struct  # noqa: E402
import types  # noqa: E402
import uuid  # noqa: E402

from django.contrib.auth import get_user_model  # noqa: E402
from django.urls import path  # noqa: E402

from djust.testing import open_live_page  # noqa: E402
from djust.tests.test_resumable_uploads_821 import _RecordingInnerWriter  # noqa: E402
from djust.uploads import FRAME_CHUNK, FRAME_COMPLETE  # noqa: E402
from djust.uploads.resumable import ResumableUploadWriter  # noqa: E402
from djust.uploads.views import upload_status_urlpatterns  # noqa: E402

#: Every byte the inner writers received, in order.
WRITTEN: list = []


class _CollectingInnerWriter(_RecordingInnerWriter):
    def write_chunk(self, chunk):
        super().write_chunk(chunk)
        WRITTEN.append(bytes(chunk))


_RESUMABLE_VIDEO = ResumableUploadWriter.with_inner(_CollectingInnerWriter)


class UploadResumePage(UploadMixin, LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root>up</div>"

    def mount(self, request, **kwargs):
        self.allow_upload(
            "video",
            accept=".mp4",
            max_file_size=1_000_000,
            writer=_RESUMABLE_VIDEO,
            resumable=True,
        )


RESUME_URLCONF = types.ModuleType("upload_resume_urls")
RESUME_URLCONF.urlpatterns = [  # type: ignore[attr-defined]
    path("t/upload/", UploadResumePage.as_view()),
    path("t/upload-elsewhere/", UploadResumePage.as_view()),
    *upload_status_urlpatterns(),
]


@pytest.fixture
def resume_site():
    from djust.uploads import resumable
    from djust.uploads.storage import _reset_default_store_for_tests

    _reset_default_store_for_tests()
    resumable._reset_suspended_uploads()
    WRITTEN.clear()
    with override_settings(
        ROOT_URLCONF=RESUME_URLCONF,
        ALLOWED_HOSTS=["*"],
        DEBUG=False,
        LIVEVIEW_ALLOWED_MODULES=["djust.tests"],
    ):
        yield
    resumable._reset_suspended_uploads()
    _reset_default_store_for_tests()


def _chunk(ref, i, data):
    return bytes([FRAME_CHUNK]) + uuid.UUID(ref).bytes + struct.pack(">I", i) + data


def _complete(ref):
    return bytes([FRAME_COMPLETE]) + uuid.UUID(ref).bytes


def _user(name):
    return get_user_model().objects.create_user(username=name, password="x")


async def _upload_two_chunks(page):
    """Register a 40-byte upload and send its first two 4-byte chunks."""
    ref = str(uuid.uuid4())
    socket = await page.connect()
    await socket.send(
        {
            "type": "upload_register",
            "upload_name": "video",
            "ref": ref,
            "client_name": "clip.mp4",
            "client_type": "video/mp4",
            "client_size": 40,
        }
    )
    registered = await socket.receive(("upload_registered", "error"))
    assert registered["type"] == "upload_registered", registered
    for i, progress in ((0, 10), (1, 20)):
        await socket.communicator.send_to(bytes_data=_chunk(ref, i, b"%04d" % i))
        frame = await socket.receive(("upload_progress",))
        assert frame["progress"] == progress, frame
    return ref, socket


async def _resume(page, ref):
    socket = await page.connect()
    await socket.send({"type": "upload_resume", "ref": ref})
    return socket, await socket.receive(("upload_resumed",))


@pytest.mark.django_db(transaction=True)
async def test_explicit_view_resumes_an_upload_after_a_websocket_reconnect(resume_site):
    alice = await sync_to_async(_user)("alice")
    page = await sync_to_async(open_live_page)("/t/upload/", user=alice)
    ref, first = await _upload_two_chunks(page)
    await first.disconnect()

    second, answer = await _resume(page, ref)
    assert answer["status"] == "resumed", answer
    assert answer["chunks_received"] == [0, 1]
    for i in range(2, 10):
        await second.communicator.send_to(bytes_data=_chunk(ref, i, b"%04d" % i))
    await second.communicator.send_to(bytes_data=_complete(ref))
    done = await second.receive(("upload_progress",))
    while done.get("status") != "complete":
        assert done.get("status") != "error", done
        done = await second.receive(("upload_progress",))
    await second.disconnect()
    assert b"".join(WRITTEN) == b"".join(b"%04d" % i for i in range(10))


@pytest.mark.django_db(transaction=True)
async def test_another_user_or_session_cannot_resume_it(resume_site):
    alice = await sync_to_async(_user)("alice")
    bob = await sync_to_async(_user)("bob")
    page = await sync_to_async(open_live_page)("/t/upload/", user=alice)
    ref, first = await _upload_two_chunks(page)
    await first.disconnect()

    for other in (
        await sync_to_async(open_live_page)("/t/upload/", user=bob),
        await sync_to_async(open_live_page)("/t/upload/"),
    ):
        socket, answer = await _resume(other, ref)
        assert answer["status"] == "not_found", answer
        assert answer["chunks_received"] == [] and answer["bytes_received"] == 0
        await socket.disconnect()

    # The refusals left the parked writer alone: alice still resumes.
    socket, answer = await _resume(page, ref)
    assert answer["status"] == "resumed", answer
    await socket.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_status_endpoint_is_bound_to_the_owner(resume_site):
    alice = await sync_to_async(_user)("alice")
    bob = await sync_to_async(_user)("bob")
    page = await sync_to_async(open_live_page)("/t/upload/", user=alice)
    ref, socket = await _upload_two_chunks(page)
    url = "/djust/uploads/%s/status" % ref

    mine = await sync_to_async(page.client.get)(url)
    assert mine.status_code == 200, mine.content
    assert json.loads(mine.content)["chunks_received"] == [0, 1]
    bobs = await sync_to_async(open_live_page)("/t/upload/", user=bob)
    assert (await sync_to_async(bobs.client.get)(url)).status_code == 404
    await socket.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_the_record_is_bound_to_the_mount_owner_not_only_the_session(resume_site):
    """Same browser session and user, another page: the session check alone
    would let it resume; the owner binding (principal + view) does not."""
    from django.contrib.sessions.backends.db import SessionStore
    from django.test import RequestFactory

    from djust._exposure_sessions import request_principal_digest
    from djust.uploads.storage import get_default_store as current_store

    alice = await sync_to_async(_user)("alice")
    page = await sync_to_async(open_live_page)("/t/upload/", user=alice)
    ref, first = await _upload_two_chunks(page)
    await first.disconnect()

    def principal():
        request = RequestFactory().get("/t/upload/")
        request.session = SessionStore(page.client.session.session_key)
        request.user = alice
        return request_principal_digest(request)

    owner = current_store().get(ref)["owner"]
    assert owner == {"principal": await sync_to_async(principal)(), "view": "/t/upload/"}

    elsewhere = await sync_to_async(open_live_page)("/t/upload-elsewhere/", client=page.client)
    assert elsewhere.session.session_key == page.session.session_key
    socket, answer = await _resume(elsewhere, ref)
    assert answer["status"] == "not_found", answer
    await socket.disconnect()

    socket, answer = await _resume(page, ref)
    assert answer["status"] == "resumed", answer
    await socket.disconnect()
