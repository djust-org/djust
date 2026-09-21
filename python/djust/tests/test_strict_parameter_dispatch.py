"""ADR-036 server policy and actual invocation regressions."""

from datetime import date
from decimal import Decimal
import gc
import json
import weakref
import uuid

import pytest

from djust.decorators import event_handler, server_function
from djust.validation import validate_handler_params, validated_call_arguments
from djust.websocket_utils import _call_handler
from djust import LiveView


class StrictTransportView(LiveView):
    template = "<div dj-root>{{ result }}</div>"
    api_name = "strict.transport"

    def mount(self, request, **kwargs):
        self.result = "initial"

    @event_handler(parameter_policy="strict", expose_api=True)
    def select(self, value: int, /, *, when: date):
        self.result = f"{value}:{when.isoformat()}"
        return self.result

    @server_function(parameter_policy="strict", coerce_types=False)
    def double(self, value: int):
        return value * 2


class StrictActorTransportView(StrictTransportView):
    use_actors = True
    api_name = "strict.actor.transport"


def test_contract_cache_does_not_retain_owner_and_metadata_omits_defaults():
    from djust.validation import get_strict_handler_contract

    class Owner(LiveView):
        @event_handler(parameter_policy="strict")
        def choose(self, value: str = "SERVER_DEFAULT_SENTINEL"):
            pass

    first, second = Owner(), Owner()
    assert get_strict_handler_contract(first.choose) is get_strict_handler_contract(second.choose)
    assert "SERVER_DEFAULT_SENTINEL" not in repr(first._extract_handler_metadata())
    assert "SERVER_DEFAULT_SENTINEL" not in repr(first.get_debug_info()["handlers"])
    ref = weakref.ref(first)
    del first
    gc.collect()
    assert ref() is None


def test_contract_cache_does_not_retain_an_owner_through_a_default():
    from typing import Any
    from djust.validation import get_strict_handler_contract

    class Owner:
        pass

    owner = Owner()

    def handler(value: Any = owner):
        return value

    owner.handler = handler
    ref = weakref.ref(owner)
    get_strict_handler_contract(handler)
    del owner, handler
    gc.collect()
    assert ref() is None


def test_cached_contract_does_not_change_python_default_application():
    from typing import Any
    from djust.validation import get_strict_handler_contract

    default = object()

    def handler(first: Any = default, *, last: int):
        return first, last

    bound = get_strict_handler_contract(handler).bind({"last": "7"})
    assert handler(*bound.args, **bound.kwargs) == (default, 7)


@pytest.mark.django_db
@pytest.mark.parametrize("flat", [False, True])
@pytest.mark.parametrize("valid", [False, True])
def test_real_http_fallback(flat, valid):
    from djust.tests.test_exposure_runtime import make_request
    from django.test import RequestFactory

    initial = make_request()
    view = StrictTransportView()
    view.get(initial)
    payload = {"_args": ["7" if valid else "SECRET_INVALID"], "when": "2026-09-21"}
    request = RequestFactory().post(
        initial.path,
        data=json.dumps(payload if flat else {"event": "select", "params": payload}),
        content_type="application/json",
        HTTP_X_DJUST_EVENT="select",
    )
    request.user, request.session, request.tenant = initial.user, initial.session, None
    response = StrictTransportView().post(request)
    assert response.status_code == (200 if valid else 400), response.content[:1000]
    if valid:
        assert b"7:2026-09-21" in response.content
    else:
        assert b"SECRET_INVALID" not in response.content


@pytest.mark.django_db
def test_test_client_uses_strict_call_plan():
    from djust.testing import LiveViewTestClient

    client = LiveViewTestClient(StrictTransportView).mount()
    result = client.send_event("select", _args=["7"], when="2026-09-21")
    assert result["success"], result
    assert client.view_instance.result == "7:2026-09-21"
    assert not client.send_event("select", _args=[True], when="2026-09-21")["success"]


@pytest.mark.django_db
@pytest.mark.parametrize("server_rpc", [False, True])
@pytest.mark.parametrize("valid", [False, True])
def test_actual_exposed_api_dispatch(server_rpc, valid):
    from django.contrib.auth import get_user_model
    from django.test import RequestFactory
    from djust.api.registry import register_api_view, reset_registry
    from djust.api.dispatch import dispatch_api, dispatch_server_function, reset_rate_buckets
    from djust.tests.test_exposure_runtime import make_request

    user = get_user_model().objects.create_user(username="strict-user")
    initial = make_request()
    body = (
        {"params": {"value": 2 if valid else "2"}}
        if server_rpc
        else {"_args": ["7" if valid else "SECRET_INVALID"], "when": "2026-09-21"}
    )
    request = RequestFactory().post(
        "/djust/api/strict.transport/select/",
        data=json.dumps(body),
        content_type="application/json",
    )
    request.user, request.session, request.tenant = user, initial.session, None
    request._dont_enforce_csrf_checks = True
    reset_registry()
    reset_rate_buckets()
    register_api_view("strict.transport", StrictTransportView)
    try:
        dispatch = dispatch_server_function if server_rpc else dispatch_api
        response = dispatch(request, "strict.transport", "double" if server_rpc else "select")
        assert response.status_code == (200 if valid else 400), response.content
        if not valid:
            assert b"SECRET_INVALID" not in response.content
    finally:
        reset_registry()
        reset_rate_buckets()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("actor_mode", [False, True])
async def test_real_websocket_strict_positional_and_rejection(actor_mode):
    from asgiref.sync import sync_to_async
    from channels.testing import WebsocketCommunicator
    from django.test import override_settings
    from djust.tests.test_exposure_runtime import make_request
    from djust.websocket import LiveViewConsumer

    request = await sync_to_async(make_request)()
    with override_settings(LIVEVIEW_ALLOWED_MODULES=["djust.tests"], DJUST_TENANTS=None):
        socket = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        socket.scope.update(session=request.session, user=request.user, tenant=None)
        assert (await socket.connect())[0]
        await socket.receive_json_from(timeout=3)
        try:
            await socket.send_json_to(
                {
                    "type": "mount",
                    "view": __name__
                    + (".StrictActorTransportView" if actor_mode else ".StrictTransportView"),
                    "url": request.path,
                }
            )
            mounted = await socket.receive_json_from(timeout=3)
            assert mounted["type"] == "mount"
            contracts = mounted["parameter_contracts"]
            assert contracts["version"] == 1
            assert contracts["owners"][0]["handlers"]["select"]["policy"] == "strict"
            assert "double" not in contracts["owners"][0]["handlers"]
            for value, expected in [("7", "patch"), ("SECRET_INVALID", "error")]:
                await socket.send_json_to(
                    {
                        "type": "event",
                        "event": "select",
                        "params": {"_args": [value], "when": "2026-09-21"},
                        "ref": 7,
                    }
                )
                frame = await socket.receive_json_from(timeout=3)
                if expected == "error":
                    assert frame["type"] == "error", frame
                    assert "SECRET_INVALID" not in json.dumps(frame)
                else:
                    assert frame["type"] in ("patch", "update", "noop"), frame
                    assert "7:2026-09-21" in json.dumps(frame)
                    assert frame["parameter_contracts"] == contracts
                    expected_view = __name__ + (
                        ".StrictActorTransportView" if actor_mode else ".StrictTransportView"
                    )
                    assert frame["parameter_contract_view"] == expected_view
                    await socket.send_json_to({"type": "request_html"})
                    recovery = await socket.receive_json_from(timeout=3)
                    assert recovery["type"] == "html_recovery"
                    assert recovery["version"] == frame["version"]
                    assert "7:2026-09-21" in recovery["html"]
                    assert recovery["parameter_contracts"] == contracts
                    assert recovery["parameter_contract_view"] == expected_view
        finally:
            await socket.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_real_sse_strict_positional_and_rejection():
    from asgiref.sync import sync_to_async
    from django.test import override_settings
    from djust.sse import DjustSSEStreamView, DjustSSEMessageView, _sse_sessions
    from djust.tests.test_exposure_sse_navigation import request_for, drain

    sid = str(uuid.uuid4())
    with override_settings(LIVEVIEW_ALLOWED_MODULES=["djust.tests"], DJUST_TENANTS=None):
        request = await sync_to_async(request_for)(
            "GET",
            f"/djust/sse/{sid}/",
            {"view": __name__ + ".StrictTransportView", "_djust_url": "/strict/"},
        )
        response = await DjustSSEStreamView().get(request, session_id=sid)
        assert response.status_code == 200
        session = _sse_sessions[sid]
        try:
            mounted = next(frame for frame in drain(session) if frame["type"] == "mount")
            assert (
                mounted["parameter_contracts"]["owners"][0]["handlers"]["select"]["policy"]
                == "strict"
            )
            for value in ("7", "SECRET_INVALID"):
                post = await sync_to_async(request_for)(
                    "POST",
                    f"/djust/sse/{sid}/message/",
                    {
                        "type": "event",
                        "event": "select",
                        "params": {"_args": [value], "when": "2026-09-21"},
                        "ref": 7,
                    },
                    request.session.session_key,
                )
                assert (await DjustSSEMessageView().post(post, session_id=sid)).status_code == 200
                frames = drain(session)
                assert session.view_instance.result == "7:2026-09-21", frames
                if value == "SECRET_INVALID":
                    assert any(frame["type"] == "error" for frame in frames), frames
                    assert "SECRET_INVALID" not in json.dumps(frames)
                else:
                    rendered = next(frame for frame in frames if frame["type"] == "patch")
                    assert rendered["parameter_contracts"] == mounted["parameter_contracts"]
        finally:
            _sse_sessions.pop(sid, None)


@pytest.mark.parametrize("decorator", [event_handler, server_function])
def test_strict_policy_declaration_and_invalid_input(decorator):
    called = []

    @decorator(parameter_policy="strict")
    def handler(value: bool):
        called.append(value)

    result = validate_handler_params(handler, {"value": "not-bool"}, "handler")
    assert not result["valid"]
    assert called == []
    assert "not-bool" not in repr(result)


@pytest.mark.parametrize("decorator", [event_handler, server_function])
@pytest.mark.parametrize("policy", ["typo", "", True, 1])
def test_invalid_decorator_policy_rejected(decorator, policy):
    with pytest.raises(ValueError):
        decorator(parameter_policy=policy)(lambda **kwargs: None)


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.asyncio
async def test_strict_positional_binding_reaches_real_invoker(asynchronous):
    called = []

    def sync(value: int, /, *, when: date):
        called.append((value, when))

    async def asynchronous_handler(value: int, /, *, when: date):
        called.append((value, when))

    handler = event_handler(parameter_policy="strict")(
        asynchronous_handler if asynchronous else sync
    )
    result = validate_handler_params(
        handler, {"when": "2026-09-21"}, "handler", positional_args=["7"]
    )
    assert result["valid"]
    args, kwargs = validated_call_arguments(result)
    await _call_handler(handler, kwargs, positional_args=args)
    assert called == [(7, date(2026, 9, 21))]


@pytest.mark.parametrize("decorator", [event_handler, server_function])
def test_coercion_disabled_in_strict_policy(decorator):
    @decorator(parameter_policy="strict", coerce_types=False)
    def handler(value: int):
        pass

    assert not validate_handler_params(handler, {"value": "2"}, "handler")["valid"]
    assert validate_handler_params(handler, {"value": 2}, "handler")["valid"]


def test_global_policy_and_explicit_legacy_override():
    from djust.config import config

    @event_handler()
    def inherited(value: bool):
        pass

    @event_handler(parameter_policy="legacy")
    def legacy(value: bool):
        pass

    old = config.get("event_parameter_policy", "legacy")
    try:
        config.set("event_parameter_policy", "strict")
        assert not validate_handler_params(inherited, {"value": "invalid"}, "inherited")["valid"]
        assert validate_handler_params(legacy, {"value": "invalid"}, "legacy")["valid"]
    finally:
        config.set("event_parameter_policy", old)


def test_legacy_invocation_result_remains_compatible():
    @event_handler(parameter_policy="legacy")
    def handler(value: int):
        return value

    result = validate_handler_params(handler, {"value": "2"}, "handler")
    args, kwargs = validated_call_arguments(result)
    assert handler(*args, **kwargs) == 2


def test_invalid_result_cannot_be_invoked():
    with pytest.raises(ValueError):
        validated_call_arguments({"valid": False, "coerced_params": {}})


@pytest.mark.parametrize("decorator", [event_handler, server_function])
def test_class_forward_return_reference_does_not_block_input_contract(decorator):
    class ForwardView(LiveView):
        @decorator(parameter_policy="strict")
        def choose(self, value: int) -> "ForwardView":
            return self

    view = ForwardView()
    result = validate_handler_params(view.choose, {"value": "7"}, "choose")
    assert result["valid"]
    args, kwargs = validated_call_arguments(result)
    assert view.choose(*args, **kwargs) is view


@pytest.mark.parametrize("decorator", [event_handler, server_function])
def test_strict_declaration_never_stringifies_a_server_default(decorator):
    class Default:
        def __str__(self):
            raise AssertionError("Declaration must not stringify server defaults")

    @decorator(parameter_policy="strict")
    def choose(value: str = Default()):
        pass

    assert "default" not in repr(
        choose._djust_decorators[
            "event_handler" if decorator is event_handler else "server_function"
        ]["params"]
    )


@pytest.mark.parametrize("positional", [None, "text", 1, {}])
def test_malformed_inline_arguments_rejected_even_without_required_inputs(positional):
    @event_handler(parameter_policy="strict")
    def handler():
        pass

    assert not validate_handler_params(handler, {"_args": positional}, "handler")["valid"]


@pytest.mark.asyncio
@pytest.mark.django_db
@pytest.mark.parametrize("route", ["root", "child", "component", "deferred"])
async def test_routed_strict_invocations_and_malformed_args(monkeypatch, route):
    from djust.tests.test_runtime_child_routing_1892 import (
        _make_sticky_parent,
        _make_runtime_with_view,
        _ClickComponent,
    )

    parent, child = _make_sticky_parent()
    runtime, transport = _make_runtime_with_view(parent)
    component = _ClickComponent("strict-component")
    parent._components["strict-component"] = component
    owner = child if route == "child" else component if route == "component" else parent
    calls = []

    @event_handler(parameter_policy="strict")
    def choose(self, value: int, /, *, enabled: bool):
        calls.append((value, enabled))

    @event_handler(parameter_policy="strict")
    def noop(self):
        calls.append("malformed-was-invoked")

    monkeypatch.setattr(type(owner), "strict_choose", choose, raising=False)
    monkeypatch.setattr(type(owner), "strict_noop", noop, raising=False)
    routing = (
        {"view_id": "child-1"}
        if route == "child"
        else {"component_id": "strict-component"}
        if route == "component"
        else {}
    )
    for name, payload in [
        ("strict_choose", {"_args": ["7"], "enabled": "off"}),
        ("strict_noop", {"_args": None}),
    ]:
        if route == "deferred":
            await runtime._dispatch_single_event(owner, name, payload)
        else:
            await runtime.dispatch_event(
                {"type": "event", "event": name, "params": {**routing, **payload}}
            )
    assert calls == [(7, False)], transport.sent


@pytest.mark.asyncio
async def test_actual_component_actor_rejection_does_not_write_fallback_state():
    from djust._rust import create_session_actor

    class Component:
        value = 0

        def get_context_data(self):
            return {"value": self.value}

        @event_handler(parameter_policy="strict")
        def choose(self, value: int):
            self.value = value

    component = Component()
    actor = await create_session_actor("strict-component-actor")
    try:
        result = await actor.mount("tests.StrictComponentHost", {}, None)
        view_id = result["view_id"]
        await actor.create_component(
            view_id, "choice", "<div>{{ value }}</div>", {"value": 0}, component
        )
        html = await actor.component_event(view_id, "choice", "choose", {"value": "7"})
        assert component.value == 7
        assert "7" in html
        with pytest.raises(Exception, match="Invalid handler parameters"):
            await actor.component_event(view_id, "choice", "choose", {"value": "SECRET_INVALID"})
        assert component.value == 7
        html = await actor.component_event(view_id, "choice", "choose", {"value": "8"})
        assert "SECRET_INVALID" not in html
        assert component.value == 8
    finally:
        await actor.shutdown()


@pytest.mark.asyncio
async def test_actual_actor_preserves_strict_python_types_and_positional_values():
    from djust._rust import create_session_actor

    class View:
        called = None

        def get_context_data(self):
            return {"ok": self.called is not None}

        @event_handler(parameter_policy="strict")
        def select(self, value: int, /, *, when: date, cost: Decimal):
            self.called = (value, when, cost)

    view = View()
    actor = await create_session_actor("strict-parameter-core-actor")
    try:
        await actor.mount("tests.StrictParameterView", {"ok": False}, view)
        await actor.event("select", {"_args": ["7"], "when": "2026-09-21", "cost": "1.25"})
        assert view.called == (7, date(2026, 9, 21), Decimal("1.25"))
        view.called = None
        with pytest.raises(Exception):
            await actor.event("select", {"_args": [True], "when": "2026-09-21", "cost": "1.25"})
        assert view.called is None
    finally:
        await actor.shutdown()
