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
from django.test import override_settings

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


def test_the_http_sticky_state_save_runs_get_context_data_inside_the_wrapper(probe):
    from djust.mixins.sticky import save_sticky_child_state_sync

    class Child(LiveView):
        sticky_id = "c2"

        def get_context_data(self, **kwargs):
            note("sticky_sync_context")
            return {"n": 1}

    save_sticky_child_state_sync(Child(), {}, "app.Parent")

    assert SEEN["sticky_sync_context"] == ["render"], SEEN


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
# The structural pin
#
# Every ``sync_to_async(<view hook>)`` must hand over a stepped callable
# (``sync_step(...)`` / ``guarded_step(...)``) or name a function whose own body
# enters the step (a "leaf"). A new raw ``sync_to_async(view.<hook>)`` fails here.
# ---------------------------------------------------------------------------

#: Attribute names that are application hooks. ``sync_to_async(x.<name>)`` with one of
#: these and no stepping helper is the exact bug of #3637 (and of #3625 before it).
HOOK_ATTRS = frozenset(
    {
        "get_object",
        "has_object_permission",
        "check_permissions",
        "connected",
        "disconnected",
        "handle_cursor_move",
        "update_presence_heartbeat",
        "get_presence_key",
        "get_context_data",
        "get_template",
        "handle_tick",
        "handle_async_result",
        "handle_info",
        "handle_params",
        "mount",
    }
)

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

#: ``sync_to_async(<name>)`` targets that are leaves (by the name they are called by).
LEAF_NAMES = {
    "check_object_permission",
    "enforce_object_permission",
    "check_view_auth",
    "check_view_auth_lightweight",
    "run_pre_mount_auth",
    "check_event_auth",
    "check_handler_permission",
    "run_on_mount_hooks",
    "run_view_disconnected",
    "untrack_view_presence",
    "_presence_probe",
    "replay_event",
}

_STEPPERS = {"sync_step", "guarded_step", "_sync_step"}
_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
_THREADERS = {"sync_to_async", "database_sync_to_async"}


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


def _raw_hook_threading(tree: ast.AST) -> List[Tuple[int, str]]:
    """(line, description) of each ``sync_to_async(<hook>)`` that is neither stepped nor a leaf."""
    bad = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and _callee(node) in _THREADERS and node.args):
            continue
        target = node.args[0]
        if isinstance(target, ast.Call):  # sync_to_async(sync_step(...)) and friends
            if _callee(target) in _STEPPERS:
                continue
            continue
        if isinstance(target, ast.Attribute) and target.attr in HOOK_ATTRS:
            bad.append((node.lineno, ast.unparse(target)))
        elif isinstance(target, ast.Name) and target.id in HOOK_ATTRS:
            bad.append((node.lineno, target.id))
    return bad


#: Raw threading of a hook that is deliberate: ``handle_tick`` / ``handle_async_result``
#: when ``async def`` (refused with the same TypeError as without a wrapper, #3625), and
#: the hot paths that already step it themselves. (file, source text) pairs.
_DELIBERATE_RAW = {
    ("_view_hooks.py", "view.handle_tick"),
}


def test_no_view_hook_is_handed_to_sync_to_async_without_a_step():
    problems = []
    for relative, tree in _source_files():
        for line, text in _raw_hook_threading(tree):
            if (relative, text) in _DELIBERATE_RAW:
                continue
            problems.append("%s:%d sync_to_async(%s)" % (relative, line, text))
    assert not problems, "raw view-hook threading (use sync_step / guarded_step):\n" + "\n".join(
        problems
    )


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
            literals = [
                arg.value
                for arg in call.args
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
            ]
            entered.append(literals)
    assert any(step in literals for literals in entered), (
        "%s::%s must call %s(..., %r, ...) itself (calls: %s)"
        % (relative, qualname, helper, step, entered)
    )


def test_a_leaf_name_a_caller_threads_is_a_registered_leaf():
    """``sync_to_async(<a framework function that runs hooks>)`` is only sound for a
    leaf. Every such name used in the package must be one the pin verifies."""
    registered = {qualname.split(".")[-1] for _file, qualname in LEAVES} | {
        "enforce_object_permission",  # leaf through check_object_permission
        "check_view_auth_lightweight",  # leaf through check_view_auth
        "run_pre_mount_auth",  # leaf through check_view_auth
        "replay_event",  # leaf: the handler is stepped by _stepped_handler
    }
    assert LEAF_NAMES <= registered, sorted(LEAF_NAMES - registered)


def test_the_pin_sees_a_raw_hook_call_and_accepts_a_stepped_one():
    """The pin is not decorative: it fails on the mutation it exists for."""
    raw = ast.parse(
        "async def f(view):\n"
        "    await sync_to_async(view.handle_cursor_move)(1, 2)\n"
        "    await sync_to_async(view.get_presence_key)()\n"
        "    await sync_to_async(self.view_instance.get_template)()\n"
    )
    assert [text for _line, text in _raw_hook_threading(raw)] == [
        "view.handle_cursor_move",
        "view.get_presence_key",
        "self.view_instance.get_template",
    ]
    stepped = ast.parse(
        "async def f(view):\n"
        "    await sync_to_async(sync_step(view, 'hook', view.handle_cursor_move))(1, 2)\n"
        "    await database_sync_to_async(guarded_step(view, 'auth', view.get_object))()\n"
    )
    assert _raw_hook_threading(stepped) == []
    leafless = ast.parse("def check_object_permission(view, request):\n    view.get_object()\n")
    function = _find_function(leafless, "check_object_permission")
    assert not [c for c in _own_calls(function) if _callee(c) == "run_guarded_step"]


#: The only places application code is called as ``<view>.get_object()`` /
#: ``.has_object_permission(...)`` / ``.check_permissions(...)`` in the package, and the
#: function that enters the step around each. A new call site fails here until it is
#: routed through a step and listed.
_SINKS = {"get_object", "has_object_permission", "check_permissions"}
EXPECTED_SINKS: Dict[Tuple[str, str, str], str] = {
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
    found = {
        site
        for site in _sink_sites()
        # a method that merely delegates to its own override (mixin MRO plumbing)
        if site[0] not in ("live_view.py",)
    }
    expected = set(EXPECTED_SINKS)
    assert found == expected, "new: %s; gone: %s" % (
        sorted(found - expected),
        sorted(expected - found),
    )
