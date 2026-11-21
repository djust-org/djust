"""Session-only writes that #4162 left unstored (#4168).

#4162 stored an explicit view's handler ``request.session`` writes before the
reply on every transport. Still lost before this:

1. a legacy view's writes over WebSocket and SSE (the socket's session object
   was never tracked or saved);
2. writes from ``handle_tick``, a push handler or ``handle_info`` when the turn
   renders nothing (the save ran only on the render branch), for both policies;
3. ``handle_params`` writes on ``url_change`` (the explicit path worked but had
   no test; the legacy one was item 1).

Each case writes from a hook that changes nothing else, then a plain Django
view (through the real session middleware) or the session row reads it.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from typing import Any, Callable, Dict, Iterator
from unittest import mock

import pytest
from asgiref.sync import sync_to_async
from django.contrib.sessions.backends.db import SessionStore
from django.urls import path as _path

from djust.decorators import event_handler
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


class LegacyPlain(Plain):
    exposure_policy = "legacy"  # legacy subject: #4168 legacy socket turns store session writes
    page_url = "/legacy-plain/"


class LegacyMountWriter(LegacyPlain):
    exposure_policy = "legacy"  # legacy subject: #4168 a legacy socket mount stores its writes
    page_url = "/legacy-mount/"

    def mount(self, request, **kwargs):
        super().mount(request, **kwargs)
        request.session["mounts"] = request.session.get("mounts", 0) + 1


class TickWriter(Plain):
    """``handle_tick`` writes only the session: the tick renders nothing."""

    exposure_policy = "explicit"
    page_url = "/tick-writer/"
    tick_interval = 150

    def handle_tick(self):
        self.request.session["ticked"] = "yes"


class LegacyTickWriter(TickWriter):
    exposure_policy = "legacy"  # legacy subject: #4168 a no-frame tick stores its writes
    page_url = "/legacy-tick-writer/"


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


class LegacyPushWriter(PushWriter):
    exposure_policy = "legacy"  # legacy subject: #4168 a no-frame push stores its writes
    page_url = "/legacy-push-writer/"


class LegacyRenderingWriter(LegacyPlain):
    """A tick and a NOTIFY hook that write the session AND render (#4168 review N4)."""

    exposure_policy = "legacy"  # legacy subject: #4168 a rendering turn logs its own source
    page_url = "/legacy-rendering-writer/"
    tick_interval = 150
    _listen_channels = frozenset({"sow4168r"})

    def handle_tick(self):
        self.request.session["ticked"] = "yes"
        self.count += 1

    def handle_info(self, message):
        self.request.session["notified"] = "yes"
        self.count += 1


class ParamsWriter(Plain):
    exposure_policy = "explicit"
    page_url = "/params/"

    def handle_params(self, params, uri):
        if params.get("q"):
            self.request.session["q"] = params["q"]


class LegacyParamsWriter(ParamsWriter):
    exposure_policy = "legacy"  # legacy subject: #4168 a legacy url_change stores its writes
    page_url = "/legacy-params/"


class LegacyLogout(LegacyPlain):
    exposure_policy = "legacy"  # legacy subject: #4168 a legacy save after a logout is dropped
    page_url = "/legacy-logout/"

    @event_handler()
    def logout_then_write(self, **kwargs):
        SessionStore(self.request.session.session_key).delete()
        self.request.session["k"] = "v"


urlpatterns = [
    _path("seed/", seed),
    _path("other/", other),
    _path("plain/", plain),
    _path("plain-view/", Plain.as_view()),
] + [
    _path(view.page_url.strip("/") + "/", view.as_view())
    for view in (
        LegacyPlain,
        LegacyMountWriter,
        TickWriter,
        LegacyTickWriter,
        PushWriter,
        LegacyPushWriter,
        ParamsWriter,
        LegacyParamsWriter,
        LegacyLogout,
        LegacyRenderingWriter,
    )
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


async def _until_stored(key: str, wanted: Callable[[Dict[str, Any]], bool], what: str) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + HANG_S
    while True:
        stored = await sync_to_async(_stored)(key)
        if wanted(stored):
            return
        assert loop.time() < deadline, f"{what} never reached the session store: {stored}"
        await asyncio.sleep(0.05)


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


# -- 1. legacy views over WebSocket and SSE -------------------------------------


@pytest.mark.parametrize("transport", ["ws", "sse", "http"])
@pytest.mark.parametrize("handler", sorted(EXPECTED))
async def test_a_legacy_session_only_write_is_read_by_a_plain_view(handler, transport):
    with _settings():
        client, counts = await turns(transport, LegacyPlain, [handler])
        seen = await sync_to_async(_read)(client)
    assert seen == EXPECTED[handler]
    assert counts[0] >= 1  # stored by the turn itself


async def test_a_legacy_ws_write_is_stored_before_the_reply():
    with _settings():
        client = await sync_to_async(_page)(LegacyPlain)
        key = _key(client)
        comm = await _ws_mount(LegacyPlain, key)
        try:
            await harness.event(comm, "set_only")
            stored = await sync_to_async(lambda: SessionStore(key).load().get("k"))()
        finally:
            await comm.disconnect()
    assert stored == "v"


@pytest.mark.parametrize("transport", ["ws", "sse"])
async def test_a_legacy_socket_mount_write_is_stored(transport):
    """The page GET's mount() wrote ``mounts = 1``; the socket's mount() makes it 2."""
    with _settings():
        client = await sync_to_async(_page)(LegacyMountWriter)
        key = _key(client)
        if transport == "ws":
            comm = await _ws_mount(LegacyMountWriter, key)
            try:
                stored = await sync_to_async(_stored)(key)
            finally:
                await comm.disconnect()
        else:
            sse = _Sse(LegacyMountWriter, key)
            try:
                await sse.open()
                stored = await sync_to_async(_stored)(key)
            finally:
                sse.close()
    assert stored.get("mounts") == 2, stored


@pytest.mark.parametrize("transport", ["ws", "sse"])
async def test_a_legacy_turn_that_does_not_write_adds_no_save(transport):
    with _settings():
        client = await sync_to_async(_page)(LegacyPlain)
        key = _key(client)
        if transport == "ws":
            comm = await _ws_mount(LegacyPlain, key)
            try:
                SAVES.clear()
                await harness.event(comm, "nothing")
                await harness.event(comm, "read_only")
                saves = list(SAVES)
            finally:
                await comm.disconnect()
        else:
            sse = _Sse(LegacyPlain, key)
            try:
                await sse.open()
                SAVES.clear()
                await sse.post({"type": "event", "event": "nothing", "params": {}})
                await sse.post({"type": "event", "event": "read_only", "params": {}})
                # The message POSTs' own middleware saves nothing unmodified.
                saves = list(SAVES)
            finally:
                sse.close()
    assert saves == [], saves


@pytest.mark.parametrize("transport", ["ws", "sse"])
async def test_a_legacy_write_keeps_another_requests_write(transport):
    """The legacy socket's save is merged: a key another request wrote after
    the mount is not overwritten by the mount-time session object."""
    with _settings():
        client = await sync_to_async(_page)(LegacyPlain)
        key = _key(client)
        if transport == "ws":
            comm = await _ws_mount(LegacyPlain, key)
            try:
                await sync_to_async(client.get)("/other/")
                await harness.event(comm, "set_only")
            finally:
                await comm.disconnect()
        else:
            sse = _Sse(LegacyPlain, key)
            try:
                await sse.open()
                await sync_to_async(client.get)("/other/")
                await sse.post({"type": "event", "event": "set_only", "params": {}})
            finally:
                sse.close()
        seen = await sync_to_async(_read)(client)
    assert seen.get("other") == "kept" and seen.get("k") == "v", seen


async def test_a_legacy_save_after_a_logout_is_dropped_quietly(caplog):
    """The session was deleted by another request: the frame still goes, the
    session is not revived, and nothing is logged as a failure."""
    with _settings():
        client = await sync_to_async(_page)(LegacyLogout)
        key = _key(client)
        comm = await _ws_mount(LegacyLogout, key)
        try:
            with caplog.at_level("WARNING"):
                frame = await harness.event(comm, "logout_then_write")
        finally:
            await comm.disconnect()
        exists = await sync_to_async(SessionStore().exists)(key)
    assert frame["type"] in ("patch", "noop", "html_update"), frame
    assert not exists, "the logged-out session was revived"
    assert "were not stored" not in caplog.text


@pytest.mark.parametrize("view", ["LegacyPlain", "LegacyMountWriter"])
async def test_a_keyless_legacy_socket_creates_no_session_row(view):
    """#4168 review N5: a legacy socket mounted without a session cookie (an
    anonymous visitor) has a session object with no key. Storing its writes
    would create a session row whose key no browser ever receives: one orphan
    per socket. Nothing is stored for it."""
    from django.contrib.sessions.models import Session

    view_cls = {"LegacyPlain": LegacyPlain, "LegacyMountWriter": LegacyMountWriter}[view]
    with _settings():
        before = await sync_to_async(Session.objects.count)()
        comm = await _ws_mount(view_cls, None)
        try:
            frame = await harness.event(comm, "set_only")
        finally:
            await comm.disconnect()
        after = await sync_to_async(Session.objects.count)()
    assert frame["type"] in ("patch", "noop", "html_update"), frame
    assert after == before


# -- 2. ticks, pushes and NOTIFY hooks that render nothing ---------------------

POLICIES = [pytest.param("explicit", id="explicit"), pytest.param("legacy", id="legacy")]
POOL = [pytest.param(None, id="stock"), pytest.param(2, id="pool")]


@pytest.fixture
def fresh_pool(monkeypatch):
    from djust import worker_pool

    monkeypatch.setattr(worker_pool, "_pool", [])


@pytest.mark.parametrize("pool", POOL)
@pytest.mark.parametrize("policy", POLICIES)
@pytest.mark.parametrize("transport", ["ws", "sse"])
async def test_a_tick_that_only_writes_the_session_is_stored(transport, policy, pool, fresh_pool):
    if transport == "sse" and pool:
        pytest.skip("SSE sessions are never on the worker pool")
    view = TickWriter if policy == "explicit" else LegacyTickWriter
    with _settings(pool):
        client = await sync_to_async(_page)(view)
        key = _key(client)
        if transport == "ws":
            comm = await _ws_mount(view, key)
            try:
                await _until_stored(key, lambda s: s.get("ticked") == "yes", "the tick's write")
            finally:
                await comm.disconnect()
        else:
            sse = _Sse(view, key)
            try:
                await sse.open()
                await _until_stored(key, lambda s: s.get("ticked") == "yes", "the tick's write")
            finally:
                sse.close()
        seen = await sync_to_async(_read)(client)
    assert seen.get("ticked") == "yes", seen


@pytest.mark.parametrize("pool", POOL)
@pytest.mark.parametrize("policy", POLICIES)
@pytest.mark.parametrize("transport", ["ws", "sse"])
async def test_a_push_that_renders_nothing_still_stores_the_session(
    transport, policy, pool, fresh_pool
):
    from djust.push import apush_to_view

    if transport == "sse" and pool:
        pytest.skip("SSE sessions are never on the worker pool")
    view = PushWriter if policy == "explicit" else LegacyPushWriter
    with _settings(pool):
        client = await sync_to_async(_page)(view)
        key = _key(client)
        if transport == "ws":
            comm = await _ws_mount(view, key)
            try:
                await apush_to_view(_path_of(view), handler="handle_quiet")
                await _until_stored(key, lambda s: s.get("pushed") == "yes", "the push's write")
            finally:
                await comm.disconnect()
        else:
            sse = _Sse(view, key)
            try:
                session = await sse.open()
                mounted = session.runtime.view_instance
                assert await session.runtime.server_push(mounted, {"handler": "handle_quiet"})
                await _until_stored(key, lambda s: s.get("pushed") == "yes", "the push's write")
            finally:
                sse.close()
        seen = await sync_to_async(_read)(client)
    assert seen.get("pushed") == "yes", seen


@pytest.mark.parametrize("pool", POOL)
@pytest.mark.parametrize("policy", POLICIES)
async def test_a_db_notify_that_renders_nothing_stores_the_session(policy, pool, fresh_pool):
    from channels.layers import get_channel_layer

    view = PushWriter if policy == "explicit" else LegacyPushWriter
    with _settings(pool):
        client = await sync_to_async(_page)(view)
        key = _key(client)
        comm = await _ws_mount(view, key)
        try:
            await get_channel_layer().group_send(
                "djust_db_notify_sow4168",
                {"type": "db_notify", "channel": "sow4168", "payload": {}},
            )
            await _until_stored(key, lambda s: s.get("notified") == "yes", "handle_info's write")
        finally:
            await comm.disconnect()
        seen = await sync_to_async(_read)(client)
    assert seen.get("notified") == "yes", seen


@pytest.mark.parametrize("pool", POOL)
@pytest.mark.parametrize("turn", ["tick", "db_notify"])
async def test_a_legacy_rendering_turn_stores_under_its_own_source(turn, pool, fresh_pool):
    """#4168 review N4: a legacy tick or ``db_notify`` turn that renders stores
    its session writes (and logs a failure) under its own source, not "push"."""
    from channels.layers import get_channel_layer

    from djust import runtime

    sources = []
    real = runtime.store_turn_session_writes

    async def spy(view: Any, source: str = "event") -> None:
        sources.append(source)
        await real(view, source)

    view = LegacyRenderingWriter
    wanted = "ticked" if turn == "tick" else "notified"
    with _settings(pool), mock.patch.object(runtime, "store_turn_session_writes", spy):
        client = await sync_to_async(_page)(view)
        key = _key(client)
        comm = await _ws_mount(view, key)
        try:
            if turn == "db_notify":
                await get_channel_layer().group_send(
                    "djust_db_notify_sow4168r",
                    {"type": "db_notify", "channel": "sow4168r", "payload": {}},
                )
            await _until_stored(key, lambda s: s.get(wanted) == "yes", f"the {turn}'s write")
        finally:
            await comm.disconnect()
    assert turn in sources, sources
    assert "push" not in sources, sources


# -- 3. url_change → handle_params ---------------------------------------------


@pytest.mark.parametrize("policy", POLICIES)
@pytest.mark.parametrize("transport", ["ws", "sse", "http"])
async def test_a_url_change_handle_params_write_is_read_by_a_plain_view(transport, policy):
    view = ParamsWriter if policy == "explicit" else LegacyParamsWriter
    message = {"type": "url_change", "params": {"q": "1"}, "uri": view.page_url + "?q=1"}
    with _settings():
        client = await sync_to_async(_page)(view)
        key = _key(client)
        if transport == "ws":
            comm = await _ws_mount(view, key)
            try:
                await comm.send_json_to(message)
                await _until_stored(key, lambda s: s.get("q") == "1", "handle_params' write")
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
