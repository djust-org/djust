"""Runtime boundary diagnostics through actual WS and SSE HTTP transports."""

import json
from collections import deque

import pytest
from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator
from django.test import override_settings
from django.urls import path

from djust import LiveView, event_handler
from djust.observability import tracebacks
from djust.runtime import WSConsumerTransport
from djust.sse import DjustSSEEventView, DjustSSEMessageView, _sse_sessions
from djust.tests.test_exposure_event_diagnostics import EventFailureView
from djust.tests.test_exposure_runtime import make_request
from djust.tests.test_exposure_sse_navigation import FirstPage
from djust.websocket import LiveViewConsumer

urlpatterns = [
    path("first/<int:pk>/", FirstPage.as_view()),
    path("djust/sse/<str:session_id>/message/", DjustSSEMessageView.as_view()),
    path("djust/sse/<str:session_id>/event/", DjustSSEEventView.as_view()),
]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("debug", [False, True])
@pytest.mark.parametrize("transient", [False, True], ids=["steady", "transient"])
@pytest.mark.parametrize(
    "initial,final",
    [
        ("explicit", "explicit"),
        ("explicit", "legacy"),
        ("legacy", "explicit"),
        ("legacy", "legacy"),
    ],
)
async def test_outer_ws_event_failure(monkeypatch, caplog, debug, initial, final, transient):
    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)
    monkeypatch.setattr(EventFailureView, "exposure_policy", initial)
    monkeypatch.setattr(tracebacks, "_buffer", deque(maxlen=50))
    called = []

    if transient:
        original = EventFailureView.explode

        @event_handler()
        def switch(self, stage: str):
            original(self, stage)
            self.exposure_policy = "explicit"

        monkeypatch.setattr(EventFailureView, "explode", switch)

    def fail(self, view, event_name, duration_ms):
        called.append(event_name)
        view.exposure_policy = final
        raise ValueError("OUTER_TRANSPORT_SENTINEL")

    monkeypatch.setattr(WSConsumerTransport, "on_handler_timing", fail)
    with override_settings(
        LIVEVIEW_ALLOWED_MODULES=["djust.tests"], DEBUG=debug, DJUST_TENANTS=None, DJUST_CONFIG={}
    ):
        request = await sync_to_async(make_request)()
        socket = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        socket.scope.update(session=request.session, user=request.user, tenant=None)
        assert (await socket.connect())[0]
        await socket.receive_json_from(timeout=3)
        try:
            await socket.send_json_to(
                {
                    "type": "mount",
                    "view": EventFailureView.__module__ + ".EventFailureView",
                    "url": request.path,
                }
            )
            mounted = await socket.receive_json_from(timeout=3)
            assert mounted["type"] == "mount", mounted
            caplog.clear()
            await socket.send_json_to(
                {"type": "event", "event": "explode", "params": {"stage": "render"}, "ref": 19}
            )
            failure = await socket.receive_json_from(timeout=3)
            assert called == ["explode"]
            assert failure["type"] == "error", failure
            legacy = initial == final == "legacy" and not transient
            observed = (
                "OUTER_TRANSPORT_SENTINEL" in caplog.text,
                "OUTER_TRANSPORT_SENTINEL" in json.dumps(failure),
                "OUTER_TRANSPORT_SENTINEL" in json.dumps(tracebacks.get_recent_tracebacks(50)),
            )
            assert observed == (legacy, legacy and debug, legacy)
            if not legacy:
                assert failure["source"] == "event"
                assert failure["ref"] == 19
            await socket.send_json_to({"type": "ping"})
            assert (await socket.receive_json_from(timeout=3))["type"] == "pong"
        finally:
            await socket.disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("debug", [False, True])
@pytest.mark.parametrize("transient", [False, True], ids=["steady", "transient"])
@pytest.mark.parametrize(
    "initial,final",
    [
        ("explicit", "explicit"),
        ("explicit", "legacy"),
        ("legacy", "explicit"),
        ("legacy", "legacy"),
    ],
)
@pytest.mark.parametrize("endpoint", ["message", "event"])
async def test_outer_sse_http_failure(
    monkeypatch, caplog, debug, initial, final, transient, endpoint
):
    from django.conf import settings
    from django.test import AsyncClient

    from djust.runtime import SSESessionTransport
    from djust.tests.test_exposure_sse_navigation import drain, start

    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)
    monkeypatch.setattr(FirstPage, "exposure_policy", initial)
    called = []

    if transient:
        original = FirstPage.change

        @event_handler()
        def switch(self):
            original(self)
            self.exposure_policy = "explicit"

        monkeypatch.setattr(FirstPage, "change", switch)

    def fail(self, view, event_name, duration_ms):
        called.append(event_name)
        view.exposure_policy = final
        raise ValueError("OUTER_SSE_SENTINEL")

    monkeypatch.setattr(SSESessionTransport, "on_handler_timing", fail)
    with override_settings(
        ROOT_URLCONF=__name__,
        LIVEVIEW_ALLOWED_MODULES=["djust"],
        DEBUG=debug,
        ALLOWED_HOSTS=["testserver"],
        DJUST_TENANTS=None,
        DJUST_CONFIG={},
    ):
        session, key = await start()
        try:
            drain(session)
            caplog.clear()
            client = AsyncClient(raise_request_exception=False)
            client.cookies[settings.SESSION_COOKIE_NAME] = key
            response = await client.post(
                f"/djust/sse/{session.session_id}/{endpoint}/",
                data=json.dumps({"type": "event", "event": "change", "params": {}, "ref": 19}),
                content_type="application/json",
            )
            assert called == ["change"], (response.content[:300], drain(session))
            legacy = initial == final == "legacy" and not transient
            frames = drain(session)
            observed = (
                "OUTER_SSE_SENTINEL" in caplog.text,
                "OUTER_SSE_SENTINEL" in response.content.decode(),
                "OUTER_SSE_SENTINEL" in json.dumps(frames),
            )
            assert observed == (legacy, legacy and debug, False)
            if legacy:
                assert response.status_code == 500
            else:
                assert response.status_code == 200
                assert len(frames) == 1
                assert frames[0]["type"] == "error"
                assert frames[0]["source"] == "event"
                assert frames[0]["ref"] == 19
                assert session.active
        finally:
            _sse_sessions.pop(session.session_id, None)


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["explicit", None, "unknown"])
@pytest.mark.parametrize("close_fails", [False, True])
async def test_protected_delivery_failure_closes_without_exposing_errors(
    policy, close_fails, caplog
):
    from types import SimpleNamespace

    from djust.runtime import ViewRuntime
    from djust.tests.test_exposure_event_diagnostics import UnprintableFailure
    from djust.tests.test_runtime_state_save_tt_1894 import MockTransport

    transport = MockTransport()
    runtime = ViewRuntime(transport)
    runtime.view_instance = SimpleNamespace(exposure_policy=policy)
    frames, closed = [], []

    async def dispatch(data):
        runtime.view_instance.exposure_policy = "legacy"
        raise UnprintableFailure()

    async def send(frame):
        frames.append(frame)
        raise ValueError("DELIVERY_SENTINEL")

    async def close(code):
        closed.append(code)
        if close_fails:
            raise ValueError("CLOSE_SENTINEL")

    runtime.dispatch_event = dispatch
    transport.send = send
    transport.close = close
    await runtime.dispatch_message({"type": "event", "ref": 21})
    assert len(frames) == 1
    assert frames[0]["ref"] == 21
    assert closed == [1011]
    assert "SENTINEL" not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


@pytest.mark.asyncio
@pytest.mark.parametrize("message", [None, [], 17])
async def test_malformed_direct_dispatch_still_has_a_protected_boundary(message, caplog):
    from types import SimpleNamespace

    from djust.runtime import ViewRuntime
    from djust.tests.test_runtime_state_save_tt_1894 import MockTransport

    transport = MockTransport()
    runtime = ViewRuntime(transport)
    runtime.view_instance = SimpleNamespace(exposure_policy="explicit")
    await runtime.dispatch_message(message)
    assert transport.sent == [{"type": "error", "error": "An error occurred. Please try again."}]
    assert all(record.exc_info is None for record in caplog.records)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ref", [float("nan"), float("inf"), float("-inf"), None, "21", {"private": "REF_SENTINEL"}]
)
async def test_invalid_reference_cannot_break_redaction(ref, caplog):
    from types import SimpleNamespace

    from djust.runtime import ViewRuntime
    from djust.tests.test_runtime_state_save_tt_1894 import MockTransport

    transport = MockTransport()
    runtime = ViewRuntime(transport)
    runtime.view_instance = SimpleNamespace(exposure_policy="explicit")

    async def fail(data):
        raise ValueError("EVENT_SENTINEL")

    runtime.dispatch_event = fail
    await runtime.dispatch_message({"type": "event", "ref": ref})
    assert len(transport.sent) == 1
    assert "ref" not in transport.sent[0]
    assert transport.sent[0]["source"] == "event"
    assert "SENTINEL" not in caplog.text + json.dumps(transport.sent)


@pytest.mark.asyncio
async def test_cancellation_is_not_swallowed_and_scope_is_reset():
    import asyncio
    from types import SimpleNamespace

    from djust._exposure_diagnostics import diagnostics_allowed
    from djust.runtime import ViewRuntime
    from djust.tests.test_runtime_state_save_tt_1894 import MockTransport

    transport = MockTransport()
    runtime = ViewRuntime(transport)
    runtime.view_instance = SimpleNamespace(exposure_policy="explicit")

    async def cancel(data):
        raise asyncio.CancelledError

    runtime.dispatch_event = cancel
    with pytest.raises(asyncio.CancelledError):
        await runtime.dispatch_message({"type": "event", "ref": 1})
    assert transport.sent == []
    assert diagnostics_allowed()


def test_protected_failure_restriction_survives_nested_unwinding_only_until_turn_exit():
    from types import SimpleNamespace

    from djust._exposure_diagnostics import (
        diagnostic_scope,
        diagnostics_allowed,
        restrict_diagnostics,
    )

    assert diagnostics_allowed()
    with diagnostic_scope():
        with pytest.raises(ValueError, match="failed"), diagnostic_scope():
            restrict_diagnostics(SimpleNamespace(exposure_policy="explicit"))
            raise ValueError("failed")
        assert not diagnostics_allowed()
    assert diagnostics_allowed()
