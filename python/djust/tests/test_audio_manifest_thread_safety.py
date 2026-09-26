"""AudioMixin's per-view manifest cache under concurrency (free-threaded 3.14t).

``AudioMixin`` builds its sound manifest once per view and reuses it on every
render. With ``LIVEVIEW_CONFIG["worker_threads"]`` and several event loops,
renders run on many threads at once, and on 3.14t nothing serialises them but
djust's own locks. These tests drive the cache from several threads at once:
the manifest must be built exactly once per view, every render must see the
same string, and different views must not share or block each other's builds.
They run in the 3.14t CI job (``python-free-threaded`` in test.yml).
"""

from __future__ import annotations

import gc
import json
import sys
import threading
import time
import weakref

import pytest

from djust import audio
from djust.audio import AudioMixin, Sound, SoundBank

N_THREADS = 16


@pytest.fixture(autouse=True)
def _switch_often():
    """Switch threads every microsecond so a GIL build interleaves the cache's
    check-then-build often; on a free-threaded build it races for real."""
    old = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    yield
    sys.setswitchinterval(old)


@pytest.fixture
def slow_static(monkeypatch):
    """Count ``static()`` calls and widen the build window, so a racing
    second build would be caught rather than slip through."""
    calls: list = []
    lock = threading.Lock()

    def fake_static(path):
        with lock:
            calls.append(path)
        time.sleep(0.002)
        return f"/static/{path}"

    monkeypatch.setattr(audio, "static", fake_static)
    return calls


class _Base:
    def __init__(self, **kwargs):
        pass

    def get_context_data(self, **kwargs):
        return {}


class _View(AudioMixin, _Base):
    audio_banks = {
        "sfx": SoundBank({f"s{i}": Sound(f"sound/s{i}.wav") for i in range(4)}),
    }


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


def test_concurrent_first_renders_of_one_view_build_the_manifest_once(slow_static):
    view = _View()
    results: list = [None] * N_THREADS

    def render(i):
        results[i] = view.get_context_data()["djust_audio_manifest"]

    _run(N_THREADS, render)

    # One build: each of the bank's four sounds resolved exactly once.
    assert sorted(slow_static) == [f"sound/s{i}.wav" for i in range(4)]
    # Every thread got the very same string, and it is complete.
    assert all(r is results[0] for r in results)
    manifest = json.loads(results[0])
    assert set(manifest["banks"]["sfx"]["sounds"]) == {f"s{i}" for i in range(4)}


def test_concurrent_renders_of_many_views_each_build_their_own(slow_static):
    views = [_View() for _ in range(N_THREADS)]
    results: list = [None] * N_THREADS

    def render(i):
        for _ in range(3):
            results[i] = views[i].get_context_data()["djust_audio_manifest"]

    _run(N_THREADS, render)

    # Four lookups per view, once each, however the threads interleaved.
    assert len(slow_static) == 4 * N_THREADS
    scopes = [json.loads(r)["scope"] for r in results]
    assert scopes == [v._audio_scope for v in views]
    assert len(set(scopes)) == N_THREADS


def test_views_on_different_stripes_build_in_parallel(monkeypatch):
    """A build holds only its own view's stripe: while one view's build is
    stalled, a view on another stripe still builds."""
    started = threading.Event()
    release = threading.Event()
    stalled = _View()

    def fake_static(path):
        if threading.current_thread().name == "stalled":
            started.set()
            assert release.wait(10)
        return f"/static/{path}"

    monkeypatch.setattr(audio, "static", fake_static)
    locks = audio._MANIFEST_BUILD_LOCKS

    def stripe(view):
        return (id(view) >> 4) % len(locks)

    other = _View()
    keep = []
    while stripe(other) == stripe(stalled):
        keep.append(other)
        other = _View()

    t = threading.Thread(target=stalled.get_context_data, name="stalled")
    t.start()
    try:
        assert started.wait(10)
        assert json.loads(other.get_context_data()["djust_audio_manifest"])["scope"] == (
            other._audio_scope
        )
    finally:
        release.set()
        t.join(10)


def test_the_cache_pins_the_banks_its_key_names_by_id():
    """The cache key holds ``id(bank)``. A freed bank's address can be reused
    by the next bank allocated, which would then match the stale key and serve
    the old manifest; so the entry keeps its banks alive until it is replaced."""
    view = _View()
    view.audio_banks = {"sfx": SoundBank({"old": Sound("sound/old.wav")})}
    view.get_context_data()
    old = weakref.ref(view.audio_banks["sfx"])
    del view.audio_banks  # the view's own reference is gone
    gc.collect()
    assert old() is not None, "the cached key names a bank that is no longer alive"

    view.audio_banks = {"sfx": SoundBank({"new": Sound("sound/new.wav")})}
    sounds = json.loads(view.get_context_data()["djust_audio_manifest"])["banks"]["sfx"]
    assert list(sounds["sounds"]) == ["new"]
    gc.collect()
    assert old() is None, "a replaced entry must release the old banks"
