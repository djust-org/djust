"""#4101: an undecorated method is never a client event, on any transport.

djust 2.0 removed the ``event_security`` "warn" and "open" modes. Before, the
WebSocket, SSE, runtime, component and test-client paths read the mode while the
page-POST fallback always refused an undecorated method with its own message,
so one event behaved differently by transport. Now every path runs one check,
``websocket_utils._check_event_security``, and answers with one message:

* each transport refuses an undecorated method over its real path (WebSocket
  consumer, SSE endpoints, page POST, a descriptor component over the socket,
  ``LiveViewTestClient``) and runs a decorated one;
* in DEBUG every transport shows the same text, which names the method and
  ``@event_handler``;
* a structural pin lists who calls the shared check, so a new client-event
  path that does not call it, or a path that goes back to its own
  ``is_event_handler`` gate, fails here;
* ``LIVEVIEW_CONFIG`` / ``DJUST_CONFIG`` ``event_security`` set to anything but
  ``"strict"`` fails startup with the migration hint (``djust._removed_settings``).
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.core.exceptions import ImproperlyConfigured
from django.test import RequestFactory, override_settings

from djust import LiveView
from djust._removed_settings import (
    MIGRATION_GUIDE_URL,
    reject_removed_settings,
    removed_settings_in_use,
)
from djust.components.descriptors.base import LiveComponent, TypedState
from djust.decorators import event_handler
from djust.websocket import LiveViewConsumer
from djust.websocket_utils import _check_event_security

from ._ws_frames import drain_extra, has_type, receive_until

RAN: list = []


class Knob(LiveComponent):
    class State(TypedState):
        turns: int = 0

    @event_handler()
    def turn(self, **kwargs: Any) -> None:
        RAN.append("knob.turn")

    def wipe(self, **kwargs: Any) -> None:
        RAN.append("knob.wipe")


class Page(LiveView):
    exposure_policy = "explicit"
    template = '<div dj-root dj-id="0"><i>{{ knob.turns }}</i></div>'
    login_required = False
    knob = Knob()

    def mount(self, request: Any, **kwargs: Any) -> None:
        pass

    @event_handler
    def ping(self, **kwargs: Any) -> None:
        RAN.append("ping")

    def wipe(self, **kwargs: Any) -> None:
        RAN.append("wipe")


PAGE = __name__ + ".Page"


@pytest.fixture(autouse=True)
def _reset():
    RAN.clear()
    yield
    RAN.clear()


def _expected(owner: object, name: str) -> str:
    message = _check_event_security(getattr(owner, name), owner, name)
    assert message is not None
    # Actionable: names the method, its class and the decorator to add.
    assert f"Event '{name}' on {type(owner).__name__}" in message
    assert "@event_handler" in message
    return message


def _settings(**extra: Any):
    return override_settings(
        LIVEVIEW_ALLOWED_MODULES=[__name__],
        LIVEVIEW_CONFIG={},
        DJUST_CONFIG={},
        DJUST_TENANTS=None,
        **extra,
    )


# --- WebSocket ---------------------------------------------------------------


def _new_session() -> SessionStore:
    session = SessionStore()
    session.save()
    return session


async def _socket() -> WebsocketCommunicator:
    socket = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    socket.scope.update(
        session=await sync_to_async(_new_session)(), user=AnonymousUser(), tenant=None
    )
    assert (await socket.connect())[0]
    await socket.receive_json_from()
    await socket.send_json_to({"type": "mount", "view": PAGE, "url": "/4101/"})
    assert (await socket.receive_json_from())["type"] == "mount"
    return socket


async def _ws_event(socket, name: str, params: dict | None = None) -> list:
    await socket.send_json_to({"type": "event", "event": name, "params": params or {}})
    frames = await receive_until(socket, has_type("error", "patch", "html_update", "noop"))
    frames += await drain_extra(socket)
    return [f["error"] for f in frames if f.get("type") == "error"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_websocket_refuses_an_undecorated_method_and_runs_a_decorated_one():
    with _settings(DEBUG=True):
        socket = await _socket()
        try:
            assert await _ws_event(socket, "wipe") == [_expected(Page(), "wipe")]
            assert await _ws_event(socket, "ping") == []
        finally:
            await socket.disconnect()
    assert RAN == ["ping"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_websocket_component_event_refuses_an_undecorated_component_method():
    with _settings(DEBUG=True):
        socket = await _socket()
        try:
            refused = await _ws_event(socket, "wipe", {"component_id": "knob"})
            assert await _ws_event(socket, "turn", {"component_id": "knob"}) == []
        finally:
            await socket.disconnect()
    assert refused == [_expected(Knob(), "wipe")]
    assert RAN == ["knob.turn"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_websocket_production_answer_is_generic():
    with _settings(DEBUG=False):
        socket = await _socket()
        try:
            assert await _ws_event(socket, "wipe") == ["Event rejected"]
        finally:
            await socket.disconnect()
    assert RAN == []


# --- SSE ---------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_sse_refuses_an_undecorated_method_and_runs_a_decorated_one():
    from djust.sse import DjustSSEStreamView, _sse_sessions

    from djust.sse import DjustSSEMessageView

    from .test_sse_runtime_convergence_1887 import ALLOWED_ORIGIN, _drain

    sid = "41014101-4101-4101-4101-410141014101"
    _sse_sessions.pop(sid, None)
    request = RequestFactory().get(
        f"/djust/sse/{sid}/?view={PAGE}",
        HTTP_ORIGIN=ALLOWED_ORIGIN,
        HTTP_HOST="example.com",
        secure=True,
    )
    request.user = AnonymousUser()
    # Explicit exposure keeps the view's state in the session; the anonymous
    # owner of the stream is that session, so every POST presents it.
    request.session = await sync_to_async(_new_session)()

    async def _post_event(_session, sid, event):
        post = RequestFactory().post(
            f"/djust/sse/{sid}/message/",
            data=json.dumps({"type": "event", "event": event, "params": {}}),
            content_type="application/json",
            HTTP_ORIGIN=ALLOWED_ORIGIN,
            HTTP_HOST="example.com",
            secure=True,
        )
        post.user, post.session = AnonymousUser(), request.session
        return await DjustSSEMessageView().post(post, session_id=sid)

    with _settings(DEBUG=True):
        await DjustSSEStreamView().get(request, session_id=sid)
        session = _sse_sessions[sid]
        _drain(session)
        await _post_event(session, sid, "wipe")
        refused = [m["error"] for m in _drain(session) if m.get("type") == "error"]
        await _post_event(session, sid, "ping")
        ran = [m for m in _drain(session) if m.get("type") == "error"]
    assert refused == [_expected(Page(), "wipe")]
    assert ran == []
    assert RAN == ["ping"]


# --- page POST fallback --------------------------------------------------------


def _post(event: str):
    from djust.tests.test_exposure_runtime import make_request

    initial = make_request()
    Page().get(initial)
    request = RequestFactory().post(
        initial.path,
        data=json.dumps({"event": event, "params": {}}),
        content_type="application/json",
    )
    request.user, request.session, request.tenant = initial.user, initial.session, None
    return Page().post(request)


@pytest.mark.django_db
def test_http_fallback_refuses_with_the_websocket_message():
    with _settings(DEBUG=True):
        refused = _post("wipe")
        assert (refused.status_code, json.loads(refused.content)) == (
            400,
            {"error": _expected(Page(), "wipe")},
        )
        assert _post("ping").status_code == 200
    with _settings(DEBUG=False):
        assert json.loads(_post("wipe").content) == {"error": "Event rejected"}
    assert RAN == ["ping"]


# --- test client -----------------------------------------------------------------


@pytest.mark.django_db
def test_test_client_refuses_with_the_transport_message():
    from djust.testing import EventRejectedError, LiveViewTestClient

    with _settings(DEBUG=True):
        client = LiveViewTestClient(Page).mount()
        with pytest.raises(EventRejectedError):
            client.send_event("wipe")
        result = client.send_event("wipe", raise_on_missing=False)
        assert result["error"] == _expected(Page(), "wipe")
        assert client.send_event("ping")["success"] is True
    assert RAN == ["ping"]


# --- the removed setting -------------------------------------------------------------


@pytest.mark.parametrize("setting", ["LIVEVIEW_CONFIG", "DJUST_CONFIG"])
@pytest.mark.parametrize("mode", ["warn", "open"])
def test_a_removed_mode_is_reported_with_the_migration_hint(setting, mode):
    [problem] = removed_settings_in_use(SimpleNamespace(**{setting: {"event_security": mode}}))
    assert f"{setting}['event_security'] = {mode!r} was removed in djust 2.0" in problem
    assert "@event_handler" in problem
    assert MIGRATION_GUIDE_URL in problem


@pytest.mark.parametrize("setting", ["LIVEVIEW_CONFIG", "DJUST_CONFIG"])
def test_strict_or_unset_is_silent(setting):
    assert removed_settings_in_use(SimpleNamespace(**{setting: {"event_security": "strict"}})) == []
    assert removed_settings_in_use(SimpleNamespace(**{setting: {}})) == []


def test_startup_refuses_a_removed_mode():
    with override_settings(LIVEVIEW_CONFIG={"event_security": "warn"}, DJUST_CONFIG={}):
        with pytest.raises(ImproperlyConfigured) as exc:
            reject_removed_settings()
    assert "event_security" in str(exc.value)
    with override_settings(LIVEVIEW_CONFIG={"event_security": "strict"}, DJUST_CONFIG={}):
        reject_removed_settings()


# --- structural pin: one check, every client-event path ----------------------------

PACKAGE = Path(__file__).resolve().parents[1]
SHARED = {"_check_event_security", "_validate_event_security"}

#: Who may call the shared check, and which client-event path each one is. A new
#: path that resolves a client event must be added here, which is the point:
#: it has to run the shared check rather than a gate of its own.
CALLERS = {
    ("websocket_utils.py", "_validate_event_security"): "WebSocket/SSE/runtime chokepoint",
    ("websocket.py", "LiveViewConsumer._dispatch_single_event_body"): "WebSocket consumer",
    ("runtime.py", "ViewRuntime._dispatch_event_render"): "runtime (WebSocket, SSE)",
    ("runtime.py", "ViewRuntime._dispatch_single_event_body"): "runtime batch events",
    ("runtime.py", "ViewRuntime._dispatch_sticky_child_event"): "sticky child views",
    ("runtime.py", "ViewRuntime._dispatch_component_event"): "component events",
    ("mixins/request.py", "RequestMixin._post_turn_body"): "page-POST fallback",
    ("testing.py", "LiveViewTestClient.send_event"): "test client",
    ("observability/views.py", "eval_handler"): "DEBUG eval_handler",
    ("time_travel.py", "replay_event"): "time-travel replay",
}


def _production_modules(*mentioning: str):
    """Parsed production modules whose source mentions any of ``mentioning``
    (a text prefilter: a call or constant the AST holds is in the text)."""
    for path in sorted(PACKAGE.rglob("*.py")):
        rel = path.relative_to(PACKAGE).as_posix()
        if rel.startswith("tests/") or "/tests/" in rel:
            continue
        source = path.read_text(encoding="utf-8")
        if any(word in source for word in mentioning):
            yield rel, ast.parse(source)


def _calls_by_scope(names: set) -> dict:
    found: dict = {}

    def walk(node: ast.AST, scope: list) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                walk(child, scope + [child.name])
                continue
            if isinstance(child, ast.Call):
                func = child.func
                name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
                if name in names:
                    found.setdefault((rel, ".".join(scope)), []).append(name)
            walk(child, scope)

    for rel, tree in _production_modules(*names):
        walk(tree, [])
    return found


def test_every_client_event_path_calls_the_shared_check():
    sites = _calls_by_scope(SHARED)
    assert set(sites) == set(CALLERS), (
        "callers of the shared event check changed; derived %d site(s): %s"
        % (len(sites), sorted(sites))
    )
    # The chokepoint itself delegates to the one function.
    assert sites[("websocket_utils.py", "_validate_event_security")] == ["_check_event_security"]


def test_no_client_event_path_keeps_its_own_decorator_gate():
    """A caller that also tested ``is_event_handler`` itself could drift from the
    shared check again (the page POST did, #4101)."""
    own_gates = _calls_by_scope({"is_event_handler"})
    overlap = sorted(
        set(own_gates) & set(CALLERS) - {("websocket_utils.py", "_validate_event_security")}
    )
    assert overlap == [], overlap


def test_nothing_reads_the_removed_setting():
    """Only the removed-settings table may name ``event_security``: no transport
    reads a mode any more."""
    readers = sorted(
        rel
        for rel, tree in _production_modules("event_security")
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and node.value == "event_security"
    )
    assert readers == ["_removed_settings.py"], readers


def test_this_module_is_importable_by_name():
    # The WebSocket and SSE paths resolve PAGE by dotted path.
    assert sys.modules[__name__].Page is Page
