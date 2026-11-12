"""#3945: a deferred explicit save in a turn that also starts background work.

The exposure-matrix browser flow clicks "spawn", whose handler calls
``start_async``. When that turn's state save misses its storage deadline, the
server logs "success frame withheld, a catch-up turn saves again", and the page
was seen never to show the background result. These tests force the save past
its deadline and check what reaches the client.

This module deliberately does NOT start with ``test_exposure_``: the conftest
raises the save bound to 30 s for those modules (#3130).
"""

import asyncio
import threading

import pytest
from asgiref.sync import sync_to_async
from django.contrib.sessions.backends.db import SessionStore
from django.test import override_settings

from djust import LiveView, event_handler
from djust.decorators import state
from djust.runtime import ViewRuntime
from djust.tests.test_exposure_runtime import make_request
from djust.tests.test_runtime_state_save_tt_1894 import MockTransport

pytestmark = [pytest.mark.django_db(transaction=True)]


class SpawnView(LiveView):
    exposure_policy = "explicit"
    template = '<div dj-root><span id="count">{{ count }}</span></div>'
    count = state(0, persist="server")

    def get_context_data(self, **kwargs):
        return super().get_context_data(count=self.count, **kwargs)

    @event_handler()
    def increment(self):
        self.count += 1

    @event_handler()
    def spawn(self):
        self.start_async(self._work)

    def _work(self):
        return 10

    def handle_async_result(self, name, result=None, error=None):
        if error is None:
            self.count += result


@pytest.fixture
def staged(monkeypatch):
    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)


async def _mount():
    request = await sync_to_async(make_request)()
    transport = MockTransport()
    transport.build_request = lambda: request

    async def fresh_event_request(view):
        return await sync_to_async(make_request)(request.session.session_key)

    transport.explicit_event_request = fresh_event_request
    runtime = ViewRuntime(transport)
    with override_settings(LIVEVIEW_ALLOWED_MODULES=["djust"]):
        await runtime.dispatch_mount(
            {"type": "mount", "view": __name__ + ".SpawnView", "url": request.path}
        )
    assert not transport.errors, transport.errors
    return runtime, transport


async def _until(predicate, what):
    for _ in range(1000):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("timed out waiting for " + what)


def _shown(frame):
    """The count a rendered frame puts on the page, or None."""
    import re

    if frame.get("html"):
        found = re.search(r'id="count">(\d+)<', frame["html"])
        return int(found.group(1)) if found else None
    for patch in frame.get("patches") or ():
        if patch.get("type") == "SetText":
            return int(patch["text"])
    return None


def _rendered(transport):
    return [
        (frame.get("source"), _shown(frame))
        for frame in transport.sent
        if frame.get("type") in {"html_update", "patch"}
    ]


async def test_the_background_result_reaches_the_client_after_a_deferred_save(staged):
    """The exposure-matrix "spawn" flow, with the spawn turn's save past its deadline.

    Before #3945 the deferred turn returned before dispatching the work its
    handler queued, and the catch-up turn did not dispatch it either: the work
    sat on the view, the page showed 2 for good, and only another event would
    have started it.
    """
    runtime, transport = await _mount()
    view = runtime.view_instance
    for _ in range(2):
        await runtime.dispatch_event({"type": "event", "event": "increment", "params": {}})
    assert view.count == 2
    transport.sent.clear()

    release = threading.Event()
    original = SessionStore.save
    calls = []

    def slow_first_save(self, *args, **kwargs):
        calls.append(True)
        if len(calls) == 1:
            release.wait(timeout=30)
        return original(self, *args, **kwargs)

    with override_settings(DJUST_EXPLICIT_STATE_SAVE_TIMEOUT=0.05, DEBUG=False):
        SessionStore.save = slow_first_save
        try:
            dispatch = asyncio.ensure_future(
                runtime.dispatch_event({"type": "event", "event": "spawn", "params": {}})
            )
            try:
                await _until(lambda: transport.errors or dispatch.done(), "the deferral")
                [error] = transport.errors
                assert error.get("transient") is True, error
                assert _rendered(transport) == [], "no success frame for the deferred turn"
            finally:
                release.set()
            await asyncio.wait_for(dispatch, timeout=10)
            await _until(
                lambda: ("async", 12) in _rendered(transport), "the background result frame"
            )
        finally:
            SessionStore.save = original

    # The catch-up frame (the committed value) comes first, then the result.
    assert _rendered(transport) == [("async", 2), ("async", 12)], transport.sent
    assert view.count == 12
    reloads = [e for e in transport.errors if not e.get("transient")]
    assert not reloads, reloads


async def test_control_the_background_result_arrives_without_a_deferral(staged):
    runtime, transport = await _mount()
    await runtime.dispatch_event({"type": "event", "event": "spawn", "params": {}})
    await _until(lambda: ("async", 10) in _rendered(transport), "the background result frame")


async def test_over_a_real_websocket(staged):
    """The same flow through ``LiveViewConsumer`` and a WebSocket communicator."""
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    from ._ws_frames import receive_until

    request = await sync_to_async(make_request)()
    socket = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    socket.scope.update(session=request.session, user=request.user, tenant=None)
    release = threading.Event()
    original = SessionStore.save
    calls = []

    def slow_first_save(self, *args, **kwargs):
        calls.append(True)
        if len(calls) == 1:
            release.wait(timeout=30)
        return original(self, *args, **kwargs)

    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], DEBUG=False, DJUST_TENANTS=None):
        assert (await socket.connect())[0]
        try:
            await socket.receive_json_from()
            await socket.send_json_to(
                {"type": "mount", "view": __name__ + ".SpawnView", "url": request.path}
            )
            assert (await socket.receive_json_from())["type"] == "mount"
            await socket.send_json_to({"type": "event", "event": "increment", "params": {}})
            await receive_until(socket, lambda fs: any(_shown(f) == 1 for f in fs), what="count=1")

            with override_settings(DJUST_EXPLICIT_STATE_SAVE_TIMEOUT=0.05):
                SessionStore.save = slow_first_save
                try:
                    await socket.send_json_to({"type": "event", "event": "spawn", "params": {}})
                    try:
                        frames = await receive_until(
                            socket,
                            lambda fs: any(f.get("type") == "error" for f in fs),
                            what="the deferral",
                        )
                    finally:
                        release.set()
                    [error] = [f for f in frames if f.get("type") == "error"]
                    assert error.get("transient") is True, error
                    frames += await receive_until(
                        socket, lambda fs: any(_shown(f) == 11 for f in fs), what="count=11"
                    )
                finally:
                    SessionStore.save = original
        finally:
            await socket.disconnect()

    shown = [_shown(f) for f in frames if f.get("type") in {"html_update", "patch"}]
    assert shown == [1, 11], frames
