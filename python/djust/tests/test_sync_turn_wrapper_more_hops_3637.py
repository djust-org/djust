"""#3637: ``sync_turn_wrapper`` also covers the object-permission check, the view-level
auth check, ``on_mount`` hooks, ``connected()`` / ``disconnected()``, the cursor and
presence hooks, and the time-travel replay.

Before this change those view hooks ran on the worker thread through a bare
``sync_to_async`` with NO wrapper entry. An app that opens a per-turn database scope
in the wrapper (a transaction with row-level-security settings) read rows with no
scope in them: ``get_object()`` found nothing and the object-permission check failed
CLOSED as "Access denied" for a caller who was allowed, on the socket only.

Every behavioural test records the step name AND whether the wrapper was open while the
hook body ran (a thread-local the wrapper sets), so a test passes only if the hook
really ran inside the scope. The structural pin at the bottom fails when a new raw
``sync_to_async(<view hook>)`` appears.
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import pathlib
import threading
from typing import Dict, List, Optional, Tuple

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.contrib.auth.decorators import user_passes_test
from django.contrib.auth.mixins import UserPassesTestMixin
from django.test import override_settings
from django.utils.decorators import method_decorator

from djust import LiveView
from djust.config import config as djust_config
from djust.decorators import event_handler
from djust.hooks import on_mount
from djust.presence import PresenceMixin

pytest.importorskip("channels")

MOD = __name__
PACKAGE = pathlib.Path(__file__).resolve().parents[1]

ENTERED: List[Tuple[str, int, Optional[Tuple[str, str]]]] = []
SEEN: Dict[str, List[Optional[str]]] = {}
_OPEN = threading.local()
#: What the wrapper does for each step name: "raise", "swallow" or "exit-raises".
MODE: Dict[str, str] = {}


def recording_wrapper(view, step, turn):
    @contextlib.contextmanager
    def scope():
        mode = MODE.get(step)
        ENTERED.append(
            (step, threading.get_ident(), None if turn is None else (turn.kind, turn.source))
        )
        if mode == "raise":
            raise RuntimeError("the wrapper cannot open its scope")
        _OPEN.step = step
        try:
            yield
        except Exception:
            if mode == "swallow":
                return
            raise
        finally:
            _OPEN.step = None
        if mode == "exit-raises":
            raise RuntimeError("the wrapper cannot commit")

    return scope()


def note(label: str) -> None:
    SEEN.setdefault(label, []).append(getattr(_OPEN, "step", None))


def steps() -> List[str]:
    return [step for step, _thread, _turn in ENTERED]


def turns(step: str) -> List[Optional[Tuple[str, str]]]:
    return [turn for entered, _thread, turn in ENTERED if entered == step]


@pytest.fixture
def probe():
    previous = djust_config.get("sync_turn_wrapper")
    ENTERED.clear()
    SEEN.clear()
    MODE.clear()
    djust_config.set("sync_turn_wrapper", recording_wrapper)
    yield
    djust_config.set("sync_turn_wrapper", previous)
    ENTERED.clear()
    SEEN.clear()
    MODE.clear()


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------


@on_mount
def _record_on_mount(view, request, **kwargs):
    note("on_mount")
    if MODE.get("on_mount-redirect"):
        return "/elsewhere/"
    if MODE.get("on_mount-raises"):
        raise RuntimeError("an on_mount hook failed")
    return None


class AuthProbeView(LiveView):
    """Every hook this change wraps, on one view."""

    template = f'<div dj-root dj-view="{MOD}.AuthProbeView"><b>{{{{ count }}}}</b></div>'
    on_mount = [_record_on_mount]

    def mount(self, request, **kwargs):
        self.count = 0
        self.denied = False

    def check_permissions(self, request):
        note("check_permissions")
        return not MODE.get("deny-check_permissions")

    def get_object(self):
        note("get_object")
        if MODE.get("no-object"):
            return None
        return object()

    def has_object_permission(self, request, obj):
        note("has_object_permission")
        return not MODE.get("deny-has_object_permission")

    def connected(self):
        note("connected")

    def disconnected(self):
        note("disconnected")

    def handle_cursor_move(self, x, y):
        note("cursor")

    def update_presence_heartbeat(self):
        note("heartbeat")

    @event_handler()
    def bump(self, **kwargs):
        self.count += 1

    def handle_params(self, params, uri, **kwargs):
        pass

    def get_context_data(self, **kwargs):
        return {"count": self.count}


class PresenceProbeView(PresenceMixin, LiveView):
    template = f'<div dj-root dj-view="{MOD}.PresenceProbeView"><b>{{{{ count }}}}</b></div>'
    presence_key = "probe"

    def mount(self, request, **kwargs):
        self.count = 0
        self.track_presence()

    def get_presence_key(self):
        note("presence_key")
        return "probe"

    def get_context_data(self, **kwargs):
        return {"count": self.count}


# ---------------------------------------------------------------------------
# A real socket
# ---------------------------------------------------------------------------


async def _open(view_name):
    from channels.testing import WebsocketCommunicator
    from django.contrib.sessions.backends.db import SessionStore

    from djust.websocket import LiveViewConsumer

    def _create_session():
        session = SessionStore()
        session.create()
        return session.session_key

    key = await sync_to_async(_create_session)()

    class _ScopeSession:
        session_key = key

    communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    communicator.scope["session"] = _ScopeSession()
    communicator.scope["user"] = AnonymousUser()
    connected, _ = await communicator.connect()
    assert connected
    await communicator.receive_json_from(timeout=3)
    await communicator.send_json_to(
        {"type": "mount", "view": f"{MOD}.{view_name}", "url": "/probe/"}
    )
    return communicator


async def _mounted(view_name):
    communicator = await _open(view_name)
    for _ in range(8):
        frame = await communicator.receive_json_from(timeout=5)
        if frame.get("type") == "mount":
            return communicator
    raise AssertionError("no mount frame")


async def _until(predicate, *, seconds=8.0):
    for _ in range(int(seconds / 0.02)):
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition not reached; entered=%r seen=%r" % (steps(), SEEN))


@contextlib.asynccontextmanager
async def socket(view_name="AuthProbeView"):
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[MOD]):
        comm = await _mounted(view_name)
        try:
            yield comm
        finally:
            await comm.disconnect()


# ---------------------------------------------------------------------------
# The socket mount: auth, object permission, on_mount, connected
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_socket_mount_runs_its_auth_and_lifecycle_hooks_inside_the_wrapper(probe):
    loop_thread = threading.get_ident()
    async with socket():
        pass

    # Each hook body ran with the matching step open.
    assert set(SEEN["check_permissions"]) == {"auth"}, SEEN
    assert set(SEEN["get_object"]) == {"auth"}, SEEN
    assert set(SEEN["has_object_permission"]) == {"auth"}, SEEN
    assert set(SEEN["on_mount"]) == {"lifecycle"}, SEEN
    assert set(SEEN["connected"]) == {"lifecycle"}, SEEN
    # On a worker thread, with the mount's own turn.
    for step in ("auth", "lifecycle"):
        threads = {thread for entered, thread, _turn in ENTERED if entered == step}
        assert threads and loop_thread not in threads, (step, threads)
    assert ("mount", "page") in set(turns("auth")) or ("mount", "slot") in set(turns("auth"))


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_socket_disconnect_runs_disconnected_inside_the_wrapper(probe):
    async with socket():
        pass
    await _until(lambda: SEEN.get("disconnected"))

    assert set(SEEN["disconnected"]) == {"lifecycle"}, SEEN


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_per_event_object_permission_check_runs_inside_the_wrapper(probe):
    async with socket() as comm:
        SEEN.clear()
        ENTERED.clear()
        await comm.send_json_to({"type": "event", "event": "bump", "params": {}, "ref": 1})
        await _until(lambda: SEEN.get("has_object_permission"))

    assert set(SEEN["get_object"]) == {"auth"}, SEEN
    assert set(SEEN["has_object_permission"]) == {"auth"}, SEEN
    assert turns("auth")[0] == ("event", "client"), turns("auth")


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_url_change_object_permission_check_runs_inside_the_wrapper(probe):
    async with socket() as comm:
        SEEN.clear()
        ENTERED.clear()
        await comm.send_json_to({"type": "url_change", "params": {"p": "2"}, "uri": "/probe/?p=2"})
        await _until(lambda: SEEN.get("has_object_permission"))

    assert set(SEEN["get_object"]) == {"auth"}, SEEN
    assert set(SEEN["has_object_permission"]) == {"auth"}, SEEN


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_socket_cursor_move_runs_inside_the_wrapper(probe):
    async with socket() as comm:
        ENTERED.clear()
        await comm.send_json_to({"type": "cursor_move", "x": 1, "y": 2})
        await _until(lambda: SEEN.get("cursor"))

    assert set(SEEN["cursor"]) == {"hook"}, SEEN
    assert "hook" in steps()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_socket_presence_heartbeat_runs_inside_the_wrapper(probe):
    async with socket() as comm:
        ENTERED.clear()
        await comm.send_json_to({"type": "presence_heartbeat"})
        await _until(lambda: SEEN.get("heartbeat"))

    assert set(SEEN["heartbeat"]) == {"hook"}, SEEN


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_get_presence_key_runs_inside_the_wrapper_on_mount_and_on_teardown(probe):
    async with socket("PresenceProbeView"):
        # joined the presence group after mount (a "hook" step), tracked in mount()
        assert SEEN["presence_key"], SEEN
    await _until(lambda: len(SEEN["presence_key"]) >= 3)

    # No read of the key happened with the wrapper closed: not the join, not the
    # untrack on disconnect.
    assert None not in SEEN["presence_key"], SEEN
    assert {"hook", "mount"} >= set(SEEN["presence_key"]), SEEN
    assert "hook" in SEEN["presence_key"], SEEN


# ---------------------------------------------------------------------------
# Time-travel replay
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_time_travel_replay_runs_the_handler_inside_the_wrapper(
    probe, settings, monkeypatch
):
    from djust.tests.test_debug_render_contracts import DebugView, setup

    @event_handler(parameter_policy="strict")
    def advance(self, value: int):
        note("replayed")
        self.count += value

    monkeypatch.setattr(DebugView, "advance", advance)
    settings.DEBUG = True
    consumer, view = await setup()
    ENTERED.clear()

    await consumer.handle_forward_replay({"from_index": 0})

    assert SEEN["replayed"] == ["event"], SEEN
    assert "event" in steps()
    assert view.count == 9


# ---------------------------------------------------------------------------
# Render hops that were still raw (get_context_data / component render)
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_streamed_fragment_render_runs_get_context_data_inside_the_wrapper(probe):
    class Streamer(LiveView):
        template = "<div dj-stream='feed'>{{ n }}</div>"

        def get_context_data(self, **kwargs):
            note("stream_context")
            return {"n": 1}

    await Streamer()._render_stream_fragment("feed")

    assert SEEN["stream_context"] == ["render"], SEEN


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_sticky_childs_state_save_runs_get_context_data_inside_the_wrapper(probe):
    from djust.mixins.sticky import save_sticky_child_state

    class Child(LiveView):
        sticky_id = "c1"

        def get_context_data(self, **kwargs):
            note("sticky_context")
            return {"n": 1}

    class Sessionish:
        async def aset(self, key, value):
            pass

        async def apop(self, key, default=None):
            return default

    await save_sticky_child_state(Child(), Sessionish(), "app.Parent")

    assert SEEN["sticky_context"] == ["render"], SEEN


def _gate(user):
    note("post_gate")
    return bool(getattr(user, "is_superuser", False))


class PostGatedView(LiveView):
    """Access control only Django's ``post()`` runs: replayed for an event."""

    template = "<div dj-root>x</div>"

    @method_decorator(user_passes_test(_gate))
    def post(self, request, *args, **kwargs):  # pragma: no cover - never called
        return None


class AsyncAuthView(UserPassesTestMixin, LiveView):
    """``async def check_permissions`` makes ``acheck_view_auth`` run the sync steps
    around it in their own hops."""

    template = "<div dj-root>x</div>"
    permission_required = "app.view"

    async def check_permissions(self, request):
        note("async_check_permissions")
        return True

    def test_func(self):
        note("test_func")
        return True


class _Person:
    is_authenticated = True
    is_active = True
    is_superuser = True
    pk = 1

    def has_perms(self, perms):
        note("has_perms")
        return True

    def has_perm(self, perm):
        note("has_perms")
        return True


def test_an_event_access_check_runs_the_replayed_decorator_inside_the_wrapper(probe):
    from django.test import RequestFactory

    from djust.auth.core import check_event_auth

    request = RequestFactory().post("/x/")
    request.user = _Person()

    assert check_event_auth(PostGatedView(), request) is None

    assert SEEN["post_gate"] == ["auth"], SEEN
    assert steps() == ["auth"], steps()


@pytest.mark.asyncio
async def test_the_async_check_permissions_path_runs_its_sync_hops_inside_the_wrapper(probe):
    from django.test import RequestFactory

    from djust.auth.core import acheck_view_auth

    request = RequestFactory().get("/x/")
    request.user = _Person()

    assert await acheck_view_auth(AsyncAuthView(), request) is None

    # ``async def`` check_permissions: awaited on the loop, never wrapped (documented).
    assert SEEN["async_check_permissions"] == [None], SEEN
    # The sync hops around it: login/permission_required, then the Django mixins.
    assert SEEN["has_perms"] == ["auth"], SEEN
    assert SEEN["test_func"] == ["auth"], SEEN
    assert steps() == ["auth", "auth"], steps()


def test_the_http_sticky_state_save_runs_get_context_data_inside_the_wrapper(probe):
    from djust.mixins.sticky import save_sticky_child_state_sync

    class Child(LiveView):
        sticky_id = "c2"

        def get_context_data(self, **kwargs):
            note("sticky_sync_context")
            return {"n": 1}

    save_sticky_child_state_sync(Child(), {}, "app.Parent")

    assert SEEN["sticky_sync_context"] == ["render"], SEEN


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_scoped_component_render_runs_component_render_inside_the_wrapper(
    probe, monkeypatch
):
    from djust.tests import test_component_scoped_render_2917 as harness

    from djust.components.base import BoundComponent

    original = BoundComponent.render

    def render(self, *args, **kwargs):
        note("component_render")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(BoundComponent, "render", render)
    view, runtime, transport = harness._mounted(harness.OpaquePage)
    SEEN.clear()  # the mount baseline above renders outside any turn

    await runtime.dispatch_event(
        {"type": "event", "event": "select", "params": {"component_id": "nav", "value": "billing"}}
    )

    assert SEEN.get("component_render"), "the scoped path must have rendered the component"
    assert set(SEEN["component_render"]) == {"render"}, SEEN


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_session_restore_mount_replays_presence_inside_the_wrapper(probe, monkeypatch):
    """The restore branch of the runtime mount replaces ``mount()``; it re-registers
    presence (``get_presence_key`` is application code) in its own hop."""
    from djust.tests import test_runtime_mount_state_restore_1913 as harness

    def _restore_presence(self):
        note("restore_presence")

    monkeypatch.setattr(
        harness._OptInSnapView, "_restore_presence", _restore_presence, raising=False
    )
    session = await sync_to_async(harness._make_db_session)()
    await session.aset(f"liveview_{harness.PAGE_URL}", {"role": "admin", "n": 42})
    await sync_to_async(session.save)()
    runtime, _transport = harness._make_runtime(harness._make_request(session))

    await harness._mount(
        runtime, {"type": "mount", "view": harness.VIEW_PATH, "url": harness.PAGE_URL}
    )

    assert runtime.view_instance._mounted_from_restore is True
    assert SEEN["restore_presence"] == ["hook"], SEEN


def _restore_harness(monkeypatch):
    """The 1913 runtime-mount harness with the view's snapshot hooks recording."""
    from djust.tests import test_runtime_mount_state_restore_1913 as harness

    view_cls = harness._OptInSnapView
    original_restore = view_cls._restore_snapshot
    original_gate = view_cls._should_restore_snapshot
    original_capture = view_cls._capture_snapshot_state

    def _restore_snapshot(self, state):
        note("restore_snapshot")
        if MODE.get("restore_snapshot-raises"):
            raise RuntimeError("the application override failed")
        return original_restore(self, state)

    def _should_restore_snapshot(self, request):
        note("should_restore")
        return original_gate(self, request)

    def _capture_snapshot_state(self, *, strict=False):
        note("capture_snapshot")
        return original_capture(self, strict=strict)

    monkeypatch.setattr(view_cls, "_restore_snapshot", _restore_snapshot)
    monkeypatch.setattr(view_cls, "_should_restore_snapshot", _should_restore_snapshot)
    monkeypatch.setattr(view_cls, "_capture_snapshot_state", _capture_snapshot_state)
    return harness


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_snapshot_restore_and_capture_overrides_run_inside_the_wrapper(
    probe, monkeypatch
):
    """``_should_restore_snapshot``, ``_restore_snapshot`` and ``_capture_snapshot_state``
    are documented subclass overrides that may read rows."""
    harness = _restore_harness(monkeypatch)
    session = await sync_to_async(harness._make_db_session)()
    runtime, transport = harness._make_runtime(harness._make_request(session))
    snap = harness._signed_snapshot(
        {"role": "admin", "n": 7}, slug=harness.VIEW_PATH, session_key=session.session_key
    )

    await harness._mount(
        runtime,
        {
            "type": "mount",
            "view": harness.VIEW_PATH,
            "url": harness.PAGE_URL,
            "state_snapshot": snap,
        },
    )

    assert runtime.view_instance._mounted_from_restore is True
    assert set(SEEN["should_restore"]) == {"lifecycle"}, SEEN
    assert set(SEEN["restore_snapshot"]) == {"lifecycle"}, SEEN
    assert SEEN["capture_snapshot"] and set(SEEN["capture_snapshot"]) == {"lifecycle"}, SEEN


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_wrapper_that_swallows_a_failing_restore_override_falls_back_to_mount(
    probe, monkeypatch
):
    harness = _restore_harness(monkeypatch)
    MODE["lifecycle"] = "swallow"
    MODE["restore_snapshot-raises"] = "1"
    session = await sync_to_async(harness._make_db_session)()
    runtime, _transport = harness._make_runtime(harness._make_request(session))
    snap = harness._signed_snapshot(
        {"role": "admin", "n": 7}, slug=harness.VIEW_PATH, session_key=session.session_key
    )

    await harness._mount(
        runtime,
        {
            "type": "mount",
            "view": harness.VIEW_PATH,
            "url": harness.PAGE_URL,
            "state_snapshot": snap,
        },
    )

    view = runtime.view_instance
    assert view._mounted_from_restore is False, "a swallowed failure is not a restore"
    assert view.role == "user", "the view mounted fresh instead of keeping half a restore"


def test_the_scoped_presence_probe_runs_inside_the_wrapper(probe):
    """``_presence_probe`` (the scoped-presence sync after a turn) is one ``hook`` step."""
    from djust.push import _presence_probe

    class V(PresenceMixin, LiveView):
        presence_key = "probe"

        def get_presence_key(self):
            note("probe_key")
            return "probe"

    key, stale = _presence_probe(V(), True, False)

    assert (key, stale) == ("probe", False)
    assert SEEN["probe_key"] == ["hook"], SEEN


# ---------------------------------------------------------------------------
# Fail closed: a wrapper that raises or swallows never opens a way through
# ---------------------------------------------------------------------------


def _view_and_request():
    from django.test import RequestFactory

    view = AuthProbeView()
    request = RequestFactory().get("/probe/")
    request.user = type("User", (), {"is_authenticated": True, "pk": 1})()
    view.request = request
    return view, request


@pytest.mark.parametrize("step", ["auth"])
@pytest.mark.parametrize("how", ["raise", "exit-raises"])
def test_a_wrapper_that_raises_denies_the_object_permission_check(probe, step, how):
    from djust.auth.core import enforce_object_permission

    view, request = _view_and_request()
    MODE[step] = how

    with pytest.raises(PermissionDenied):
        enforce_object_permission(view, request)
    # A refused check caches no object, even when the hooks themselves allowed it.
    assert getattr(view, "_object", None) is None


def test_a_wrapper_that_swallows_a_denial_still_denies_the_object_permission_check(probe):
    from djust.auth.core import check_object_permission

    view, request = _view_and_request()
    MODE["auth"] = "swallow"
    MODE["deny-has_object_permission"] = "1"

    with pytest.raises(PermissionDenied):
        check_object_permission(view, request)
    assert getattr(view, "_object", None) is None


def test_a_wrapper_that_swallows_a_denial_still_denies_the_view_level_check(probe):
    from djust.auth.core import check_view_auth

    view, request = _view_and_request()
    MODE["auth"] = "swallow"
    # ``check_permissions`` returns False for a signed-in person: PermissionDenied.
    MODE["deny-check_permissions"] = "1"

    with pytest.raises(PermissionDenied):
        check_view_auth(view, request)


def test_a_wrapper_that_raises_denies_the_view_level_check(probe):
    from djust.auth.core import check_view_auth

    view, request = _view_and_request()
    MODE["auth"] = "raise"

    with pytest.raises(RuntimeError):
        check_view_auth(view, request)
    assert "check_permissions" not in SEEN, "the hook must not run when the scope cannot open"


def test_a_wrapper_that_swallows_a_failing_on_mount_hook_refuses_the_mount(probe):
    from djust.hooks import run_on_mount_hooks

    view, request = _view_and_request()
    MODE["lifecycle"] = "swallow"
    MODE["on_mount-raises"] = "1"

    with pytest.raises(PermissionDenied):
        run_on_mount_hooks(view, request)


def test_an_allowing_wrapper_changes_nothing_for_the_verdicts(probe):
    from djust.auth.core import check_object_permission, check_view_auth
    from djust.hooks import run_on_mount_hooks

    view, request = _view_and_request()

    assert check_view_auth(view, request) is None
    check_object_permission(view, request)
    assert view._object is not None
    assert run_on_mount_hooks(view, request) is None
    MODE["on_mount-redirect"] = "1"
    assert run_on_mount_hooks(view, request) == "/elsewhere/"
    assert {"auth", "lifecycle"} <= set(steps())


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_wrapper_that_raises_refuses_a_socket_mount_instead_of_mounting(probe):
    MODE["auth"] = "raise"
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[MOD]):
        comm = await _open("AuthProbeView")
        try:
            frames = []
            for _ in range(6):
                try:
                    frames.append(await comm.receive_json_from(timeout=3))
                except (asyncio.TimeoutError, Exception):  # noqa: BLE001 - socket closed
                    break
        finally:
            with contextlib.suppress(asyncio.CancelledError):
                await comm.disconnect()

    assert "mount" not in {frame.get("type") for frame in frames}, frames
    assert "check_permissions" not in SEEN and "get_object" not in SEEN, SEEN


# ---------------------------------------------------------------------------
# No double entry, nothing paid when unset
# ---------------------------------------------------------------------------


def test_an_auth_step_inside_another_step_does_not_enter_the_wrapper_again(probe):
    from djust.auth.core import check_object_permission, check_view_auth
    from djust.sync_turn import run_sync_step

    view, request = _view_and_request()

    def inside_an_event():
        before = len(ENTERED)
        check_view_auth(view, request)
        check_object_permission(view, request)
        return len(ENTERED) - before

    assert run_sync_step(view, "event", inside_an_event) == 0
    assert steps() == ["event"]
    # Their hooks ran under the OUTER step.
    assert set(SEEN["get_object"]) == {"event"}


def test_the_wrapper_is_not_paid_for_when_unset():
    from djust._child_lifecycle import begin_live_connection
    from djust.auth.core import check_object_permission
    from djust.sync_turn import guarded_step, sync_step

    assert djust_config.get("sync_turn_wrapper") in (None, "")

    def fn():
        return 1

    assert sync_step(None, "lifecycle", fn) is fn
    assert guarded_step(None, "auth", fn) is fn

    view, request = _view_and_request()
    check_object_permission(view, request)
    assert view._object is not None
    hook = begin_live_connection(view)
    assert hook is not None and getattr(hook, "__func__", None) is AuthProbeView.connected


def test_the_http_fallbacks_result_hook_is_an_async_step_not_a_generic_hook(probe):
    """#3630 runs ``handle_async_result`` through ``call_hook_sync``; it must enter
    the ``async`` step like every other owner, not ``hook``."""
    from djust._view_hooks import call_hook_sync

    class V(LiveView):
        def handle_async_result(self, name, result=None, error=None):
            note("http_result")

        def handle_tick(self):
            note("http_tick")

    view = V()
    call_hook_sync(view.handle_async_result, "job", result=1, error=None)
    call_hook_sync(view.handle_tick)

    assert SEEN["http_result"] == ["async"] and SEEN["http_tick"] == ["tick"], SEEN


@pytest.mark.parametrize("helper", ["sync_step", "guarded_step"])
def test_an_async_def_hook_is_never_hidden_behind_a_step_wrapper(probe, helper):
    """``functools.wraps`` copies the name, not the coroutine-ness: ``sync_to_async`` would
    take the wrapper for a sync function and return the coroutine un-awaited, the hook
    body silently never running. The helpers hand an ``async def`` back unchanged, so
    ``sync_to_async`` refuses it loudly, as it does with no wrapper."""
    from djust import sync_turn

    class V(PresenceMixin, LiveView):
        async def handle_cursor_move(self, x, y):
            note("never")

        async def update_presence_heartbeat(self):
            note("never")

        async def get_presence_key(self):
            note("never")

        async def connected(self):
            note("never")

    view = V()
    make = getattr(sync_turn, helper)
    for name in (
        "handle_cursor_move",
        "update_presence_heartbeat",
        "get_presence_key",
        "connected",
    ):
        hook = getattr(view, name)
        stepped = make(view, "hook", hook)
        assert stepped == hook, name
        with pytest.raises(TypeError):
            sync_to_async(stepped)
    assert ENTERED == []


@pytest.mark.parametrize("helper", ["run_sync_step", "run_guarded_step"])
def test_the_run_helpers_do_not_enter_the_wrapper_for_an_async_def_hook(probe, helper):
    from djust import sync_turn

    async def hook():
        note("coroutine")

    coroutine = getattr(sync_turn, helper)(None, "hook", hook)
    assert asyncio.iscoroutine(coroutine)
    coroutine.close()
    assert ENTERED == []


@pytest.mark.asyncio
async def test_a_presence_broadcast_from_async_code_runs_inside_the_wrapper(probe):
    """``PresenceMixin._broadcast_presence_change`` called under a running loop hands
    itself to a worker; that hop reads ``get_presence_key()`` and is a ``hook`` step."""

    class V(PresenceMixin, LiveView):
        presence_key = "probe"

        def get_presence_key(self):
            note("broadcast_key")
            return "probe"

    view = V()
    view._broadcast_presence_change()
    for _ in range(100):
        if "hook" in steps():
            break
        await asyncio.sleep(0.02)

    assert "hook" in steps(), steps()


def test_auth_and_lifecycle_are_declared_steps():
    from djust.sync_turn import SYNC_STEPS

    assert {"auth", "lifecycle"} <= SYNC_STEPS


def test_a_guarded_step_returns_the_result_and_propagates_a_real_failure(probe):
    from djust.sync_turn import run_guarded_step

    assert run_guarded_step(None, "auth", lambda: "ok") == "ok"
    with pytest.raises(ValueError):
        run_guarded_step(None, "auth", _raise, ValueError("boom"))


def _raise(exc):
    raise exc


def test_handler_permission_runs_in_the_wrapper_and_a_swallowed_failure_denies(probe):
    from djust.auth.core import check_handler_permission

    class _User:
        is_authenticated = True
        allowed = False

        def has_perms(self, perms):
            note("has_perm")
            if self.allowed == "crash":
                raise RuntimeError("the permission backend failed")
            return bool(self.allowed)

    class _Handler:
        _djust_decorators = {"permission_required": "app.do"}

    user = _User()
    request = type("R", (), {"user": user})()

    assert check_handler_permission(_Handler(), request) is False
    assert SEEN["has_perm"] == ["auth"], SEEN
    user.allowed = True
    assert check_handler_permission(_Handler(), request) is True
    # A backend failure the wrapper swallows is a refusal, never a pass.
    user.allowed = "crash"
    MODE["auth"] = "swallow"
    with pytest.raises(PermissionDenied):
        check_handler_permission(_Handler(), request)


# ---------------------------------------------------------------------------
# The structural pin: an ALLOWLIST
#
# Every call in python/djust that hands a callable to a thread (``sync_to_async``,
# ``database_sync_to_async``, ``asyncio.to_thread``, ``run_in_executor``) must be
#   * STEPPED: the callable is ``sync_step(...)`` / ``guarded_step(...)``;
#   * a LEAF: a framework function that enters its own step (verified below); or
#   * on SAFE: a framework-internal callable, each with a reason string.
# Anything else (a raw ``view.<hook>``, a lambda, ``functools.partial``, ``getattr``,
# a call in a NEW file) fails. SAFE may never name an application hook.
# ---------------------------------------------------------------------------

_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
_THREADERS = {"sync_to_async", "database_sync_to_async", "to_thread", "run_in_executor"}
_STEPPERS = {"sync_step", "guarded_step", "_sync_step", "_lifecycle_step"}

#: Application-overridable hooks. Derived names (``_derived_override_points``) must all
#: appear here; the value says where each is entered, or why it is not.
HOOKS: Dict[str, str] = {
    # entered at the framework function that calls them (LEAVES)
    "get_object": "leaf: check_object_permission",
    "has_object_permission": "leaf: check_object_permission",
    "check_permissions": "leaf: check_view_auth (async def: awaited on the loop, never wrapped)",
    # sync_step at the thread hop
    "connected": "begin_live_connection",
    "disconnected": "run_view_disconnected",
    "handle_cursor_move": "websocket.py sync_step hook",
    "update_presence_heartbeat": "websocket.py sync_step hook",
    "get_presence_key": "runtime.py / push.py / presence.py sync_step hook",
    "get_template": "runtime.py actor mount + websocket.py hot reload: render step",
    "_restore_presence": "runtime.py restore branch: hook step",
    "_should_restore_snapshot": "runtime.py: lifecycle step",
    "_restore_snapshot": "runtime.py: lifecycle step (guarded)",
    "_capture_snapshot_state": "runtime.py mount snapshot: lifecycle step",
    "handle_restore": "call_hook (hook step)",
    "get_context_data": "render step at every hop",
    "handle_tick": "tick step (call_tick / tick_turn)",
    "handle_async_result": "async step (call_async_result / call_hook_sync)",
    "handle_info": "call_hook (hook step)",
    "handle_params": "call_hook (handle_params step)",
    "mount": "call_hook (mount step)",
    "replay_event": "leaf: the handler is stepped by time_travel._stepped_handler",
    "run_on_mount_hooks": "leaf: lifecycle",
    # documented overrides that run inside another step or have no turn (see the guide)
    "handle_component_event": "runs inside the event step of the handler that routes to it",
    "update": "LiveComponent.update: runs inside the event step",
    "serialize_for_template": "runs inside the render hop",
    "get_render_only_context": "runs inside the render hop (not its own step)",
    "get_http_status": "HTTP response phase, outside any hop",
    "unmount": "LiveComponent.unmount: runs inside the event / render that removes it",
    "_on_sticky_unmount": "teardown without a turn; documented as not wrapped",
    "_on_sticky_update": "runs inside the parent's render hop",
    "_get_all_child_views": "a registry lookup, not application code that reads rows",
    "_handle_event": "internal dispatch",
    "as_view": "the Django class method",
}

#: Framework functions that call application hooks and enter the step INSIDE their body,
#: so ``sync_to_async(<leaf>)`` is sound: (file, qualname) -> (helper, step).
LEAVES: Dict[Tuple[str, str], Tuple[str, str]] = {
    ("auth/core.py", "check_object_permission"): ("run_guarded_step", "auth"),
    ("auth/core.py", "check_view_auth"): ("run_guarded_step", "auth"),
    ("auth/core.py", "check_event_auth"): ("run_guarded_step", "auth"),
    ("auth/core.py", "check_handler_permission"): ("run_guarded_step", "auth"),
    ("hooks.py", "run_on_mount_hooks"): ("run_guarded_step", "lifecycle"),
    ("_child_lifecycle.py", "run_view_disconnected"): ("run_sync_step", "lifecycle"),
    ("_child_lifecycle.py", "begin_live_connection"): ("sync_step", "lifecycle"),
    ("_child_lifecycle.py", "untrack_view_presence"): ("run_sync_step", "hook"),
    ("push.py", "_presence_probe"): ("run_sync_step", "hook"),
    ("time_travel.py", "_stepped_handler"): ("sync_step", "event"),
}

#: ``sync_to_async(<name>)`` targets that are leaves, by the name they are called by.
#: The first group are the leaves above; the second call only those leaves.
LEAF_NAMES = {
    "check_object_permission",
    "check_view_auth",
    "check_event_auth",
    "check_handler_permission",
    "run_on_mount_hooks",
    "run_view_disconnected",
    "untrack_view_presence",
    "_presence_probe",
    "replay_event",
    # call only leaves
    "enforce_object_permission",
    "check_view_auth_lightweight",
    "run_pre_mount_auth",
}


def _safe(reason: str, *names: str) -> Dict[str, str]:
    return {name: reason for name in names}


#: Framework-internal callables handed to a thread, with the reason each is not a hook.
SAFE: Dict[str, str] = {
    **_safe(
        "render plumbing: template build, Rust state sync, HTML post-processing and diff; "
        "get_context_data is its own render step (guide: Not wrapped)",
        "_extract_liveview_content",
        "_strip_comments_and_whitespace",
        "_initialize_rust_view",
        "_sync_state_to_rust",
        "render_with_diff",
        "render_view_with_diff",
        "render_background_sync",
        "_render_hot",
        "_sync_and_render",
        "_sync_context_and_render",
        "_sync_strip_and_extract",
        "render_embedded_child_html",
        "render_pending_layout",
        "render",
        "render_to_string",
        "_render_eager",
        "_reconstruct_explicit_page_shell",
        "encode_view_state",
        "update_template",
        "contract_snapshot",
        "mount_frame_metadata",
        "navigation_title",
        "page_shell",
        "legacy_snapshot_fields",
        "capture",
        "snapshot_codec",
        "project_view",
        "server_state_adapter",
    ),
    **_safe(
        "session, auth-state or storage I/O of the framework, no view code",
        "_request_owns_session",
        "_request_user_pk",
        "save",
        "save_merged",
        "pop",
        "load_server_state",
        "request_binding",
        "fresh_socket_request",
        "_event_request",
        "authorize_event",
        "bind_csrf_cookie",
        "establish_mount_session",
        "_claim_observation",
        "resume_entry",
        "track_session",
        "page_request",
        "per_connection",
        "_attach_socket_tenant",
        "_tenant",
    ),
    **_safe(
        "framework state hop (restore/capture of framework-owned state, child bookkeeping)",
        "_assign_component_ids",
        "_capture_dirty_baseline",
        "_initialize_temporary_assigns",
        "_restore_component_state",
        "_restore_listen_channels",
        "_restore_private_state",
        "_restore_upload_configs",
        "_snapshot_user_private_attrs",
        "_get_private_state",
        "_child_update",
        "_capture_batch",
        "resolve_explicit_child",
        "restore_snapshot",
        "restore_component_snapshot",
    ),
    **_safe(
        "teardown of framework-owned state; no turn (guide: Not wrapped)",
        "_clear_live_handles",
        "abandon",
        "release_root_view",
        "_preserve_sticky_children",
    ),
    **_safe(
        "calls only leaf functions that enter their own step",
        "_authorize",
    ),
    **_safe(
        "already stepped by the caller (a sync_step / run_sync_step result or a step body)",
        "stepped",
        "call",
        "connected_hook",
        "tick_turn",
        "turn",
        "call_body",
    ),
    **_safe(
        "request/transport plumbing around hops that enter their own steps",
        "dispatch",
        "get",
        "protected_server_error",
        "run",
        "CALL:detached",
    ),
}

#: Deliberate raw threading of a hook: (file, source text). An ``async def`` hook is
#: refused by ``sync_to_async`` with the same ``TypeError`` with or without a wrapper
#: (#3625), so these stay raw on purpose.
DELIBERATE_RAW = {
    ("_view_hooks.py", "view.handle_tick"),
    ("_view_hooks.py", "handler"),
}


def _callee(call: ast.Call) -> Optional[str]:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _own_calls(function: ast.AST):
    pending = list(ast.iter_child_nodes(function))
    while pending:
        node = pending.pop()
        if isinstance(node, ast.Call):
            yield node
        if not isinstance(node, _FUNCTIONS + (ast.Lambda,)):
            pending.extend(ast.iter_child_nodes(node))


def _source_files():
    for path in sorted(PACKAGE.rglob("*.py")):
        relative = path.relative_to(PACKAGE).as_posix()
        if relative.startswith("tests/") or "/tests/" in relative:
            continue
        yield relative, ast.parse(path.read_text())


def _thread_target(call: ast.Call) -> Optional[ast.AST]:
    args = call.args[1:] if _callee(call) == "run_in_executor" else call.args
    return args[0] if args else None


def _classify(call: ast.Call) -> Tuple[str, str]:
    """(verdict key, source text) of a callable handed to a thread."""
    target = _thread_target(call)
    if target is None:
        return "<no callable>", ""
    text = ast.unparse(target)
    if isinstance(target, ast.Call):
        callee = _callee(target)
        return ("STEPPED" if callee in _STEPPERS else "CALL:%s" % callee), text
    if isinstance(target, ast.Attribute):
        return target.attr, text
    if isinstance(target, ast.Name):
        return target.id, text
    return "%s" % type(target).__name__.upper(), text


def _unaccounted_thread_calls(tree: ast.AST, relative: str) -> List[str]:
    problems = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and _callee(node) in _THREADERS):
            continue
        key, text = _classify(node)
        if key == "STEPPED" or (relative, text) in DELIBERATE_RAW:
            continue
        if key in LEAF_NAMES or key in SAFE:
            continue
        problems.append("%s:%d %s(%s) [%s]" % (relative, node.lineno, _callee(node), text, key))
    return problems


def test_every_callable_handed_to_a_thread_is_stepped_a_leaf_or_on_the_safe_list():
    problems = []
    for relative, tree in _source_files():
        problems.extend(_unaccounted_thread_calls(tree, relative))
    assert not problems, (
        "a callable is handed to a thread with no sync-turn step: route it through "
        "sync_step / guarded_step, or add it to SAFE with a reason if it calls no "
        "application hook:\n" + "\n".join(problems)
    )


def test_the_safe_list_never_names_an_application_hook_and_has_no_stale_entries():
    assert not set(SAFE) & set(HOOKS), sorted(set(SAFE) & set(HOOKS))
    assert all(reason.strip() for reason in SAFE.values())
    used = set()
    for relative, tree in _source_files():
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _callee(node) in _THREADERS:
                used.add(_classify(node)[0])
    stale = sorted(set(SAFE) - used)
    assert not stale, "SAFE entries no call uses any more: %s" % stale


def _derived_override_points() -> set:
    """Names of the LiveView / LiveComponent methods whose docstring documents an override."""
    import inspect
    import re

    from djust.components.base import LiveComponent

    found = set()
    for cls in (LiveView, LiveComponent):
        for name in dir(cls):
            if name.startswith("__"):
                continue
            try:
                member = inspect.getattr_static(cls, name)
            except AttributeError:
                continue
            member = getattr(getattr(member, "fget", member), "__func__", member)
            member = getattr(member, "fget", member)
            if callable(member) and re.search(
                r"overrid", getattr(member, "__doc__", "") or "", re.I
            ):
                found.add(name)
    return found


def test_every_documented_override_point_has_a_disposition():
    missing = sorted(_derived_override_points() - set(HOOKS))
    assert not missing, (
        "documented override points with no entry in HOOKS (enter a step or say why "
        "not): %s" % missing
    )


def test_the_pin_catches_every_escape_a_name_list_missed():
    """The allowlist is load-bearing: each way of threading a hook around the helpers,
    in a file the pin has never seen, is reported."""
    escapes = {
        "raw attribute": "await sync_to_async(view.handle_restore)(request)",
        "private hook": "await sync_to_async(view._should_restore_snapshot)(request)",
        "lambda": "await sync_to_async(lambda: view.get_presence_key())()",
        "partial": "await sync_to_async(functools.partial(view.get_presence_key))()",
        "getattr": "await sync_to_async(getattr(view, 'x'))()",
        "to_thread": "await asyncio.to_thread(view.update_presence_heartbeat)",
        "executor": "await loop.run_in_executor(None, view.handle_cursor_move)",
        "database_sync_to_async": "await database_sync_to_async(view.get_template)()",
        "unknown helper": "await sync_to_async(some_new_helper)(view)",
    }
    for label, source in escapes.items():
        tree = ast.parse("async def f(view):\n    %s\n" % source)
        assert _unaccounted_thread_calls(tree, "brand_new.py"), label
    accepted = ast.parse(
        "async def f(view):\n"
        "    await sync_to_async(sync_step(view, 'hook', view.handle_cursor_move))(1, 2)\n"
        "    await database_sync_to_async(guarded_step(view, 'auth', view.get_object))()\n"
        "    await sync_to_async(check_object_permission)(view, request)\n"
        "    await asyncio.to_thread(sync_step(view, 'hook', view.get_presence_key))\n"
    )
    assert _unaccounted_thread_calls(accepted, "brand_new.py") == []


def _find_function(tree: ast.AST, qualname: str) -> Optional[ast.AST]:
    parts = qualname.split(".")

    def walk(body, remaining):
        for node in body:
            if isinstance(node, _FUNCTIONS + (ast.ClassDef,)) and node.name == remaining[0]:
                if len(remaining) == 1:
                    return node
                return walk(node.body, remaining[1:])
        return None

    return walk(tree.body, parts)


@pytest.mark.parametrize("leaf", sorted(LEAVES), ids=lambda leaf: "%s::%s" % leaf)
def test_every_leaf_enters_its_step_inside_its_own_body(leaf):
    relative, qualname = leaf
    helper, step = LEAVES[leaf]
    tree = dict(_source_files())[relative]
    function = _find_function(tree, qualname)
    assert function is not None, "%s no longer defines %s" % leaf
    entered = []
    for call in _own_calls(function):
        if _callee(call) == helper:
            entered.append(
                [
                    arg.value
                    for arg in call.args
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
                ]
            )
    assert any(step in literals for literals in entered), (
        "%s::%s must call %s(..., %r, ...) itself (calls: %s)"
        % (relative, qualname, helper, step, entered)
    )


def test_a_leaf_without_its_step_call_is_reported():
    leafless = ast.parse("def check_object_permission(view, request):\n    view.get_object()\n")
    function = _find_function(leafless, "check_object_permission")
    assert not [c for c in _own_calls(function) if _callee(c) == "run_guarded_step"]


#: The only places application code is called as ``<view>.get_object()`` /
#: ``.has_object_permission(...)`` / ``.check_permissions(...)`` in the package, and the
#: function that enters the step around each. A new call site fails here until it is
#: routed through a step and listed.
_SINKS = {"get_object", "has_object_permission", "check_permissions"}
EXPECTED_SINKS: Dict[Tuple[str, str, str], Optional[str]] = {
    ("auth/core.py", "_resolve_object_permission", "get_object"): "check_object_permission",
    ("auth/core.py", "_resolve_object_permission", "has_object_permission"): (
        "check_object_permission"
    ),
    ("auth/core.py", "_check_view_auth", "check_permissions"): "check_view_auth",
    # Async ``check_permissions``: awaited on the loop, an ``async def`` is never wrapped.
    ("auth/core.py", "acheck_view_auth", "check_permissions"): None,
    # ``ModelAdmin.get_object(request, id)`` inside the admin views' own ``mount()``
    # (an ordinary lookup, under the mount step), not the view hook.
    ("admin_ext/forms.py", "mount", "get_object"): None,
    ("admin_ext/views.py", "mount", "get_object"): None,
}


def _sink_sites() -> Dict[Tuple[str, str, str], None]:
    found = {}
    for relative, tree in _source_files():
        parents = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                parents[child] = node
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in _SINKS
            ):
                continue
            if isinstance(node.func.value, ast.Call):  # super().x(...)
                continue
            names = []
            current = parents.get(node)
            while current is not None:
                if isinstance(current, _FUNCTIONS):
                    names.append(current.name)
                current = parents.get(current)
            found[(relative, names[0] if names else "<module>", node.func.attr)] = None
    return found


def test_application_auth_hooks_are_called_from_exactly_the_audited_sites():
    found = {site for site in _sink_sites() if site[0] not in ("live_view.py",)}
    expected = set(EXPECTED_SINKS)
    assert found == expected, "new: %s; gone: %s" % (
        sorted(found - expected),
        sorted(expected - found),
    )
