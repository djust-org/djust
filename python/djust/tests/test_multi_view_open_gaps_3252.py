"""#3252: what is still open, pinned as strict expected failures.

Each test states the behaviour the issue asks for and fails today for the reason
given. ``xfail(strict=True)`` turns into a failure the day one is fixed, so the
fix has to delete its marker (and move the test into the transport's own file).

Open on #3252 after the SSE work: a refused login-required lazy view still
sends the whole page to the login page (a slow handler in one view no longer
delays the others: ``test_multi_view_render_locks_3252.py``). Not pinned here because they are
client-side: events sent from code with no element go to the page view, the
destination's ``dj-lazy`` containers do not hydrate after ``live_redirect``, and
the page-POST fallback (a browser with no ``EventSource``) cannot host lazy
views at all (``tests/playwright/test_multi_view_sse_http.py`` pins that it says
so); and server-side, a non-sticky ``{% live_render %}`` child's state between
HTTP requests (``docs/website/guides/http-only-mode.md``).
"""

import asyncio

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.test import override_settings

from djust import LiveView

pytestmark = [pytest.mark.asyncio, pytest.mark.django_db(transaction=True)]

MOD = __name__


class Guarded(LiveView):
    exposure_policy = "legacy"
    login_required = True
    template = '<div dj-view="' + MOD + '.Guarded" dj-id="0"><b>guarded</b></div>'


@pytest.fixture(autouse=True)
def setup():
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[MOD], DEBUG=False):
        yield


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
    strict=True, reason="a refused lazy view's login redirect is a page-level navigate frame"
)
async def test_a_refused_lazy_view_does_not_send_the_whole_page_to_login():
    communicator = await _connect()
    try:
        await communicator.send_json_to({"type": "mount", "view": MOD + ".Slow", "url": "/p/"})
        await _until(communicator, "mount")
        await communicator.send_json_to(
            {"type": "mount", "view": MOD + ".Guarded", "url": "/p/", "target_id": "g"}
        )
        frames = await _until(communicator, "navigate", "error", "mount")
        assert not any(f.get("type") == "navigate" for f in frames), frames
    finally:
        await communicator.disconnect()
