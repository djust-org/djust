"""Bounded threads under an HTTP overload burst (#3114).

Django's ``ASGIHandler`` runs every request inside asgiref's
``ThreadSensitiveContext()``, which gives each request a NEW thread. Under an
overload burst that is one thread per in-flight request. On free-threaded
CPython each thread gets its own allocator heap, and the memory stays resident
after the thread exits: 256 concurrent page GETs took the snake-arena process
from 81 MB to 1.26 GB of RSS.

``djust.worker_pool.PooledHTTP`` binds each HTTP request to one thread of a
bounded pool before Django's handler runs. ``ThreadSensitiveContext`` is
re-entrant, so the handler then reuses that thread. These tests drive Django's
real ``ASGIHandler``:

* a burst of concurrent requests runs on at most ``threads`` threads (and
  without the wrapper, on one thread per request: the control);
* the pool is off, or the scope is not ``http`` -> pass-through, unchanged;
* an outer context that already chose a thread is kept;
* the WebSocket-only event-loop offload stays off on the HTTP path;
* slots are released, also when the app raises;
* pool threads start from an empty context, so on 3.14+ (where a new thread
  copies its starter's context) they don't keep the first caller's context
  values alive.
"""

from __future__ import annotations

import asyncio
import contextvars
import gc
import sys
import threading
import time
import weakref

import pytest
from django.http import HttpResponse
from django.test import override_settings
from django.urls import path

from djust.config import config as djust_config

_SEEN: list = []
_SEEN_LOCK = threading.Lock()


def _slow_view(request):
    with _SEEN_LOCK:
        _SEEN.append(threading.get_ident())
    # Long enough that the whole burst is in flight at once.
    time.sleep(0.02)
    return HttpResponse("ok")


urlpatterns = [path("slow/", _slow_view)]


@pytest.fixture
def pools(monkeypatch):
    """Fresh WS and HTTP pools for one test; ``worker_threads`` settable."""
    from djust import worker_pool

    monkeypatch.setattr(worker_pool, "_pool", [])
    monkeypatch.setattr(worker_pool, "_http_pool", [])
    _SEEN.clear()

    def _set(value):
        monkeypatch.setitem(djust_config._config, "worker_threads", value)

    _set(None)
    yield _set
    _SEEN.clear()


async def _request(app, url="/slow/"):
    """One HTTP request through an ASGI app; returns the status code."""
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": url,
        "raw_path": url.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"testserver")],
        "client": ("127.0.0.1", 1234),
        "server": ("testserver", 80),
    }
    sent: list = []
    body_sent = False

    async def receive():
        nonlocal body_sent
        if not body_sent:
            body_sent = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await asyncio.sleep(3600)  # no disconnect while the response runs

    async def send(message):
        sent.append(message)

    await app(scope, receive, send)
    return next(m["status"] for m in sent if m["type"] == "http.response.start")


def _django_app():
    from django.core.handlers.asgi import ASGIHandler

    return ASGIHandler()


async def _burst(app, n=48):
    before = threading.active_count()
    peak = before
    stop = False

    async def watch():
        nonlocal peak
        while not stop:
            peak = max(peak, threading.active_count())
            await asyncio.sleep(0.002)

    watcher = asyncio.ensure_future(watch())
    try:
        statuses = await asyncio.gather(*(_request(app) for _ in range(n)))
    finally:
        stop = True
        await watcher
    return statuses, peak - before


@override_settings(ROOT_URLCONF=__name__, ALLOWED_HOSTS=["testserver"])
@pytest.mark.asyncio
async def test_without_the_wrapper_a_burst_uses_one_thread_per_request(pools):
    """The control: Django's handler alone creates a thread per request."""
    statuses, _grew = await _burst(_django_app())
    assert statuses == [200] * 48
    assert len(set(_SEEN)) > 3


@override_settings(ROOT_URLCONF=__name__, ALLOWED_HOSTS=["testserver"])
@pytest.mark.asyncio
async def test_pooled_http_bounds_a_burst_to_the_pool(pools):
    from djust import worker_pool
    from djust.worker_pool import PooledHTTP

    statuses, grew = await _burst(PooledHTTP(_django_app(), threads=3))
    assert statuses == [200] * 48
    # Every request's sync work ran on one of the 3 pool threads ...
    assert len(set(_SEEN)) <= 3
    names = {t.name for t in threading.enumerate() if t.ident in set(_SEEN)}
    assert names and all(n.startswith("djust-http-") for n in names)
    # ... and the burst started no more than the pool's own threads.
    assert grew <= 3
    # Every binding was released.
    assert [s["requests"] for s in worker_pool.http_pool_stats()] == [0, 0, 0]


@override_settings(ROOT_URLCONF=__name__, ALLOWED_HOSTS=["testserver"])
@pytest.mark.asyncio
async def test_threads_none_follows_worker_threads(pools):
    from djust import worker_pool
    from djust.worker_pool import PooledHTTP

    pools(2)
    app = PooledHTTP(_django_app())
    statuses, _grew = await _burst(app, n=16)
    assert statuses == [200] * 16
    assert len(set(_SEEN)) <= 2
    assert len(worker_pool.http_pool_stats()) == 2
    # The HTTP pool is separate from the WebSocket sessions' pool.
    assert worker_pool.pool_stats() == []


@override_settings(ROOT_URLCONF=__name__, ALLOWED_HOSTS=["testserver"])
@pytest.mark.asyncio
@pytest.mark.parametrize("worker_threads, threads", [(None, None), (0, None), (4, 0)])
async def test_pool_off_is_a_pass_through(pools, worker_threads, threads):
    """``worker_threads`` off (the default) and ``threads=None``, or
    ``threads=0``: Django's own thread-per-request behaviour, unchanged."""
    from djust import worker_pool
    from djust.worker_pool import PooledHTTP

    pools(worker_threads)
    statuses, _grew = await _burst(PooledHTTP(_django_app(), threads=threads), n=16)
    assert statuses == [200] * 16
    assert worker_pool.http_pool_stats() == []
    assert len(set(_SEEN)) > 2


@pytest.mark.asyncio
@pytest.mark.parametrize("scope_type", ["websocket", "lifespan"])
async def test_other_scopes_pass_through_unbound(pools, scope_type):
    from asgiref.sync import SyncToAsync

    from djust import worker_pool
    from djust.worker_pool import PooledHTTP

    seen = {}

    async def inner(scope, receive, send):
        seen["context"] = SyncToAsync.thread_sensitive_context.get(None)

    await PooledHTTP(inner, threads=2)({"type": scope_type}, None, None)
    assert seen == {"context": None}
    assert worker_pool.http_pool_stats() == []


@pytest.mark.asyncio
async def test_an_outer_thread_sensitive_context_is_kept(pools):
    from asgiref.sync import SyncToAsync, ThreadSensitiveContext

    from djust import worker_pool
    from djust.worker_pool import PooledHTTP

    seen = {}

    async def inner(scope, receive, send):
        seen["context"] = SyncToAsync.thread_sensitive_context.get(None)

    async with ThreadSensitiveContext() as outer:
        await PooledHTTP(inner, threads=2)({"type": "http"}, None, None)
    assert seen["context"] is outer
    assert worker_pool.http_pool_stats() == []


@pytest.mark.asyncio
async def test_event_loop_offload_stays_off_for_http(pools):
    """``offload_enabled()`` is the WebSocket consumer's switch; an HTTP slot
    must not turn it on."""
    from djust.worker_pool import PooledHTTP, offload_enabled

    seen = {}

    async def inner(scope, receive, send):
        from asgiref.sync import SyncToAsync

        seen["bound"] = SyncToAsync.thread_sensitive_context.get(None) is not None
        seen["offload"] = offload_enabled()

    await PooledHTTP(inner, threads=2)({"type": "http"}, None, None)
    assert seen == {"bound": True, "offload": False}


@pytest.mark.asyncio
async def test_the_slot_is_released_when_the_app_raises(pools):
    from djust import worker_pool
    from djust.worker_pool import PooledHTTP

    async def inner(scope, receive, send):
        assert sorted(s["requests"] for s in worker_pool.http_pool_stats()) == [0, 1]
        raise RuntimeError("view blew up")

    with pytest.raises(RuntimeError, match="view blew up"):
        await PooledHTTP(inner, threads=2)({"type": "http"}, None, None)
    assert [s["requests"] for s in worker_pool.http_pool_stats()] == [0, 0]


def test_threads_value_is_validated():
    from djust.worker_pool import PooledHTTP

    with pytest.raises(ValueError, match="threads"):
        PooledHTTP(lambda *a: None, threads=-1)
    with pytest.raises(ValueError, match="threads"):
        PooledHTTP(lambda *a: None, threads="8")


_MARKER: contextvars.ContextVar = contextvars.ContextVar("djust_test_3114_marker")


class _Session:
    """Stands in for a consumer that a context value refers to."""


def _pool_threads(executors):
    threads = []
    for ex in executors:
        threads.extend(getattr(ex, "_threads", ()))
    return threads


@pytest.mark.parametrize("which", ["websocket", "http"])
def test_pool_threads_start_from_an_empty_context(pools, which):
    """On 3.14+ a new thread copies its starter's context
    (``sys.flags.thread_inherit_context``), and a pool thread lives as long as
    the process. Started lazily by the first session's call, it kept that
    session's context values -- and the session they refer to -- alive
    forever. The pool now starts its threads up front, in an empty context."""
    from asgiref.sync import SyncToAsync

    from djust import worker_pool

    session = _Session()
    ref = weakref.ref(session)
    token = _MARKER.set(session)
    try:
        if which == "websocket":
            pools(2)
            binding = worker_pool.bind_session()
            slots = list(worker_pool._pool)
        else:
            binding = worker_pool.bind_http_request(2)
            slots = list(worker_pool._http_pool)
        binding.release()
    finally:
        _MARKER.reset(token)
    executors = [SyncToAsync.context_to_thread_executor[s] for s in slots]
    threads = _pool_threads(executors)
    assert len(threads) == 2, "the pool starts its threads when it is created"
    for t in threads:
        ctx = getattr(t, "_context", None)
        if ctx is not None:  # Python 3.14+: the context the thread runs in
            assert _MARKER not in ctx
    del session, binding
    gc.collect()
    assert ref() is None, "a pool thread kept the creating caller's context alive"


@pytest.mark.skipif(
    not getattr(sys.flags, "thread_inherit_context", 0),
    reason="threads copy their starter's context only where thread_inherit_context is on (3.14t)",
)
def test_control_a_plain_executor_thread_inherits_the_context():
    """The control for the test above, where it is meaningful."""
    from concurrent.futures import ThreadPoolExecutor

    session = _Session()
    token = _MARKER.set(session)
    try:
        ex = ThreadPoolExecutor(max_workers=1)
        ex.submit(lambda: None).result()
    finally:
        _MARKER.reset(token)
    (thread,) = _pool_threads([ex])
    assert _MARKER in thread._context
    ex.shutdown()
