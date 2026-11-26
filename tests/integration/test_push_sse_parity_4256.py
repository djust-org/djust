"""``push_state`` and ``flush_push_events`` reach an SSE client as they reach a
WebSocket client (#4256).

#4232 routed the seven stream ops through one send target (the WebSocket
consumer, else the SSE session's transport while that session still serves
the view). ``push_state`` and ``flush_push_events`` kept their
``_ws_consumer``-only early return, so over SSE:

* ``push_state()`` logs a warning and sends nothing: the intermediate render
  never reaches the client;
* ``flush_push_events()`` is a no-op mid-task: the events wait for the turn's
  end, so a ``@background`` handler's step-by-step pushes arrive all at once.

Each scenario runs over both transports and compares what the client got.
"""

from __future__ import annotations

import asyncio
import sys
import types

import pytest
from django.test import override_settings
from django.urls import include, path
from djust import LiveView, state
from djust.decorators import background, event_handler
from djust.sse import sse_urlpatterns
from djust.tests._frames import effects_of, legacy_type

pytestmark = pytest.mark.django_db(transaction=True)

#: Set by the test once it has seen the mid-task push; the background handler
#: waits on it (bounded) so "arrived mid-task" is observable, not a race.
_GATE: dict = {}


class PushParity4256View(LiveView):
    exposure_policy = "explicit"
    template = '<div dj-root><p id="status">{{ status }}</p></div>'
    status = state("idle")

    @event_handler
    async def work(self, **kwargs):
        self.status = "working-4256"
        await self.push_state()
        self.status = "done-4256"

    @event_handler
    @background
    async def steps(self, **kwargs):
        self.push_event("step4256", {"n": 1})
        await self.flush_push_events()
        gate = _GATE.get("seen")
        if gate is not None:
            try:
                await asyncio.wait_for(gate.wait(), 30.0)
            except asyncio.TimeoutError:
                pass
        self.push_event("step4256", {"n": 2})
        self.status = "stepped"


_MODULE = __name__
_URLCONF = "tests.integration._test_push_sse_parity_4256_urls"
_urls = types.ModuleType(_URLCONF)
_urls.urlpatterns = [
    path("p4256/", PushParity4256View.as_view()),
    path("djust/", include(sse_urlpatterns)),
]
sys.modules[_URLCONF] = _urls


def _settings():
    from django.conf import settings

    allowed = list(getattr(settings, "LIVEVIEW_ALLOWED_MODULES", []) or [])
    return override_settings(ROOT_URLCONF=_URLCONF, LIVEVIEW_ALLOWED_MODULES=allowed + [_MODULE])


def _open(transport, scenario):
    from djust.testing import close_live_pages, open_live_page

    page = open_live_page("/p4256/")

    async def run():
        connection = await (page.connect() if transport == "ws" else page.connect_sse())
        try:
            if scenario == "push_state":
                await connection.send_event("work", {})
                for _ in range(10):
                    try:
                        await connection.receive(timeout=0.1)
                    except asyncio.TimeoutError:
                        pass
                return list(connection.frames), None
            _GATE["seen"] = asyncio.Event()
            await connection.send_event("steps", {})
            mid_task = None
            for _ in range(30):
                try:
                    await connection.receive(timeout=0.1)
                except asyncio.TimeoutError:
                    pass
                steps = _steps(connection.frames)
                if mid_task is None and steps:
                    mid_task = list(steps)
                    _GATE["seen"].set()
                if steps == [1, 2]:
                    break
            return list(connection.frames), mid_task
        finally:
            _GATE.pop("seen", None)
            await connection.close()

    try:
        return asyncio.run(run())
    finally:
        close_live_pages()


def _render_frames(frames):
    """Render frames carrying the intermediate state, as their frame type."""
    out = []
    for frame in frames:
        if isinstance(frame, dict) and legacy_type(frame) in ("patch", "html_update"):
            if "working-4256" in repr(frame.get("patches") or frame.get("html")):
                out.append(legacy_type(frame))
    return out


def _steps(frames):
    return [
        (effect.get("payload") or {}).get("n")
        for effect in effects_of([f for f in frames if isinstance(f, dict)], "push_event")
        if effect.get("event") == "step4256"
    ]


@pytest.mark.parametrize("transport", ["ws", "sse"])
def test_push_state_reaches_the_client(transport):
    with _settings():
        frames, _ = _open(transport, "push_state")
    assert _render_frames(frames) == ["patch"], (transport, frames[-6:])


@pytest.mark.parametrize("transport", ["ws", "sse"])
def test_flush_push_events_delivers_mid_task(transport):
    with _settings():
        frames, mid_task = _open(transport, "flush")
    # Step 1 arrived while the task was still waiting, step 2 after; once each.
    assert mid_task == [1], (transport, mid_task, frames[-6:])
    assert _steps(frames) == [1, 2], (transport, frames[-6:])


def test_sse_push_frames_match_websocket():
    with _settings():
        ws = _render_frames(_open("ws", "push_state")[0])
        sse = _render_frames(_open("sse", "push_state")[0])
    assert ws == ["patch"], ws
    assert sse == ws, {"ws": ws, "sse": sse}
