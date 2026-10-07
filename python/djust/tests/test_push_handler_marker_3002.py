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
    is_push_only,
    is_server_function,
    permission_required,
    push_handler,
    rate_limit,
    server_function,
    throttle,
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


class MarkedMixin:
    @push_handler
    def mixin_refresh(self, **kwargs):
        RAN.append(("mixin_refresh",))


class OverrideView(MarkedMixin, RoomView):
    """Every override below forgets the marker (or adds @event_handler)."""

    def refresh_room(self, room="", **kwargs):
        RAN.append(("override_refresh", room))

    def mixin_refresh(self, **kwargs):  # shadows the mixin's marked method
        RAN.append(("shadow_refresh",))

    @event_handler
    def called_form(self, **kwargs):
        RAN.append(("override_event_handler",))


class DeepView(OverrideView):
    """A third level, unmarked again."""

    def refresh_room(self, room="", **kwargs):
        RAN.append(("deep_refresh", room))


class StaticView(LiveView):
    template = "<div dj-root></div>"
    login_required = False

    @push_handler
    @staticmethod
    def above_static(**kwargs): ...

    @staticmethod
    @push_handler
    def below_static(**kwargs): ...

    @push_handler
    @classmethod
    def above_class(cls, **kwargs): ...

    @classmethod
    @push_handler
    def below_class(cls, **kwargs): ...


VIEW = __name__ + ".RoomView"
OVERRIDE_VIEW = __name__ + ".OverrideView"
OVERRIDES = ("refresh_room", "mixin_refresh", "called_form")


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


async def _mounted(view=VIEW):
    socket = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    socket.scope.update(session=await sync_to_async(_session)(), user=AnonymousUser(), tenant=None)
    assert (await socket.connect())[0]
    await socket.receive_json_from(timeout=3)
    await socket.send_json_to({"type": "mount", "view": view, "url": "/r/"})
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


def test_marker_survives_stacking_with_background_in_either_order():
    class Stacked:
        @push_handler
        @background
        def outer(self, **kwargs): ...

        @background
        @push_handler
        def inner(self, **kwargs): ...

    for name in ("outer", "inner"):
        assert is_push_handler(getattr(Stacked(), name)), name


@pytest.mark.parametrize(
    "inert",
    [
        lambda: permission_required("app.change_thing"),
        lambda: rate_limit(rate=5, burst=2),
        lambda: debounce(0.2),
        lambda: throttle(0.2),
    ],
    ids=["permission_required", "rate_limit", "debounce", "throttle"],
)
def test_decorators_server_push_does_not_enforce_are_refused_in_both_orders(inert):
    def one(self, **kwargs): ...

    with pytest.raises(TypeError, match="would have no effect"):
        push_handler(inert()(one))

    def two(self, **kwargs): ...

    with pytest.raises(TypeError, match="cannot be combined with @"):
        inert()(push_handler(two))


@pytest.mark.parametrize("order", ["marker_above", "marker_below"])
@pytest.mark.parametrize("kind", ["staticmethod", "classmethod"])
def test_marker_works_with_staticmethod_and_classmethod_in_either_order(kind, order):
    name = ("above_" if order == "marker_above" else "below_") + kind[:-6]
    # What dispatch resolves (getattr on the instance) carries the marker.
    resolved = getattr(StaticView(), name)
    assert is_push_handler(resolved), name
    assert is_push_only(StaticView(), name, resolved)
    assert not is_event_handler(resolved)


def test_marker_above_staticmethod_conflicts_with_event_handler():
    def one(**kwargs): ...

    with pytest.raises(TypeError, match="cannot be combined with @event_handler"):
        push_handler(staticmethod(event_handler(one)))


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


# --- the marker is inherited by overrides ---------------------------------------


def test_is_push_only_follows_the_mro_not_just_the_resolved_function():
    for cls in (OverrideView, DeepView):
        view = cls()
        for name in OVERRIDES:
            resolved = getattr(view, name)
            assert not is_push_handler(resolved), (cls, name)  # the override itself is unmarked
            assert is_push_only(view, name, resolved), (cls, name)
        assert is_push_only(cls, "refresh_room")  # a class works as owner too
        assert not is_push_only(view, "ping", view.ping)
        assert not is_push_only(view, "handle_plain", view.handle_plain)
        assert not is_push_only(view, "nothing_here")


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("mode", MODES)
async def test_ws_unmarked_override_of_a_marked_method_is_still_refused(mode):
    with _mode(mode):
        socket = await _mounted(OVERRIDE_VIEW)
        try:
            missing = _errors(await _event(socket, "no_such_event"))
            assert [e["error"] for e in missing] == ["Event rejected"]
            for name in OVERRIDES:
                assert _errors(await _event(socket, name)) == missing, (mode, name)
            assert RAN == []
        finally:
            await socket.disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_server_push_reaches_unmarked_overrides_under_any_name():
    with _mode("open"):
        socket = await _mounted(OVERRIDE_VIEW)
        try:
            layer = get_channel_layer()
            group = view_group_name(OVERRIDE_VIEW)
            for handler in ("refresh_room", "mixin_refresh"):
                await layer.group_send(
                    group, {"type": "server_push", "handler": handler, "payload": {}}
                )
            await wait_until(lambda: len(RAN) >= 2, what="the two overridden push handlers")
            await drain_extra(socket)
        finally:
            await socket.disconnect()
    assert sorted(RAN) == [("override_refresh", ""), ("shadow_refresh",)]


@pytest.mark.django_db
@pytest.mark.parametrize("mode", MODES)
def test_http_fallback_refuses_an_unmarked_override_like_a_missing_method(mode):
    with _mode(mode):
        config.reset()
        missing = _post(OverrideView, "no_such_event")
        for name in OVERRIDES:
            response = _post(OverrideView, name)
            assert (response.status_code, response.content) == (
                missing.status_code,
                missing.content,
            ), (mode, name)
        assert RAN == []


def _v021(*classes):
    from djust.checks.components import check_push_handler_overrides

    labels = {"%s.%s" % (c.__module__, c.__qualname__): c for c in classes}
    return [
        m
        for m in check_push_handler_overrides(None)
        if any(m.msg.startswith(label + ".") for label in labels)
    ]


def test_v021_reports_overrides_that_drop_the_marker_or_add_event_handler():
    by_name = {m.msg.split("() ")[0].rsplit(".", 1)[-1]: m for m in _v021(OverrideView)}
    assert set(by_name) == {"refresh_room", "mixin_refresh", "called_form"}, by_name
    assert by_name["refresh_room"].level < 30  # Info: still protected
    assert by_name["mixin_refresh"].level < 30
    assert by_name["called_form"].level == 30  # Warning: the @event_handler is dead
    assert "@event_handler" in by_name["called_form"].msg
    assert all(m.id == "djust.V021" for m in by_name.values())

    # A re-marked override, and the marked base itself, are not reported.
    class Remarked(RoomView):
        @push_handler
        def refresh_room(self, room="", **kwargs): ...

    assert _v021(Remarked, RoomView) == []
    # DeepView overrides only refresh_room (marked two levels up).
    assert {m.msg.split("() ")[0].rsplit(".", 1)[-1] for m in _v021(DeepView)} == {"refresh_room"}


def test_v021_can_be_suppressed():
    with override_settings(DJUST_CONFIG={"suppress_checks": ["V021"]}):
        config.reset()
        assert _v021(OverrideView) == []
    config.reset()


def test_v004_does_not_flag_an_unmarked_override_of_a_marked_method():
    from djust.checks.components import check_liveviews

    class Base(LiveView):
        template = "<div dj-root></div>"
        login_required = False

        def mount(self, request, **kwargs): ...

        @push_handler
        def on_remote_update(self, **kwargs): ...

    class Child(Base):
        def on_remote_update(self, **kwargs): ...

        def on_plain(self, **kwargs): ...

    label = "%s.%s" % (Child.__module__, Child.__qualname__)
    messages = [m.msg for m in check_liveviews(None) if m.id == "djust.V004" and label in m.msg]
    assert any("on_plain" in m for m in messages), messages
    assert not any("on_remote_update" in m for m in messages), messages


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


class BoundChild(Bound):
    template = """<div dj-root><button dj-click="refresh_room">a</button></div>"""

    def refresh_room(self, **kwargs):  # unmarked override
        pass


class BoundEventHandler(Bound):
    template = """<div dj-root><button dj-click="refresh_room">a</button></div>"""

    @event_handler
    def refresh_room(self, **kwargs):  # override that adds @event_handler
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
        for name in ("Bound", "BoundChild", "BoundEventHandler"):
            getattr(module, name).abstract = True
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


# --- tooling: test client, smoke test, eval_handler -------------------------------


def _client(view_cls=RoomView):
    from djust.testing import LiveViewTestClient

    client = LiveViewTestClient(view_cls)
    client.mount()
    return client


def test_test_client_send_event_refuses_a_marked_handler_like_a_missing_one():
    from djust.testing import NoHandlerFoundError

    for view_cls in (RoomView, OverrideView):
        client = _client(view_cls)
        for name in MARKED if view_cls is RoomView else OVERRIDES:
            with pytest.raises(NoHandlerFoundError):
                client.send_event(name)
            assert client.send_event(name, raise_on_missing=False)["success"] is False
    assert RAN == []
    assert _client().send_event("ping")["success"] is True


def test_test_client_send_push_reaches_push_handlers_and_the_consumer_allowlist():
    client = _client()
    assert client.send_push("refresh_room", payload={"room": "a"})["success"] is True
    assert client.send_push("handle_marked")["success"] is True
    assert client.send_push("handle_plain")["success"] is True
    blocked = client.send_push("plain_helper")
    assert blocked["success"] is False and "not callable by server push" in blocked["error"]
    assert client.send_push(state={"n": 5})["state_after"]["n"] == 5
    assert RAN == [("refresh_room", "a"), ("handle_marked",), ("handle_plain",)]


def test_smoke_test_does_not_report_a_push_handler_as_reachable_but_unfuzzed(monkeypatch):
    from djust.testing import _unfuzzed_reachable_methods

    real = config.get
    monkeypatch.setattr(
        config,
        "get",
        lambda key, default=None: "warn" if key == "event_security" else real(key, default),
    )
    # ``handle_plain`` and ``plain_helper`` really are reachable under warn.
    assert _unfuzzed_reachable_methods(RoomView) == ["handle_plain", "plain_helper"]
    assert _unfuzzed_reachable_methods(OverrideView) == ["handle_plain", "plain_helper"]


def test_eval_handler_answers_a_marked_handler_like_a_missing_one():
    from djust.observability.registry import _clear_registry, register_view
    from djust.observability.views import eval_handler

    from .conftest import observability_request_factory

    def call(name):
        request = observability_request_factory().post(
            "/?session_id=s",
            data=json.dumps({"handler_name": name}),
            content_type="application/json",
        )
        return eval_handler(request)

    _clear_registry()
    try:
        with override_settings(DEBUG=True):
            room = RoomView()  # the registry holds views weakly
            register_view("s", room)
            for name in ("refresh_room", "handle_marked"):
                marked = call(name)
                missing = call("nope_" + name)
                assert marked.status_code == missing.status_code == 404
                assert "has no callable" in json.loads(missing.content)["error"]
                strip = lambda r, n: json.loads(r.content)["error"].replace(n, "X")  # noqa: E731
                assert strip(marked, name) == strip(missing, "nope_" + name)
            override = OverrideView()
            register_view("s", override)
            assert call("refresh_room").status_code == 404
            # Control: an ordinary undecorated method is the 403 it always was.
            assert call("plain_helper").status_code == 403
    finally:
        _clear_registry()
    assert RAN == []


def _t019(owner):
    from djust.checks.bindings import _messages, binding_reports

    reports = [
        r
        for r in binding_reports()
        if r.owner.__module__ == BINDING_MODULE and r.owner.__qualname__ == owner
    ]
    return [m.msg.split(": ", 1)[1] for m in _messages(reports)]


@pytest.mark.parametrize("owner", ["BoundChild", "BoundEventHandler"])
@pytest.mark.parametrize("mode", MODES)
def test_t019_follows_the_marker_through_overrides(binding_module, mode, owner):
    with override_settings(LIVEVIEW_CONFIG={"event_security": mode}):
        config.reset()
        found = _t019(owner)
    assert len(found) == 1 and "server push handler" in found[0], found


# --- every gate, on an override (so a per-method check would not pass) ---------------


def test_declared_handlers_and_client_metadata_drop_push_only_names():
    from djust._parameter_metadata import _event_methods, declared_handlers

    names = {h.name for h in declared_handlers(OverrideView)}
    assert "ping" in names and not names & set(OVERRIDES), names
    methods = _event_methods(OverrideView())
    assert "ping" in methods and not set(methods) & set(OVERRIDES), set(methods)


def test_check_event_security_refuses_an_override_in_every_mode():
    view = OverrideView()
    for mode in MODES:
        with override_settings(LIVEVIEW_CONFIG={"event_security": mode}):
            config.reset()
            for name in OVERRIDES:
                assert "push" in _check_event_security(getattr(view, name), view, name), name


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_ws_override_is_refused_as_not_found_not_as_a_security_failure(caplog):
    # The not-found gate and the security gate give the same production frame, so
    # the DEBUG text and the log line tell them apart.
    import logging

    with _mode("open", DEBUG=True), caplog.at_level(logging.WARNING):
        socket = await _mounted(OVERRIDE_VIEW)
        try:
            frames = _errors(await _event(socket, "refresh_room"))
        finally:
            await socket.disconnect()
    assert frames[0]["error"].startswith("No handler found for event: refresh_room"), frames
    assert "Handler not found: refresh_room" in caplog.text
    assert RAN == []


def test_debug_hints_skip_overrides_of_push_handlers():
    class Base(LiveView):
        @push_handler
        def _secret(self, **kwargs): ...

    class Child(Base):
        def _secret(self, **kwargs): ...

    with override_settings(DEBUG=True):
        view = OverrideView()
        # Typo suggestions: the unmarked override of refresh_room is not offered.
        message = _format_handler_not_found_error(view, "refresh_rooms")
        assert "refresh_room" not in message.partition("\n")[2], message
        # "Available handlers": the @event_handler override of a push-only name
        # is not listed; a real handler still is.
        message = _format_handler_not_found_error(view, "zzz_unknown")
        assert "ping" in message and "called_form" not in message, message
        # The private twin of an overridden push handler is not hinted at.
        assert "private" not in _format_handler_not_found_error(Child(), "secret")


@pytest.mark.django_db
def test_http_api_refuses_an_unmarked_override_like_an_unknown_handler():
    from djust.api.dispatch import dispatch_api, reset_rate_buckets
    from djust.api.registry import register_api_view, reset_registry

    class ApiBase(LiveView):
        api_name = "push.base"
        login_required = False

        @push_handler
        def refresh_room(self, **kwargs): ...

    class ApiChild(ApiBase):
        api_name = "push.child"

        @event_handler(expose_api=True)  # exposed, yet still push-only by name
        def refresh_room(self, **kwargs):
            RAN.append(("api_child",))

    reset_registry()
    reset_rate_buckets()
    register_api_view("push.child", ApiChild)
    try:
        request = RequestFactory().post(
            "/djust/api/x/y/", data=b"{}", content_type="application/json"
        )
        SessionMiddleware(lambda r: None).process_request(request)
        request.session.save()
        request.user = get_user_model().objects.create_user(username="pusher2", password="pw")
        request._dont_enforce_csrf_checks = True
        response = dispatch_api(request, "push.child", "refresh_room")
    finally:
        reset_registry()
        reset_rate_buckets()
    assert response.status_code == 404
    assert json.loads(response.content)["error"] == "unknown_handler"
    assert RAN == []


def test_component_test_client_refuses_marked_and_overridden_handlers():
    from djust.components.base import LiveComponent
    from djust.testing import LiveComponentTestClient, NoHandlerFoundError

    class Marked(LiveComponent):
        def mount(self, **kwargs) -> None: ...

        @push_handler
        def refresh(self, **kwargs):
            RAN.append(("component_refresh",))

        def plain(self, **kwargs):
            RAN.append(("component_plain",))

    class Overridden(Marked):
        def refresh(self, **kwargs):
            RAN.append(("component_override",))

    for cls in (Marked, Overridden):
        with pytest.raises(NoHandlerFoundError):
            LiveComponentTestClient(cls).mount().send_event("refresh")
    assert RAN == []
    LiveComponentTestClient(Marked).mount().send_event("plain")
    assert RAN == [("component_plain",)]


def test_is_push_handler_unwraps_descriptors_on_its_own():
    # What a class body holds for each of these is the descriptor, not the function.
    for name in ("above_static", "below_static", "above_class", "below_class"):
        assert is_push_handler(StaticView.__dict__[name]), name
    assert not is_push_handler(staticmethod(lambda: None))
    assert not is_push_handler(classmethod(lambda cls: None))


@pytest.mark.asyncio
async def test_time_travel_replay_refuses_an_event_handler_override_of_a_push_name():
    from djust.time_travel import EventSnapshot, replay_event

    def snapshot(name):
        return EventSnapshot(event_name=name, params={}, ref=None, ts=0.0, state_before={})

    view = OverrideView()
    view.mount(RequestFactory().get("/"))
    for name in ("called_form", "refresh_room", "mixin_refresh"):
        assert await sync_to_async(replay_event)(view, snapshot(name), record_replay=False) is None
    assert RAN == []
    # Control: a real @event_handler still replays.
    await sync_to_async(replay_event)(view, snapshot("ping"), record_replay=False)
    assert RAN == [("ping",)]


def test_v021_sees_an_unmarked_definition_on_another_base_than_the_marker():
    class Marker(LiveView):
        template = "<div dj-root></div>"

        @push_handler
        def foo(self, **kwargs): ...

        @push_handler
        def bar(self, **kwargs): ...

    class Eh(LiveView):
        @event_handler
        def foo(self, **kwargs): ...

    class Plain:  # a mixin: never checked on its own
        def bar(self, **kwargs): ...

    class Combined(Eh, Plain, Marker):
        pass

    found = {m.msg.split("() ")[0].rsplit(".", 1)[-1]: m for m in _v021(Combined)}
    assert set(found) == {"foo", "bar"}, found
    assert found["foo"].level == 30 and ".Eh)" in found["foo"].msg
    assert found["bar"].level < 30 and ".Plain)" in found["bar"].msg
    # Eh and Plain do not mark anything themselves, so only Combined is reported.
    assert _v021(Eh) == []
