"""Save frequency, step 4: SSE closes and navigations, and server shutdown.

With ``DJUST_STATE_SAVE_INTERVAL`` set, what an SSE turn deferred is written

* when the stream closes, before the linger (a reconnect during the linger
  reads the store), and again after the linger (a POST that arrived during
  it changed the view again);
* before a ``live_redirect_mount`` replaces the page, and before a view
  beside the page view is unmounted;

and on any transport, when the ASGI ``lifespan`` shutdown runs
(``djust.lifespan``), for a server that stops without closing its sockets
first. A runtime whose server work stops forgets its timer.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, List

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth import get_user
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory
from django.urls import path as _path
from django.utils.functional import SimpleLazyObject

from djust import LiveView, runtime, sse
from djust.decorators import event_handler
from djust.lifespan import LifespanApp
from djust.sse import DjustSSEMessageView, DjustSSEStreamView, _sse_sessions
from djust.tests import _per_turn_harness as harness
from djust.tests import _save_frequency as sf

pytestmark = pytest.mark.django_db(transaction=True)

MODULE = __name__


class SseCounter(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root><pre>{{ count }}</pre></div>"

    def mount(self, request, **kwargs):
        self.count = 0

    @event_handler()
    def inc(self, **kwargs):
        self.count += 1

    def get_context_data(self, **kwargs):
        return {"count": self.count}


class Elsewhere(SseCounter):
    pass


urlpatterns = [
    _path(sf.PATH.strip("/") + "/", SseCounter.as_view()),
    _path("elsewhere/", Elsewhere.as_view()),
]


@pytest.fixture(autouse=True)
def _each(monkeypatch):
    sf.one_tab(monkeypatch)
    _sse_sessions.clear()
    yield
    _sse_sessions.clear()


def _settings(**extra):
    return harness.ws_settings(MODULE, ROOT_URLCONF=MODULE, DJUST_STATE_SAVE_INTERVAL=60, **extra)


def _count(key: str) -> Any:
    found = sf.stored_envelopes(key)
    for envelope in found.values():
        return envelope["values"].get("count")
    return None


async def _stored(key: str) -> Any:
    return await sync_to_async(_count)(key)


def _request(method: str, url: str, body: Any, key: str) -> Any:
    factory = RequestFactory()
    if method == "GET":
        made = factory.get(url, data=body)
    else:
        made = factory.post(url, data=json.dumps(body), content_type="application/json")
    made.session = SessionStore(key)
    made.user = SimpleLazyObject(lambda: get_user(made))
    made.tenant = None
    return made


async def _open(key: str) -> tuple:
    sid = str(uuid.uuid4())
    request = await sync_to_async(_request)(
        "GET",
        f"/djust/sse/{sid}/",
        {"view": f"{MODULE}.SseCounter", "_djust_url": sf.PATH, "_djust_tab": sf.TAB},
        key,
    )
    response = await DjustSSEStreamView().get(request, session_id=sid)
    assert response.status_code == 200
    stream = response._iterator
    assert "sse_connect" in await stream.__anext__()
    return _sse_sessions[sid], stream


async def _post(session: Any, key: str, body: dict) -> None:
    request = await sync_to_async(_request)(
        "POST", f"/djust/sse/{session.session_id}/message/", body, key
    )
    answer = await DjustSSEMessageView().post(request, session_id=session.session_id)
    assert answer.status_code in (200, 202, 204), answer.content


async def _inc(session: Any, key: str) -> None:
    await _post(session, key, {"type": "event", "event": "inc", "params": {}})


async def test_an_sse_close_writes_before_the_linger(monkeypatch):
    seen: List[Any] = []

    async def linger() -> None:
        seen.append(await _stored(key))  # what a reconnect now would read

    monkeypatch.setattr(sse, "_linger", linger)
    with _settings():
        key = await sync_to_async(sf.page_get)(SseCounter)
        session, stream = await _open(key)
        await _inc(session, key)
        await _inc(session, key)
        assert await _stored(key) == 0
        assert session.runtime.state_unsaved
        await stream.aclose()
    assert seen == [2]
    assert await _stored(key) == 2


async def test_a_post_during_the_linger_is_written_by_the_final_shutdown(monkeypatch):
    async def linger() -> None:
        await _inc(session, key)  # in flight while the stream closes

    monkeypatch.setattr(sse, "_linger", linger)
    with _settings():
        key = await sync_to_async(sf.page_get)(SseCounter)
        session, stream = await _open(key)
        await _inc(session, key)
        await stream.aclose()
    assert await _stored(key) == 2
    assert session.runtime.view_instance is None and session.runtime._state_timer is None


async def test_an_sse_navigation_writes_the_outgoing_page_first(monkeypatch):
    monkeypatch.setattr(sse, "_SESSION_LINGER_S", 0)
    with _settings():
        key = await sync_to_async(sf.page_get)(SseCounter)
        session, stream = await _open(key)
        try:
            await _inc(session, key)
            old = session.runtime
            assert old.state_unsaved
            await _post(
                session, key, {"type": "live_redirect_mount", "url": "/elsewhere/", "params": {}}
            )
            assert type(session.view_instance) is Elsewhere
            assert not old.state_unsaved and old._state_timer is None
        finally:
            await stream.aclose()
    found = await sync_to_async(sf.stored_envelopes)(key)
    counts = sorted(envelope["values"]["count"] for envelope in found.values())
    assert 1 in counts, counts  # the outgoing page's tab, written before it went


async def test_the_lifespan_shutdown_writes_every_unsaved_view(runtimes_registry):
    """For a server that stops without closing its sockets first (the close
    flushes them): the WebSocket's views here, unflushed when it runs."""
    with _settings():
        key = await sync_to_async(sf.page_get)(SseCounter)
        comm, _ = await harness.mount_view(SseCounter, key)
        try:
            await harness.event(comm, "inc")
            assert await _stored(key) == 0
            assert len(runtime._UNSAVED_RUNTIMES) == 1
            sent: List[dict] = []

            async def receive():
                return {"type": "lifespan.shutdown"}

            async def send(message):
                sent.append(message)

            await LifespanApp(lambda *a: None)({"type": "lifespan"}, receive, send)
            assert sent == [{"type": "lifespan.shutdown.complete"}]
            assert await _stored(key) == 1
            assert len(runtime._UNSAVED_RUNTIMES) == 0
        finally:
            await comm.disconnect()


@pytest.fixture
def runtimes_registry():
    runtime._UNSAVED_RUNTIMES.clear()
    yield
    runtime._UNSAVED_RUNTIMES.clear()


async def test_flush_unsaved_states_reports_what_it_wrote(runtimes_registry):
    with _settings():
        key = await sync_to_async(sf.page_get)(SseCounter)
        comm, _ = await harness.mount_view(SseCounter, key)
        try:
            assert await runtime.flush_unsaved_states() == 0
            await harness.event(comm, "inc")
            assert await runtime.flush_unsaved_states() == 1
            assert await runtime.flush_unsaved_states() == 0
            assert await _stored(key) == 1
        finally:
            await comm.disconnect()


async def test_stopping_server_work_forgets_the_timer(monkeypatch):
    monkeypatch.setattr(sse, "_SESSION_LINGER_S", 0)
    with _settings():
        key = await sync_to_async(sf.page_get)(SseCounter)
        session, stream = await _open(key)
        try:
            await _inc(session, key)
            rt = session.runtime
            assert rt._state_timer is not None
            rt.stop_server_work()
            assert rt._state_timer is None and not rt.state_unsaved
            assert rt not in runtime._UNSAVED_RUNTIMES
        finally:
            await stream.aclose()
    await asyncio.sleep(0)


SLOT_TAB = "slot-tab-0001"


async def _slot_counts(key: str) -> list:
    found = await sync_to_async(sf.stored_envelopes)(key)
    return sorted(envelope["values"]["count"] for envelope in found.values())


async def test_an_sse_view_beside_the_page_is_written_before_its_unmount(monkeypatch):
    monkeypatch.setattr(sse, "_SESSION_LINGER_S", 0)
    with _settings():
        key = await sync_to_async(sf.page_get)(SseCounter)
        session, stream = await _open(key)
        try:
            await _post(
                session,
                key,
                {
                    "type": "mount",
                    "target_id": "s1",
                    "view": f"{MODULE}.SseCounter",
                    "url": sf.PATH,
                    "tab": SLOT_TAB,
                },
            )
            await _post(session, key, {"type": "event", "event": "inc", "target_id": "s1"})
            slot_runtime = session._slots["s1"].runtime
            assert slot_runtime.state_unsaved
            await _post(session, key, {"type": "unmount", "target_id": "s1"})
            assert "s1" not in session._slots
            assert not slot_runtime.state_unsaved
        finally:
            await stream.aclose()
    assert 1 in await _slot_counts(key)


async def test_a_websocket_view_beside_the_page_is_written_before_its_unmount():
    with _settings():
        key = await sync_to_async(sf.page_get)(SseCounter)
        comm, _ = await harness.mount_view(SseCounter, key)
        try:
            await comm.send_json_to(
                {
                    "type": "mount",
                    "target_id": "w1",
                    "view": f"{MODULE}.SseCounter",
                    "url": sf.PATH,
                    "tab": SLOT_TAB,
                }
            )
            frame = await comm.receive_json_from()
            assert frame["type"] == "mount" and frame.get("target_id") == "w1", frame
            await comm.send_json_to(
                {"type": "event", "event": "inc", "params": {}, "target_id": "w1", "ref": 5}
            )
            from djust.tests._ws_frames import has_frame, receive_until

            await receive_until(comm, has_frame(ref=5), what="the slot's reply")
            assert 1 not in await _slot_counts(key)
            await comm.send_json_to({"type": "unmount", "target_id": "w1"})
            await comm.send_json_to({"type": "ping"})
            await receive_until(comm, has_frame("pong"), what="the pong after the unmount")
            assert 1 in await _slot_counts(key)
        finally:
            await comm.disconnect()
