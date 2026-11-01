"""``async def mount`` and ``async def handle_params`` run on the event loop.

``mount()`` was sync only. The socket mount ran it through ``sync_to_async``,
which raises ``TypeError`` for an ``async def``; the HTTP render called it bare,
so an ``async def mount`` returned a coroutine that never ran. And a sync
``mount()`` runs on asgiref's ONE shared sync thread: every page's mount queues
behind every other user's database read, so forty pages opening at once each
took about the sum of their reads.

Now ``mount()`` and ``handle_params()`` may be ``async def``. Over the socket
they are awaited on the event loop, so their awaits (async ORM, an async
service) overlap with other sessions' work instead of queueing behind it; the
HTTP render runs them to completion before it renders. A sync ``mount()``
keeps running exactly as before.
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
import threading
from pathlib import Path

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import override_settings

from djust import LiveView
from djust.decorators import event_handler

pytest.importorskip("channels")

_MODULE = __name__
_SEEN: dict = {}


def _note(key):
    _SEEN.setdefault(key, []).append(threading.get_ident())


class _AsyncMountView(LiveView):
    exposure_policy = "legacy"
    template = "<div dj-root><b>{{ greeting }}</b><i>{{ tab }}</i></div>"

    async def mount(self, request, **kwargs):
        _note("mount")
        await asyncio.sleep(0)  # a real await: the body runs past it
        self.greeting = "hello-async"
        self.tab = "none"

    async def handle_params(self, params, uri):
        _note("params")
        await asyncio.sleep(0)
        self.tab = params.get("tab", "none")


class _SyncMountView(LiveView):
    exposure_policy = "legacy"
    template = "<div dj-root><b>{{ greeting }}</b></div>"

    def mount(self, request, **kwargs):
        _note("sync_mount")
        self.greeting = "hello-sync"


@pytest.fixture(autouse=True)
def _reset():
    _SEEN.clear()
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[_MODULE]):
        yield
    _SEEN.clear()


async def _receive_until(communicator, wanted, *, tries=8, timeout=5):
    last = None
    for _ in range(tries):
        last = await communicator.receive_json_from(timeout=timeout)
        if last.get("type") in wanted:
            return last
    return last


async def _ws_mount(view, url="/m/", params=None):
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    comm = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    connected, _ = await comm.connect()
    assert connected
    await comm.receive_json_from()
    frame = {"type": "mount", "view": f"{_MODULE}.{view}", "url": url}
    if params is not None:
        frame["params"] = params
    await comm.send_json_to(frame)
    return comm, await _receive_until(comm, ("mount", "error"))


# --------------------------------------------------------------------------- #
# The socket mount
# --------------------------------------------------------------------------- #


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_socket_awaits_async_mount_and_handle_params_on_the_loop():
    loop_thread = threading.get_ident()
    comm, frame = await _ws_mount("_AsyncMountView", params={"tab": "stats"})
    await comm.disconnect()
    assert frame.get("type") == "mount", frame
    assert "hello-async" in frame["html"]
    assert "stats" in frame["html"]
    assert _SEEN["mount"] == [loop_thread]
    assert _SEEN["params"] == [loop_thread]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_socket_still_runs_a_sync_mount_off_the_loop():
    loop_thread = threading.get_ident()
    comm, frame = await _ws_mount("_SyncMountView")
    await comm.disconnect()
    assert "hello-sync" in frame["html"]
    (mount_thread,) = _SEEN["sync_mount"]
    assert mount_thread != loop_thread


# --------------------------------------------------------------------------- #
# Concurrent mounts overlap instead of queueing on the shared sync thread
# --------------------------------------------------------------------------- #

_N = 8
#: How many mount bodies are inside ``mount()`` right now, and which mounts
#: saw every one of the ``_N`` inside at the same time.
_MEET: dict = {}


class _AwaitingMountView(LiveView):
    """A page whose first read is an await (an async ORM query, say)."""

    exposure_policy = "legacy"
    template = "<div dj-root><b>{{ rows }}</b></div>"

    async def mount(self, request, **kwargs):
        _MEET["inside"] += 1
        if _MEET["inside"] == _N:
            _MEET["all_in"].set()
        try:
            # Wait until every page is inside its mount at once. Mounts that
            # queue one behind another never get there; the deadline is only
            # reached then.
            await asyncio.wait_for(_MEET["all_in"].wait(), timeout=10)
            _MEET["met"] += 1
        except asyncio.TimeoutError:
            _MEET["missed"] += 1
        self.rows = "rows-loaded"


class _BlockingSyncMountView(LiveView):
    """The same page with a sync mount and a blocking read."""

    exposure_policy = "legacy"
    template = "<div dj-root><b>{{ rows }}</b></div>"

    def mount(self, request, **kwargs):
        try:
            _MEET["barrier"].wait()
            _MEET["met"] += 1
        except threading.BrokenBarrierError:
            _MEET["missed"] += 1
        self.rows = "rows-loaded"


async def _mount_concurrently(view, count):
    results = await asyncio.gather(*(_ws_mount(view) for _ in range(count)))
    for comm, frame in results:
        await comm.disconnect()
        assert frame.get("type") == "mount", frame


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_concurrent_async_mounts_overlap():
    _MEET.update(inside=0, met=0, missed=0, all_in=asyncio.Event())
    await _mount_concurrently("_AwaitingMountView", _N)
    assert (_MEET["met"], _MEET["missed"]) == (_N, 0)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_concurrent_sync_mounts_do_not():
    """Gate-off sibling: sync mounts share asgiref's one sync thread, so they
    run one at a time and never all meet; the measurement above tells the two
    apart. (The barrier's timeout releases the first; the rest see it broken.)"""
    count = 3
    _MEET.update(met=0, missed=0, barrier=threading.Barrier(count, timeout=1))
    await _mount_concurrently("_BlockingSyncMountView", count)
    assert (_MEET["met"], _MEET["missed"]) == (0, count)


# --------------------------------------------------------------------------- #
# The HTTP render
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
def test_http_get_runs_async_mount_and_handle_params(rf):
    request = rf.get("/m/", {"tab": "box"})
    request.user = AnonymousUser()
    from django.contrib.sessions.backends.db import SessionStore

    request.session = SessionStore()
    response = _AsyncMountView.as_view()(request)
    html = response.content.decode()
    assert response.status_code == 200
    assert "hello-async" in html
    assert "box" in html


class _AsyncChild(LiveView):
    exposure_policy = "legacy"
    template = '<p class="child">{{ child_value }}</p>'

    async def mount(self, request, **kwargs):
        await asyncio.sleep(0)
        self.child_value = "child-mounted"


_PAGE = (
    "<!DOCTYPE html><html><body><div dj-root>"
    '{%% load live_tags %%}{%% live_render "%s._AsyncChild" %%}'
    "</div></body></html>"
)


def test_live_render_runs_an_async_child_mount(rf):
    from django.conf import settings

    from djust.utils import clear_template_dirs_cache

    tmpdir = Path(tempfile.mkdtemp())
    (tmpdir / "async_mount_page.html").write_text(_PAGE % _MODULE)
    templates = [dict(t) for t in settings.TEMPLATES]
    templates[0]["DIRS"] = [str(tmpdir), *templates[0].get("DIRS", [])]
    page_cls = type(
        "AsyncMountPage",
        (LiveView,),
        {"template_name": "async_mount_page.html", "mount": lambda self, request, **kw: None},
    )
    try:
        with override_settings(TEMPLATES=templates, DJUST_LIVE_RENDER_ALLOWED_MODULES=[_MODULE]):
            clear_template_dirs_cache()
            request = rf.get("/page/")
            request.user = AnonymousUser()
            view = page_cls()
            view.request = request
            view.mount(request)
            view.get_template()
            html = view.render_full_template(request)
    finally:
        clear_template_dirs_cache()
        shutil.rmtree(tmpdir, ignore_errors=True)
    assert "child-mounted" in html, html


class _AsyncMountPostView(LiveView):
    exposure_policy = "legacy"
    template = "<div dj-root><b>{{ greeting }}-{{ clicks }}</b></div>"

    async def mount(self, request, **kwargs):
        await asyncio.sleep(0)
        self.greeting = "posted-async"
        self.clicks = 0

    @event_handler()
    def click(self, **kwargs):
        self.clicks += 1


@pytest.mark.django_db
def test_http_post_fallback_runs_async_mount(rf):
    """The page-POST fallback mounts a view it has no saved state for."""
    import json

    from django.contrib.sessions.backends.db import SessionStore

    request = rf.post(
        "/m/",
        data=json.dumps({"event": "click", "params": {}}),
        content_type="application/json",
        HTTP_X_DJUST_EVENT="click",
    )
    request.user = AnonymousUser()
    request.tenant = None
    request.session = SessionStore()
    request.session.create()
    response = _AsyncMountPostView().post(request)
    assert response.status_code == 200, response.content[:500]
    assert b"posted-async-1" in response.content, response.content[:1000]
