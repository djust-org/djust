"""LIVEVIEW_MIDDLEWARE wraps every turn kind over every transport (ADR-044).

Partial coverage is the failure this guards against: an app keying owner
scoping or row-level security on a context variable set by turn middleware
leaks or breaks on any turn the chain does not wrap. Each test drives a real
transport entry point and asserts the chain ran (kind, source, transport) and
that context the middleware set reached the view code.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import sys
from typing import Any, Dict, List

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.test import RequestFactory, override_settings
from django.urls import path

from djust import LiveView, turns
from djust.decorators import background, event_handler
from djust.turns import Refuse

pytest.importorskip("channels")

pytestmark = pytest.mark.django_db(transaction=True)

MOD = __name__

#: Every turn the recording middleware saw: (kind, source, transport, view, event).
TURNS: List[tuple] = []
#: What view code observed in ``caller`` / ``turn_tag``, by site.
SEEN: Dict[str, Any] = {}
#: kind or (kind, source) -> a no-arg Refusal factory, read by ``refuse``.
REFUSE: Dict[Any, Any] = {}
#: (kind, source) -> exception to raise from ``explode``.
EXPLODE: Dict[Any, BaseException] = {}

#: Set by the middleware on every turn except ``async`` ones, so a background
#: callback seeing it proves the value propagated from the turn that started it.
caller: contextvars.ContextVar = contextvars.ContextVar("tm_caller", default=None)
#: Set by the middleware on every turn: which turn the code is running in.
turn_tag: contextvars.ContextVar = contextvars.ContextVar("tm_turn", default=None)


async def record(turn, next):
    TURNS.append(
        (
            turn.kind,
            turn.source,
            turn.transport,
            turn.view_class.__name__ if turn.view_class is not None else None,
            turn.event,
        )
    )
    tag = turn_tag.set("%s:%s" % (turn.kind, turn.source))
    who = caller.set("caller-for-%s" % turn.kind) if turn.kind != "async" else None
    try:
        return await next(turn)
    finally:
        if who is not None:
            caller.reset(who)
        turn_tag.reset(tag)


async def refuse(turn, next):
    factory = REFUSE.get((turn.kind, turn.source)) or REFUSE.get(turn.kind)
    if factory is not None:
        return factory()
    return await next(turn)


async def explode(turn, next):
    exc = EXPLODE.get((turn.kind, turn.source)) or EXPLODE.get(turn.kind)
    if exc is not None:
        raise exc
    return await next(turn)


CHAIN = [f"{MOD}.record", f"{MOD}.refuse", f"{MOD}.explode"]


def _seen(site):
    SEEN[site] = (caller.get(), turn_tag.get())


class TurnView(LiveView):
    template = '<div dj-view="x" dj-id="0">n={{ n }}</div>'
    tick_interval = None

    def mount(self, request, **kwargs):
        self.n = 0
        _seen("mount")

    def handle_params(self, params, uri):
        _seen("params")

    @event_handler()
    def bump(self, **kwargs):
        self.n += 1
        _seen("event")

    @event_handler()
    def work(self, **kwargs):
        self.start_async(self._work)

    def _work(self):
        _seen("callback")
        return 1

    def handle_async_result(self, name, result=None, error=None):
        _seen("result")

    @event_handler()
    @background
    def bg(self, **kwargs):
        _seen("background")

    def handle_refresh(self, **kwargs):
        self.n = 7
        _seen("server_push")

    def handle_info(self, message):
        self.n = 8
        _seen("db_notify")


class TickView(TurnView):
    tick_interval = 30

    def handle_tick(self):
        self.n += 1
        _seen("tick")


class OtherView(TurnView):
    pass


for _cls in (TurnView, TickView, OtherView):
    setattr(sys.modules[MOD], _cls.__name__, _cls)

urlpatterns = [
    path("turns/", TurnView.as_view(), name="turns"),
    path("turns/other/", OtherView.as_view(), name="turns-other"),
]


@pytest.fixture(autouse=True)
def _clean():
    TURNS.clear()
    SEEN.clear()
    REFUSE.clear()
    EXPLODE.clear()
    turns.reset_chain()
    with override_settings(
        LIVEVIEW_MIDDLEWARE=CHAIN,
        LIVEVIEW_ALLOWED_MODULES=[MOD],
        ROOT_URLCONF=MOD,
        LOGIN_URL="/login/",
        ALLOWED_HOSTS=["*"],
    ):
        yield
    turns.reset_chain()


def _kinds(transport=None):
    return [(k, s) for (k, s, t, _v, _e) in TURNS if transport is None or t == transport]


# --------------------------------------------------------------------------- #
# WebSocket
# --------------------------------------------------------------------------- #


class _User:
    is_authenticated = True
    is_active = True
    is_anonymous = False
    pk = 1

    def has_perms(self, perms):
        return True


def _asgi(app):
    async def wrapped(scope, receive, send):
        scope = dict(scope)
        scope["user"] = _User()
        scope["session"] = {}
        return await app(scope, receive, send)

    return wrapped


async def _frames(comm, wait=0.4, *, deadline=3.0):
    """Frames until ``wait`` seconds pass with none (or ``deadline`` overall)."""
    out = []
    loop = asyncio.get_running_loop()
    stop = loop.time() + deadline
    while True:
        remaining = stop - loop.time()
        if remaining <= 0:
            return out
        try:
            msg = await asyncio.wait_for(comm.output_queue.get(), min(wait, remaining))
        except asyncio.TimeoutError:
            return out
        if msg["type"] == "websocket.close":
            out.append({"__close__": msg.get("code")})
            return out
        if msg["type"] == "websocket.send" and msg.get("text"):
            out.append(json.loads(msg["text"]))


async def _ws(view="TurnView", *, mount_extra=None, expect_mount=True):
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    comm = WebsocketCommunicator(_asgi(LiveViewConsumer.as_asgi()), "/ws/")
    ok, _ = await comm.connect()
    assert ok
    await _frames(comm, 0.3)  # connect banner
    frame = {"type": "mount", "view": f"{MOD}.{view}", "url": "/turns/"}
    frame.update(mount_extra or {})
    await comm.send_json_to(frame)
    first = await _frames(comm, 0.6)
    if expect_mount:
        assert any(f.get("type") in ("mount", "html_update", "patch") for f in first), first
    return comm, first


async def _close(comm):
    try:
        await comm.disconnect()
    except Exception:  # noqa: BLE001
        pass


async def test_ws_every_client_and_server_turn_runs_the_chain():
    comm, _ = await _ws()
    try:
        assert ("mount", "page", "ws", "TurnView", None) in TURNS
        assert SEEN["mount"] == ("caller-for-mount", "mount:page")

        await comm.send_json_to({"type": "event", "event": "bump", "params": {}, "ref": 1})
        await _frames(comm)
        assert SEEN["event"] == ("caller-for-event", "event:client")

        await comm.send_json_to({"type": "url_change", "params": {"q": "1"}, "uri": "/turns/?q=1"})
        await _frames(comm)
        assert SEEN["params"] == ("caller-for-params", "params:url_change")

        await comm.send_input({"type": "server_push", "handler": "handle_refresh", "payload": {}})
        await _frames(comm)
        assert SEEN["server_push"] == ("caller-for-push", "push:server_push")

        await comm.send_input({"type": "db_notify", "channel": "c", "payload": {}})
        await _frames(comm)
        assert SEEN["db_notify"] == ("caller-for-push", "push:db_notify")

        await comm.send_input({"type": "presence_event", "event": "join", "payload": {}})
        await comm.send_input({"type": "client_push_event", "event": "hi", "payload": {}})
        pushed = await _frames(comm)
        assert any(f.get("type") == "presence_event" for f in pushed)
        assert any(f.get("type") == "push_event" for f in pushed)

        await comm.send_json_to({"type": "request_html"})
        await _frames(comm)
    finally:
        await _close(comm)

    ws = _kinds("ws")
    for expected in [
        ("mount", "page"),
        ("event", "client"),
        ("params", "url_change"),
        ("push", "server_push"),
        ("push", "db_notify"),
        ("push", "presence"),
        ("push", "client_push"),
        ("render", "recovery"),
    ]:
        assert expected in ws, (expected, ws)


async def test_ws_tick_is_a_push_turn():
    comm, _ = await _ws("TickView")
    try:
        await _frames(comm, 0.5, deadline=0.6)  # ticks every 30ms: never quiet
    finally:
        await _close(comm)
    assert ("push", "tick") in _kinds("ws")
    assert SEEN["tick"] == ("caller-for-push", "push:tick")


async def test_ws_reconnect_hint_makes_a_reconnect_turn():
    comm, _ = await _ws(mount_extra={"reconnect": True})
    await _close(comm)
    assert _kinds("ws")[0] == ("reconnect", "page")


async def test_ws_batch_slot_and_navigation_mounts_run_the_chain():
    comm, _ = await _ws()
    try:
        await comm.send_json_to(
            {
                "type": "mount_batch",
                "views": [{"view": f"{MOD}.OtherView", "url": "/turns/other/", "target_id": "a"}],
            }
        )
        await _frames(comm)
        await comm.send_json_to(
            {"type": "mount", "view": f"{MOD}.OtherView", "url": "/turns/other/", "target_id": "b"}
        )
        await _frames(comm)
        await comm.send_json_to(
            {"type": "live_redirect_mount", "view": f"{MOD}.OtherView", "url": "/turns/other/"}
        )
        await _frames(comm)
    finally:
        await _close(comm)
    sources = [(k, s, v) for (k, s, t, v, _e) in TURNS if k == "mount"]
    assert ("mount", "batch", "OtherView") in sources, sources
    assert ("mount", "slot", "OtherView") in sources, sources
    assert ("mount", "navigation", "OtherView") in sources, sources


@pytest.mark.parametrize("handler", ["work", "bg"])
async def test_ws_context_propagates_into_background_work(handler):
    comm, _ = await _ws()
    try:
        await comm.send_json_to({"type": "event", "event": handler, "params": {}, "ref": 3})
        await _frames(comm, 0.8)
    finally:
        await _close(comm)
    site = "callback" if handler == "work" else "background"
    # The value the event turn's middleware set reached the background work...
    assert SEEN[site][0] == "caller-for-event"
    # ...which ran inside its own async turn.
    assert SEEN[site][1] == "async:start"
    if handler == "work":
        assert SEEN["result"] == ("caller-for-event", "async:result")
    assert ("async", "start") in _kinds("ws")


async def test_ws_refused_event_never_reaches_the_handler():
    REFUSE["event"] = lambda: Refuse.deny("nope")
    comm, _ = await _ws()
    try:
        await comm.send_json_to({"type": "event", "event": "bump", "params": {}, "ref": 9})
        frames = await _frames(comm)
    finally:
        await _close(comm)
    assert "event" not in SEEN
    errors = [f for f in frames if f.get("type") == "error"]
    assert errors and errors[0]["code"] == "permission_denied" and errors[0]["ref"] == 9
    assert errors[0]["error"] == "nope"
    assert not any("__close__" in f for f in frames)  # an event deny keeps the socket


async def test_ws_mount_refused_with_login_navigates_and_closes():
    REFUSE["mount"] = lambda: Refuse.login()
    comm, frames = await _ws(expect_mount=False)
    frames += await _frames(comm)
    await _close(comm)
    assert "mount" not in SEEN
    assert {"type": "navigate", "to": "/login/"} in frames
    assert {"__close__": 4403} in frames


async def test_ws_flash_refusal_keeps_the_socket():
    REFUSE["event"] = lambda: Refuse.flash("Slow down", level="warning")
    comm, _ = await _ws()
    try:
        await comm.send_json_to({"type": "event", "event": "bump", "params": {}, "ref": 4})
        frames = await _frames(comm)
    finally:
        await _close(comm)
    assert "event" not in SEEN
    assert {"type": "flash", "action": "put", "level": "warning", "message": "Slow down"} in frames
    assert any(f.get("type") == "noop" and f.get("ref") == 4 for f in frames)
    assert not any("__close__" in f for f in frames)


async def test_ws_refused_push_applies_nothing_and_sends_nothing():
    REFUSE[("push", "server_push")] = lambda: Refuse.deny()
    comm, _ = await _ws()
    try:
        await comm.send_input({"type": "server_push", "handler": "handle_refresh", "payload": {}})
        frames = await _frames(comm)
        # The render lock was released: a later event still runs.
        await comm.send_json_to({"type": "event", "event": "bump", "params": {}, "ref": 2})
        later = await _frames(comm)
    finally:
        await _close(comm)
    assert "server_push" not in SEEN
    assert frames == []
    assert SEEN["event"][0] == "caller-for-event"
    assert any(f.get("type") in ("patch", "html_update") for f in later)


async def test_ws_permission_denied_from_middleware_is_a_deny():
    EXPLODE["event"] = PermissionDenied("x")
    comm, _ = await _ws()
    try:
        await comm.send_json_to({"type": "event", "event": "bump", "params": {}, "ref": 5})
        frames = await _frames(comm)
    finally:
        await _close(comm)
    assert any(f.get("type") == "error" and f.get("code") == "permission_denied" for f in frames)


async def test_ws_middleware_error_gets_the_protected_envelope():
    EXPLODE["event"] = RuntimeError("SECRET-IN-MIDDLEWARE")
    comm, _ = await _ws()
    try:
        await comm.send_json_to({"type": "event", "event": "bump", "params": {}, "ref": 6})
        frames = await _frames(comm)
    finally:
        await _close(comm)
    # A legacy view with diagnostics allowed: the error goes on to the
    # transport's own handler, as dispatch_message's catch always let it.
    # Either way no exception value reaches the client.
    assert "SECRET-IN-MIDDLEWARE" not in json.dumps(frames)
    assert any(f.get("type") == "error" for f in frames)
    assert "event" not in SEEN


# --------------------------------------------------------------------------- #
# SSE
# --------------------------------------------------------------------------- #

# The request's own origin: SSE checks the full origin (scheme, host, port).
ORIGIN = "http://testserver"
SID = "31111111-1111-1111-1111-111111111111"


def _sse_user():
    user = _User()
    user.pk = 7
    return user


async def _sse_mount(view="TurnView"):
    from djust.sse import DjustSSEStreamView, _sse_sessions

    request = RequestFactory().get(
        f"/djust/sse/{SID}/?view={MOD}.{view}&_djust_url=/turns/", HTTP_ORIGIN=ORIGIN
    )
    request.user = _sse_user()
    await DjustSSEStreamView().get(request, session_id=SID)
    return _sse_sessions.get(SID)


async def _sse_post(frame):
    from djust.sse import DjustSSEMessageView

    request = RequestFactory().post(
        f"/djust/sse/{SID}/message/",
        data=json.dumps(frame),
        content_type="application/json",
        HTTP_ORIGIN=ORIGIN,
    )
    request.user = _sse_user()
    return await DjustSSEMessageView().post(request, session_id=SID)


def _sse_drain(session):
    out = []
    while not session.queue.empty():
        msg = session.queue.get_nowait()
        if msg is not None:
            out.append(msg)
    return out


@pytest.fixture
def _sse_clean():
    from djust.sse import _sse_sessions

    _sse_sessions.pop(SID, None)
    yield
    session = _sse_sessions.pop(SID, None)
    if session is not None:
        session.shutdown()


async def test_sse_every_turn_kind_runs_the_chain(_sse_clean):
    session = await _sse_mount()
    assert session is not None
    assert SEEN["mount"] == ("caller-for-mount", "mount:page")

    await _sse_post({"type": "event", "event": "bump", "params": {}, "ref": 1})
    assert SEEN["event"] == ("caller-for-event", "event:client")

    await _sse_post({"type": "url_change", "params": {"q": "2"}, "uri": "/turns/?q=2"})
    assert SEEN["params"] == ("caller-for-params", "params:url_change")

    await _sse_post({"type": "event", "event": "work", "params": {}, "ref": 2})
    for _ in range(50):
        if "result" in SEEN:
            break
        await asyncio.sleep(0.02)
    assert SEEN["callback"] == ("caller-for-event", "async:start")
    assert SEEN["result"] == ("caller-for-event", "async:result")

    sse = _kinds("sse")
    for expected in [
        ("mount", "page"),
        ("event", "client"),
        ("params", "url_change"),
        ("async", "start"),
        ("async", "result"),
    ]:
        assert expected in sse, (expected, sse)


async def test_sse_stream_reopened_with_the_same_id_is_a_reconnect(_sse_clean):
    await _sse_mount()
    TURNS.clear()
    await _sse_mount()
    assert _kinds("sse")[0] == ("reconnect", "page")


async def test_sse_refused_event_never_reaches_the_handler(_sse_clean):
    session = await _sse_mount()
    _sse_drain(session)
    REFUSE["event"] = lambda: Refuse.deny()
    await _sse_post({"type": "event", "event": "bump", "params": {}, "ref": 3})
    frames = _sse_drain(session)
    assert "event" not in SEEN
    assert any(f.get("type") == "error" and f.get("code") == "permission_denied" for f in frames)


async def test_sse_root_events_are_rate_limited_at_ingress(_sse_clean):
    session = await _sse_mount()
    _sse_drain(session)
    from djust.rate_limit import ConnectionRateLimiter

    session._rate_limiter = ConnectionRateLimiter(rate=0.001, burst=1, max_warnings=99)
    await _sse_post({"type": "event", "event": "bump", "params": {}, "ref": 1})
    SEEN.clear()
    await _sse_post({"type": "event", "event": "bump", "params": {}, "ref": 2})
    frames = _sse_drain(session)
    assert "event" not in SEEN
    assert any(f.get("code") == "rate_limited" and f.get("ref") == 2 for f in frames)


# --------------------------------------------------------------------------- #
# HTTP (page GET, page-POST fallback) and the auto-exposed API
# --------------------------------------------------------------------------- #


def _http(method, url="/turns/", **kwargs):
    from django.contrib.sessions.middleware import SessionMiddleware

    rf = RequestFactory()
    request = getattr(rf, method)(url, **kwargs)
    SessionMiddleware(lambda r: None).process_request(request)
    request.user = AnonymousUser()
    return request


def test_http_get_and_post_run_the_chain():
    response = TurnView.as_view()(_http("get"))
    assert response.status_code == 200
    assert SEEN["mount"] == ("caller-for-mount", "mount:page")

    SEEN.clear()
    request = _http(
        "post",
        data=json.dumps({"event": "bump", "params": {}}),
        content_type="application/json",
    )
    response = TurnView.as_view()(request)
    assert response.status_code == 200, response.content
    assert SEEN["event"] == ("caller-for-event", "event:client")
    assert ("mount", "page") in _kinds("http")
    assert ("event", "client") in _kinds("http")


def test_http_get_login_refusal_redirects_with_next():
    REFUSE["mount"] = lambda: Refuse.login()
    response = TurnView.as_view()(_http("get", "/turns/?a=1"))
    assert response.status_code == 302
    assert response["Location"] == "/login/?next=%2Fturns%2F%3Fa%3D1"
    assert "mount" not in SEEN


def test_http_get_deny_raises_permission_denied_for_the_403_handler():
    REFUSE["mount"] = lambda: Refuse.deny()
    with pytest.raises(PermissionDenied):
        TurnView.as_view()(_http("get"))
    assert "mount" not in SEEN


def test_http_post_refusal_is_json():
    REFUSE["event"] = lambda: Refuse.deny("no")
    request = _http(
        "post", data=json.dumps({"event": "bump", "params": {}}), content_type="application/json"
    )
    response = TurnView.as_view()(request)
    assert response.status_code == 403
    assert json.loads(response.content) == {"error": "no", "code": "permission_denied"}
    assert "event" not in SEEN


def test_http_redirect_refusal_rejects_offsite_targets():
    REFUSE["mount"] = lambda: Refuse.redirect("https://evil.example/")
    response = TurnView.as_view()(_http("get"))
    assert response.status_code == 302 and response["Location"] == "/"


def test_api_dispatch_runs_the_chain():
    from django.contrib.auth import get_user_model

    from djust.api.dispatch import dispatch_api, reset_rate_buckets
    from djust.api.registry import register_api_view, reset_registry

    class ApiView(LiveView):
        api_name = "turns.api"

        def mount(self, request, **kwargs):
            self.n = 0

        @event_handler(expose_api=True)
        def ping(self, **kwargs):
            _seen("api")
            return {"ok": True}

    reset_registry()
    reset_rate_buckets()
    register_api_view("turns.api", ApiView)
    try:
        request = _http(
            "post", "/djust/api/turns.api/ping/", data=b"{}", content_type="application/json"
        )
        request.user = get_user_model().objects.create_user(username="api-turns", password="pw")
        request._dont_enforce_csrf_checks = True
        response = dispatch_api(request, "turns.api", "ping")
        assert response.status_code == 200, response.content
        assert SEEN["api"] == ("caller-for-event", "event:api")
        assert ("event", "api", "api", "ApiView", "ping") in TURNS

        REFUSE["event"] = lambda: Refuse.deny()
        response = dispatch_api(request, "turns.api", "ping")
        assert response.status_code == 403
        assert json.loads(response.content)["error"] == "permission_denied"
    finally:
        reset_registry()
        reset_rate_buckets()
