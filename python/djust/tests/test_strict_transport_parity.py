"""ADR-036 P2 (c): one conversion/binding matrix, identical on every transport.

Each case is (handler, payload, expected). ``expected`` is the repr the handler
records, or REJECT: the event must be refused before application code runs.
The same matrix is sent through the shared runtime (WebSocket/SSE dispatch),
real WebSocket sessions in normal and actor mode, a real SSE session, both
HTTP-fallback body shapes, the exposed event API and the test client.
"""

import json
import uuid
from datetime import date
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

import pytest
from asgiref.sync import sync_to_async

from djust import LiveView
from djust.decorators import event_handler

CALLS: list = []
REJECT = object()
UID = "12345678-1234-5678-1234-567812345678"


def _record(name, value):
    CALLS.append((name, type(value).__name__ + ":" + repr(value)))


def strict(**options):
    return event_handler(parameter_policy="strict", expose_api=True, **options)


class ParityView(LiveView):
    template = "<div dj-root><p>{{ n }}</p></div>"
    api_name = "strict.parity"

    def mount(self, request, **kwargs):
        self.n = 0

    def _done(self, name, value):
        _record(name, value)
        self.n += 1

    @strict()
    def t_str(self, value: str):
        self._done("t_str", value)

    @strict()
    def t_int(self, value: int):
        self._done("t_int", value)

    @strict()
    def t_float(self, value: float):
        self._done("t_float", value)

    @strict()
    def t_bool(self, value: bool):
        self._done("t_bool", value)

    @strict()
    def t_decimal(self, value: Decimal):
        self._done("t_decimal", value)

    @strict()
    def t_uuid(self, value: UUID):
        self._done("t_uuid", value)

    @strict()
    def t_date(self, value: date):
        self._done("t_date", value)

    @strict()
    def t_optional(self, value: Optional[int]):
        self._done("t_optional", value)

    @strict()
    def t_list(self, value: list[int]):
        self._done("t_list", value)

    @strict()
    def t_any(self, value: Any):
        self._done("t_any", value)

    @strict(coerce_types=False)
    def t_raw_int(self, value: int):
        self._done("t_raw_int", value)

    @strict()
    def t_kw(self, a: int, /, *, b: date):
        self._done("t_kw", (a, b))

    @strict()
    def t_form(self, **fields: str):
        self._done("t_form", dict(fields))


class ActorParityView(ParityView):
    use_actors = True
    api_name = "strict.parity.actor"


MATRIX = [
    ("t_str", {"value": "  x "}, "str:'  x '"),
    ("t_str", {"value": 5}, REJECT),
    ("t_int", {"value": "42"}, "int:42"),
    ("t_int", {"value": " 7 "}, "int:7"),
    ("t_int", {"value": 42}, "int:42"),
    ("t_int", {"value": "4.2"}, REJECT),
    ("t_int", {"value": "1_000"}, REJECT),
    ("t_int", {"value": ""}, REJECT),
    ("t_int", {"value": True}, REJECT),
    ("t_float", {"value": "1.5"}, "float:1.5"),
    ("t_float", {"value": 2}, "float:2.0"),
    ("t_float", {"value": "nan"}, REJECT),
    ("t_float", {"value": True}, REJECT),
    ("t_bool", {"value": "yes"}, "bool:True"),
    ("t_bool", {"value": "OFF"}, "bool:False"),
    ("t_bool", {"value": True}, "bool:True"),
    ("t_bool", {"value": 1}, REJECT),
    ("t_bool", {"value": "maybe"}, REJECT),
    ("t_decimal", {"value": "1.25"}, "Decimal:Decimal('1.25')"),
    ("t_decimal", {"value": 3}, "Decimal:Decimal('3')"),
    ("t_decimal", {"value": 1.5}, REJECT),
    ("t_decimal", {"value": "NaN"}, REJECT),
    ("t_uuid", {"value": UID}, "UUID:UUID('%s')" % UID),
    ("t_uuid", {"value": "nope"}, REJECT),
    ("t_date", {"value": "2026-09-24"}, "date:datetime.date(2026, 9, 24)"),
    ("t_date", {"value": "2026-02-30"}, REJECT),
    ("t_date", {"value": "24/09/2026"}, REJECT),
    ("t_optional", {"value": None}, "NoneType:None"),
    ("t_optional", {"value": "3"}, "int:3"),
    ("t_optional", {"value": ""}, REJECT),
    ("t_optional", {}, REJECT),
    ("t_list", {"value": ["1", 2]}, "list:[1, 2]"),
    ("t_list", {"value": "1,2"}, REJECT),
    ("t_any", {"value": {"a": 1}}, "dict:{'a': 1}"),
    ("t_raw_int", {"value": 42}, "int:42"),
    ("t_raw_int", {"value": "42"}, REJECT),
    ("t_kw", {"_args": ["1"], "b": "2026-09-24"}, "tuple:(1, datetime.date(2026, 9, 24))"),
    ("t_kw", {"a": 1, "b": "2026-09-24"}, REJECT),
    ("t_kw", {"_args": ["1"]}, REJECT),
    ("t_form", {"x": "1", "y": "2"}, "dict:{'x': '1', 'y': '2'}"),
    ("t_form", {"x": 1}, REJECT),
    ("t_int", {"value": "1", "extra": "x"}, REJECT),
    ("t_int", {"value": "1", "component": "forged"}, REJECT),
]


@pytest.fixture(autouse=True)
def _clear_calls():
    CALLS.clear()
    yield
    CALLS.clear()


def _check(outcomes):
    """``outcomes``: per MATRIX row, the recorded call (or None)."""
    mismatches = []
    for (handler, payload, expected), got in zip(MATRIX, outcomes):
        want = None if expected is REJECT else (handler, expected)
        if got != want:
            mismatches.append((handler, payload, want, got))
    assert not mismatches, mismatches


def _take():
    got = CALLS[0] if CALLS else None
    assert len(CALLS) <= 1, CALLS
    CALLS.clear()
    return got


@pytest.mark.asyncio
@pytest.mark.django_db
async def test_shared_runtime():
    from djust.tests.test_runtime_child_routing_1892 import _make_runtime_with_view

    outcomes = []
    for handler, payload, _ in MATRIX:
        view = ParityView()
        view.mount(None)
        runtime, _transport = _make_runtime_with_view(view)
        await runtime.dispatch_event({"type": "event", "event": handler, "params": dict(payload)})
        outcomes.append(_take())
    _check(outcomes)


@pytest.mark.django_db
@pytest.mark.parametrize("flat", [False, True])
def test_http_fallback(flat):
    from django.test import RequestFactory
    from djust.tests.test_exposure_runtime import make_request

    initial = make_request()
    ParityView().get(initial)
    outcomes = []
    for handler, payload, expected in MATRIX:
        request = RequestFactory().post(
            initial.path,
            data=json.dumps(payload if flat else {"event": handler, "params": payload}),
            content_type="application/json",
            HTTP_X_DJUST_EVENT=handler,
        )
        request.user, request.session, request.tenant = initial.user, initial.session, None
        response = ParityView().post(request)
        assert response.status_code == (400 if expected is REJECT else 200), (handler, payload)
        outcomes.append(_take())
    _check(outcomes)


@pytest.mark.django_db
def test_exposed_api():
    from django.contrib.auth import get_user_model
    from django.test import RequestFactory
    from djust.api.dispatch import dispatch_api, reset_rate_buckets
    from djust.api.registry import register_api_view, reset_registry
    from djust.tests.test_exposure_runtime import make_request

    user = get_user_model().objects.create_user(username="parity-%s" % uuid.uuid4().hex[:8])
    session = make_request().session
    reset_registry()
    register_api_view("strict.parity", ParityView)
    outcomes = []
    try:
        for handler, payload, _ in MATRIX:
            reset_rate_buckets()
            request = RequestFactory().post(
                "/djust/api/strict.parity/%s/" % handler,
                data=json.dumps(payload),
                content_type="application/json",
            )
            request.user, request.session, request.tenant = user, session, None
            request._dont_enforce_csrf_checks = True
            dispatch_api(request, "strict.parity", handler)
            outcomes.append(_take())
    finally:
        reset_registry()
        reset_rate_buckets()
    _check(outcomes)


@pytest.mark.django_db
def test_test_client():
    from djust.testing import LiveViewTestClient

    outcomes = []
    for handler, payload, _ in MATRIX:
        client = LiveViewTestClient(ParityView).mount()
        client.send_event(handler, **payload)
        outcomes.append(_take())
    _check(outcomes)


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("view", ["ParityView", "ActorParityView"])
async def test_real_websocket(view):
    from channels.testing import WebsocketCommunicator
    from django.test import override_settings
    from djust.tests.test_exposure_runtime import make_request
    from djust.websocket import LiveViewConsumer

    from djust.config import config

    request = await sync_to_async(make_request)()
    outcomes = []
    # One socket carries the whole matrix; lift the per-connection burst cap.
    limits = config.get("rate_limit")
    config.set("rate_limit", {**limits, "burst": 1000})
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
            for ref, (handler, payload, expected) in enumerate(MATRIX, 1):
                await socket.send_json_to(
                    {"type": "event", "event": handler, "params": payload, "ref": ref}
                )
                frame = await socket.receive_json_from(timeout=3)
                assert (frame["type"] == "error") is (expected is REJECT), (handler, frame)
                outcomes.append(_take())
        finally:
            await socket.disconnect()
            config.set("rate_limit", limits)
    _check(outcomes)


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_real_sse():
    from django.test import override_settings
    from djust.sse import DjustSSEMessageView, DjustSSEStreamView, _sse_sessions
    from djust.tests.test_exposure_sse_navigation import drain, request_for

    sid = str(uuid.uuid4())
    outcomes = []
    with override_settings(LIVEVIEW_ALLOWED_MODULES=["djust.tests"], DJUST_TENANTS=None):
        request = await sync_to_async(request_for)(
            "GET", f"/djust/sse/{sid}/", {"view": __name__ + ".ParityView", "_djust_url": "/p/"}
        )
        assert (await DjustSSEStreamView().get(request, session_id=sid)).status_code == 200
        session = _sse_sessions[sid]
        try:
            drain(session)
            for ref, (handler, payload, expected) in enumerate(MATRIX, 1):
                post = await sync_to_async(request_for)(
                    "POST",
                    f"/djust/sse/{sid}/message/",
                    {"type": "event", "event": handler, "params": payload, "ref": ref},
                    request.session.session_key,
                )
                assert (await DjustSSEMessageView().post(post, session_id=sid)).status_code == 200
                frames = drain(session)
                assert any(f["type"] == "error" for f in frames) is (expected is REJECT), frames
                outcomes.append(_take())
        finally:
            _sse_sessions.pop(sid, None)
    _check(outcomes)


@pytest.mark.asyncio
async def test_actor_bridge_keeps_open_payload_key_order():
    """The actor path once carried event params in a Rust HashMap, reordering a
    ``**`` payload. 64 keys in a shuffled order, repeated: a hash map passes
    this by chance with negligible probability."""
    import random

    from djust._rust import create_session_actor

    received = []

    class View:
        def get_context_data(self):
            return {}

        @event_handler(parameter_policy="strict")
        def collect(self, **fields: str):
            received.append(list(fields))

    keys = ["k%02d" % i for i in range(64)]
    view = View()
    actor = await create_session_actor("strict-order-actor")
    try:
        await actor.mount("tests.StrictOrderView", {}, view)
        for seed in range(10):
            random.Random(seed).shuffle(keys)
            await actor.event("collect", {key: "v" for key in keys})
            assert received[-1] == keys
    finally:
        await actor.shutdown()
