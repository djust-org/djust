"""#3080: ``InMemoryStateBackend`` enforces its TTL.

``default_ttl`` / ``SESSION_TTL`` used to apply only inside
``cleanup_expired()``, which nothing called at runtime (only the ``djust
clear`` CLI). Every session's ``RustLiveView`` therefore stayed in memory for
the life of the process: under snake-arena load each session left about
270 KB of live heap behind, and a ``SESSION_TTL`` of 60 s changed nothing
(entries 65 -> 129 -> 193 -> 257 across 64-client cycles).

Now an entry not written for the TTL is a miss on ``get()`` and is dropped,
and ``set()`` sweeps expired entries at most once per ``min(ttl, 60)`` s.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from djust._rust import RustLiveView
from djust.state_backends import memory as memory_mod
from djust.state_backends.memory import InMemoryStateBackend


def _view() -> RustLiveView:
    return RustLiveView("<div>{{ n }}</div>", [])


class _Clock:
    """Drives both clocks the backend reads (wall for entry age, monotonic for sweeps)."""

    def __init__(self) -> None:
        self.wall = 1_000_000.0
        self.mono = 5_000.0

    def advance(self, seconds: float) -> None:
        self.wall += seconds
        self.mono += seconds


@pytest.fixture
def clock(monkeypatch):
    c = _Clock()
    # Replace the module's ``time`` binding, not the global time functions.
    monkeypatch.setattr(
        memory_mod, "time", SimpleNamespace(time=lambda: c.wall, monotonic=lambda: c.mono)
    )
    return c


def test_get_of_an_expired_entry_is_a_miss_and_frees_it(clock):
    backend = InMemoryStateBackend(default_ttl=60)
    backend.set("old", _view())
    clock.advance(61)
    assert backend.get("old") is None
    assert "old" not in backend._cache
    assert "old" not in backend._state_sizes


def test_get_of_a_live_entry_still_hits(clock):
    backend = InMemoryStateBackend(default_ttl=60)
    backend.set("live", _view())
    clock.advance(59)
    hit = backend.get("live")
    assert hit is not None
    assert isinstance(hit[0], RustLiveView)


def test_set_sweeps_expired_entries_once_the_interval_passes(clock):
    backend = InMemoryStateBackend(default_ttl=60)
    for i in range(5):
        backend.set(f"s{i}", _view())
    clock.advance(61)
    backend.set("fresh", _view())  # first set past the interval runs the sweep
    assert set(backend._cache) == {"fresh"}
    assert set(backend._state_sizes) <= {"fresh"}


def test_sweep_runs_at_most_once_per_interval(clock, monkeypatch):
    backend = InMemoryStateBackend(default_ttl=600)  # interval capped at 60 s
    calls = []
    real = backend._remove_expired
    monkeypatch.setattr(backend, "_remove_expired", lambda ttl: calls.append(ttl) or real(ttl))
    backend.set("a", _view())
    assert calls == []  # interval not reached yet
    clock.advance(61)
    backend.set("b", _view())
    backend.set("c", _view())
    backend.set("d", _view())
    assert calls == [600]  # one sweep for three sets inside the same interval
    clock.advance(61)
    backend.set("e", _view())
    assert calls == [600, 600]


def test_sweep_keeps_entries_younger_than_the_ttl(clock):
    backend = InMemoryStateBackend(default_ttl=120)
    backend.set("old", _view())
    clock.advance(100)
    backend.set("young", _view())  # sweeps (old is 100 s: kept)
    clock.advance(61)  # old is 161 s, young 61 s; the next sweep is due
    backend.set("newest", _view())
    assert set(backend._cache) == {"young", "newest"}


def test_rewriting_an_entry_restarts_its_ttl(clock):
    backend = InMemoryStateBackend(default_ttl=60)
    backend.set("k", _view())
    clock.advance(50)
    backend.set("k", _view())  # the WebSocket mount re-saves on a cache hit
    clock.advance(50)
    assert backend.get("k") is not None


@pytest.mark.parametrize("ttl", [0, -1])
def test_non_positive_ttl_never_expires(clock, ttl):
    backend = InMemoryStateBackend(default_ttl=ttl)
    backend.set("k", _view())
    clock.advance(10 * 365 * 24 * 3600)
    backend.set("other", _view())
    assert backend.get("k") is not None
    assert set(backend._cache) == {"k", "other"}


def test_real_clock_smoke():
    """No clock patching: a fresh entry is a hit and the sweep is not due."""
    backend = InMemoryStateBackend(default_ttl=3600)
    backend.set("k", _view())
    assert backend.get("k") is not None
    assert backend._next_sweep > time.monotonic()


@pytest.mark.parametrize(
    ("raw", "expected"), [("60", 60), (60.0, 60), (None, 3600), ("soon", 3600)]
)
def test_ttl_that_is_not_an_int_is_coerced_not_fatal(raw, expected):
    """A TTL from the environment (a string) must not break every mount."""
    backend = InMemoryStateBackend(default_ttl=raw)
    assert backend._default_ttl == expected
    backend.set("k", _view())
    assert backend.get("k") is not None
