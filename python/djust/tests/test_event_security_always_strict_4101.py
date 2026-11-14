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
* outside DEBUG a missing name and a refused one get the same answer, on the
  page POST and the WebSocket, so the answer does not tell which methods exist;
* ``LiveComponentTestClient`` refuses what the transports refuse;
* two structural pins (see their docstrings for what they cannot see);
* ``LIVEVIEW_CONFIG`` / ``DJUST_CONFIG`` ``event_security``: ``"strict"`` (any
  case) is silent, ``"open"`` / ``"warn"`` fail startup with the migration
  hint, anything else fails startup as an unknown value
  (``djust._removed_settings``).
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


@pytest.mark.django_db
def test_component_test_client_refuses_with_the_transport_message():
    from djust.components.base import LiveComponent as InstanceComponent
    from djust.testing import EventRejectedError, LiveComponentTestClient

    class Dial(InstanceComponent):
        template = "<i></i>"

        def mount(self, **props: Any) -> None:
            pass

        @event_handler
        def turn(self, **kwargs: Any) -> None:
            RAN.append("dial.turn")

        def wipe(self, **kwargs: Any) -> None:
            RAN.append("dial.wipe")

    client = LiveComponentTestClient(Dial).mount()
    with pytest.raises(EventRejectedError) as exc:
        client.send_event("wipe")
    assert str(exc.value) == _expected(client.component, "wipe")
    with pytest.raises(EventRejectedError):
        client.send_event("_private")
    assert client.send_event("turn")["success"] is True
    assert RAN == ["dial.turn"]


# --- a missing name and a refused one read alike in production ----------------------

#: An inherited public framework method (undecorated) and a server-push method:
#: both exist, neither is an event. A client must not tell them from a name that
#: does not exist.
EXISTING_BUT_REFUSED = ("wipe", "get_template", "server_push", "resume_wizard")


@pytest.mark.django_db
def test_http_fallback_missing_and_refused_answers_are_equal_in_production():
    with _settings(DEBUG=False):
        missing = _post("no_such_method")
        answers = {name: _post(name) for name in EXISTING_BUT_REFUSED}
    for name, response in answers.items():
        assert (response.status_code, response.content) == (
            missing.status_code,
            missing.content,
        ), name
    assert json.loads(missing.content) == {"error": "Event rejected"}
    assert RAN == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_websocket_missing_and_refused_answers_are_equal_in_production():
    with _settings(DEBUG=False):
        socket = await _socket()
        try:
            missing = await _ws_event(socket, "no_such_method")
            for name in EXISTING_BUT_REFUSED:
                assert await _ws_event(socket, name) == missing, name
        finally:
            await socket.disconnect()
    assert missing == ["Event rejected"]
    assert RAN == []


@pytest.mark.django_db
def test_http_fallback_debug_answer_for_a_missing_name_stays_helpful():
    with _settings(DEBUG=True):
        body = json.loads(_post("pign").content)
    assert body["error"].startswith("No handler found for event: pign")
    assert "ping" in body["error"]  # the typo hint


# --- the removed setting -------------------------------------------------------------


@pytest.mark.parametrize("setting", ["LIVEVIEW_CONFIG", "DJUST_CONFIG"])
@pytest.mark.parametrize("mode", ["warn", "open", "WARN", " Open "])
def test_a_removed_mode_is_reported_with_the_migration_hint(setting, mode):
    [problem] = removed_settings_in_use(SimpleNamespace(**{setting: {"event_security": mode}}))
    assert f"{setting}['event_security'] = {mode!r} was removed in djust 2.0" in problem
    assert "@event_handler" in problem
    assert MIGRATION_GUIDE_URL in problem


@pytest.mark.parametrize("setting", ["LIVEVIEW_CONFIG", "DJUST_CONFIG"])
@pytest.mark.parametrize("value", ["strict", "Strict", " STRICT "])
def test_strict_in_any_case_or_unset_is_silent(setting, value):
    assert removed_settings_in_use(SimpleNamespace(**{setting: {"event_security": value}})) == []
    assert removed_settings_in_use(SimpleNamespace(**{setting: {}})) == []


@pytest.mark.parametrize("setting", ["LIVEVIEW_CONFIG", "DJUST_CONFIG"])
@pytest.mark.parametrize("value", ["stirct", "", None, True])
def test_an_unknown_value_says_unknown_and_to_delete_the_key(setting, value):
    [problem] = removed_settings_in_use(SimpleNamespace(**{setting: {"event_security": value}}))
    assert f"{setting}['event_security'] = {value!r} is an unknown value" in problem
    assert "Delete the key" in problem
    assert "was removed" not in problem


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
    ("testing.py", "LiveComponentTestClient.send_event"): "component test client",
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
    """Pins the KNOWN callers of the shared check to :data:`CALLERS`.

    What it catches: a caller removed (a path that stopped calling the check)
    or added without review. What it cannot see on its own: a new path that
    resolves a client event and never calls the check at all, e.g. a fresh
    ``getattr(view, name)()``; that is
    :func:`test_dynamic_dispatch_on_an_event_name_is_reviewed`'s job, and it is
    a name heuristic, not proof.
    """
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


#: Functions that call a method named by a variable holding an event or handler
#: name WITHOUT the shared check, each reviewed: why it is not a client event.
REVIEWED_DISPATCH = {
    ("api/dispatch.py", "_dispatch_api_authenticated"): (
        "HTTP API: refuses all but @event_handler(expose_api=True)"
    ),
    ("websocket.py", "LiveViewConsumer._prepare_server_push"): (
        "server push from the channel layer, not a client"
    ),
    ("websocket.py", "LiveViewConsumer._route_to_view"): (
        "a consumer method the router names (uploads, request_html, time travel)"
    ),
    ("websocket.py", "LiveViewConsumer._run_lane_item"): "a queued _route_to_view item",
    ("theming/gallery/live_views.py", "_make_forwarder.handler"): (
        "a fixed component action, chosen at import"
    ),
}


def _dynamic_dispatch_by_scope() -> dict:
    """``getattr(obj, X)`` where ``X`` is a variable or attribute whose name
    mentions ``event`` or ``handler`` (``event_name``, ``handler_name``,
    ``snapshot.event_name`` ...), by enclosing function."""
    found: dict = {}

    def walk(rel: str, node: ast.AST, scope: list) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                walk(rel, child, scope + [child.name])
                continue
            if (
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Name)
                and child.func.id == "getattr"
                and len(child.args) >= 2
            ):
                arg = child.args[1]
                name = (
                    arg.id
                    if isinstance(arg, ast.Name)
                    else arg.attr
                    if isinstance(arg, ast.Attribute)
                    else ""
                )
                if "event" in name or "handler" in name:
                    found.setdefault((rel, ".".join(scope)), []).append(child.lineno)
            walk(rel, child, scope)

    for rel, tree in _production_modules("getattr"):
        walk(rel, tree, [])
    return found


def test_dynamic_dispatch_on_an_event_name_is_reviewed():
    """Every function that looks up a method by an event/handler-name variable
    either calls the shared check (:data:`CALLERS`) or is listed, with a reason,
    in :data:`REVIEWED_DISPATCH`. A heuristic: a lookup through a variable named
    otherwise (``name``, ``action``) is not seen, so review still matters."""
    sites = _dynamic_dispatch_by_scope()
    unchecked = set(sites) - set(CALLERS)
    assert unchecked == set(REVIEWED_DISPATCH), (
        "dynamic dispatch on an event name outside the shared check; derived %d "
        "site(s): %s" % (len(unchecked), sorted(unchecked))
    )
