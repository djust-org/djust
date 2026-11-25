"""``start_async(..., survive_disconnect=True)`` / ``@background(survive_disconnect=True)`` (#4182).

An explicit view is disposed with its work when its connection closes. Work
started with the opt-in runs to completion instead; its result is discarded,
and touching the disposed view (declared state, a push, more scheduling)
raises ``ViewDisposedError``. Work without the opt-in is still cancelled.
"""

from __future__ import annotations

import asyncio
import logging
import threading

import pytest
from asgiref.sync import sync_to_async
from django.test import override_settings
from django.urls import include, path

import djust.sse
from djust import LiveView, state
from djust._surviving_work import SURVIVING_OWNER, ViewDisposedError
from djust.decorators import background, event_handler
from djust.mixins.async_work import ViewDisposedError as ReexportedViewDisposedError
from djust.sse import sse_urlpatterns
from djust.testing import open_live_page

pytest.importorskip("channels")

pytestmark = [pytest.mark.django_db(transaction=True)]

EVENTS: list = []
VIEWS: list = []
GATE: dict = {}
THREAD_GATE = threading.Event()


class Survivor(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root><p>{{ status }}</p></div>"

    status = state("")

    def mount(self, request, **kwargs):
        VIEWS.append(self)

    @event_handler
    def send(self, **kwargs):
        self.start_async(self._work, survive_disconnect=True, name="send")

    @event_handler
    def plain(self, **kwargs):
        self.start_async(self._work, name="plain")

    @event_handler
    @background(survive_disconnect=True)
    async def bg(self, **kwargs):
        await self._work()

    @event_handler
    def touch(self, **kwargs):
        self.start_async(self._touch, survive_disconnect=True)

    @event_handler
    def threaded(self, **kwargs):
        self.start_async(self._threaded_work, survive_disconnect=True, name="threaded")

    @event_handler
    def threaded_plain(self, **kwargs):
        self.start_async(self._threaded_work, name="threaded")

    async def _work(self):
        EVENTS.append("started")
        try:
            await GATE["gate"].wait()
        except asyncio.CancelledError:
            EVENTS.append("cancelled")
            raise
        EVENTS.append("finished")
        return "done"

    async def _touch(self):
        EVENTS.append("started")
        await GATE["gate"].wait()
        self.status = "done"
        EVENTS.append("touched")

    async def _threaded_work(self):
        EVENTS.append("started")
        try:
            await asyncio.to_thread(THREAD_GATE.wait, 10)
        except asyncio.CancelledError:
            EVENTS.append("cancelled")
            raise
        EVENTS.append("finished")

    def handle_async_result(self, name, result=None, error=None):
        EVENTS.append(("handled", name, result))


urlpatterns = [
    path("t/survivor/", Survivor.as_view()),
    path("djust/", include(sse_urlpatterns)),
]

SETTINGS = {
    "ROOT_URLCONF": __name__,
    "ALLOWED_HOSTS": ["*"],
    "DEBUG": False,
    "LIVEVIEW_ALLOWED_MODULES": [__name__.rsplit(".", 1)[0]],
}


@pytest.fixture(autouse=True)
def _setup(monkeypatch):
    monkeypatch.setattr(djust.sse, "_SESSION_LINGER_S", 0)
    EVENTS.clear()
    VIEWS.clear()
    THREAD_GATE.clear()
    GATE["gate"] = asyncio.Event()
    with override_settings(**SETTINGS):
        yield
    GATE["gate"].set()
    THREAD_GATE.set()


async def _until(predicate, what):
    for _ in range(500):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("timed out waiting for " + what)


async def _start_then_close(event: str, transport: str) -> None:
    """Send ``event``, wait for its work to start, close the connection and
    wait until the view really was disposed."""
    page = await sync_to_async(open_live_page)("/t/survivor/")
    if transport == "ws":
        socket = await page.connect()
        await socket.send_event(event)
        await _until(lambda: "started" in EVENTS, "the work to start")
        await socket.disconnect()
    else:
        stream = await page.connect_sse()
        await stream.send_event(event)
        await _until(lambda: "started" in EVENTS, "the work to start")
        await stream.close()
    await _until(
        lambda: getattr(VIEWS[-1], "_djust_child_disposed", False) is True,
        "the view to be disposed",
    )


@pytest.mark.parametrize("transport", ["ws", "sse"])
@pytest.mark.parametrize("event", ["send", "bg"])
async def test_survivor_finishes_after_the_connection_closes(event, transport):
    await _start_then_close(event, transport)
    assert "cancelled" not in EVENTS
    GATE["gate"].set()
    await _until(lambda: "finished" in EVENTS, "the survivor to finish")
    await asyncio.sleep(0.05)
    assert not [e for e in EVENTS if isinstance(e, tuple)], "no handle_async_result after disposal"


@pytest.mark.parametrize("transport", ["ws", "sse"])
async def test_work_without_the_opt_in_is_cancelled(transport):
    await _start_then_close("plain", transport)
    await _until(lambda: "cancelled" in EVENTS, "the work to be cancelled")
    GATE["gate"].set()
    await asyncio.sleep(0.05)
    assert "finished" not in EVENTS


async def test_a_sync_survivor_runs_with_its_owner_in_context():
    """A sync callback runs on a worker thread: the disposed-view guard must
    see its owner there too (``sync_to_async`` copies the context)."""
    from djust._surviving_work import mark_surviving
    from djust.mixins.async_work import run_async_callback

    view = _Plain()
    seen = []

    def work():
        seen.append(SURVIVING_OWNER.get())
        return "ok"

    assert await run_async_callback(mark_surviving(work), owner=view) == "ok"
    assert seen == [view]
    assert SURVIVING_OWNER.get() is None
    seen.clear()
    await run_async_callback(work, owner=view)
    assert seen == [None]


async def test_survivor_touching_the_disposed_view_gets_a_clear_error(caplog):
    caplog.set_level(logging.DEBUG)
    page = await sync_to_async(open_live_page)("/t/survivor/")
    socket = await page.connect()
    await socket.send_event("touch")
    await _until(lambda: "started" in EVENTS, "the work to start")
    await socket.disconnect()
    view = VIEWS[-1]
    await _until(lambda: view._djust_child_disposed is True, "the view to be disposed")
    frames = len(socket.frames)
    GATE["gate"].set()

    def refused():
        return [
            r
            for r in caplog.records
            if r.levelno == logging.ERROR and "survive_disconnect=True" in r.getMessage()
        ]

    await _until(lambda: refused(), "the refusal to be logged")
    message = refused()[0].getMessage()
    assert "'status'" in message and "Survivor" in message, message
    assert "touched" not in EVENTS
    assert view.__dict__.get("_state_status") != "done"
    assert len(socket.frames) == frames


async def test_survivor_result_renders_while_connected():
    """Decision 3: while the view is live, a survivor's result renders as usual."""
    page = await sync_to_async(open_live_page)("/t/survivor/")
    async with await page.connect() as socket:
        await socket.send_event("send")
        await _until(lambda: "started" in EVENTS, "the work to start")
        GATE["gate"].set()
        await _until(lambda: ("handled", "send", "done") in EVENTS, "the result handler")
        await socket.receive(("patch", "html_update", "noop"), timeout=5)


def test_http_fallback_budget_does_not_cancel_a_survivor():
    page = open_live_page("/t/survivor/")
    with override_settings(LIVEVIEW_CONFIG={"http_fallback_background_timeout": 0.05}):
        response = page.post_event("threaded")
    assert response.status_code == 200
    assert "started" in EVENTS and "cancelled" not in EVENTS
    THREAD_GATE.set()
    for _ in range(500):
        if "finished" in EVENTS:
            break
        threading.Event().wait(0.01)
    assert "finished" in EVENTS, EVENTS


def test_http_fallback_budget_still_cancels_other_work():
    page = open_live_page("/t/survivor/")
    with override_settings(LIVEVIEW_CONFIG={"http_fallback_background_timeout": 0.05}):
        page.post_event("threaded_plain")
    for _ in range(500):
        if "cancelled" in EVENTS:
            break
        threading.Event().wait(0.01)
    assert "cancelled" in EVENTS, EVENTS


# --------------------------------------------------------------------------- #
# Units
# --------------------------------------------------------------------------- #


class _Plain(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root></div>"
    status = state("")

    @event_handler
    @background
    def bare(self, **kwargs):
        pass

    @event_handler
    @background()
    def called(self, **kwargs):
        pass

    @event_handler
    @background(survive_disconnect=True)
    def surviving(self, **kwargs):
        pass


def test_background_bare_and_called_forms_still_work():
    from djust._surviving_work import is_surviving

    view = _Plain()
    view.bare()
    view.called()
    view.surviving()
    tasks = view._async_tasks
    assert set(tasks) == {"bare", "called", "surviving"}
    assert not is_surviving(tasks["bare"][0])
    assert not is_surviving(tasks["called"][0])
    assert is_surviving(tasks["surviving"][0])


def test_survive_disconnect_must_be_a_bool():
    view = _Plain()
    with pytest.raises(TypeError):
        view.start_async(lambda: None, survive_disconnect="yes")
    with pytest.raises(TypeError):
        background(survive_disconnect=1)


def test_a_disposed_view_refuses_a_survivors_push_state_and_scheduling():
    from djust._child_lifecycle import dispose_child_subtree

    assert ReexportedViewDisposedError is ViewDisposedError
    view = _Plain()
    dispose_child_subtree(view)
    token = SURVIVING_OWNER.set(view)
    try:
        with pytest.raises(ViewDisposedError, match="push events"):
            view.push_event("x", {})
        with pytest.raises(ViewDisposedError, match="schedule more work"):
            view.start_async(lambda: None)
        with pytest.raises(ViewDisposedError, match="schedule more work"):
            view.defer(lambda: None)
        with pytest.raises(ViewDisposedError, match="'status'"):
            view.status = "x"
    finally:
        SURVIVING_OWNER.reset(token)
    # Outside a survivor the disposed view behaves as before.
    with pytest.raises(RuntimeError):
        view.start_async(lambda: None)
    view.status = "y"
