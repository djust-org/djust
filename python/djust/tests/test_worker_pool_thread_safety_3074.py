"""Shared state that sessions' sync code touches, made safe for several threads (#3074).

With ``LIVEVIEW_CONFIG["worker_threads"]`` two sessions' handlers and renders
run at the same time on different threads (they already could beside an HTTP
request thread). The audit for that PR found these read-modify-write sites;
each test drives the site from several threads at once.
"""

from __future__ import annotations

import sys
import threading
import time

import pytest


@pytest.fixture(autouse=True)
def _switch_often():
    """Switch threads every microsecond so a GIL build interleaves the
    read-modify-write sequences often enough to lose updates. On a
    free-threaded build they race for real."""
    old = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    yield
    sys.setswitchinterval(old)


def _run(n_threads, target):
    barrier = threading.Barrier(n_threads)
    errors: list = []

    def wrapper(i):
        try:
            barrier.wait(10)
            target(i)
        except Exception as exc:  # noqa: BLE001 - reported below
            errors.append(repr(exc))

    threads = [threading.Thread(target=wrapper, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert not errors, errors


def test_encoder_depth_is_per_thread():
    """One thread's serialisation depth is invisible to another thread."""
    from djust.serialization import DjangoJSONEncoder

    seen: dict = {}
    inside = threading.Event()
    release = threading.Event()

    def deep():
        DjangoJSONEncoder._depth += 5
        inside.set()
        release.wait(5)
        DjangoJSONEncoder._depth -= 5

    t = threading.Thread(target=deep)
    t.start()
    inside.wait(5)
    try:
        seen["other"] = DjangoJSONEncoder._depth
    finally:
        release.set()
        t.join(5)
    assert seen["other"] == 0
    assert DjangoJSONEncoder._depth == 0


def test_encoder_depth_instance_spelling_reads_the_same_counter():
    """``self._depth`` (a subclass reading it) is the class-level per-thread
    value, and writing it through the instance leaves nothing on the instance."""
    from djust.serialization import DjangoJSONEncoder

    enc = DjangoJSONEncoder()
    assert enc._depth == 0
    enc._depth += 2
    try:
        assert DjangoJSONEncoder._depth == 2
        assert "_depth" not in vars(enc)
    finally:
        DjangoJSONEncoder._depth = 0
    assert enc._depth == 0


def test_backend_registry_builds_one_backend_under_concurrent_first_use():
    from djust.utils import BackendRegistry

    built: list = []

    def factory(kind, cfg):
        time.sleep(0.02)  # widen the check-then-create window
        obj = object()
        built.append(obj)
        return obj

    reg = BackendRegistry("TEST_BACKEND_3074", "memory", factory, warn_on_default=False)
    got: list = []
    _run(8, lambda i: got.append(reg.get()))
    assert len(built) == 1
    assert all(g is built[0] for g in got)


def test_tenant_memory_presence_keeps_every_concurrent_join():
    from djust.tenants.backends import TenantAwareMemoryBackend

    tenant = "tenant-3074"
    TenantAwareMemoryBackend.clear_tenant(tenant)
    try:

        def join(i):
            backend = TenantAwareMemoryBackend(tenant_id=tenant)
            for j in range(50):
                backend.join("room", f"user-{i}-{j}", {})

        _run(8, join)
        assert TenantAwareMemoryBackend(tenant_id=tenant).count("room") == 8 * 50
    finally:
        TenantAwareMemoryBackend.clear_tenant(tenant)


def test_tenant_presence_manager_builds_one_backend_per_tenant():
    from djust.tenants.backends import TenantPresenceManager

    TenantPresenceManager.clear_cache()
    try:
        got: list = []
        _run(8, lambda i: got.append(TenantPresenceManager.for_tenant("t-3074")))
        assert len({id(g) for g in got}) == 1
    finally:
        TenantPresenceManager.clear_cache()


@pytest.mark.django_db
def test_cursor_updates_from_several_threads_are_all_kept():
    from django.core.cache import cache

    from djust.presence import CursorTracker

    key = "cursors-3074"
    cache.delete(CursorTracker.cursor_cache_key(key))
    _run(8, lambda i: [CursorTracker.update_cursor(key, f"u{i}-{j}", i, j) for j in range(25)])
    assert len(CursorTracker.get_cursors(key)) == 8 * 25


def test_auto_component_keys_are_unique_across_threads():
    from djust.components.base import Component

    class _Probe(Component):
        template = "<span></span>"

    keys: list = []
    lock = threading.Lock()

    def build(i):
        mine = [_Probe()._component_key for _ in range(200)]
        with lock:
            keys.extend(mine)

    _run(8, build)
    assert len(keys) == len(set(keys)) == 8 * 200
