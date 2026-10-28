"""#3625: ``sync_turn_wrapper`` also covers ``handle_tick``, ``handle_async_result``
and the test client's own calls.

Before this fix the wrapper was entered around an event handler, ``mount``,
``handle_params``, a hook, a ``start_async`` callback and ``get_context_data()``,
but NOT around ``handle_tick()`` or ``handle_async_result()`` (the consumer, the
runtime and the embedded-child path each called them through a bare
``sync_to_async``), and ``LiveViewTestClient.send_event()`` / ``send_push()`` /
``render_async()`` called the handler or callback directly. An app that opens a
per-turn database scope in the wrapper (row-level security) read rows with no
scope in exactly those calls, and a test of a handler read them with none either.

Every test records the step name AND whether the wrapper was open when the view
code ran, so a test passes only if the code really ran inside the scope.
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import functools
import pathlib
import threading
from typing import Dict, List, Optional, Tuple

import pytest
from asgiref.sync import sync_to_async
from django.test import override_settings

from djust import LiveView
from djust.components.base import LiveComponent
from djust.config import config as djust_config
from djust.decorators import event_handler, push_handler
from djust.testing import LiveComponentTestClient, LiveViewTestClient

pytest.importorskip("channels")

MOD = __name__
PACKAGE = pathlib.Path(__file__).resolve().parents[1]

#: (step, thread, (turn kind, turn source) or None) for each wrapper entry.
ENTERED: List[Tuple[str, int, Optional[Tuple[str, str]]]] = []
#: What the view code saw: {label: [step open while it ran, ...]}.
SEEN: Dict[str, List[Optional[str]]] = {}
#: Wrapper entries made by a get_context_data() called from inside a step.
NESTED: List[int] = []
_OPEN = threading.local()


def recording_wrapper(view, step, turn):
    @contextlib.contextmanager
    def scope():
        ENTERED.append(
            (
                step,
                threading.get_ident(),
                None if turn is None else (turn.kind, turn.source),
            )
        )
        _OPEN.step = step
        try:
            yield
        finally:
            _OPEN.step = None

    return scope()


def note(label: str) -> None:
    SEEN.setdefault(label, []).append(getattr(_OPEN, "step", None))


def steps() -> List[str]:
    return [step for step, _thread, _turn in ENTERED]


def turns(step: str) -> List[Optional[Tuple[str, str]]]:
    """The ``turn`` argument the wrapper got for each entry of ``step``."""
    return [turn for entered, _thread, turn in ENTERED if entered == step]


@pytest.fixture
def probe():
    previous = djust_config.get("sync_turn_wrapper")
    ENTERED.clear()
    SEEN.clear()
    NESTED.clear()
    djust_config.set("sync_turn_wrapper", recording_wrapper)
    yield
    djust_config.set("sync_turn_wrapper", previous)
    ENTERED.clear()
    SEEN.clear()
    NESTED.clear()


@pytest.fixture(params=[None, 2], ids=["stock", "pool"])
def pool(request, monkeypatch):
    """The tick runs in two shapes: ``worker_threads`` off (``call_tick``) and on
    (``tick_turn`` on the session's thread). Both must enter the step."""
    from djust import worker_pool

    monkeypatch.setitem(djust_config._config, "worker_threads", request.param)
    monkeypatch.setattr(worker_pool, "_pool", [])
    yield request.param


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------


class ProbeView(LiveView):
    template = f'<div dj-root dj-view="{MOD}.ProbeView"><b>{{{{ count }}}}</b> {{{{ note }}}}</div>'
    tick_interval = 30

    def mount(self, request, **kwargs):
        self.count = 0
        self.note = ""
        self.ticks = 0

    def handle_tick(self):
        note("tick")
        # A step inside a step does not enter the wrapper again.
        entries = len(ENTERED)
        self.get_context_data()
        NESTED.append(len(ENTERED) - entries)
        if self.ticks < 1:
            self.ticks += 1
            self.count += 1

    @event_handler()
    def work(self, **kwargs):
        self.start_async(self._work, name="job")

    @event_handler()
    def fail(self, **kwargs):
        self.start_async(self._fail, name="boom")

    def _work(self):
        note("callback")
        return 1

    def _fail(self):
        note("callback")
        raise RuntimeError("on purpose")

    def handle_async_result(self, name, result=None, error=None):
        note("result:error" if error is not None else "result")
        # Nor does a render inside the result hook.
        entries = len(ENTERED)
        self.get_context_data()
        NESTED.append(len(ENTERED) - entries)
        self.note = "failed" if error is not None else "done"

    @event_handler()
    def bump(self, **kwargs):
        note("event")
        self.count += 1

    @push_handler
    def handle_pushed(self, **kwargs):
        note("push")

    def handle_info(self, message):
        note("info")

    def get_context_data(self, **kwargs):
        note("render")
        return {"count": self.count, "note": self.note}


class ProbeComponent(LiveComponent):
    template = "<span>{{ n }}</span>"

    def mount(self, **props):
        self.n = 0

    def get_context_data(self, **kwargs):
        return {"n": self.n}

    def poke(self, **kwargs):
        note("component")
        self.n += 1


# ---------------------------------------------------------------------------
# A real socket: WebsocketCommunicator against LiveViewConsumer
# ---------------------------------------------------------------------------


async def _connect(view_name):
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
    connected, _ = await communicator.connect()
    assert connected
    await communicator.receive_json_from(timeout=3)
    await communicator.send_json_to(
        {"type": "mount", "view": f"{MOD}.{view_name}", "url": "/probe/"}
    )
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


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_socket_tick_runs_inside_the_wrapper(probe, pool):
    loop_thread = threading.get_ident()
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[MOD]):
        comm = await _connect("ProbeView")
        try:
            await _until(lambda: SEEN.get("tick"))
        finally:
            await comm.disconnect()

    # handle_tick ran with the wrapper open, on a worker thread.
    assert set(SEEN["tick"]) == {"tick"}
    tick_threads = {thread for step, thread, _turn in ENTERED if step == "tick"}
    assert tick_threads and loop_thread not in tick_threads
    # The render inside handle_tick did not enter the wrapper a second time.
    assert NESTED and set(NESTED) == {0}, NESTED
    # The wrapper is given the tick's own turn.
    assert set(turns("tick")) == {("push", "tick")}, turns("tick")


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("event, outcome", [("work", "result"), ("fail", "result:error")])
async def test_a_socket_async_result_runs_inside_the_wrapper(probe, pool, event, outcome):
    loop_thread = threading.get_ident()
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[MOD]):
        comm = await _connect("ProbeView")
        try:
            # Past the first beats of the tick so only the async steps remain.
            ENTERED.clear()
            SEEN.clear()
            await comm.send_json_to({"type": "event", "event": event, "params": {}, "ref": 1})
            await _until(lambda: SEEN.get(outcome))
        finally:
            await comm.disconnect()

    # The callback was already an async step; the result hook is now a second one.
    assert set(SEEN["callback"]) == {"async"}
    assert set(SEEN[outcome]) == {"async"}
    async_threads = {thread for step, thread, _turn in ENTERED if step == "async"}
    assert loop_thread not in async_threads
    assert steps().count("async") >= 2, steps()
    assert NESTED and set(NESTED) == {0}, NESTED
    # The result hook is entered with the turn that delivers it.
    assert turns("async")[-1] == ("async", "result"), turns("async")


# ---------------------------------------------------------------------------
# The consumer's own _run_async_work (the WS twin of the runtime's result turn)
# ---------------------------------------------------------------------------


def _consumer_with_view():
    from djust.websocket import LiveViewConsumer

    consumer = LiveViewConsumer()
    consumer.scope = {"session": None, "user": None}
    consumer.sent = []

    async def _capture(payload):
        consumer.sent.append(payload)

    consumer.send_json = _capture
    view = ProbeView()
    view.mount(None)
    view.render_with_diff()
    consumer.view_instance = view
    return consumer, view


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("fails", [False, True], ids=["success", "error"])
async def test_the_consumers_async_work_enters_the_wrapper_for_the_result(probe, fails):
    consumer, view = _consumer_with_view()
    ENTERED.clear()
    SEEN.clear()
    callback = view._fail if fails else view._work

    await consumer._run_async_work("job", callback, (), {}, event_name="work")

    outcome = "result:error" if fails else "result"
    assert set(SEEN[outcome]) == {"async"}, SEEN
    assert set(SEEN["callback"]) == {"async"}
    assert steps().count("async") == 2, steps()


# ---------------------------------------------------------------------------
# The result hook of an explicit root and of embedded children (#3625 review)
#
# Each arm below was a ``handle_async_result`` call the socket tests above do
# not reach: the runtime's explicit-exposure turn, an explicit child, and a
# legacy sticky child. A real runtime drives each one end to end.
# ---------------------------------------------------------------------------


def _result_probe(label):
    def handle_async_result(self, name, result=None, error=None):
        note(label)

    return handle_async_result


@pytest.fixture
def staged(monkeypatch):
    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_explicit_roots_result_hook_runs_inside_the_wrapper(probe, staged, monkeypatch):
    from djust.tests import test_exposure_root_background_turns as harness

    monkeypatch.setattr(
        harness.BackgroundView, "handle_async_result", _result_probe("root_result"), raising=False
    )
    harness.HANDLED.clear()
    harness.GATE.set()
    request = await sync_to_async(harness.make_request)()
    runtime, _transport = await harness.mount(request, harness.BackgroundView)
    view = runtime.view_instance
    try:
        await runtime.dispatch_event({"type": "event", "event": "spawn", "params": {}})
        await harness._drain(view)
    finally:
        harness.GATE.set()

    assert SEEN["root_result"] == ["async"], SEEN
    assert turns("async")[-1] == ("async", "result"), turns("async")


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_explicit_childs_result_hook_runs_inside_the_wrapper(
    probe, staged, monkeypatch, settings
):
    from djust.tests.test_exposure_child_events import EventChild, mount

    settings.DJUST_LIVE_RENDER_ALLOWED_MODULES = ["djust.tests.test_exposure_child_events"]

    @event_handler()
    def begin(self):
        self.start_async(lambda: 1, name="child-job")

    monkeypatch.setattr(EventChild, "begin", begin, raising=False)
    monkeypatch.setattr(
        EventChild, "handle_async_result", _result_probe("child_result"), raising=False
    )
    runtime, _transport, _request = await mount()
    child = runtime.view_instance._get_child_view("menu")
    try:
        await runtime.dispatch_event({"event": "begin", "params": {"view_id": "menu"}, "ref": 1})
        handles = tuple(getattr(child, "_async_task_handles", ()))
        assert handles, "the routed child must own its dispatched background task"
        await asyncio.wait_for(asyncio.gather(*handles), 5)
    finally:
        child.cancel_async_all()
        runtime.view_instance.cancel_async_all()

    assert SEEN["child_result"] == ["async"], SEEN
    assert turns("async")[-1] == ("async", "result"), turns("async")


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_legacy_sticky_childs_result_hook_runs_inside_the_wrapper(
    probe, staged, monkeypatch
):
    from djust.tests.test_runtime_child_routing_1892 import (
        _StickyChildView,
        _make_runtime_with_view,
        _make_sticky_parent,
    )

    @event_handler(parameter_policy="strict")
    def begin(self):
        self.start_async(lambda: 1, name="sticky-job")

    monkeypatch.setattr(_StickyChildView, "begin", begin, raising=False)
    monkeypatch.setattr(
        _StickyChildView, "handle_async_result", _result_probe("sticky_result"), raising=False
    )
    parent, child = _make_sticky_parent()
    runtime, _transport = _make_runtime_with_view(parent)
    runtime._parameter_contract_view = "tests.Parent"
    try:
        await runtime.dispatch_event({"event": "begin", "params": {"view_id": "child-1"}, "ref": 1})
        handles = tuple(getattr(child, "_async_task_handles", ()))
        assert handles, "the routed child must own its dispatched background task"
        await asyncio.wait_for(asyncio.gather(*handles), 5)
    finally:
        child.cancel_async_all()
        parent.cancel_async_all()

    assert SEEN["sticky_result"] == ["async"], SEEN
    assert turns("async")[-1] == ("async", "result"), turns("async")


# ---------------------------------------------------------------------------
# The test client enters the same steps as the consumer
# ---------------------------------------------------------------------------


@pytest.fixture
def client(probe):
    test_client = LiveViewTestClient(ProbeView)
    test_client.mount()
    ENTERED.clear()
    SEEN.clear()
    return test_client


@pytest.mark.django_db
def test_client_send_event_runs_the_handler_inside_the_wrapper(client):
    result = client.send_event("bump")

    assert result["success"], result
    assert SEEN["event"] == ["event"]
    assert steps() == ["event"]


@pytest.mark.django_db
def test_client_send_push_runs_the_push_handler_inside_the_wrapper(client):
    result = client.send_push("handle_pushed")

    assert result["success"], result
    assert SEEN["push"] == ["hook"]
    assert steps() == ["hook"]


@pytest.mark.django_db
def test_client_trigger_info_runs_handle_info_inside_the_wrapper(client):
    result = client.trigger_info({"type": "ping"})

    assert result["success"], result
    assert SEEN["info"] == ["hook"]


@pytest.mark.django_db
@pytest.mark.parametrize("fails", [False, True], ids=["success", "error"])
def test_client_render_async_runs_callback_and_result_inside_the_wrapper(client, fails):
    client.send_event("fail" if fails else "work")
    ENTERED.clear()
    SEEN.clear()

    if fails:
        with pytest.raises(RuntimeError):
            client.render_async()
    else:
        client.render_async()

    assert SEEN["callback"] == ["async"]
    assert SEEN["result:error" if fails else "result"] == ["async"]
    assert NESTED == [0]
    # Two hops (callback, result), each entered once; the render inside the
    # result hook did not nest.
    assert steps() == ["async", "async"]


@pytest.mark.django_db
def test_client_component_send_event_runs_inside_the_wrapper(probe):
    component_client = LiveComponentTestClient(ProbeComponent)
    component_client.mount()
    ENTERED.clear()
    SEEN.clear()

    component_client.send_event("poke")

    assert SEEN["component"] == ["event"]


@pytest.mark.django_db
def test_client_without_a_wrapper_behaves_as_before():
    assert djust_config.get("sync_turn_wrapper") is None
    test_client = LiveViewTestClient(ProbeView)
    test_client.mount()

    assert test_client.send_event("bump")["success"]
    assert test_client.send_event("work")["success"]
    test_client.render_async()
    assert test_client.send_push("handle_pushed")["success"]
    assert SEEN["result"] == [None]


# ---------------------------------------------------------------------------
# async def hooks (#3625 review)
# ---------------------------------------------------------------------------


class AsyncHookView(LiveView):
    template = f'<div dj-root dj-view="{MOD}.AsyncHookView">{{{{ n }}}}</div>'

    def mount(self, request, **kwargs):
        self.n = 0

    @push_handler
    async def handle_pushed(self, **kwargs):
        note("async-push")
        self.n += 1

    async def handle_info(self, message):
        note("async-info")
        self.n += 10

    def get_context_data(self, **kwargs):
        return {"n": self.n}


class AsyncResultView(LiveView):
    template = f'<div dj-root dj-view="{MOD}.AsyncResultView">x</div>'
    tick_interval = 30

    def mount(self, request, **kwargs):
        pass

    async def handle_async_result(self, name, result=None, error=None):
        note("async-result")

    async def handle_tick(self):
        note("async-tick")


@pytest.mark.django_db
def test_client_runs_async_hooks_to_completion_outside_a_loop():
    test_client = LiveViewTestClient(AsyncHookView)
    test_client.mount()

    assert test_client.send_push("handle_pushed")["success"]
    assert test_client.trigger_info({"type": "ping"})["success"]
    assert test_client.view_instance.n == 11
    assert SEEN["async-push"] == [None] and SEEN["async-info"] == [None]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_client_runs_async_hooks_inside_a_running_loop(probe):
    """On main this was a silent no-op; the first version of the fix failed it
    with "You cannot use AsyncToSync in the same thread as an async event loop"
    and left the coroutine un-awaited."""
    import gc
    import warnings

    test_client = LiveViewTestClient(AsyncHookView)
    await sync_to_async(test_client.mount)()
    ENTERED.clear()

    with warnings.catch_warnings():
        warnings.simplefilter("error")  # "coroutine ... was never awaited"
        pushed = test_client.send_push("handle_pushed")
        info = test_client.trigger_info({"type": "ping"})
        gc.collect()

    assert pushed["success"], pushed
    assert info["success"], info
    assert test_client.view_instance.n == 11
    # An async hook runs on the loop, unwrapped, as in the consumer.
    assert steps() == []


@pytest.mark.parametrize("wrapped", [False, True], ids=["no-wrapper", "wrapper"])
def test_an_async_def_result_or_tick_hook_is_refused_the_same_way_with_or_without_a_wrapper(
    wrapped,
):
    """With a wrapper the ``functools.wraps`` shim used to hide the ``async
    def`` and the coroutine was dropped un-awaited (loud -> silent)."""
    from djust._view_hooks import call_async_result, call_tick

    previous = djust_config.get("sync_turn_wrapper")
    djust_config.set("sync_turn_wrapper", recording_wrapper if wrapped else None)
    ENTERED.clear()
    SEEN.clear()
    view = AsyncResultView()
    view.mount(None)

    async def run():
        with pytest.raises(TypeError, match="sync_to_async can only be applied to sync"):
            await call_async_result(view, view.handle_async_result, "job", result=1, error=None)
        with pytest.raises(TypeError, match="sync_to_async can only be applied to sync"):
            await call_tick(view)

    try:
        asyncio.run(run())
    finally:
        djust_config.set("sync_turn_wrapper", previous)
    assert "async-result" not in SEEN and "async-tick" not in SEEN
    assert steps() == []


# ---------------------------------------------------------------------------
# Re-entrancy: a step on a step's thread never enters the wrapper twice
# ---------------------------------------------------------------------------


def test_call_tick_and_call_async_result_do_not_nest(probe):
    from djust._view_hooks import call_async_result, call_tick

    view = ProbeView()
    view.mount(None)
    ENTERED.clear()

    async def run():
        await call_tick(view)
        await call_async_result(view, view.handle_async_result, "job", result=1, error=None)

    asyncio.run(run())

    # handle_tick and handle_async_result both call get_context_data(): one
    # entry each, not two.
    assert steps() == ["tick", "async"]
    assert NESTED == [0, 0]


def test_the_wrapper_is_not_paid_for_when_unset():
    from djust._view_hooks import call_async_result, call_tick

    assert djust_config.get("sync_turn_wrapper") is None
    view = ProbeView()
    view.mount(None)
    SEEN.clear()

    async def run():
        await call_tick(view)
        await call_async_result(view, view.handle_async_result, "job", result=1, error=None)

    asyncio.run(run())

    assert SEEN["tick"] == [None] and SEEN["result"] == [None]


def test_tick_is_a_declared_step():
    from djust.sync_turn import SYNC_STEPS

    assert {"tick", "async"} <= SYNC_STEPS


# ---------------------------------------------------------------------------
# The SET of places that run these hooks (#1646): a new one fails here
# ---------------------------------------------------------------------------

#: Every non-test reference to ``handle_tick`` / ``handle_async_result`` in the
#: package: (file, enclosing function, name) -> the call the SAME function must
#: make to enter the ``sync_turn_wrapper`` step, or None for a reference that
#: calls nothing (a lookup, a definition check, a schema listing). The set is
#: exhaustive, so a new site (a new transport, a new owner kind) fails here
#: until it is decided; the callee makes it fail again if the site stops using
#: the shared helper, which a name-keyed set alone would not notice.
EXPECTED_HOOK_REFERENCES = {
    # Consumer: success and error arms of the async-work body.
    ("websocket.py", "LiveViewConsumer._run_async_work_body.complete", "handle_async_result"): (
        "call_async_result"
    ),
    (
        "websocket.py",
        "LiveViewConsumer._run_async_work_body.complete_error",
        "handle_async_result",
    ): ("call_async_result"),
    # Consumer: the worker-pool tick enters the "tick" step with run_sync_step;
    # the stock tick goes through call_tick below.
    ("websocket.py", "LiveViewConsumer._tick_locked.tick_turn", "handle_tick"): "run_sync_step",
    ("_view_hooks.py", "call_tick", "handle_tick"): "sync_step",
    # Runtime: the result turns of a start_async task, success and error arms,
    # and the explicit-exposure twin.
    ("runtime.py", "ViewRuntime._execute_async_task_body.complete", "handle_async_result"): (
        "call_async_result"
    ),
    ("runtime.py", "ViewRuntime._execute_async_task_body.complete_error", "handle_async_result"): (
        "call_async_result"
    ),
    ("runtime.py", "ViewRuntime._execute_explicit_async_task.complete", "handle_async_result"): (
        "call_async_result"
    ),
    # Embedded / sticky children: explicit and legacy owners.
    ("_child_async.py", "_execute_body.complete", "handle_async_result"): "call_async_result",
    ("_child_async.py", "dispatch_legacy_child_work.run_body.complete", "handle_async_result"): (
        "call_async_result"
    ),
    # The HTTP fallback runs a start_async task in the request and hands its
    # outcome to the hook through call_hook_sync, which names the step "async".
    ("mixins/request.py", "_run_http_background_work", "handle_async_result"): "call_hook_sync",
    # The test client drains the same hook (render_async).
    ("testing.py", "LiveViewTestClient.render_async", "handle_async_result"): "_run_step",
    # Lookups that call nothing: the tick task is only started for a view that
    # overrides handle_tick; static checks and the schema listing read the names.
    ("runtime.py", "maybe_start_tick_task", "handle_tick"): None,
    ("checks/components.py", "check_liveviews", "handle_async_result"): None,
    ("checks/components.py", "check_liveviews", "handle_tick"): None,
    ("schema.py", "<module>", "handle_tick"): None,
    # The step map that names ``handle_async_result`` an "async" step.
    ("_view_hooks.py", "<module>", "handle_async_result"): None,
}

_NAMES = {"handle_tick", "handle_async_result"}
_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)


def _own_nodes(function: ast.AST):
    """The nodes of ``function``, not descending into nested functions."""
    pending = list(ast.iter_child_nodes(function))
    while pending:
        node = pending.pop()
        yield node
        if not isinstance(node, _FUNCTIONS + (ast.Lambda,)):
            pending.extend(ast.iter_child_nodes(node))


def _callee(call: ast.Call) -> Optional[str]:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _raw_threaded_hook_call(call: ast.Call) -> bool:
    """``sync_to_async(<the hook>)`` where the hook is ``handle_async_result``
    itself or the local named ``handler`` that holds it."""
    if _callee(call) != "sync_to_async" or not call.args:
        return False
    target = call.args[0]
    return (isinstance(target, ast.Attribute) and target.attr in _NAMES) or (
        isinstance(target, ast.Name) and target.id == "handler"
    )


@functools.lru_cache(maxsize=1)
def _hook_references() -> Dict[Tuple[str, str, str], Optional[Tuple[set, bool]]]:
    """(file, function, name) -> (callees the function makes, makes a raw call)."""
    found: Dict[Tuple[str, str, str], Tuple[set, bool]] = {}
    for path in sorted(PACKAGE.rglob("*.py")):
        relative = path.relative_to(PACKAGE).as_posix()
        if relative.startswith(("tests/", "templatetags/")) or "/tests/" in relative:
            continue
        tree = ast.parse(path.read_text())
        parents: Dict[ast.AST, ast.AST] = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                parents[child] = node

        def enclosing(node: ast.AST):
            current = parents.get(node)
            while current is not None and not isinstance(current, _FUNCTIONS):
                current = parents.get(current)
            return current

        def qualname(node: ast.AST) -> str:
            names = []
            current = parents.get(node)
            while current is not None:
                if isinstance(current, _FUNCTIONS + (ast.ClassDef,)):
                    names.append(current.name)
                current = parents.get(current)
            return ".".join(reversed(names)) or "<module>"

        for node in ast.walk(tree):
            name = None
            if isinstance(node, ast.Attribute) and node.attr in _NAMES:
                name = node.attr
            elif isinstance(node, ast.Constant) and node.value in _NAMES:
                name = node.value
            if name is None:
                continue
            function = enclosing(node)
            calls = (
                [n for n in _own_nodes(function) if isinstance(n, ast.Call)]
                if function is not None
                else []
            )
            found[(relative, qualname(node), name)] = (
                {_callee(call) for call in calls},
                any(_raw_threaded_hook_call(call) for call in calls),
            )
    return found


def test_the_set_of_hook_references_is_exactly_the_audited_one():
    found = set(_hook_references())
    expected = set(EXPECTED_HOOK_REFERENCES)
    assert found == expected, "new: %s; gone: %s" % (
        sorted(found - expected),
        sorted(expected - found),
    )


@pytest.mark.parametrize(
    "reference", sorted(EXPECTED_HOOK_REFERENCES), ids=lambda r: "%s::%s" % r[:2]
)
def test_every_hook_call_enters_the_step_through_the_shared_helper(reference):
    required = EXPECTED_HOOK_REFERENCES[reference]
    callees, raw = _hook_references()[reference]
    if required is None:
        return
    assert required in callees, "%s no longer calls %s (calls: %s)" % (
        reference,
        required,
        sorted(c for c in callees if c),
    )
    if reference[0] != "_view_hooks.py":  # the helpers' own async-def guard is deliberate
        assert not raw, "%s hands the hook to sync_to_async itself" % (reference,)


def test_the_pin_sees_a_raw_call_and_a_replacement_helper():
    """The pin is not decorative: it fails on the two mutations it exists for."""
    raw = ast.parse("async def f():\n    await sync_to_async(handler)(name)\n")
    (call,) = [
        n for n in ast.walk(raw) if isinstance(n, ast.Call) and _callee(n) == "sync_to_async"
    ]
    assert _raw_threaded_hook_call(call)
    helper = ast.parse("async def f():\n    await raw_result(view, name)\n")
    assert "call_async_result" not in {
        _callee(n) for n in ast.walk(helper) if isinstance(n, ast.Call)
    }


# ---------------------------------------------------------------------------
# The HTTP fallback's result hook (the pin above lists it; this proves the step)
# ---------------------------------------------------------------------------


def _http_result_view():
    from djust.tests import test_http_fallback_background_work_3624 as bg

    seen: list = []

    class ResultStepView(bg.BackgroundView):
        def handle_async_result(self, name, result=None, error=None):
            seen.append(getattr(_OPEN, "step", None))

    return bg, ResultStepView, seen


@pytest.mark.django_db
def test_the_http_fallbacks_result_hook_runs_in_the_async_step(probe):
    """``start_async`` work run inside a fallback POST hands its outcome to
    ``handle_async_result`` as an ``async`` step, as on the socket (#3625)."""
    bg, view_cls, seen = _http_result_view()
    with bg.STATELESS:
        bg._post(view_cls, "kick_off")
    assert seen == ["async"], seen
