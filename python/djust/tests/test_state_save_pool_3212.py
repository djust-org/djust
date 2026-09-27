"""#3212 review (#3229) B1/B2/N3: the pool that runs request-scoped saves.

Saves made inside a request's executors (the SSE event POST) run on a pool
of long-lived threads, not on the request's own executor (#3212 item 1).

- **B1.** A long-lived thread gets no ``request_started``/``request_finished``,
  so each job is bracketed by ``close_old_connections()``, as the worker pool
  does (#3095). Otherwise ``CONN_MAX_AGE`` never applies and a connection
  broken under the thread stays broken for every later save.
- **B2.** One thread serialized every SSE save in the process, and a save's
  deadline starts only when it runs, so one hung store write stalled every
  other session. It is a pool now, and the wait for a thread is bounded too.
- **N3.** Saves are ordered per runtime. On SSE a navigation and a reconnect
  each build a new runtime, so the new one starts out ordered after the old
  one's still-running save (the single thread used to do that by accident).

The probes are the #3229 reviewer's.
"""

import asyncio
import threading
import time
import uuid

import pytest
from asgiref.sync import ThreadSensitiveContext, sync_to_async
from django.contrib.sessions.backends.db import SessionStore
from django.test import override_settings
from django.urls import path

from djust import LiveView
from djust import runtime as runtime_module
from djust.sse import DjustSSEMessageView, DjustSSEStreamView, _sse_sessions
from djust.tests.test_exposure_sse_resilience_3200 import BackgroundPage, _fresh_key, _request

pytestmark = [pytest.mark.asyncio, pytest.mark.django_db(transaction=True)]

VALVE_S = 3.0

urlpatterns = [path("bg/", BackgroundPage.as_view())]

VIEW = "djust.tests.test_exposure_sse_resilience_3200.BackgroundPage"


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


async def _start(key, sid=None):
    sid = sid or str(uuid.uuid4())
    request = await sync_to_async(_request)(
        "GET", f"/djust/sse/{sid}/", {"view": VIEW, "_djust_url": "/bg/"}, key
    )
    response = await DjustSSEStreamView().get(request, session_id=sid)
    assert response.status_code == 200
    session = _sse_sessions[sid]
    while not session.queue.empty():
        session.queue.get_nowait()
    return session


async def _post(session, key, body=None):
    request = await sync_to_async(_request)(
        "POST",
        f"/djust/sse/{session.session_id}/message/",
        body or {"type": "event", "event": "increment", "params": {}},
        key,
    )
    async with ThreadSensitiveContext():  # what Django's ASGI handler wraps
        return await DjustSSEMessageView().post(request, session_id=session.session_id)


@pytest.fixture
def hung_store(monkeypatch):
    """Saves of the given session keys block until released (a valve releases
    them after a few seconds so a regression cannot hang the run)."""
    release = threading.Event()
    blocked = set()
    original = SessionStore.save

    def save(self, *args, **kwargs):
        if self.session_key in blocked and not release.is_set():
            release.wait(timeout=30)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(SessionStore, "save", save)
    valve = threading.Timer(VALVE_S, release.set)
    valve.start()
    yield blocked, release
    release.set()
    valve.cancel()


# ---- B2 ------------------------------------------------------------------


async def test_one_hung_store_write_does_not_stall_other_sessions(hung_store):
    blocked, release = hung_store
    key_a = await sync_to_async(_fresh_key)()
    key_b = await sync_to_async(_fresh_key)()
    a = await _start(key_a)
    b = await _start(key_b)
    blocked.add(key_a)

    assert (await _post(a, key_a)).status_code == 200  # deferred; its save holds a thread
    t0 = time.monotonic()
    rb = await _post(b, key_b)
    elapsed = time.monotonic() - t0
    released_first = release.is_set()

    assert rb.status_code == 200
    assert not released_first and elapsed < 1.0, (
        f"B's event took {elapsed:.2f}s and only finished after A's store answered"
    )
    frames = [f for f in list(b.queue._queue) if f]
    assert not [f for f in frames if f.get("type") == "error"], frames


async def test_a_save_that_cannot_get_a_thread_is_deferred(monkeypatch):
    """Every thread busy: the turn is deferred at the start bound instead of
    waiting indefinitely, and the queued save still runs later, ordered."""
    one = runtime_module._SaveExecutor(max_workers=1, thread_name_prefix="test-3212-save")
    monkeypatch.setattr(runtime_module, "_save_executor", one)
    monkeypatch.setattr(runtime_module, "_SAVE_START_TIMEOUT_S", 0.2)
    gate = threading.Event()
    one.submit(gate.wait, 30)  # the only thread is busy
    ran = []

    class Owner:
        _explicit_save_pending = None
        _explicit_save_started_at = 0.0

    owner = Owner()
    try:
        async with ThreadSensitiveContext():
            with pytest.raises(runtime_module.ExplicitSaveDeferred):
                await runtime_module._run_explicit_save(
                    owner, lambda: ran.append("first"), deadline=5.0
                )
        assert ran == []
        queued = owner._explicit_save_pending
    finally:
        gate.set()
    await asyncio.wait_for(queued, 5)
    assert ran == ["first"]
    one.shutdown()


# ---- B1 ------------------------------------------------------------------


def _single_save_thread(name):
    """One save thread, so consecutive jobs share a thread (and its connection)."""
    return runtime_module._SaveExecutor(max_workers=1, thread_name_prefix=name)


def _closable(connection):
    """SQLite ignores close() on the in-memory test database. Let this
    thread's wrapper close for real, as it would on Postgres: a new connection
    reattaches to the shared-cache database the test thread keeps open."""
    connection.is_in_memory_db = lambda: False


async def test_each_save_job_closes_old_connections():
    """``CONN_MAX_AGE=0`` means one connection per request. A save job gets the
    same treatment: its thread's connection does not outlive the job."""
    from django.db import connection

    loop = asyncio.get_running_loop()
    executor = _single_save_thread("test-3212-age")

    def touch():
        _closable(connection)
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        return connection.settings_dict["CONN_MAX_AGE"]

    try:
        max_age = await loop.run_in_executor(executor, touch)
        # A raw submit: what is left on the thread once the job's own
        # bracket has run, before the next job's opening close.
        leftover = await asyncio.wrap_future(
            runtime_module.ThreadPoolExecutor.submit(executor, lambda: connection.connection)
        )
    finally:
        executor.shutdown()
    assert max_age == 0
    assert leftover is None, "the thread kept its connection past the job"


async def test_a_broken_connection_does_not_outlive_its_job():
    """The server dropped the socket under a save thread (a failover, an idle
    timeout). The next job on that thread gets a working connection."""
    from django.db import connection

    loop = asyncio.get_running_loop()
    executor = _single_save_thread("test-3212-db")

    def query():
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            return cursor.fetchone()[0]

    def break_it():
        _closable(connection)
        query()
        connection.connection.close()

    try:
        await loop.run_in_executor(executor, break_it)
        results = []
        for _ in range(3):
            try:
                results.append(await loop.run_in_executor(executor, query))
            except Exception as exc:  # noqa: BLE001 - the result is the assertion
                results.append(type(exc).__name__)
    finally:
        executor.shutdown()
    assert results == [1, 1, 1], results


async def test_a_request_scoped_save_runs_on_the_hygienic_pool(monkeypatch):
    """The real spawn path uses the pool, and each job is bracketed."""
    import django.db

    calls = []
    real = django.db.close_old_connections

    def spy():
        calls.append(threading.current_thread().name)
        return real()

    monkeypatch.setattr(django.db, "close_old_connections", spy)
    seen = []
    async with ThreadSensitiveContext():
        await runtime_module._spawn_save(lambda: seen.append(threading.current_thread().name))
    [name] = seen
    assert name.startswith("djust-state-save")
    assert calls == [name, name]


# ---- N3 ------------------------------------------------------------------


async def test_a_reconnect_is_ordered_after_the_old_sessions_running_save(hung_store):
    blocked, release = hung_store
    key = await sync_to_async(_fresh_key)()
    old = await _start(key)
    blocked.add(key)
    assert (await _post(old, key)).status_code == 200  # deferred; its save runs on
    pending = old.runtime._explicit_save_pending
    assert pending is not None and not pending.done()

    blocked.discard(key)  # the reconnect's own mount must not block
    new = await _start(key, sid=old.session_id)

    assert new is not old
    assert new.runtime._explicit_save_pending is pending


async def test_a_navigation_is_ordered_after_the_old_pages_running_save(hung_store):
    blocked, release = hung_store
    key = await sync_to_async(_fresh_key)()
    session = await _start(key)
    blocked.add(key)
    assert (await _post(session, key)).status_code == 200
    old_runtime = session.runtime
    pending = old_runtime._explicit_save_pending
    assert pending is not None and not pending.done()

    blocked.discard(key)
    response = await _post(session, key, {"type": "live_redirect_mount", "url": "/bg/"})
    assert response.status_code == 200
    assert session.runtime is not old_runtime
    assert session.runtime._explicit_save_pending is pending
