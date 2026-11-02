"""Metadata decorators keep an ``async def`` handler a coroutine function.

``_make_metadata_decorator`` (``@debounce``, ``@throttle``, ``@cache``,
``@client_state``, ``@rate_limit``, ``@permission_required``) and ``@optimistic``
wrapped the handler in a plain ``def``. On an ``async def`` handler djust then
saw a sync handler, ran it as sync code, got an un-awaited coroutine back, and
the body never ran: no error, no log. Found by a port of a production app whose
``@permission_required`` async handlers silently did nothing.
"""

from __future__ import annotations

import inspect
import json
import sys

import pytest

from djust import LiveView
from djust.decorators import (
    cache,
    client_state,
    debounce,
    event_handler,
    optimistic,
    permission_required,
    rate_limit,
    throttle,
)

DECORATORS = {
    "debounce": debounce(wait=0.1),
    "throttle": throttle(interval=0.1),
    "cache": cache(ttl=10),
    "client_state": client_state(keys=["x"]),
    "rate_limit": rate_limit(rate=100, burst=100),
    "permission_required": permission_required("auth.view_user"),
    "optimistic": optimistic,
}


@pytest.mark.parametrize("name", sorted(DECORATORS))
def test_an_async_handler_stays_a_coroutine_function(name):
    @DECORATORS[name]
    @event_handler
    async def handler(self, **kwargs):
        return "ran"

    assert inspect.iscoroutinefunction(handler), name


@pytest.mark.parametrize("name", sorted(DECORATORS))
def test_a_sync_handler_stays_sync(name):
    @DECORATORS[name]
    @event_handler
    def handler(self, **kwargs):
        return "ran"

    assert not inspect.iscoroutinefunction(handler), name
    assert handler(None) == "ran"


@pytest.mark.parametrize("name", sorted(DECORATORS))
async def test_awaiting_the_wrapper_runs_the_body(name):
    @DECORATORS[name]
    @event_handler
    async def handler(self, **kwargs):
        return "ran"

    assert await handler(None) == "ran"


_RAN: list[str] = []


class _AsyncHandlersView(LiveView):
    template = '<div dj-view="djust.tests.test_metadata_decorators_async._AsyncHandlersView" dj-id="0">{{ n }}</div>'

    def mount(self, request, **kwargs):
        self.n = 0

    @permission_required("auth.view_user")
    @event_handler
    async def guarded(self, **kwargs):
        _RAN.append("guarded")
        self.n += 1

    @debounce(wait=0.1)
    @event_handler
    async def debounced(self, **kwargs):
        _RAN.append("debounced")
        self.n += 1


setattr(sys.modules[__name__], "_AsyncHandlersView", _AsyncHandlersView)


class _Superuser:
    is_authenticated = True
    is_active = True
    is_anonymous = False
    is_superuser = True

    def has_perm(self, perm, obj=None):
        return True

    def has_perms(self, perms, obj=None):
        return True


async def _event(communicator, name):
    await communicator.send_json_to({"type": "event", "event": name, "params": {}})
    for _ in range(6):
        output = await communicator.receive_output()
        if output["type"] == "websocket.send":
            frame = json.loads(output.get("text") or "{}")
            if frame.get("type") in ("patch", "noop", "html_update", "error"):
                return frame
    raise AssertionError("no reply to the event")


@pytest.mark.django_db
async def test_decorated_async_handlers_run_over_the_socket(settings):
    pytest.importorskip("channels")
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    settings.LIVEVIEW_ALLOWED_MODULES = [__name__]
    _RAN.clear()
    inner = LiveViewConsumer.as_asgi()

    async def app(scope, receive, send):
        scope = dict(scope)
        scope["user"] = _Superuser()
        scope.setdefault("session", {})
        return await inner(scope, receive, send)

    communicator = WebsocketCommunicator(app, "/ws/")
    connected, _ = await communicator.connect()
    assert connected
    await communicator.receive_json_from()
    await communicator.send_json_to({"type": "mount", "view": f"{__name__}._AsyncHandlersView"})
    assert (await communicator.receive_json_from()).get("type") == "mount"
    await _event(communicator, "guarded")
    await _event(communicator, "debounced")
    assert _RAN == ["guarded", "debounced"]
    await communicator.disconnect()
