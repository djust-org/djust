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

#: (step, thread) for each wrapper entry, in order.
ENTERED: List[Tuple[str, int]] = []
#: What the view code saw: {label: [step open while it ran, ...]}.
SEEN: Dict[str, List[Optional[str]]] = {}
#: Wrapper entries made by a get_context_data() called from inside a step.
NESTED: List[int] = []
_OPEN = threading.local()


def recording_wrapper(view, step, turn):
    @contextlib.contextmanager
    def scope():
        ENTERED.append((step, threading.get_ident()))
        _OPEN.step = step
        try:
            yield
        finally:
            _OPEN.step = None

    return scope()


def note(label: str) -> None:
    SEEN.setdefault(label, []).append(getattr(_OPEN, "step", None))


def steps() -> List[str]:
    return [step for step, _thread in ENTERED]


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
    tick_threads = {thread for step, thread in ENTERED if step == "tick"}
    assert tick_threads and loop_thread not in tick_threads
    # The render inside handle_tick did not enter the wrapper a second time.
    assert NESTED and set(NESTED) == {0}, NESTED


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("event, outcome", [("work", "result"), ("fail", "result:error")])
async def test_a_socket_async_result_runs_inside_the_wrapper(probe, event, outcome):
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
    async_threads = {thread for step, thread in ENTERED if step == "async"}
    assert loop_thread not in async_threads
    assert steps().count("async") >= 2, steps()
    assert NESTED and set(NESTED) == {0}, NESTED


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
#: package as (file, enclosing function, name). The sync call of each is
#: ``call_tick`` / ``call_async_result`` / ``run_sync_step(..., "tick", ...)``,
#: which is pinned separately below; this set is the exhaustive list of where a
#: view's hook is looked up or called, so a new site (a new transport, a new
#: owner kind) fails here until it is decided.
EXPECTED_HOOK_REFERENCES = {
    # Consumer: success and error arms of the async-work body, both through
    # call_async_result.
    ("websocket.py", "LiveViewConsumer._run_async_work_body.complete", "handle_async_result"),
    ("websocket.py", "LiveViewConsumer._run_async_work_body.complete_error", "handle_async_result"),
    # Consumer: the worker-pool tick (tick_turn enters the "tick" step with
    # run_sync_step); the stock tick goes through call_tick below.
    ("websocket.py", "LiveViewConsumer._tick_locked.tick_turn", "handle_tick"),
    ("_view_hooks.py", "call_tick", "handle_tick"),
    # Runtime: the result turns of a start_async task, success and error arms,
    # and the explicit-exposure twin.
    ("runtime.py", "ViewRuntime._execute_async_task_body.complete", "handle_async_result"),
    ("runtime.py", "ViewRuntime._execute_async_task_body.complete_error", "handle_async_result"),
    ("runtime.py", "ViewRuntime._execute_explicit_async_task.complete", "handle_async_result"),
    # Embedded / sticky children: explicit and legacy owners.
    ("_child_async.py", "_execute_body.complete", "handle_async_result"),
    ("_child_async.py", "dispatch_legacy_child_work.run_body.complete", "handle_async_result"),
    # The tick task is only started for a view that overrides handle_tick.
    ("runtime.py", "maybe_start_tick_task", "handle_tick"),
    # The test client drains the same hook (render_async).
    ("testing.py", "LiveViewTestClient.render_async", "handle_async_result"),
    # Static checks and the schema listing look the hooks up; they call nothing.
    ("checks/components.py", "check_liveviews", "handle_async_result"),
    ("checks/components.py", "check_liveviews", "handle_tick"),
    ("schema.py", "<module>", "handle_tick"),
}

_NAMES = {"handle_tick", "handle_async_result"}


def _hook_references() -> set:
    found = set()
    for path in sorted(PACKAGE.rglob("*.py")):
        relative = path.relative_to(PACKAGE).as_posix()
        if relative.startswith(("tests/", "templatetags/")) or "/tests/" in relative:
            continue
        tree = ast.parse(path.read_text())
        parents: Dict[ast.AST, ast.AST] = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                parents[child] = node

        def qualname(node: ast.AST) -> str:
            names = []
            current = parents.get(node)
            while current is not None:
                if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    names.append(current.name)
                current = parents.get(current)
            return ".".join(reversed(names)) or "<module>"

        for node in ast.walk(tree):
            name = None
            if isinstance(node, ast.Attribute) and node.attr in _NAMES:
                name = node.attr
            elif isinstance(node, ast.Constant) and node.value in _NAMES:
                name = node.value
            if name is not None:
                found.add((relative, qualname(node), name))
    return found


def test_the_set_of_hook_references_is_exactly_the_audited_one():
    found = _hook_references()
    assert found == EXPECTED_HOOK_REFERENCES, "new: %s; gone: %s" % (
        sorted(found - EXPECTED_HOOK_REFERENCES),
        sorted(EXPECTED_HOOK_REFERENCES - found),
    )


_RAW_CALL = (
    "sync_to_async(view.handle_async_result)",
    "sync_to_async(handler)",
    "sync_to_async(self.view_instance.handle_tick)",
    "tick_view.handle_tick()",
)


@pytest.mark.parametrize("relative", ["websocket.py", "runtime.py", "_child_async.py"])
def test_no_raw_threaded_call_of_the_hooks_remains(relative):
    code = (PACKAGE / relative).read_text()
    code = "\n".join(line for line in code.splitlines() if not line.lstrip().startswith("#"))
    for raw in _RAW_CALL:
        assert raw not in code, (relative, raw)
