"""Regression test for djust #1430.

`zstandard.ZstdCompressor` / `ZstdDecompressor` are not thread-safe when
shared across threads (python-zstandard #244, closed "by design"). The
prior `RedisStateBackend.__init__` stored a single instance of each on
`self`, so concurrent callers raced on the C-level state. Symptoms:
"decompression error: Unknown frame descriptor", "Data corruption
detected", and — under the right kernel + libc combination — outright
SIGSEGV inside `ZSTD_decompressSequencesLong_default`.

The fix moves both objects into a `threading.local`, so each thread
gets its own. This test hammers `_compress` / `_decompress` from many
threads and asserts no exception escapes.

The test does NOT require a running Redis instance — it exercises the
compression code path directly, which is where the race lived.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from djust.state_backends.base import (
    COMPRESSION_MARKER,
    NO_COMPRESSION_MARKER,
    ZSTD_AVAILABLE,
)

if not ZSTD_AVAILABLE:
    pytest.skip("zstandard not installed", allow_module_level=True)


def _make_backend(monkeypatch):
    """Construct a RedisStateBackend without touching a real Redis.

    We skip __init__'s ping by stubbing redis.from_url to a mock that
    has a no-op .ping(). Everything we exercise is the compression path,
    which doesn't talk to Redis.
    """
    import redis as redis_mod

    from djust.state_backends.redis import RedisStateBackend

    class _FakeClient:
        def ping(self):
            return True

    monkeypatch.setattr(redis_mod, "from_url", lambda url: _FakeClient())
    return RedisStateBackend(
        redis_url="redis://test/0",
        compression_enabled=True,
        compression_threshold_kb=0,  # always compress so the race surface is hit
    )


def test_concurrent_compress_decompress_no_race(monkeypatch):
    """Hammer _compress and _decompress from 16 threads, 250 ops each.

    On the pre-fix code (shared ZstdCompressor / ZstdDecompressor on
    self), this typically raises within the first few hundred ops with
    one of:
      - ZstdError("decompression error: Unknown frame descriptor")
      - ZstdError("decompression error: Data corruption detected")
      - ValueError("cannot compress: Operation not authorized ...")

    On the fixed code (threading.local), it completes cleanly.
    """
    backend = _make_backend(monkeypatch)

    # Realistic-sized payload — bigger than the compression threshold so
    # _compress actually invokes the C extension.
    payload = (b"x" * 50_000) + b"djust-state-backend-race-test"

    errors: list[BaseException] = []
    barrier = threading.Barrier(16)

    def hammer():
        barrier.wait()  # maximize concurrency on the first call
        try:
            for _ in range(250):
                blob = backend._compress(payload)
                # Sanity: marker byte is present.
                assert blob[:1] in (COMPRESSION_MARKER, NO_COMPRESSION_MARKER)
                got = backend._decompress(blob)
                assert got == payload
        except BaseException as exc:  # noqa: BLE001 — we want SIGSEGV-survivors too
            errors.append(exc)

    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = [pool.submit(hammer) for _ in range(16)]
        for f in futures:
            f.result()

    assert not errors, (
        f"shared zstd objects raced — got {len(errors)} exception(s); first: {errors[0]!r}"
    )


def test_per_thread_decompressor_identity(monkeypatch):
    """Each thread should see a distinct ZstdDecompressor instance.

    Guards against a future refactor that accidentally reverts to a
    single shared object. We hold all threads at a barrier so they're
    concurrently alive when they call `_get_decompressor()` — without
    that, threading.get_ident() can recycle TIDs across short-lived
    threads.
    """
    backend = _make_backend(monkeypatch)

    n = 8
    barrier = threading.Barrier(n)
    release = threading.Event()
    seen_ids: list[int] = []
    lock = threading.Lock()

    def grab():
        d = backend._get_decompressor()
        with lock:
            seen_ids.append(id(d))
        barrier.wait()  # everyone has grabbed
        release.wait()  # hold the threads alive until main releases

    threads = [threading.Thread(target=grab) for _ in range(n)]
    for t in threads:
        t.start()
    # Wait for all n threads to have grabbed before letting them exit.
    # Plain time.sleep — allocating a fresh threading.Event per iteration
    # is wasteful (Stage 11 review #1431).
    while True:
        with lock:
            if len(seen_ids) == n:
                break
        time.sleep(0.01)
    release.set()
    for t in threads:
        t.join()

    # n threads were alive concurrently when each called the accessor;
    # each should have gotten its own decompressor identity.
    assert len(set(seen_ids)) == n, (
        f"expected {n} distinct decompressor objects, got {len(set(seen_ids))}"
    )


def test_per_thread_compressor_identity(monkeypatch):
    """Symmetric to the decompressor identity test: each thread should
    see a distinct ZstdCompressor instance. The fix lives or dies on
    BOTH paths — only-decompressor coverage would let a future refactor
    re-introduce a single shared compressor undetected.
    """
    backend = _make_backend(monkeypatch)

    n = 8
    barrier = threading.Barrier(n)
    release = threading.Event()
    seen_ids: list[int] = []
    lock = threading.Lock()

    def grab():
        c = backend._get_compressor()
        with lock:
            seen_ids.append(id(c))
        barrier.wait()
        release.wait()

    threads = [threading.Thread(target=grab) for _ in range(n)]
    for t in threads:
        t.start()
    while True:
        with lock:
            if len(seen_ids) == n:
                break
        time.sleep(0.01)
    release.set()
    for t in threads:
        t.join()

    assert len(set(seen_ids)) == n, (
        f"expected {n} distinct compressor objects, got {len(set(seen_ids))}"
    )


def test_decompressor_reused_within_thread(monkeypatch):
    """A single thread should reuse its ZstdDecompressor instead of
    constructing a fresh one per call (the construction cost adds up
    on hot paths)."""
    backend = _make_backend(monkeypatch)
    first = backend._get_decompressor()
    second = backend._get_decompressor()
    assert first is second


def test_compressor_reused_within_thread(monkeypatch):
    """Symmetric: same-thread compressor calls return the same instance."""
    backend = _make_backend(monkeypatch)
    first = backend._get_compressor()
    second = backend._get_compressor()
    assert first is second
