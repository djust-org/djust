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
really ran inside the scope. The structural pin that fails when a new raw ``sync_to_async(<view hook>)``
appears moved to ``test_sync_turn_structural_pin_3646.py`` (#3646).
"""

from __future__ import annotations

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
    await communicator.receive_json_from()
    await communicator.send_json_to(
        {"type": "mount", "view": f"{MOD}.{view_name}", "url": "/probe/"}
    )
    return communicator


async def _mounted(view_name):
    communicator = await _open(view_name)
    for _ in range(8):
        frame = await communicator.receive_json_from()
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
