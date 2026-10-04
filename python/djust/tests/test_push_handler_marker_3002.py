"""#3002: ``@push_handler`` marks a method only server push may call.

``server_push`` calls a ``handle_*`` method without ``@event_handler`` by
design. That is a push-only handler under ``event_security = "strict"``, but
``"warn"`` and ``"open"`` let a browser call any public method, and V004 used
to suggest ``@event_handler``, which makes it a browser event target. The
marker is explicit and holds in EVERY mode on EVERY transport:

* a browser event naming a marked method is refused exactly as if the method
  did not exist (WebSocket, SSE, HTTP POST fallback, HTTP API), with no hint
  that it exists;
* server push reaches it under any name;
* unmarked ``handle_*`` methods and ``@event_handler`` methods are unchanged;
* V004 does not flag it; T019 reports a template binding to it in every mode.
"""

import gc
import importlib.util
import json
import sys
import textwrap
from unittest.mock import MagicMock

import pytest
from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory, override_settings

from djust import LiveView
from djust.config import config
from djust.decorators import (
    action,
    background,
    debounce,
    event_handler,
    is_event_handler,
    is_push_handler,
    is_server_function,
    permission_required,
    push_handler,
    rate_limit,
    server_function,
)
from djust.push import view_group_name
from djust.security import is_safe_event_name
from djust.websocket import LiveViewConsumer
from djust.websocket_utils import _check_event_security, _format_handler_not_found_error

from ._ws_frames import drain_extra, has_type, receive_until, wait_until

RAN: list = []
SETTINGS = dict(DEBUG=False, DJUST_TENANTS=None)
MARKED = ("refresh_room", "handle_marked", "called_form")
MODES = ["strict", "warn", "open"]


class RoomView(LiveView):
    template = "<div dj-root><b>{{ n }}</b></div>"
    login_required = False

    def mount(self, request, **kwargs):
        self.n = 0

    def get_context_data(self, **kwargs):
        return {"n": self.n}

    @push_handler
    def refresh_room(self, room="", **kwargs):
        RAN.append(("refresh_room", room))
        self.n += 1

    @push_handler
    def handle_marked(self, **kwargs):
        RAN.append(("handle_marked",))

    @push_handler()
    def called_form(self, **kwargs):
        RAN.append(("called_form",))

    def handle_plain(self, **kwargs):
        RAN.append(("handle_plain",))

    def plain_helper(self, **kwargs):
        RAN.append(("plain_helper",))

    @event_handler
    def ping(self, **kwargs):
        RAN.append(("ping",))


VIEW = __name__ + ".RoomView"


@pytest.fixture(autouse=True)
def _reset():
    RAN.clear()
    config.reset()
    yield
    config.reset()


def _mode(mode, **extra):
    return override_settings(
        LIVEVIEW_ALLOWED_MODULES=[__name__],
        LIVEVIEW_CONFIG={"event_security": mode},
        DJUST_CONFIG={},
        **{**SETTINGS, **extra},
    )


def _session():
    session = SessionStore()
    session.save()
    return session


async def _mounted():
    socket = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    socket.scope.update(session=await sync_to_async(_session)(), user=AnonymousUser(), tenant=None)
    assert (await socket.connect())[0]
    await socket.receive_json_from(timeout=3)
    await socket.send_json_to({"type": "mount", "view": VIEW, "url": "/r/"})
    assert (await socket.receive_json_from(timeout=3))["type"] == "mount"
    return socket


async def _event(socket, name):
    """The frames one browser event produces: an error, or a render/noop."""
    await socket.send_json_to({"type": "event", "event": name, "params": {}})
    frames = await receive_until(socket, has_type("error", "patch", "html_update", "noop"))
    return frames + await drain_extra(socket)


def _errors(frames):
    return [f for f in frames if f.get("type") == "error"]


# --- the decorator -----------------------------------------------------------


def test_bare_and_called_forms_mark_the_function_and_return_it_unchanged():
    def plain(self):
        return "ran"

    assert push_handler(plain) is plain
    assert is_push_handler(plain) and plain(None) == "ran"

    def other(self):
        pass

    assert push_handler()(other) is other and is_push_handler(other)
    assert not is_event_handler(plain) and not is_server_function(plain)


def test_the_marker_is_not_client_addressable():
    # The attribute and the metadata key sit under an underscore-private name,
    # so is_safe_event_name refuses it before any getattr.
    assert not is_safe_event_name("_djust_decorators")
    assert RoomView.refresh_room._djust_decorators["push_handler"] is True


def test_a_test_double_is_not_mistaken_for_a_marked_handler():
    assert not is_push_handler(MagicMock())
    assert not is_push_handler(lambda: None)
    assert not is_push_handler(None)


def test_marker_survives_stacking_with_other_decorators_in_either_order():
    class Stacked:
        @push_handler
        @permission_required("app.change_thing")
        def outer(self, **kwargs): ...

        @rate_limit(rate=5, burst=2)
        @push_handler
        def inner(self, **kwargs): ...

        @push_handler
        @background
        def backgrounded(self, **kwargs): ...

        @debounce(0.2)
        @push_handler
        def debounced(self, **kwargs): ...

    for name in ("outer", "inner", "backgrounded", "debounced"):
        assert is_push_handler(getattr(Stacked(), name)), name
    # The other decorators' metadata is still there.
    assert Stacked.outer._djust_decorators["permission_required"] == "app.change_thing"
    assert Stacked.inner._djust_decorators["rate_limit"]["rate"] == 5


@pytest.mark.parametrize("first", ["event_handler", "server_function"])
def test_marker_conflicts_with_browser_decorators_in_both_orders(first):
    browser = {"event_handler": event_handler, "server_function": server_function}[first]

    def one(self, **kwargs): ...

    with pytest.raises(TypeError, match="cannot be combined with @" + first):
        push_handler(browser(one))

    def two(self, **kwargs): ...

    with pytest.raises(TypeError, match="cannot be combined with @push_handler"):
        browser(push_handler(two))


def test_action_over_a_push_handler_is_refused():
    def work(self, **kwargs): ...

    with pytest.raises(TypeError, match="@push_handler"):
        action(push_handler(work))


# --- WebSocket: browser events ------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("mode", MODES)
async def test_ws_browser_event_naming_a_marked_handler_is_refused_in_every_mode(mode):
    with _mode(mode):
        socket = await _mounted()
        try:
            missing = _errors(await _event(socket, "no_such_event"))
            assert [e["error"] for e in missing] == ["Event rejected"]
            for name in MARKED:
                frames = await _event(socket, name)
                # Same refusal as a method that does not exist: same frame.
                assert _errors(frames) == missing, (mode, name, frames)
            assert RAN == [], "a browser event must never run a push handler"
            # The socket is still usable afterwards.
            assert not _errors(await _event(socket, "ping"))
            assert RAN == [("ping",)]
        finally:
            await socket.disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_ws_debug_refusal_does_not_reveal_that_the_handler_exists():
    with _mode("open", DEBUG=True):
        socket = await _mounted()
        try:
            frames = _errors(await _event(socket, "refresh_room"))
        finally:
            await socket.disconnect()
    assert len(frames) == 1
    first, _, hints = frames[0]["error"].partition("\n")
    assert first == "No handler found for event: refresh_room"
    assert not any(name in hints for name in MARKED), hints
    assert RAN == []


def test_debug_hints_never_suggest_a_push_handler():
    class Twin(LiveView):
        @push_handler
        def _secret(self, **kwargs): ...

    with override_settings(DEBUG=True):
        for typo in ("refresh_rooms", "handle_marke", "called_for"):
            message = _format_handler_not_found_error(RoomView(), typo)
            assert not any(name in message.partition("\n")[2] for name in MARKED), message
        # A private twin of a push handler is not hinted at either.
        assert "private" not in _format_handler_not_found_error(Twin(), "secret")
        # Control: an ordinary private twin still is.

        class Ordinary(LiveView):
            def _secret(self, **kwargs): ...

        assert "private" in _format_handler_not_found_error(Ordinary(), "secret")


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_unmarked_handle_methods_behave_exactly_as_before():
    # strict: refused; warn/open: a browser can still call it. The marker is
    # opt-in, so none of this moves.
    with _mode("strict"):
        socket = await _mounted()
        try:
            assert _errors(await _event(socket, "handle_plain"))
        finally:
            await socket.disconnect()
    assert RAN == []
    for mode in ("warn", "open"):
        with _mode(mode):
            socket = await _mounted()
            try:
                assert not _errors(await _event(socket, "handle_plain")), mode
            finally:
                await socket.disconnect()
    assert RAN == [("handle_plain",), ("handle_plain",)]


def test_check_event_security_refuses_a_marked_handler_in_every_mode():
    view = RoomView()
    for mode in MODES:
        with override_settings(LIVEVIEW_CONFIG={"event_security": mode}):
            config.reset()
            assert "push" in _check_event_security(view.refresh_room, view, "refresh_room")
            # ...and the gate still lets an @event_handler through.
            assert _check_event_security(view.ping, view, "ping") is None


# --- WebSocket: server push ---------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("mode", MODES)
async def test_server_push_reaches_marked_handlers_under_any_name(mode):
    with _mode(mode):
        socket = await _mounted()
        try:
            layer = get_channel_layer()
            group = view_group_name(VIEW)
            for handler, payload in (
                ("refresh_room", {"room": "a"}),
                ("handle_marked", {}),
                ("called_form", {}),
                ("handle_plain", {}),  # unchanged: handle_* is a push target
                ("plain_helper", {}),  # unchanged: blocked, neither prefix nor marker
            ):
                await layer.group_send(
                    group, {"type": "server_push", "handler": handler, "payload": payload}
                )
            await wait_until(lambda: len(RAN) >= 4, what="the four allowed push handlers")
            await drain_extra(socket)
        finally:
            await socket.disconnect()
    assert sorted(RAN) == sorted(
        [("refresh_room", "a"), ("handle_marked",), ("called_form",), ("handle_plain",)]
    )


# --- SSE ----------------------------------------------------------------------


@override_settings(ALLOWED_HOSTS=["example.com"])
@pytest.mark.django_db
@pytest.mark.asyncio
@pytest.mark.parametrize("mode", MODES)
async def test_sse_event_naming_a_marked_handler_is_refused_in_every_mode(mode):
    from .test_sse_runtime_convergence_1887 import (
        _allowlist,
        _drain,
        _mount_stream,
        _post_event,
        _register,
    )

    path = _register(RoomView)
    with _allowlist(), override_settings(LIVEVIEW_CONFIG={"event_security": mode}):
        config.reset()
        _resp, session, sid = await _mount_stream(path)
        _drain(session)
        await _post_event(session, sid, "no_such_event")
        missing = [m for m in _drain(session) if m.get("type") == "error"]
        assert len(missing) == 1, missing
        for name in MARKED:
            await _post_event(session, sid, name)
            refused = [m for m in _drain(session) if m.get("type") == "error"]
            assert refused == missing, (mode, name, refused)
        assert RAN == []
        await _post_event(session, sid, "ping")
        assert RAN == [("ping",)]


# --- HTTP POST fallback and HTTP API -------------------------------------------


def _post(view_cls, event):
    from djust.tests.test_exposure_runtime import make_request

    initial = make_request()
    view_cls().get(initial)
    request = RequestFactory().post(
        initial.path,
        data=json.dumps({"event": event, "params": {}}),
        content_type="application/json",
    )
    request.user, request.session, request.tenant = initial.user, initial.session, None
    return view_cls().post(request)


@pytest.mark.django_db
@pytest.mark.parametrize("mode", MODES)
def test_http_fallback_treats_a_marked_handler_as_missing_in_every_mode(mode):
    with _mode(mode):
        config.reset()
        missing = _post(RoomView, "no_such_event")
        for name in MARKED:
            response = _post(RoomView, name)
            assert (response.status_code, response.content) == (
                missing.status_code,
                missing.content,
            ), (mode, name)
        assert RAN == []
        assert _post(RoomView, "ping").status_code == 200
        assert RAN == [("ping",)]


@pytest.mark.django_db
def test_http_api_answers_unknown_handler_for_a_marked_handler():
    from djust.api.dispatch import dispatch_api, reset_rate_buckets
    from djust.api.registry import register_api_view, reset_registry

    class ApiRoom(LiveView):
        api_name = "push.room"
        login_required = False

        @push_handler
        def refresh_room(self, **kwargs):
            RAN.append(("api_refresh",))

        def plain_method(self, **kwargs): ...

    reset_registry()
    reset_rate_buckets()
    register_api_view("push.room", ApiRoom)
    try:
        request = RequestFactory().post(
            "/djust/api/x/y/", data=b"{}", content_type="application/json"
        )
        SessionMiddleware(lambda r: None).process_request(request)
        request.session.save()
        request.user = get_user_model().objects.create_user(username="pusher", password="pw")
        request._dont_enforce_csrf_checks = True
        marked = dispatch_api(request, "push.room", "refresh_room")
        absent = dispatch_api(request, "push.room", "nothing_here")
    finally:
        reset_registry()
        reset_rate_buckets()
    assert marked.status_code == absent.status_code == 404
    assert json.loads(marked.content)["error"] == json.loads(absent.content)["error"]
    assert json.loads(marked.content)["error"] == "unknown_handler"
    assert RAN == []


# --- system checks ------------------------------------------------------------


def test_v004_skips_marked_handlers_of_any_name_but_keeps_flagging_the_rest():
    from djust.checks.components import check_liveviews

    class Checked(LiveView):
        template = "<div dj-root></div>"
        login_required = False

        def mount(self, request, **kwargs): ...

        @push_handler
        def on_remote_update(self, **kwargs): ...

        @push_handler
        def handle_remote_update(self, **kwargs): ...

        def on_plain(self, **kwargs): ...

        def toggle_plain(self, **kwargs): ...

        def handle_unmarked(self, **kwargs): ...  # still not flagged (#3039)

    label = "%s.%s" % (Checked.__module__, Checked.__qualname__)
    messages = [m.msg for m in check_liveviews(None) if m.id == "djust.V004" and label in m.msg]
    assert any("on_plain" in m for m in messages), messages
    assert any("toggle_plain" in m for m in messages), messages
    assert not any("on_remote_update" in m or "handle_" in m for m in messages), messages
    assert "@push_handler" in [m.hint for m in check_liveviews(None) if m.id == "djust.V004"][0]


BINDING_MODULE = "push_handler_binding_fixture"
BINDING_SOURCE = '''
from djust import LiveView
from djust.decorators import event_handler, push_handler


class Bound(LiveView):
    template = """<div dj-root>
<button dj-click="refresh_room">a</button>
<button dj-click="helper">b</button>
<button dj-click="ping">c</button>
</div>"""

    @push_handler
    def refresh_room(self, **kwargs):
        pass

    def helper(self, **kwargs):
        pass

    @event_handler
    def ping(self, **kwargs):
        pass
'''


@pytest.fixture
def binding_module(tmp_path):
    path = tmp_path / ("%s.py" % BINDING_MODULE)
    path.write_text(textwrap.dedent(BINDING_SOURCE), encoding="utf-8")
    spec = importlib.util.spec_from_file_location(BINDING_MODULE, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[BINDING_MODULE] = module
    spec.loader.exec_module(module)
    try:
        yield module
    finally:
        sys.modules.pop(BINDING_MODULE, None)
        module.Bound.abstract = True
        gc.collect()


@pytest.mark.parametrize("mode", MODES)
def test_t019_reports_a_binding_to_a_push_handler_in_every_mode(binding_module, mode):
    from djust.checks.bindings import _messages, binding_reports

    with override_settings(LIVEVIEW_CONFIG={"event_security": mode}):
        config.reset()
        reports = [r for r in binding_reports() if r.owner.__module__ == BINDING_MODULE]
        found = {m.msg.split(": ", 1)[1] for m in _messages(reports)}
    pushed = [m for m in found if m.startswith("'refresh_room'")]
    assert pushed and "server push handler" in pushed[0], found
    # The undecorated helper is reported only under strict, as before.
    assert bool([m for m in found if m.startswith("'helper'")]) == (mode == "strict"), found
    assert not [m for m in found if m.startswith("'ping'")], found
