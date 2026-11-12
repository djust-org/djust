"""2.0 removed ``enable_state_snapshot`` and ``PersistentLiveView``; both are refused.

``enable_state_snapshot = True`` saved a legacy view's whole public state to
the session after every socket event and restored it, in lieu of ``mount()``,
on a reconnect. ``PersistentLiveView`` was ``LiveView`` with the flag on.
Ignoring the flag would leave such a view silently mounting fresh, so it is
refused the way ``use_actors`` is (``test_removed_view_attributes``): when the
class is created, and again on the mount path for the shapes the class check
cannot see. Every refusal names the replacement, ``state(persist="server")``,
and the migration guide.

The last section drives a real ``WebsocketCommunicator``: a legacy-pinned view
whose session still holds a 1.x save mounts fresh and writes nothing back.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ImproperlyConfigured

from djust import LiveView, event_handler
from djust._removed_settings import MIGRATION_GUIDE_URL, REMOVED_VIEW_ATTRIBUTES


def _assert_names_the_replacement(message: str) -> None:
    assert "enable_state_snapshot" in message
    assert "removed in djust 2.0" in message
    assert 'state(..., persist="server")' in message
    assert MIGRATION_GUIDE_URL in message


# --------------------------------------------------------------------------
# Class creation
# --------------------------------------------------------------------------


@pytest.mark.parametrize("policy", ["legacy", "explicit"])
def test_a_view_that_sets_the_flag_is_refused_at_class_creation(policy):
    with pytest.raises(ImproperlyConfigured) as refused:

        class SnapshotView(LiveView):
            exposure_policy = policy
            template = "<div dj-root></div>"
            enable_state_snapshot = True

    assert "SnapshotView" in str(refused.value)
    _assert_names_the_replacement(str(refused.value))


def test_a_mixin_that_sets_the_flag_is_refused_where_it_meets_liveview():
    class SnapshotMixin:
        enable_state_snapshot = True

    with pytest.raises(ImproperlyConfigured, match="enable_state_snapshot"):

        class MixedView(SnapshotMixin, LiveView):
            exposure_policy = "legacy"
            template = "<div dj-root></div>"


def test_a_subclass_of_a_plain_view_that_turns_it_on_is_refused():
    class Base(LiveView):
        exposure_policy = "legacy"
        template = "<div dj-root></div>"

    with pytest.raises(ImproperlyConfigured, match="Child"):

        class Child(Base):
            enable_state_snapshot = True


def test_the_flag_false_and_an_ordinary_view_are_accepted():
    class Off(LiveView):
        exposure_policy = "legacy"
        template = "<div dj-root></div>"
        enable_state_snapshot = False

    assert Off() is not None
    assert not hasattr(LiveView, "enable_state_snapshot")
    assert "enable_state_snapshot" in REMOVED_VIEW_ATTRIBUTES


# --------------------------------------------------------------------------
# PersistentLiveView
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "statement",
    ["from djust import PersistentLiveView", "from djust.live_view import PersistentLiveView"],
)
def test_importing_persistent_live_view_names_the_replacement(statement):
    with pytest.raises(ImportError) as raised:
        exec(statement, {})
    message = str(raised.value)
    assert "PersistentLiveView was removed in djust 2.0" in message
    assert 'state(..., persist="server")' in message
    assert MIGRATION_GUIDE_URL in message


@pytest.mark.parametrize("module_name", ["djust", "djust.live_view"])
def test_reaching_persistent_live_view_as_an_attribute_is_refused(module_name):
    import importlib

    module = importlib.import_module(module_name)
    with pytest.raises(ImportError, match="removed in djust 2.0"):
        module.PersistentLiveView  # noqa: B018 — the attribute access is the subject
    assert "PersistentLiveView" not in module.__all__


def test_an_unknown_name_is_still_an_ordinary_attribute_error():
    import djust
    import djust.live_view

    assert not hasattr(djust, "no_such_name_was_ever_here")
    assert not hasattr(djust.live_view, "no_such_name_was_ever_here")


# --------------------------------------------------------------------------
# The mount path. Three shapes the class-creation check cannot see, each over
# the HTTP GET and the shared runtime mount (WebSocket and SSE).
# --------------------------------------------------------------------------


class InstanceAttributeSnapshotView(LiveView):
    """Sets it on the instance, in ``mount()``."""

    exposure_policy = "legacy"
    template = "<div dj-root>instance</div>"

    def mount(self, request, **kwargs):
        self.enable_state_snapshot = True


class LateClassAttributeSnapshotView(LiveView):
    """Plain when created; a test assigns the class attribute afterwards."""

    exposure_policy = "legacy"
    template = "<div dj-root>late</div>"


class _SkipsSuper:
    def __init_subclass__(cls, **kwargs):
        pass  # does not call super(): LiveView.__init_subclass__ never runs


class HiddenFromInitSubclassSnapshotView(_SkipsSuper, LiveView):
    exposure_policy = "legacy"
    template = "<div dj-root>hidden</div>"
    enable_state_snapshot = True


class PlainSnapshotlessView(LiveView):
    exposure_policy = "legacy"
    template = "<div dj-root>plain</div>"


SHAPES = [
    "InstanceAttributeSnapshotView",
    "LateClassAttributeSnapshotView",
    "HiddenFromInitSubclassSnapshotView",
]


@pytest.fixture
def shape(request, monkeypatch):
    if request.param == "LateClassAttributeSnapshotView":
        monkeypatch.setattr(
            LateClassAttributeSnapshotView, "enable_state_snapshot", True, raising=False
        )
    return globals()[request.param]


def _http_request(rf):
    from django.contrib.auth.models import AnonymousUser
    from django.contrib.sessions.backends.db import SessionStore

    request = rf.get("/removed-snapshot/")
    request.user = AnonymousUser()
    request.session = SessionStore()
    request.session.create()
    return request


@pytest.mark.django_db
@pytest.mark.parametrize("shape", SHAPES, indirect=True)
def test_the_http_get_refuses_each_shape(shape, rf):
    with pytest.raises(ImproperlyConfigured) as refused:
        shape.as_view()(_http_request(rf))
    assert shape.__name__ in str(refused.value)
    _assert_names_the_replacement(str(refused.value))


@pytest.mark.django_db
def test_the_http_get_still_serves_a_plain_view(rf):
    response = PlainSnapshotlessView.as_view()(_http_request(rf))
    assert response.status_code == 200 and b"plain" in response.content


async def _runtime_mount(view_class):
    from asgiref.sync import sync_to_async
    from django.test import override_settings

    from djust.runtime import ViewRuntime

    from .test_exposure_runtime import make_request
    from .test_runtime_state_save_tt_1894 import MockTransport

    request = await sync_to_async(make_request)()
    transport = MockTransport()
    transport.build_request = lambda: request
    runtime = ViewRuntime(transport)
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], DEBUG=True):
        await runtime.dispatch_mount(
            {
                "type": "mount",
                "view": "%s.%s" % (__name__, view_class.__name__),
                "url": "/removed-snapshot/",
            }
        )
    return transport


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("shape", SHAPES, indirect=True)
async def test_the_runtime_mount_refuses_each_shape(shape, caplog):
    transport = await _runtime_mount(shape)
    frames = [frame["type"] for frame in transport.sent]
    assert "mount" not in frames, frames
    assert frames[-1] == "error", frames
    assert "enable_state_snapshot" in caplog.text and "removed in djust 2.0" in caplog.text


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_the_runtime_mount_still_mounts_a_plain_view():
    transport = await _runtime_mount(PlainSnapshotlessView)
    assert [frame["type"] for frame in transport.sent][-1] == "mount"


# --------------------------------------------------------------------------
# A legacy view over a real socket: a 1.x session save is neither read nor written
# --------------------------------------------------------------------------

_PATH = "/removed-snapshot-ws/"


class LegacyCounterView(LiveView):
    exposure_policy = "legacy"
    template = '<div dj-root dj-id="0"><b>n={{ n }}</b></div>'

    def mount(self, request, **kwargs):
        self.n = 0

    @event_handler()
    def bump(self, **kwargs):
        self.n += 1


def _last_is(*types):
    return lambda frames: bool(frames) and frames[-1].get("type") in types


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_a_legacy_view_ignores_a_saved_session_state_on_reconnect_and_saves_none():
    pytest.importorskip("channels")
    from asgiref.sync import sync_to_async
    from channels.testing import WebsocketCommunicator
    from django.contrib.auth.models import AnonymousUser
    from django.contrib.sessions.backends.db import SessionStore
    from django.test import override_settings

    from djust.websocket import LiveViewConsumer

    from ._ws_frames import receive_until

    def planted():
        store = SessionStore()
        # What a 1.x per-event save left behind for this page.
        store[f"liveview_{_PATH}"] = {"n": 41}
        store.create()
        return store.session_key

    key = await sync_to_async(planted)()
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], DEBUG=False):
        socket = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        socket.scope["session"] = SessionStore(key)
        socket.scope["user"] = AnonymousUser()
        connected, _ = await socket.connect()
        assert connected
        try:
            await socket.receive_json_from()  # connect ack
            await socket.send_json_to(
                {"type": "mount", "view": f"{__name__}.LegacyCounterView", "url": _PATH}
            )
            mounted = (await receive_until(socket, _last_is("mount", "error")))[-1]
            assert mounted["type"] == "mount", mounted
            assert "n=0" in mounted["html"], mounted["html"]
            await socket.send_json_to({"type": "event", "event": "bump", "params": {}})
            changed = (await receive_until(socket, _last_is("patch", "html_update", "error")))[-1]
            assert changed["type"] != "error" and "1" in str(changed), changed
        finally:
            await socket.disconnect()

    stored = await sync_to_async(lambda: SessionStore(key).load())()
    assert stored.get(f"liveview_{_PATH}") == {"n": 41}, "the socket turn wrote no state"


# --------------------------------------------------------------------------
# C304, retargeted: it now reads declared client fields (plan §5 G3)
# --------------------------------------------------------------------------


def _c304_text():
    from djust.checks.integrations import check_service_worker_advanced

    return " ".join(m.msg for m in check_service_worker_advanced(None) if m.id == "djust.C304")


def test_c304_names_a_pii_like_field_the_browser_receives():
    from djust.decorators import state

    class LeakyClientState(LiveView):
        exposure_policy = "explicit"
        template = "<div dj-root></div>"
        api_key = state("", client=True)
        password = state("", persist="client", client=True)

    text = _c304_text()
    assert "LeakyClientState" in text
    assert "api_key (client=True)" in text
    assert 'password (persist="client")' in text


def test_c304_is_silent_for_server_side_and_clean_client_fields():
    from djust.decorators import state

    class ServerSideSecrets(LiveView):
        exposure_policy = "explicit"
        template = "<div dj-root></div>"
        token = state("", persist="server")
        secret = state("")
        title = state("", client=True)

    class LegacyWithPiiNames(LiveView):
        exposure_policy = "legacy"
        template = "<div dj-root></div>"
        password = ""

    text = _c304_text()
    assert "ServerSideSecrets" not in text
    assert "LegacyWithPiiNames" not in text
