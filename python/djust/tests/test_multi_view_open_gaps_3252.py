"""#3252: what is still open, pinned as strict expected failures.

Each test states the behaviour the issue asks for and fails today for the reason
given. ``xfail(strict=True)`` turns into a failure the day one is fixed, so the
fix has to delete its marker (and move the test into the transport's own file).

Open on #3252 after the SSE work: a slow handler in one view delays the others
on its connection (one render lock). A refused lazy view used to send the whole
page to the login page; it is now refused alone
(``test_multi_view_refusal_3252.py``). Not pinned here because they are
client-side: events sent from code with no element go to the page view, the
destination's ``dj-lazy`` containers do not hydrate after ``live_redirect``, and
the page-POST fallback (a browser with no ``EventSource``) cannot host lazy
views at all (``tests/playwright/test_multi_view_sse_http.py`` pins that it says
so); and server-side, a non-sticky ``{% live_render %}`` child's state between
HTTP requests (``docs/website/guides/http-only-mode.md``).
"""

import asyncio
import threading

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.test import override_settings

from djust import LiveView, event_handler

pytestmark = [pytest.mark.asyncio, pytest.mark.django_db(transaction=True)]

MOD = __name__
STARTED = threading.Event()
RELEASE = threading.Event()


class Slow(LiveView):
    exposure_policy = "legacy"
    template = '<div dj-view="' + MOD + '.Slow" dj-id="0"><b>x</b></div>'

    @event_handler()
    def hold(self, **kwargs):
        STARTED.set()
        RELEASE.wait(10)

    @event_handler()
    def quick(self, **kwargs):
        pass


@pytest.fixture(autouse=True)
def setup():
    STARTED.clear()
    RELEASE.clear()
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[MOD], DEBUG=False):
        yield
    RELEASE.set()


async def _connect():
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    store = SessionStore()
    await sync_to_async(store.create)()
    communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    communicator.scope["session"] = store
    communicator.scope["user"] = AnonymousUser()
    assert (await communicator.connect())[0]
    await communicator.receive_json_from(timeout=3)
    return communicator


async def _until(communicator, *types, timeout=10.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    seen = []
    while True:
        seen.append(await communicator.receive_json_from(timeout=deadline - loop.time()))
        if seen[-1].get("type") in types:
            return seen


@pytest.mark.xfail(
    strict=True, reason="one render lock per connection: a slow view delays the rest"
)
async def test_a_slow_handler_in_one_view_does_not_delay_another_view():
    communicator = await _connect()
    try:
        for target in (None, "b"):
            frame = {"type": "mount", "view": MOD + ".Slow", "url": "/p/"}
            if target:
                frame["target_id"] = target
            await communicator.send_json_to(frame)
            await _until(communicator, "mount")
        await communicator.send_json_to({"type": "event", "event": "hold", "params": {}})
        # (Not ``sync_to_async``: the handler occupies the Django thread.)
        assert await asyncio.get_running_loop().run_in_executor(None, STARTED.wait, 5)
        await communicator.send_json_to(
            {"type": "event", "event": "quick", "params": {}, "target_id": "b"}
        )
        # The other view's event is answered while the first handler still runs.
        frames = await _until(communicator, "patch", "html_update", "noop", "error", timeout=2.0)
        assert frames[-1]["target_id"] == "b"
    finally:
        RELEASE.set()
        await communicator.disconnect()
