"""ADR-038 E2-6: uploads under the explicit exposure policy (decision D-g).

An explicit view renders upload progress from a registered ``djust.uploads``
provider, a render-only projection of ``UploadManager.get_upload_state()``
that drops the raw client-supplied ``client_name`` (templates get the
sanitized ``safe_client_name``) and the server-side ``writer_result``.

D-g: entries in flight are not preserved across reconnect. The remount starts
with an empty upload manager, the ``upload_resume`` handshake answers
``not_found`` for an explicit view without consulting the resumable state
store, and the client falls back to ``upload_register``, which still works.

Legacy views keep their exact behaviour (no ``uploads`` context, store-backed
resume); each explicit case pins a legacy control. Only the staged
construction gate is bypassed. Runtime cases use the real ``ViewRuntime`` with
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
from djust.uploads import UploadMixin
from djust.uploads.storage import InMemoryUploadState, get_default_store, set_default_store

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
    time_travel_enabled = True
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


@pytest.mark.parametrize("policy", ["legacy", "explicit"])
def test_uploads_as_application_kwarg(staged, monkeypatch, policy):
    monkeypatch.setattr(UploadPage, "exposure_policy", policy)
    view = UploadPage()
    view.allow_upload("avatar", accept=".png")
    if policy == "legacy":
        # Control: legacy has no uploads provider, and never had an uploads
        # key; its attribute walk ran (the declared field is there).
        context = view.get_context_data(uploads="APP_SENTINEL")
        assert "uploads" not in context
        assert context["count"] == 0
    else:
        with pytest.raises(ExposureError, match="collision"):
            view.get_context_data(uploads="APP_SENTINEL")


# ---------------------------------------------------------------------------
# Rendered HTML.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("view_class", [UploadPage, LiveViewFirstPage])
@pytest.mark.parametrize("policy", ["legacy", "explicit"])
async def test_upload_progress_renders_from_the_provider(staged, monkeypatch, view_class, policy):
    monkeypatch.setattr(view_class, "exposure_policy", policy)
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
    if policy == "legacy":
        # Control: legacy views have never had an uploads context.
        assert "|entry:" not in frames and "|cfg" not in frames
    else:
        assert f"|entry:{SAFE_NAME}:0|" in frames
        assert "|cfg" in frames


# ---------------------------------------------------------------------------
# The mount frame carries configuration only.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("policy", ["legacy", "explicit"])
async def test_mount_frame_upload_configs_are_configuration_only(staged, monkeypatch, policy):
    monkeypatch.setattr(MountRegisteredPage, "exposure_policy", policy)
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
            "chunk_size": 64 * 1024,
            "auto_upload": True,
            "resumable": False,
        }
    }
    configs = json.dumps(frame["upload_configs"])
    for sentinel in PERSISTENCE_SENTINELS + ("ref-1", '"entries"'):
        assert sentinel not in configs
    whole = json.dumps(frame)
    assert "RAWDIR_SENTINEL" not in whole and WRITER_SENTINEL not in whole
    if policy == "explicit":
        # The only place the entry reaches the frame is the rendered page.
        assert f"|entry:{SAFE_NAME}:0|" in frame["html"]


# ---------------------------------------------------------------------------
# State destinations: server envelope, signed snapshot, debug, time travel.
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
    history = json.dumps([snapshot.to_dict() for snapshot in view._time_travel_buffer._buf])
    assert history != "[]", "time travel must have recorded the event"
    with override_settings(DEBUG=True):
        register_view("exposure-upload-test", view)
        try:
            response = await sync_to_async(view_assigns)(
                rf.get("/debug/", {"session_id": "exposure-upload-test"})
            )
        finally:
            unregister_view("exposure-upload-test")
    assert response.status_code == 200
    for sentinel in PERSISTENCE_SENTINELS + ("ref-1",):
        assert sentinel not in stored
        assert sentinel not in token
        assert sentinel not in debug
        assert sentinel not in history
        assert sentinel not in response.content.decode()


# ---------------------------------------------------------------------------
# D-g: reconnect starts over.
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
@pytest.mark.parametrize("policy", ["legacy", "explicit"])
async def test_upload_resume_then_reregister(staged, monkeypatch, resumable_store, policy):
    monkeypatch.setattr(UploadPage, "exposure_policy", policy)
    view = UploadPage()
    view.allow_upload("avatar", accept=".png", max_entries=3, max_file_size=100000)
    consumer = _consumer(view)

    await consumer._handle_upload_resume({"type": "upload_resume", "ref": "ref-resume"})
    reply = consumer.send_json.await_args.args[0]
    assert reply["type"] == "upload_resumed" and reply["ref"] == "ref-resume"
    if policy == "legacy":
        # Control: legacy resumes from the state store.
        assert reply["status"] == "resumed"
        assert reply["bytes_received"] == 65536
    else:
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
