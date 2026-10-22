"""A closing socket's teardown waits for a render of its view still in flight.

The disconnect teardown drops the Rust view's live handles
(``clear_live_handles`` / ``set_raw_py_values``), which borrow the view
mutably. A render of the same view could still be running on a sync thread:
a server-push turn, a tick, a background result. Cancelling the coroutine that
awaited it does not stop the thread. The teardown ran on the event loop at the
same time and PyO3 refused it: ``set_raw_py_values failed during teardown:
Already borrowed``, and the handles it should have dropped stayed.

The teardown now runs on the view's sync thread, after the work queued there.
"""

from __future__ import annotations

import asyncio
import logging
import threading

import pytest
from django.test import override_settings

from djust import LiveView
from djust.config import config as djust_config
from djust.push import apush_to_view

pytest.importorskip("channels")

VIEW = f"{__name__}._SlowRenderPage"
GATE = {"armed": False, "entered": threading.Event(), "release": threading.Event()}


class _Blocker:
    """A template value that holds the render (and its Rust borrow) when armed."""

    @property
    def value(self):
        if GATE["armed"]:
            GATE["armed"] = False
            GATE["entered"].set()
            assert GATE["release"].wait(timeout=20), "the test never released the render"
        return "v"


class _SlowRenderPage(LiveView):
    exposure_policy = "legacy"
    template = "<div dj-root><b>{{ count }}</b><i>{{ blocker.value }}</i></div>"

    def mount(self, request, **kwargs):
        self.count = 0
        self.blocker = _Blocker()

    def handle_bump(self, **kwargs):
        self.count += 1
        self.blocker = _Blocker()  # a new value, so its region re-renders


@pytest.fixture(params=[None, 4], ids=["stock", "pool"])
def workers(request, monkeypatch):
    from djust import worker_pool

    monkeypatch.setitem(djust_config._config, "worker_threads", request.param)
    monkeypatch.setattr(worker_pool, "_pool", [])
    GATE["armed"] = False
    GATE["entered"].clear()
    GATE["release"].clear()
    yield request.param
    GATE["release"].set()


async def _wait_thread_event(event, what):
    for _ in range(1000):
        if event.is_set():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("timed out waiting for " + what)


def _borrow_warnings(caplog):
    return [
        r.getMessage()
        for r in caplog.records
        if "failed during teardown" in r.getMessage() or "Already borrowed" in r.getMessage()
    ]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_closing_during_a_pushed_render_does_not_race_the_teardown(workers, caplog):
    """A push turn's render in flight when the socket closes. (Disconnect's own
    thread hops already queue behind it; this pins that it stays so.)"""
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    caplog.set_level(logging.WARNING)
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        comm = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        assert (await comm.connect())[0]
        await comm.receive_json_from(timeout=10)
        await comm.send_json_to({"type": "mount", "view": VIEW, "url": "/slow/"})
        while (await comm.receive_json_from(timeout=10)).get("type") != "mount":
            pass
        GATE["armed"] = True
        await apush_to_view(VIEW, handler="handle_bump")
        # The pushed render is now inside the Rust render, holding its borrow.
        await _wait_thread_event(GATE["entered"], "the render to start")
        closing = asyncio.ensure_future(comm.disconnect())
        await asyncio.sleep(0.2)  # the teardown runs while the render is held
        GATE["release"].set()
        await asyncio.wait_for(closing, timeout=20)
    assert _borrow_warnings(caplog) == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_render_that_starts_after_the_last_thread_hop_is_waited_for(
    workers, caplog, monkeypatch
):
    """The race the pool exposed: work of the view reaches its sync thread
    after the teardown's last hop there (a tick, a deferred push, a background
    result that was already queued), and the handles were then cleared on the
    event loop while that render held the view. Deterministic form: start the
    render from the ``disconnected()`` step, just before the release."""
    from asgiref.sync import sync_to_async
    from channels.testing import WebsocketCommunicator

    from djust import _child_lifecycle
    from djust.websocket import LiveViewConsumer

    real_hook = _child_lifecycle.fire_view_disconnected
    started = []

    async def hook_then_render(view):
        await real_hook(view)
        view.blocker = _Blocker()
        GATE["armed"] = True
        started.append(asyncio.ensure_future(sync_to_async(view.render_with_diff)()))
        await _wait_thread_event(GATE["entered"], "the render to start")

    monkeypatch.setattr(_child_lifecycle, "fire_view_disconnected", hook_then_render)
    caplog.set_level(logging.WARNING)
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        comm = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        assert (await comm.connect())[0]
        await comm.receive_json_from(timeout=10)
        await comm.send_json_to({"type": "mount", "view": VIEW, "url": "/slow/"})
        while (await comm.receive_json_from(timeout=10)).get("type") != "mount":
            pass
        closing = asyncio.ensure_future(comm.disconnect())
        await _wait_thread_event(GATE["entered"], "the render to start")
        await asyncio.sleep(0.2)  # the release runs while the render is held
        GATE["release"].set()
        await asyncio.wait_for(closing, timeout=20)
        await asyncio.wait_for(started[0], timeout=20)
    assert _borrow_warnings(caplog) == []
