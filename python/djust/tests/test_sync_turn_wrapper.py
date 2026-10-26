"""``LIVEVIEW_CONFIG["sync_turn_wrapper"]``: a scope around each sync step.

Reported downstream (Point Guard N-30, djust 1.3.0rc11): a socket turn runs a
view's sync code in separate hops with awaits between them, so a
``LIVEVIEW_MIDDLEWARE`` cannot hold a database transaction for the turn. The
app overrode ``get_context_data()`` and decorated handlers instead. The
wrapper is entered on the thread around each sync step djust runs for a view.
"""

from __future__ import annotations

import contextlib
import json
import pathlib
import re
import threading
import uuid
from typing import Any, Dict, List, Optional

import pytest
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory

from djust import LiveView
from djust.config import config
from djust.decorators import event_handler

PACKAGE = pathlib.Path(__file__).resolve().parents[1]


#: The step a recorded wrapper has open on this thread.
_OPEN = threading.local()
#: (view class, step, turn kind, thread) for each wrapper entry.
ENTERED: List[tuple] = []


def recording_wrapper(view, step, turn):
    @contextlib.contextmanager
    def scope():
        ENTERED.append(
            (type(view).__name__, step, getattr(turn, "kind", None), threading.get_ident())
        )
        _OPEN.step = step
        try:
            yield
        finally:
            _OPEN.step = None

    return scope()


def open_step():
    return getattr(_OPEN, "step", None)


def steps():
    return [step for _view, step, _kind, _thread in ENTERED]


@pytest.fixture
def probe():
    previous = config.get("sync_turn_wrapper")
    ENTERED.clear()
    config.set("sync_turn_wrapper", recording_wrapper)
    yield ENTERED
    config.set("sync_turn_wrapper", previous)
    ENTERED.clear()


class ProbeView(LiveView):
    template = '<div dj-root dj-view="tests.ProbeView">{{ count }} {{ seen }}</div>'

    def mount(self, request, **kwargs):
        self.count = 0
        self.seen = ""
        self.mount_scope = open_step()

    @event_handler()
    def bump(self, **kwargs):
        self.count += 1
        self.handler_scope = open_step()
        # A step inside a step does not enter the wrapper again.
        self.get_context_data()

    @event_handler()
    def later(self, **kwargs):
        self.start_async(self._work)

    def _work(self):
        self.seen = open_step() or "none"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["render_scope"] = open_step()
        return context


# ---------------------------------------------------------------------------
# The helper itself
# ---------------------------------------------------------------------------


def test_no_wrapper_configured_returns_the_function_itself():
    from djust.sync_turn import run_sync_step, sync_step

    def fn():
        return 1

    assert config.get("sync_turn_wrapper") is None
    assert sync_step(object(), "render", fn) is fn
    assert run_sync_step(object(), "render", fn) == 1


def test_a_nested_step_does_not_enter_the_wrapper_again(probe):
    from djust.sync_turn import run_sync_step

    def inner():
        return run_sync_step(object(), "render", lambda: "done")

    assert run_sync_step(object(), "event", inner) == "done"
    assert steps() == ["event"]


def test_a_dotted_path_is_imported(settings):
    from djust.sync_turn import run_sync_step

    previous = config.get("sync_turn_wrapper")
    config.set("sync_turn_wrapper", "djust.tests.test_sync_turn_wrapper.dotted_wrapper")
    try:
        DOTTED.clear()
        run_sync_step(object(), "mount", lambda: None)
        assert DOTTED == ["mount"]
    finally:
        config.set("sync_turn_wrapper", previous)


DOTTED: List[str] = []


def dotted_wrapper(view, step, turn):
    DOTTED.append(step)
    return contextlib.nullcontext()


def test_a_wrapper_that_is_not_callable_is_refused():
    from django.core.exceptions import ImproperlyConfigured

    from djust.sync_turn import run_sync_step

    previous = config.get("sync_turn_wrapper")
    config.set("sync_turn_wrapper", 42)
    try:
        with pytest.raises(ImproperlyConfigured):
            run_sync_step(object(), "render", lambda: None)
    finally:
        config.set("sync_turn_wrapper", previous)


# ---------------------------------------------------------------------------
# A socket turn (the runtime, as the WebSocket and SSE transports drive it)
# ---------------------------------------------------------------------------


class _Transport:
    def __init__(self):
        self._session_id = str(uuid.uuid4())
        self.sent: List[Dict[str, Any]] = []

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def client_ip(self) -> Optional[str]:
        return None

    async def send(self, data):
        self.sent.append(data)

    async def send_error(self, error, **kwargs):
        self.sent.append({"type": "error", "error": error, **kwargs})

    async def close(self, code=1000):
        pass

    def next_client_version(self, html, rust_version):
        return rust_version

    def build_request(self):
        return None

    def on_view_mounted(self, view_instance):
        pass

    async def on_event_recorded(self, view, snapshot):
        pass

    @contextlib.asynccontextmanager
    async def event_context(self, view):
        yield


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_socket_event_handler_and_render_run_inside_the_wrapper(probe):
    from djust.runtime import ViewRuntime

    view = ProbeView()
    view.mount(None)
    ENTERED.clear()
    runtime = ViewRuntime(_Transport(), scope=None)
    runtime.view_instance = view

    await runtime.dispatch_event({"type": "event", "event": "bump", "params": {}, "ref": 1})

    assert view.count == 1
    assert view.handler_scope == "event"
    seen = steps()
    assert seen[0] == "event"
    assert "render" in seen[1:]
    # Every step is entered on the thread that runs it, one call each.
    event_thread = ENTERED[0][3]
    assert event_thread != threading.get_ident()
    # The handler's own get_context_data() did not enter it again.
    assert seen.count("event") == 1
    assert {kind for _v, _s, kind, _t in ENTERED} == {"event"}


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_start_async_callback_runs_inside_the_wrapper(probe):
    import asyncio

    from djust.runtime import ViewRuntime

    view = ProbeView()
    view.mount(None)
    runtime = ViewRuntime(_Transport(), scope=None)
    runtime.view_instance = view

    await runtime.dispatch_event({"type": "event", "event": "later", "params": {}, "ref": 1})
    for _ in range(100):
        if view.seen:
            break
        await asyncio.sleep(0.02)

    assert view.seen == "async"
    assert "async" in steps()


# ---------------------------------------------------------------------------
# HTTP: the GET's mount and render, the fallback POST's handler
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_http_get_and_post_enter_the_wrapper(probe):
    request = RequestFactory().get("/probe/")
    SessionMiddleware(lambda r: None).process_request(request)
    request.session.save()
    view = ProbeView()
    assert view.get(request).status_code == 200
    assert view.mount_scope == "mount"
    assert "render" in steps()

    ENTERED.clear()
    post = RequestFactory().post(
        "/probe/",
        data=json.dumps({"event": "bump", "params": {}}),
        content_type="application/json",
    )
    post.session = request.session
    assert ProbeView().post(post).status_code == 200
    assert "event" in steps()


# ---------------------------------------------------------------------------
# One producer: every framework call of get_context_data() goes through it
# ---------------------------------------------------------------------------


_BARE_CALL = re.compile(
    r"(?<![\w.])(?:self|view|child_view|target_view|self\.view|self\.view_instance)\.get_context_data\(\)"
)


@pytest.mark.parametrize(
    "relative",
    [
        "mixins/rust_bridge.py",
        "mixins/template.py",
        "mixins/request.py",
        "mixins/layout.py",
        "runtime.py",
        "websocket.py",
        "renderers/native.py",
        "simple_live_view.py",
        "testing.py",
    ],
)
def test_framework_render_calls_go_through_the_wrapper(relative):
    source = (PACKAGE / relative).read_text()
    code = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith(("#", "``", "-"))
    )
    assert not _BARE_CALL.search(code), relative
    assert "sync_to_async(view.get_context_data)" not in code, relative
