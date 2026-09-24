"""#2955: ``start_async`` queued in a server-originated turn must run.

``_tick_once``, ``server_push`` and ``db_notify`` run their hook
(``handle_tick``, the push handler, ``handle_info``) and render, but none of
them dispatched queued background work, so the callback sat in
``_async_tasks`` until some later user event drained it, or forever. Each turn
now dispatches once its hook has succeeded; a hook that raises dispatches
nothing.

Ported from ``56c36d726`` (PR #2954, ADR-038 completion) without its explicit
child-queue sweep, which depends on ADR-038.
"""

import asyncio
import json

import pytest
from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.test import override_settings

from djust import LiveView
from djust.push import view_group_name
from djust.websocket import LiveViewConsumer

SETTINGS = dict(DEBUG=False, DJUST_TENANTS=None, DJUST_CONFIG={})
RAN = []


class WorkView(LiveView):
    template = "<div dj-root><span>{{ count }}</span></div>"
    _listen_channels = frozenset({"turn_work_2955"})

    def mount(self, request, **kwargs):
        self.count = 0

    def get_context_data(self, **kwargs):
        return {"count": self.count}

    def _work(self):
        RAN.append("work")
        return 21

    def handle_async_result(self, name, result=None, error=None):
        if error is None:
            self.count = result


class NotifyWorkView(WorkView):
    def handle_info(self, message):
        RAN.append("hook")
        self.start_async(self._work)


class PushWorkView(WorkView):
    def handle_go(self, **kwargs):
        RAN.append("hook")
        self.start_async(self._work)


class TickWorkView(WorkView):
    tick_interval = 50

    def handle_tick(self):
        if "hook" not in RAN:
            RAN.append("hook")
            self.start_async(self._work)


class FailingPushView(WorkView):
    def handle_go(self, **kwargs):
        RAN.append("hook")
        self.start_async(self._work)
        raise ValueError("hook failed")


async def _notify(view_class):
    await get_channel_layer().group_send(
        "djust_db_notify_turn_work_2955",
        {"type": "db_notify", "channel": "turn_work_2955", "payload": {}},
    )


async def _push(view_class):
    await get_channel_layer().group_send(
        view_group_name(__name__ + "." + view_class.__name__),
        {"type": "server_push", "handler": "handle_go", "payload": {}},
    )


async def _tick(view_class):
    pass  # the view's own tick loop drives it


def _session():
    session = SessionStore()
    session.save()
    return session


async def _mounted(view_class):
    socket = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    socket.scope.update(session=await sync_to_async(_session)(), user=AnonymousUser(), tenant=None)
    assert (await socket.connect())[0]
    await socket.receive_json_from(timeout=3)
    await socket.send_json_to(
        {"type": "mount", "view": __name__ + "." + view_class.__name__, "url": "/w/"}
    )
    assert (await socket.receive_json_from(timeout=3))["type"] == "mount"
    return socket


async def _frames(socket, quiet=0.6):
    frames = []
    while not await socket.receive_nothing(timeout=quiet):
        frames.append(json.loads((await socket.receive_output(timeout=3))["text"]))
    return frames


@pytest.fixture(autouse=True)
def _reset():
    RAN.clear()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "view_class,trigger",
    [(NotifyWorkView, _notify), (PushWorkView, _push), (TickWorkView, _tick)],
    ids=["db_notify", "server_push", "tick"],
)
async def test_start_async_from_a_server_originated_turn_runs(view_class, trigger):
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], **SETTINGS):
        socket = await _mounted(view_class)
        try:
            await trigger(view_class)
            for _ in range(60):
                if "work" in RAN:
                    break
                await asyncio.sleep(0.05)
            frames = await _frames(socket)
            assert RAN[:2] == ["hook", "work"], (RAN, frames)
            results = [f for f in frames if f.get("source") == "async"]
            assert results and "21" in json.dumps(results), frames
            # No user event owns server-turn work, so no loading state is named.
            assert all(f.get("event_name") is None for f in results), results
        finally:
            await socket.disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_failed_hook_dispatches_nothing():
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], **SETTINGS):
        socket = await _mounted(FailingPushView)
        try:
            await _push(FailingPushView)
            await _frames(socket)
            assert RAN == ["hook"], "work queued by a raising hook must not run"
        finally:
            await socket.disconnect()
