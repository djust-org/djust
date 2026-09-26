"""ADR-036 D5: framework dispatch context never comes from the client payload.

Strict handlers see exactly the application arguments on every transport:
transport bookkeeping (``_cacheRequestId``, ``_activity``) is dropped, an
unconsumed routing key (``view_id``, ``component_id``) fails closed, and any
other key, including the ADR-034 source name ``component``, is rejected as
an extra argument. A framework-injected (trusted) parameter binds only from
server-owned values. Legacy handlers keep their existing keyword mapping.
"""

import json
import uuid

import pytest
from asgiref.sync import async_to_sync, sync_to_async

from djust import LiveView
from djust._parameter_contract import ContractError, ParameterContract, ParameterError
from djust.decorators import event_handler, server_function
from djust.validation import get_strict_handler_contract

CALLS: list = []

METADATA = {
    "cache": {"value": "1", "_cacheRequestId": "c1"},
    "activity": {"value": "1", "_activity": "panel"},
}
FORGED = {
    "source": {"value": "1", "component": "forged"},
    "underscore": {"value": "1", "_forged": "x"},
    "view_id": {"value": "1", "view_id": "not-a-child"},
    "component_id": {"value": "1", "component_id": "not-a-component"},
    "positional": {"value": "1", "_args": ["2"]},
}


class DispatchView(LiveView):
    template = "<div dj-root>{{ n }}</div>"
    api_name = "trusted.dispatch"

    def mount(self, request, **kwargs):
        self.n = 0

    @event_handler(parameter_policy="strict", expose_api=True)
    def choose(self, value: int):
        CALLS.append(("choose", value))
        self.n = value

    @server_function(parameter_policy="strict")
    def rpc(self, value: int):
        CALLS.append(("rpc", value))
        return value

    @event_handler
    def legacy(self, value=None, **kwargs):
        CALLS.append(("legacy", value, dict(kwargs)))


class ActorDispatchView(DispatchView):
    use_actors = True
    api_name = "trusted.dispatch.actor"


@pytest.fixture(autouse=True)
def _clear_calls():
    CALLS.clear()
    yield
    CALLS.clear()


# --- the contract -----------------------------------------------------------


class _Source:
    pass


def _callback(first: int, component: _Source, value: int) -> None:
    pass


def _contract() -> ParameterContract:
    return ParameterContract.compile(_callback, frozenset({"component"}))


def test_trusted_parameter_binds_only_from_server_values_and_is_not_published():
    source = _Source()
    bound = _contract().bind({"value": "3"}, ["2"], trusted={"component": source})
    assert bound.arguments == {"first": 2, "component": source, "value": 3}
    assert [item["name"] for item in _contract().metadata()] == ["first", "value"]


@pytest.mark.parametrize(
    "params,positional",
    [
        ({"value": 1, "component": "forged"}, [1]),  # client key
        ({"value": 1}, [1, "forged"]),  # positional value reaching the slot
    ],
)
def test_client_cannot_supply_a_trusted_parameter(params, positional):
    with pytest.raises(ParameterError) as error:
        _contract().bind(params, positional, trusted={"component": _Source()})
    assert "forged" not in str(error.value)


def test_trusted_name_beats_a_catch_all():
    def handler(component: _Source, **fields: str):
        pass

    contract = ParameterContract.compile(handler, frozenset({"component"}))
    with pytest.raises(ParameterError):
        contract.bind({"component": "forged"}, trusted={"component": _Source()})
    bound = contract.bind({"other": "x"}, trusted={"component": _Source()})
    assert bound.arguments["fields"] == {"other": "x"}


@pytest.mark.parametrize("trusted", [None, {}, {"component": 1, "extra": 2}])
def test_dispatcher_must_supply_exactly_the_trusted_values(trusted):
    with pytest.raises(ContractError):
        _contract().bind({"value": 1}, [1], trusted=trusted)


def test_trusted_names_must_be_named_keyword_parameters():
    def positional_only(component: _Source, /, value: int):
        pass

    def open_only(**component: _Source):
        pass

    for handler, name in [(_callback, "missing"), (positional_only, "component")]:
        with pytest.raises(ContractError, match="named keyword parameter"):
            ParameterContract.compile(handler, frozenset({name}))
    with pytest.raises(ContractError, match="named keyword parameter"):
        ParameterContract.compile(open_only, frozenset({"component"}))


def test_trusted_variants_are_cached_separately():
    def handler(component: int, value: int):
        pass

    plain = get_strict_handler_contract(handler)
    trusted = get_strict_handler_contract(handler, frozenset({"component"}))
    assert plain is not trusted
    assert trusted is get_strict_handler_contract(handler, frozenset({"component"}))
    assert [i["name"] for i in plain.metadata()] == ["component", "value"]
    assert [i["name"] for i in trusted.metadata()] == ["value"]


# --- every transport --------------------------------------------------------


def _expect(case: str, handler: str = "choose") -> list:
    return [(handler, 1)] if case in METADATA else []


@pytest.mark.asyncio
@pytest.mark.django_db
@pytest.mark.parametrize("case", sorted(set(METADATA) - {"activity"} | set(FORGED)))
async def test_shared_runtime_route(case):
    # WebSocket and SSE both dispatch through ViewRuntime.dispatch_event.
    # (_activity is the runtime's own deferral gate there, covered elsewhere.)
    from djust.tests.test_runtime_child_routing_1892 import _make_runtime_with_view

    view = DispatchView()
    view.mount(None)
    runtime, transport = _make_runtime_with_view(view)
    payload = {**METADATA, **FORGED}[case]
    await runtime.dispatch_event({"type": "event", "event": "choose", "params": dict(payload)})
    assert CALLS == _expect(case), transport.sent
    if case in FORGED:
        assert any(frame.get("type") == "error" for frame in transport.sent)
        assert "forged" not in json.dumps(transport.sent, default=str)


@pytest.mark.django_db
@pytest.mark.parametrize("flat", [False, True])
@pytest.mark.parametrize("case", sorted(set(METADATA) | set(FORGED)))
def test_http_fallback(flat, case):
    from django.test import RequestFactory
    from djust.tests.test_exposure_runtime import make_request

    initial = make_request()
    DispatchView().get(initial)
    payload = {**METADATA, **FORGED}[case]
    request = RequestFactory().post(
        initial.path,
        data=json.dumps(payload if flat else {"event": "choose", "params": payload}),
        content_type="application/json",
        HTTP_X_DJUST_EVENT="choose",
    )
    request.user, request.session, request.tenant = initial.user, initial.session, None
    response = DispatchView().post(request)
    assert CALLS == _expect(case), response.content
    assert response.status_code == (200 if case in METADATA else 400)
    assert b"forged" not in response.content


@pytest.mark.django_db
@pytest.mark.parametrize("server_rpc", [False, True])
@pytest.mark.parametrize("case", sorted(set(METADATA) | set(FORGED)))
def test_exposed_api_and_server_function(server_rpc, case):
    from django.contrib.auth import get_user_model
    from django.test import RequestFactory
    from djust.api.dispatch import dispatch_api, dispatch_server_function, reset_rate_buckets
    from djust.api.registry import register_api_view, reset_registry
    from djust.tests.test_exposure_runtime import make_request

    user = get_user_model().objects.create_user(username="trusted-%s" % uuid.uuid4().hex[:8])
    payload = {**METADATA, **FORGED}[case]
    request = RequestFactory().post(
        "/djust/api/trusted.dispatch/x/",
        data=json.dumps({"params": payload} if server_rpc else payload),
        content_type="application/json",
    )
    request.user, request.session, request.tenant = user, make_request().session, None
    request._dont_enforce_csrf_checks = True
    reset_registry()
    reset_rate_buckets()
    register_api_view("trusted.dispatch", DispatchView)
    try:
        dispatch = dispatch_server_function if server_rpc else dispatch_api
        response = dispatch(request, "trusted.dispatch", "rpc" if server_rpc else "choose")
    finally:
        reset_registry()
        reset_rate_buckets()
    assert CALLS == _expect(case, "rpc" if server_rpc else "choose"), response.content
    assert response.status_code == (200 if case in METADATA else 400)
    assert b"forged" not in response.content


@pytest.mark.django_db
@pytest.mark.parametrize("case", sorted(set(METADATA) | set(FORGED)))
def test_test_client(case):
    from djust.testing import LiveViewTestClient

    client = LiveViewTestClient(DispatchView).mount()
    result = client.send_event("choose", **{**METADATA, **FORGED}[case])
    assert CALLS == _expect(case)
    assert bool(result["success"]) is (case in METADATA)


@pytest.mark.asyncio
@pytest.mark.parametrize("case", sorted(set(METADATA) | set(FORGED)))
async def test_time_travel_replay(case, monkeypatch):
    from djust.tests.test_debug_render_contracts import DebugView, setup
    from djust.time_travel import replay_event

    @event_handler(parameter_policy="strict")
    def advance(self, value: int):
        CALLS.append(("choose", value))

    monkeypatch.setattr(DebugView, "advance", advance)
    _, view = await setup()
    payload = {**METADATA, **FORGED}[case]
    await sync_to_async(replay_event)(view, view._time_travel_buffer.jump(0), dict(payload))
    assert CALLS == _expect(case)


def test_actor_bridge():
    from djust.validation import actor_handler_arguments

    view = ActorDispatchView()
    for case, payload in {**METADATA, **FORGED}.items():
        if case in METADATA:
            assert actor_handler_arguments(view.choose, dict(payload)) == ((1,), {}), case
        else:
            with pytest.raises(ParameterError) as error:
                actor_handler_arguments(view.choose, dict(payload))
            assert "forged" not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("actor_mode", [False, True])
async def test_real_websocket(actor_mode):
    from channels.testing import WebsocketCommunicator
    from django.test import override_settings
    from djust.tests.test_exposure_runtime import make_request
    from djust.websocket import LiveViewConsumer

    request = await sync_to_async(make_request)()
    view = "ActorDispatchView" if actor_mode else "DispatchView"
    cases = {**METADATA, **FORGED}
    if not actor_mode:
        cases.pop("activity")  # the non-actor runtime's deferral gate
    with override_settings(LIVEVIEW_ALLOWED_MODULES=["djust.tests"], DJUST_TENANTS=None):
        socket = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        socket.scope.update(session=request.session, user=request.user, tenant=None)
        assert (await socket.connect())[0]
        await socket.receive_json_from(timeout=3)
        try:
            await socket.send_json_to(
                {"type": "mount", "view": __name__ + "." + view, "url": request.path}
            )
            assert (await socket.receive_json_from(timeout=3))["type"] == "mount"
            for case, payload in cases.items():
                CALLS.clear()
                await socket.send_json_to(
                    {"type": "event", "event": "choose", "params": payload, "ref": 1}
                )
                frame = await socket.receive_json_from(timeout=3)
                assert CALLS == _expect(case), (case, frame)
                assert (frame["type"] == "error") is (case in FORGED), (case, frame)
                assert "forged" not in json.dumps(frame)
        finally:
            await socket.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_real_sse():
    from django.test import override_settings
    from djust.sse import DjustSSEMessageView, DjustSSEStreamView, _sse_sessions
    from djust.tests.test_exposure_sse_navigation import drain, request_for

    sid = str(uuid.uuid4())
    with override_settings(LIVEVIEW_ALLOWED_MODULES=["djust.tests"], DJUST_TENANTS=None):
        request = await sync_to_async(request_for)(
            "GET", f"/djust/sse/{sid}/", {"view": __name__ + ".DispatchView", "_djust_url": "/t/"}
        )
        assert (await DjustSSEStreamView().get(request, session_id=sid)).status_code == 200
        session = _sse_sessions[sid]
        try:
            drain(session)
            for case, payload in {"cache": METADATA["cache"], **FORGED}.items():
                CALLS.clear()
                post = await sync_to_async(request_for)(
                    "POST",
                    f"/djust/sse/{sid}/message/",
                    {"type": "event", "event": "choose", "params": payload, "ref": 1},
                    request.session.session_key,
                )
                assert (await DjustSSEMessageView().post(post, session_id=sid)).status_code == 200
                frames = drain(session)
                assert CALLS == _expect(case), (case, frames)
                assert any(f["type"] == "error" for f in frames) is (case in FORGED), frames
                assert "forged" not in json.dumps(frames)
        finally:
            _sse_sessions.pop(sid, None)


# --- legacy handlers keep their keyword mapping ------------------------------


@pytest.mark.django_db
def test_legacy_handler_still_receives_framework_keys_as_before():
    from djust.testing import LiveViewTestClient

    client = LiveViewTestClient(DispatchView).mount()
    client.send_event("legacy", value="1", _cacheRequestId="c1", component="c")
    assert CALLS == [("legacy", "1", {"_cacheRequestId": "c1", "component": "c"})]


@pytest.mark.django_db
def test_legacy_http_flat_body_still_drops_underscore_keys():
    from django.test import RequestFactory
    from djust.tests.test_exposure_runtime import make_request

    initial = make_request()
    DispatchView().get(initial)
    request = RequestFactory().post(
        initial.path,
        data=json.dumps({"value": "1", "_forged": "x", "_cacheRequestId": "c1"}),
        content_type="application/json",
        HTTP_X_DJUST_EVENT="legacy",
    )
    request.user, request.session, request.tenant = initial.user, initial.session, None
    assert DispatchView().post(request).status_code == 200
    assert CALLS == [("legacy", "1", {"_cacheRequestId": "c1"})]


# --- ADR-034 output callbacks: the source is trusted context ----------------


def _menu_page():
    from djust.tests.test_interactive_bindings import mounted

    return mounted()


@pytest.mark.parametrize("forged", [{"component": "forged"}, {"_args": ["forged"]}])
def test_component_action_rejects_a_forged_source(forged):
    from djust.validation import validate_handler_params

    view = _menu_page()
    view.menu.open = True
    validation = validate_handler_params(view.menu.select, {"value": "edit", **forged}, "select")
    assert not validation["valid"]
    assert "forged" not in json.dumps(validation, default=str)
    assert view.calls == 0 and view.menu.open


def test_output_callback_binds_payload_strictly_and_injects_the_actual_source():
    view = _menu_page()
    view.menu.open = True
    async_to_sync(view.menu.select)(value="edit")
    assert view.result == "project:edit" and view.calls == 1
    contract = get_strict_handler_contract(view.project_selected, frozenset({"component"}))
    assert [item["name"] for item in contract.metadata()] == ["value"]


@pytest.mark.django_db
def test_real_http_component_action_rejects_forged_source():
    from django.test import RequestFactory
    from djust.tests.test_exposure_runtime import make_request
    from djust.tests.test_interactive_bindings import MenuPage

    request = make_request()
    view = MenuPage()
    view.get(request)
    identity = view.menu.component_id
    post = RequestFactory().post(
        request.path,
        data=json.dumps(
            {
                "event": "select",
                "params": {"component_id": identity, "value": "edit", "component": "forged"},
            }
        ),
        content_type="application/json",
    )
    post.user, post.session, post.tenant = request.user, request.session, None
    restored = MenuPage()
    response = restored.post(post)
    assert response.status_code == 400
    assert restored.calls == 0 and restored.result == "initial"
    assert b"forged" not in response.content


def test_output_payload_outside_the_strict_contract_never_reaches_the_callback():
    from djust.components._interactive import DropdownMenu

    class Page(LiveView):
        menu = DropdownMenu(label="Menu", items=[{"label": "Edit", "value": "edit"}])

        @menu.on.selected
        def loose(self, component: DropdownMenu, value: object) -> None:
            CALLS.append("loose")

    view = Page()
    view.menu.open = True
    with pytest.raises(ContractError, match="Parameter 'value'"):
        async_to_sync(view.menu.select)(value="edit")
    assert CALLS == []
