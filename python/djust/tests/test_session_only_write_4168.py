"""Session-only writes that #4162 left unstored (#4168).

#4162 stored an explicit view's handler ``request.session`` writes before the
reply on every transport. Still lost before this:

1. a storage error on a turn's session save was dropped as if it were a logout;
2. writes from ``handle_tick``, a push handler or ``handle_info`` when the turn
   renders nothing (the save ran only on the render branch);
3. ``handle_params`` writes on ``url_change`` (the explicit path worked but had
   no test).

Each case writes from a hook that changes nothing else, then a plain Django
view (through the real session middleware) or the session row reads it.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from typing import Any, Dict, Iterator
from unittest import mock

import pytest
from asgiref.sync import sync_to_async
from django.contrib.sessions.backends.db import SessionStore
from django.urls import path as _path

from djust.tests import _per_turn_harness as harness
from djust.tests.test_session_only_write_4162 import (  # noqa: F401 - autouse fixture
    EXPECTED,
    SAVES,
    Plain,
    _key,
    _page,
    _read,
    _tab_and_saves,
    _ws_mount,
    other,
    plain,
    seed,
    turns,
)

pytestmark = pytest.mark.django_db(transaction=True)
MODULE = __name__
HANG_S = 30


class TickWriter(Plain):
    """``handle_tick`` writes only the session: the tick renders nothing."""

    exposure_policy = "explicit"
    page_url = "/tick-writer/"
    tick_interval = 150

    def handle_tick(self):
        self.request.session["ticked"] = "yes"


class PushWriter(Plain):
    """Push and NOTIFY hooks that write only the session and skip the render."""

    exposure_policy = "explicit"
    page_url = "/push-writer/"
    _listen_channels = frozenset({"sow4168"})

    def handle_quiet(self, **kwargs):
        self.request.session["pushed"] = "yes"
        self._skip_render = True

    def handle_info(self, message):
        self.request.session["notified"] = "yes"
        self._skip_render = True


class ParamsWriter(Plain):
    exposure_policy = "explicit"
    page_url = "/params/"

    def handle_params(self, params, uri):
        if params.get("q"):
            self.request.session["q"] = params["q"]


urlpatterns = [
    _path("seed/", seed),
    _path("other/", other),
    _path("plain/", plain),
    _path("plain-view/", Plain.as_view()),
] + [
    _path(view.page_url.strip("/") + "/", view.as_view())
    for view in (TickWriter, PushWriter, ParamsWriter)
]


@contextlib.contextmanager
def _settings(pool: Any = None) -> Iterator[None]:
    """The #4162 harness settings for this module; ``pool`` turns the worker
    pool on (``LIVEVIEW_CONFIG["worker_threads"]``)."""
    from djust.config import config as djust_config

    with harness.ws_settings(MODULE, ROOT_URLCONF=MODULE):
        if pool:
            djust_config.set("worker_threads", pool)
        yield


def _path_of(view: type) -> str:
    return f"{view.__module__}.{view.__qualname__}"


def _stored(key: str) -> Dict[str, Any]:
    return dict(SessionStore(key).load())


@contextlib.asynccontextmanager
async def _store_done(source: str) -> Any:
    """An event set once a turn of ``source`` has finished storing its session
    writes (#4274).

    The tests wait for this, close the connection (which stops ticks, so no
    later write overlaps) and then read the row ONCE. Polling the row while the
    turn writes it raced: with the worker pool the save runs on another
    thread's connection to the shared-cache in-memory test database, whose
    table lock fails the overlapping write at once (it ignores the busy
    timeout), and the turn's write was lost.

    ``source`` is what the runtime passes to ``store_turn_session_writes``:
    ``"tick"``, ``"push"``, ``"db_notify"`` or ``"event"``.
    """
    from djust import runtime

    done = asyncio.Event()
    real = runtime.store_turn_session_writes

    async def spy(view: Any, turn_source: str = "event") -> None:
        try:
            await real(view, turn_source)
        finally:
            if turn_source == source:
                done.set()

    with mock.patch.object(runtime, "store_turn_session_writes", spy):
        yield done


async def _wait(done: asyncio.Event) -> None:
    await asyncio.wait_for(done.wait(), HANG_S)


# -- SSE ----------------------------------------------------------------------


class _Sse:
    """One SSE stream mounted on a page's session, and its message endpoint."""

    def __init__(self, view: type, key: str) -> None:
        self.view = view
        self.key = key
        self.sid = str(uuid.uuid4())

    def _request(self, method: str, target: str, body: Any) -> Any:
        from django.contrib.auth import get_user
        from django.test import RequestFactory
        from django.utils.functional import SimpleLazyObject

        factory = RequestFactory()
        if method == "GET":
            made = factory.get(target, data=body)
        else:
            made = factory.post(target, data=json.dumps(body), content_type="application/json")
        made.session = SessionStore(self.key)
        made.user = SimpleLazyObject(lambda: get_user(made))
        made.tenant = None
        return made

    @staticmethod
    def _finish(made: Any, response: Any) -> Any:
        from django.contrib.sessions.middleware import SessionMiddleware

        return SessionMiddleware(lambda r: response).process_response(made, response)

    async def open(self) -> Any:
        from djust.sse import DjustSSEStreamView, _sse_sessions

        stream = await sync_to_async(self._request)(
            "GET",
            f"/djust/sse/{self.sid}/",
            {
                "view": _path_of(self.view),
                "_djust_url": self.view.page_url,
                "_djust_tab": harness.TAB,
            },
        )
        response = await DjustSSEStreamView().get(stream, session_id=self.sid)
        assert response.status_code == 200
        await sync_to_async(self._finish)(stream, response)
        return _sse_sessions[self.sid]

    async def post(self, body: Dict[str, Any]) -> None:
        from djust.sse import DjustSSEMessageView

        posted = await sync_to_async(self._request)("POST", f"/djust/sse/{self.sid}/message/", body)
        answer = await DjustSSEMessageView().post(posted, session_id=self.sid)
        assert answer.status_code in (200, 202, 204), answer.content
        await sync_to_async(self._finish)(posted, answer)

    def close(self) -> None:
        from djust.sse import _sse_sessions

        session = _sse_sessions.pop(self.sid, None)
        if session is not None:
            session.runtime.stop_server_work()


# -- 1. a storage error on a turn's session save -------------------------------


async def test_a_storage_error_on_a_turn_session_save_is_logged_not_dropped(caplog):
    """#4274: Django's db session store turns ANY database error on the update
    into ``UpdateError`` (a locked table, say). While the row still exists that
    is a storage failure, logged as one, not a logout dropped at DEBUG."""
    from channels.layers import get_channel_layer
    from django.contrib.sessions.backends.base import UpdateError

    def failing_save(self: Any, must_create: bool = False) -> None:
        raise UpdateError

    view = PushWriter
    with _settings():
        client = await sync_to_async(_page)(view)
        key = _key(client)
        async with _store_done("db_notify") as done:
            comm = await _ws_mount(view, key)
            try:
                with (
                    mock.patch.object(SessionStore, "save", failing_save),
                    caplog.at_level("DEBUG", logger="djust.runtime"),
                ):
                    await get_channel_layer().group_send(
                        "djust_db_notify_sow4168",
                        {"type": "db_notify", "channel": "sow4168", "payload": {}},
                    )
                    await _wait(done)
            finally:
                await comm.disconnect()
        exists = await sync_to_async(SessionStore().exists)(key)
    assert exists
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert any("db_notify turn were not stored" in r.getMessage() for r in warnings), caplog.text
    assert "the session moved on" not in caplog.text


# -- 2. ticks, pushes and NOTIFY hooks that render nothing ---------------------

POOL = [pytest.param(None, id="stock"), pytest.param(2, id="pool")]


@pytest.fixture
def fresh_pool(monkeypatch):
    from djust import worker_pool

    monkeypatch.setattr(worker_pool, "_pool", [])


@pytest.mark.parametrize("pool", POOL)
@pytest.mark.parametrize("transport", ["ws", "sse"])
async def test_a_tick_that_only_writes_the_session_is_stored(transport, pool, fresh_pool):
    if transport == "sse" and pool:
        pytest.skip("SSE sessions are never on the worker pool")
    view = TickWriter
    with _settings(pool):
        client = await sync_to_async(_page)(view)
        key = _key(client)
        async with _store_done("tick") as done:
            if transport == "ws":
                comm = await _ws_mount(view, key)
                try:
                    await _wait(done)
                finally:
                    await comm.disconnect()
            else:
                sse = _Sse(view, key)
                try:
                    await sse.open()
                    await _wait(done)
                finally:
                    sse.close()
        seen = await sync_to_async(_read)(client)
    assert seen.get("ticked") == "yes", seen


@pytest.mark.parametrize("pool", POOL)
@pytest.mark.parametrize("transport", ["ws", "sse"])
async def test_a_push_that_renders_nothing_still_stores_the_session(transport, pool, fresh_pool):
    from djust.push import apush_to_view

    if transport == "sse" and pool:
        pytest.skip("SSE sessions are never on the worker pool")
    view = PushWriter
    with _settings(pool):
        client = await sync_to_async(_page)(view)
        key = _key(client)
        async with _store_done("push") as done:
            if transport == "ws":
                comm = await _ws_mount(view, key)
                try:
                    await apush_to_view(_path_of(view), handler="handle_quiet")
                    await _wait(done)
                finally:
                    await comm.disconnect()
            else:
                sse = _Sse(view, key)
                try:
                    session = await sse.open()
                    mounted = session.runtime.view_instance
                    assert await session.runtime.server_push(mounted, {"handler": "handle_quiet"})
                    await _wait(done)
                finally:
                    sse.close()
        seen = await sync_to_async(_read)(client)
    assert seen.get("pushed") == "yes", seen


@pytest.mark.parametrize("pool", POOL)
async def test_a_db_notify_that_renders_nothing_stores_the_session(pool, fresh_pool):
    from channels.layers import get_channel_layer

    view = PushWriter
    with _settings(pool):
        client = await sync_to_async(_page)(view)
        key = _key(client)
        async with _store_done("db_notify") as done:
            comm = await _ws_mount(view, key)
            try:
                await get_channel_layer().group_send(
                    "djust_db_notify_sow4168",
                    {"type": "db_notify", "channel": "sow4168", "payload": {}},
                )
                await _wait(done)
            finally:
                await comm.disconnect()
        seen = await sync_to_async(_read)(client)
    assert seen.get("notified") == "yes", seen


# -- 3. url_change → handle_params ---------------------------------------------


@pytest.mark.parametrize("transport", ["ws", "sse", "http"])
async def test_a_url_change_handle_params_write_is_read_by_a_plain_view(transport):
    view = ParamsWriter
    message = {"type": "url_change", "params": {"q": "1"}, "uri": view.page_url + "?q=1"}
    with _settings():
        client = await sync_to_async(_page)(view)
        key = _key(client)
        if transport == "ws":
            comm = await _ws_mount(view, key)
            try:
                await comm.send_json_to(message)
                # The turn stores its session writes before its reply frame.
                await asyncio.wait_for(comm.receive_json_from(), HANG_S)
            finally:
                await comm.disconnect()
        elif transport == "sse":
            sse = _Sse(view, key)
            try:
                await sse.open()
                await sse.post(message)
            finally:
                sse.close()
        else:
            await sync_to_async(client.get)(view.page_url + "?q=1")
        seen = await sync_to_async(_read)(client)
    assert seen.get("q") == "1", seen
