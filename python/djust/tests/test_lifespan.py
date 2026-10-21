"""``djust.lifespan``: answer the ASGI ``lifespan`` protocol for a djust app.

Django's ASGI handler refuses a ``lifespan`` scope ("Django can only handle
ASGI/HTTP connections") and channels' ``ProtocolTypeRouter`` raises for a scope
type it has no entry for, so an application had no supported place to start
services bound to the server's event loop (an async database engine, a LISTEN
connection, a background pump) and stop them on shutdown. Each app wrote its
own lifespan loop around the router.

``with_lifespan(app, startup=..., shutdown=...)`` answers ``lifespan`` itself,
runs the hooks on the server's loop, and passes every other scope to ``app``.
Hooks can also be registered with ``@on_startup`` / ``@on_shutdown`` (from an
``AppConfig.ready()``, for example).
"""

from __future__ import annotations

import asyncio
import threading

import pytest

from djust import lifespan
from djust.lifespan import on_shutdown, on_startup, with_lifespan

pytestmark = pytest.mark.asyncio


class _Server:
    """Drives one lifespan conversation the way uvicorn does."""

    def __init__(self, app):
        self.app = app
        self.inbox: asyncio.Queue = asyncio.Queue()
        self.sent: list = []

    async def receive(self):
        return await self.inbox.get()

    async def send(self, message):
        self.sent.append(message)

    def types(self):
        return [m["type"] for m in self.sent]

    async def run(self, *messages):
        task = asyncio.ensure_future(
            self.app({"type": "lifespan", "asgi": {"version": "3.0"}}, self.receive, self.send)
        )
        for message in messages:
            await self.inbox.put({"type": message})
        await asyncio.wait_for(task, timeout=5)


@pytest.fixture(autouse=True)
def _clean_registry():
    lifespan._reset_registry_for_tests()
    yield
    lifespan._reset_registry_for_tests()


async def _never_called(scope, receive, send):  # pragma: no cover - asserted unused
    raise AssertionError("the inner app must not see the lifespan scope")


async def test_startup_and_shutdown_hooks_run_on_the_servers_loop():
    loop = asyncio.get_running_loop()
    calls = []

    async def start():
        calls.append(("start", asyncio.get_running_loop() is loop))

    async def stop():
        calls.append(("stop", asyncio.get_running_loop() is loop))

    server = _Server(with_lifespan(_never_called, startup=[start], shutdown=[stop]))
    await server.run("lifespan.startup", "lifespan.shutdown")
    assert server.types() == ["lifespan.startup.complete", "lifespan.shutdown.complete"]
    assert calls == [("start", True), ("stop", True)]


async def test_sync_hooks_run_off_the_loop():
    loop_thread = threading.get_ident()
    threads = []
    server = _Server(
        with_lifespan(
            _never_called,
            startup=[lambda: threads.append(threading.get_ident())],
            shutdown=[lambda: threads.append(threading.get_ident())],
        )
    )
    await server.run("lifespan.startup", "lifespan.shutdown")
    assert server.types() == ["lifespan.startup.complete", "lifespan.shutdown.complete"]
    assert len(threads) == 2 and loop_thread not in threads


async def test_a_failing_startup_hook_reports_startup_failed():
    async def start():
        raise RuntimeError("database unreachable")

    server = _Server(with_lifespan(_never_called, startup=[start]))
    await server.run("lifespan.startup")
    assert server.types() == ["lifespan.startup.failed"]
    assert "database unreachable" in server.sent[0]["message"]


async def test_shutdown_runs_every_hook_in_reverse_even_when_one_fails():
    order = []

    async def first():
        order.append("first")

    async def second():
        order.append("second")
        raise RuntimeError("close failed")

    server = _Server(with_lifespan(_never_called, shutdown=[first, second]))
    await server.run("lifespan.startup", "lifespan.shutdown")
    assert order == ["second", "first"]
    assert server.types() == ["lifespan.startup.complete", "lifespan.shutdown.failed"]


async def test_registered_hooks_run_after_the_explicit_ones():
    order = []

    @on_startup
    async def registered_start():
        order.append("registered-start")

    @on_shutdown
    def registered_stop():
        order.append("registered-stop")

    server = _Server(
        with_lifespan(
            _never_called,
            startup=[lambda: order.append("explicit-start")],
            shutdown=[lambda: order.append("explicit-stop")],
        )
    )
    await server.run("lifespan.startup", "lifespan.shutdown")
    assert order == ["explicit-start", "registered-start", "registered-stop", "explicit-stop"]


async def test_other_scopes_go_to_the_inner_app():
    seen = []

    async def inner(scope, receive, send):
        seen.append(scope["type"])

    app = with_lifespan(inner)
    await app({"type": "http"}, None, None)
    await app({"type": "websocket"}, None, None)
    assert seen == ["http", "websocket"]


async def test_wraps_a_real_protocol_type_router():
    """The shape a project's asgi.py builds: the router itself would raise
    ``ValueError`` for ``lifespan``."""
    pytest.importorskip("channels")
    from channels.routing import ProtocolTypeRouter
    from django.core.asgi import get_asgi_application

    router = ProtocolTypeRouter({"http": get_asgi_application()})
    with pytest.raises(ValueError):
        await router({"type": "lifespan"}, None, None)

    started = []
    server = _Server(with_lifespan(router, startup=[lambda: started.append(True)]))
    await server.run("lifespan.startup", "lifespan.shutdown")
    assert started == [True]
    assert server.types() == ["lifespan.startup.complete", "lifespan.shutdown.complete"]
