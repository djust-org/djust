"""Sockets of one session on one page do not share a Rust view (worker pool on).

The sockets of one session on one page use one state-backend key. The
in-memory backend stored the socket's own live ``RustLiveView`` under that
key, and every other socket's ``get()`` cloned it by calling
``serialize_msgpack()`` on that same object. On asgiref's single shared
thread those calls never overlapped. With ``LIVEVIEW_CONFIG["worker_threads"]``
they do: one thread serialized the view while its owner rendered it (the
render releases the GIL) or tore it down, and PyO3's borrow check refused:
``round-trip failed ... Already mutably borrowed``, ``set_raw_py_values failed
during teardown: Already borrowed``, and once a failed render.

The backend now stores a serialized snapshot, as the Redis backend does, so no
two sockets ever touch the same Rust object.
"""

from __future__ import annotations

import asyncio
import logging

import pytest
from asgiref.sync import sync_to_async
from django.test import override_settings

from djust import LiveView
from djust._rust import RustLiveView
from djust.config import config as djust_config
from djust.decorators import event_handler
from djust.state_backends.memory import InMemoryStateBackend

pytest.importorskip("channels")

VIEW = f"{__name__}._TablePage"
_BORROW_MARKERS = (
    "Already borrowed",
    "Already mutably borrowed",
    "round-trip failed",
    "failed during teardown",
)


class _TablePage(LiveView):
    """Big enough that a render takes a while (the race window)."""

    exposure_policy = "legacy"
    template = (
        "<div dj-root><h1>{{ title }}</h1><ul>"
        "{% for r in rows %}<li class=\"{% cycle 'a' 'b' %}\">{{ r.name|title }} "
        "{% if r.on %}<b>on</b>{% endif %}{{ r.score|floatformat:2 }}</li>{% endfor %}"
        "</ul><p>{{ picked }}</p></div>"
    )

    def mount(self, request, **kwargs):
        self.title = "rows"
        self.picked = 0
        self.rows = [{"name": "row %d" % i, "on": i % 2 == 0, "score": i * 1.5} for i in range(400)]

    @event_handler()
    def pick(self, **kwargs):
        self.picked += 1


@pytest.fixture
def pool(monkeypatch):
    from djust import worker_pool

    monkeypatch.setitem(djust_config._config, "worker_threads", 8)
    monkeypatch.setattr(worker_pool, "_pool", [])
    yield


@pytest.fixture
def memory_backend():
    from djust.state_backends import get_backend, set_backend

    previous = get_backend()
    backend = InMemoryStateBackend()
    set_backend(backend)
    yield backend
    set_backend(previous)


async def _session_key():
    from django.contrib.sessions.backends.db import SessionStore

    def create():
        s = SessionStore()
        s.create()
        return s.session_key

    return await sync_to_async(create)()


async def _one_page(key, results, delay=0.0, events=4):
    """Mount the page in this session, send one event, close."""
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    class _ScopeSession:
        session_key = key

    await asyncio.sleep(delay)
    comm = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    comm.scope["session"] = _ScopeSession()
    assert (await comm.connect())[0]
    try:
        await comm.receive_json_from(timeout=10)
        await comm.send_json_to({"type": "mount", "view": VIEW, "url": "/table/"})
        while True:
            frame = await comm.receive_json_from(timeout=30)
            if frame.get("type") in ("mount", "error"):
                results.append(frame["type"])
                break
        if frame["type"] == "mount":
            for ref in range(1, events + 1):
                await comm.send_json_to(
                    {"type": "event", "event": "pick", "params": {}, "ref": ref}
                )
                while str((await comm.receive_json_from(timeout=30)).get("ref")) != str(ref):
                    pass
    finally:
        await comm.disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_many_sockets_of_one_session_on_one_page_do_not_race(pool, memory_backend, caplog):
    key = await _session_key()
    results: list = []
    caplog.set_level(logging.WARNING)
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        # Staggered starts, so one socket's mount (a read of the shared key)
        # lands while others render events or tear down.
        await asyncio.gather(*(_one_page(key, results, delay=i * 0.005) for i in range(48)))
    borrow_errors = [
        r.getMessage() for r in caplog.records if any(m in r.getMessage() for m in _BORROW_MARKERS)
    ]
    assert borrow_errors == []
    assert results == ["mount"] * 48


def test_the_backend_never_hands_out_or_keeps_the_callers_object():
    """The unit form: a stored view can be rendered and mutated by its owner
    while other callers read the key, because the backend keeps no reference
    to it."""
    backend = InMemoryStateBackend()
    live = RustLiveView("<div>{{ x }}</div>")
    live.update_state({"x": "first"})
    backend.set("k", live, warn_on_large_state=False)
    # The owner keeps changing its own object after the save...
    live.update_state({"x": "second"})
    clone, _ = backend.get("k")
    # ...and the backend's copy is the state as saved, not the live object.
    assert clone is not live
    assert "first" in clone.render()
    assert all(entry is not live for entry, _ in backend._cache.values())


class _ReadsTheKeyDuringRender:
    """A template value resolved through Python while the owner renders.

    Its lookup runs inside the owner's render, which holds the Rust view
    mutably borrowed: exactly when another socket's mount reads the same key
    with the pool on. Reading the key here makes that overlap deterministic.
    """

    def __init__(self, backend):
        self.backend = backend
        self.reads = []

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        self.reads.append(self.backend.get("k"))
        return "read"


def test_a_read_of_the_key_while_its_saver_renders_succeeds(caplog):
    backend = InMemoryStateBackend()
    live = RustLiveView("<div>{{ count }}-{{ user.name }}</div>")
    live.update_state({"count": 1})
    backend.set("k", live, warn_on_large_state=False)
    sidecar = _ReadsTheKeyDuringRender(backend)
    live.set_raw_py_values({"user": sidecar})
    caplog.set_level(logging.WARNING)
    html = live.render()
    assert "1-read" in html
    assert sidecar.reads and all(r is not None for r in sidecar.reads), sidecar.reads
    assert not [r for r in caplog.records if "round-trip failed" in r.getMessage()]


class _CachedCounter(LiveView):
    exposure_policy = "legacy"
    template = "<div dj-root><p>{{ count }}</p></div>"

    def mount(self, request, **kwargs):
        self.count = 0


def test_a_render_leaves_the_latest_state_in_the_backend(rf, memory_backend):
    """The next request of the page (the HTTP page-POST fallback diffs against
    it) must start from the last render, as when the backend kept the live
    object, although it now keeps only snapshots."""
    from types import SimpleNamespace

    request = rf.get("/counter/")
    request.session = SimpleNamespace(session_key="one-session")
    view = _CachedCounter()
    view.request = request
    view.mount(request)
    view.render_with_diff(request)
    view.count = 7
    view.render_with_diff(request)
    (key,) = memory_backend._cache
    restored, _ = memory_backend.get(key)
    assert "<p>7</p>" in restored.render()


class _BrokenRender(_TablePage):
    def _sync_state_to_rust(self, *args, **kwargs):
        raise RuntimeError("Already borrowed")


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("workers", [None, 8])
async def test_a_render_failure_at_mount_answers_with_an_error_frame(workers, monkeypatch):
    """The client must not be left waiting for a mount frame that never comes."""
    from channels.testing import WebsocketCommunicator

    from djust import worker_pool
    from djust.websocket import LiveViewConsumer

    monkeypatch.setitem(djust_config._config, "worker_threads", workers)
    monkeypatch.setattr(worker_pool, "_pool", [])
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        comm = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        assert (await comm.connect())[0]
        try:
            await comm.receive_json_from(timeout=10)
            await comm.send_json_to(
                {"type": "mount", "view": f"{__name__}._BrokenRender", "url": "/table/"}
            )
            frame = await comm.receive_json_from(timeout=10)
        finally:
            await comm.disconnect()
    assert frame["type"] == "error", frame
