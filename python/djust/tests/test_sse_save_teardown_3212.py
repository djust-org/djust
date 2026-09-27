"""#3212 item 1: an SSE event whose save outruns its deadline must not block.

Every HTTP request Django serves over ASGI runs inside a per-request
``ThreadSensitiveContext``, so the SSE event POST's thread-sensitive calls go
to a single-thread executor that belongs to that request. When the explicit
save outran its deadline (#3200) the POST answered with the transient error
and returned, and the context's ``__aexit__`` then called
``executor.shutdown()``, which waits for the abandoned save: the EVENT LOOP
stopped until storage answered. Under a sync middleware stack the POST's
``async_to_sync`` bridge thread was held the same way.

The save now runs outside the request's executors, on a dedicated thread
(``djust-state-save``), so the request ends
when its turn does.

The save is blocked on a ``threading.Event`` and a timer thread releases it
after a few seconds as a safety valve. The assertion is an ordering: the
POST, context exit included, finished while the save was still blocked. If
the loop is blocked, the test coroutine cannot run again until the valve
fires, so the order flips.
"""

import asyncio
import threading
import uuid

import pytest
from asgiref.sync import ThreadSensitiveContext, async_to_sync, sync_to_async
from django.contrib.sessions.backends.db import SessionStore
from django.test import override_settings

from django.urls import path

from djust import LiveView, event_handler
from djust.decorators import state
from djust.sse import DjustSSEMessageView, DjustSSEStreamView, _sse_sessions
from djust.tests.test_exposure_sse_resilience_3200 import BackgroundPage, _fresh_key, _request

pytestmark = [pytest.mark.asyncio, pytest.mark.django_db(transaction=True)]

VALVE_S = 4.0

#: Holds the slow background callback until the test (or the valve) sets it.
WORK_RELEASE = threading.Event()
WORK_ENTERED = threading.Event()


class SlowWorkPage(BackgroundPage):
    count = state(0, persist="server")

    @event_handler()
    def spawn_slow(self):
        self.start_async(self._slow)

    def _slow(self):
        WORK_ENTERED.set()
        WORK_RELEASE.wait(timeout=30)
        return 1


urlpatterns = [path("bg/", BackgroundPage.as_view()), path("slow/", SlowWorkPage.as_view())]


@pytest.fixture(autouse=True)
def staged(monkeypatch):
    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)
    with override_settings(
        ROOT_URLCONF=__name__,
        LIVEVIEW_ALLOWED_MODULES=["djust"],
        DEBUG=False,
        DJUST_EXPLICIT_STATE_SAVE_TIMEOUT=0.05,
    ):
        yield
    _sse_sessions.clear()


async def _start(
    key, view="djust.tests.test_exposure_sse_resilience_3200.BackgroundPage", url="/bg/"
):
    sid = str(uuid.uuid4())
    request = await sync_to_async(_request)(
        "GET", f"/djust/sse/{sid}/", {"view": view, "_djust_url": url}, key
    )
    response = await DjustSSEStreamView().get(request, session_id=sid)
    assert response.status_code == 200
    session = _sse_sessions[sid]
    while not session.queue.empty():
        session.queue.get_nowait()
    return session


@pytest.fixture
def blocked_store(monkeypatch):
    """Once armed, the next session save blocks until released; a timer
    releases it. Armed after the mount, so only the event's save blocks."""
    release = threading.Event()
    entered = threading.Event()
    original = SessionStore.save
    valve = threading.Timer(VALVE_S, release.set)

    def save(self, *args, **kwargs):
        if not release.is_set():
            entered.set()
            release.wait(timeout=30)
        return original(self, *args, **kwargs)

    def arm():
        monkeypatch.setattr(SessionStore, "save", save)
        valve.start()

    yield arm, release, entered
    release.set()
    valve.cancel()


def _body(session):
    return {"type": "event", "event": "increment", "params": {}}


async def _until(predicate, what, limit=VALVE_S * 2):
    loop = asyncio.get_running_loop()
    end = loop.time() + limit
    while not predicate():
        if loop.time() > end:
            raise AssertionError("timed out waiting for " + what)
        await asyncio.sleep(0.01)


async def test_a_deferred_save_does_not_block_the_loop_at_request_teardown(blocked_store):
    arm, release, entered = blocked_store
    key = await sync_to_async(_fresh_key)()
    session = await _start(key)
    arm()

    async def post_as_django_serves_it():
        request = await sync_to_async(_request)(
            "POST", f"/djust/sse/{session.session_id}/message/", _body(session), key
        )
        async with ThreadSensitiveContext():  # what django's ASGIHandler wraps
            return await DjustSSEMessageView().post(request, session_id=session.session_id)

    post = asyncio.ensure_future(post_as_django_serves_it())
    await _until(post.done, "the POST to finish")
    finished_while_blocked = not release.is_set()
    assert entered.is_set(), "the save never reached the store; vacuous"
    assert finished_while_blocked, "the request's teardown waited for the abandoned save"
    assert post.result().status_code == 200

    errors = [f for f in list(session.queue._queue) if f and f.get("type") == "error"]
    assert errors and errors[0].get("transient") is True, errors

    # Storage answers; the catch-up turn delivers as before.
    release.set()
    await _until(
        lambda: any(
            f and f.get("source") == "async" and f.get("type") == "html_update"
            for f in list(session.queue._queue)
        ),
        "the catch-up frame",
    )


async def test_a_deferred_save_does_not_hold_a_sync_bridge_thread(blocked_store):
    """Under a sync middleware stack the POST runs inside ``async_to_sync``
    from a worker thread; its thread-sensitive calls went to that thread."""
    arm, release, entered = blocked_store
    key = await sync_to_async(_fresh_key)()
    session = await _start(key)
    arm()

    def post_through_a_sync_bridge():
        request = _request("POST", f"/djust/sse/{session.session_id}/message/", _body(session), key)
        return async_to_sync(DjustSSEMessageView().post)(request, session_id=session.session_id)

    post = asyncio.ensure_future(
        sync_to_async(post_through_a_sync_bridge, thread_sensitive=False)()
    )
    await _until(post.done, "the POST to finish")
    finished_while_blocked = not release.is_set()
    assert entered.is_set(), "the save never reached the store; vacuous"
    assert finished_while_blocked, "the bridge thread waited for the abandoned save"
    assert post.result().status_code == 200


async def test_a_long_lived_worker_slot_keeps_its_saves():
    """A WebSocket worker-pool slot (#3074) or a ``PooledHTTP`` slot is never
    shut down, so a save made there stays on the slot's thread."""
    from concurrent.futures import ThreadPoolExecutor

    from asgiref.sync import SyncToAsync

    from djust.runtime import _spawn_save

    class Slot:
        pass

    slot = Slot()
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="slot-3212")
    SyncToAsync.context_to_thread_executor[slot] = executor
    token = SyncToAsync.thread_sensitive_context.set(slot)
    names = []
    try:
        await _spawn_save(lambda: names.append(threading.current_thread().name))
        async with ThreadSensitiveContext():  # re-entrant: the slot stays chosen
            await _spawn_save(lambda: names.append(threading.current_thread().name))
    finally:
        SyncToAsync.thread_sensitive_context.reset(token)
        SyncToAsync.context_to_thread_executor.pop(slot, None)
        executor.shutdown()
    assert len(names) == 2 and all(name.startswith("slot-3212") for name in names), names


async def test_background_work_does_not_block_the_loop_at_request_teardown():
    """The parallel path (#1646): a ``start_async`` callback spawned by the
    event POST ran its sync work on the request's executor too, so a slow one
    still running when the POST ended blocked the loop the same way."""
    WORK_RELEASE.clear()
    WORK_ENTERED.clear()
    valve = threading.Timer(VALVE_S, WORK_RELEASE.set)
    valve.start()
    try:
        key = await sync_to_async(_fresh_key)()
        session = await _start(key, view=__name__ + ".SlowWorkPage", url="/slow/")

        async def post_as_django_serves_it():
            request = await sync_to_async(_request)(
                "POST",
                f"/djust/sse/{session.session_id}/message/",
                {"type": "event", "event": "spawn_slow", "params": {}},
                key,
            )
            async with ThreadSensitiveContext():
                response = await DjustSSEMessageView().post(request, session_id=session.session_id)
                # The callback is running before the request ends.
                await _until(WORK_ENTERED.is_set, "the background callback to start")
                return response

        post = asyncio.ensure_future(post_as_django_serves_it())
        await _until(post.done, "the POST to finish")
        assert not WORK_RELEASE.is_set(), "the request's teardown waited for background work"
        assert post.result().status_code == 200
        WORK_RELEASE.set()
        await _until(
            lambda: session.view_instance is not None and session.view_instance.count == 1,
            "the background result",
        )
    finally:
        WORK_RELEASE.set()
        valve.cancel()
