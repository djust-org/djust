"""#2946: ``start_async`` work from a NOTIFY-released activity event must run.

An event sent to a hidden ``dj-activity`` panel is queued; a ``db_notify`` whose
``handle_info`` reveals the panel releases it through the consumer's
``_dispatch_single_event``. That dispatcher only started background work when
the legacy single-task ``_async_pending`` was set, but ``start_async`` writes
``_async_tasks`` — so the scheduled callback was silently dropped. The runtime's
twin already dispatches unconditionally (#1887); both arms here (skip-render
noop and re-render) must too.
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

from djust import LiveView, event_handler
from djust.websocket import LiveViewConsumer

RAN = []


class NotifyAsyncView(LiveView):
    template = "<div dj-root>{{ count }}</div>"
    # Class-level so the consumer joins the NOTIFY group at wiring time.
    _listen_channels = frozenset({"notify_async_2946"})

    def mount(self, request, **kwargs):
        self.count = 0
        self.set_activity_visible("panel", False)

    def get_context_data(self, **kwargs):
        return {"count": self.count}

    @event_handler()
    def spawn_quiet(self, **kwargs):
        # State unchanged: the dispatcher's skip-render (noop) arm.
        RAN.append("spawn_quiet")
        self.start_async(self._work)

    @event_handler()
    def spawn_render(self, **kwargs):
        # State changed: the dispatcher's re-render arm.
        RAN.append("spawn_render")
        self.count += 1
        self.start_async(self._work)

    def _work(self):
        RAN.append("work")

    def handle_info(self, message):
        self.set_activity_visible("panel", True)


async def _drain(socket, quiet=0.4):
    while not await socket.receive_nothing(timeout=quiet):
        out = await socket.receive_output(timeout=3)
        if out["type"] == "websocket.close":
            return
        json.loads(out["text"])


def _session():
    session = SessionStore()
    session.save()
    return session


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("event", ["spawn_quiet", "spawn_render"])
async def test_notify_released_event_runs_its_start_async_work(event):
    RAN.clear()
    with override_settings(
        LIVEVIEW_ALLOWED_MODULES=[__name__], DEBUG=False, DJUST_TENANTS=None, DJUST_CONFIG={}
    ):
        session = await sync_to_async(_session)()
        socket = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        socket.scope.update(session=session, user=AnonymousUser(), tenant=None)
        assert (await socket.connect())[0]
        await socket.receive_json_from(timeout=3)
        try:
            await socket.send_json_to(
                {"type": "mount", "view": __name__ + ".NotifyAsyncView", "url": "/n/"}
            )
            assert (await socket.receive_json_from(timeout=3))["type"] == "mount"
            await socket.send_json_to(
                {"type": "event", "event": event, "params": {"_activity": "panel"}}
            )
            await _drain(socket)
            assert RAN == [], "the event must be queued while the panel is hidden"

            await get_channel_layer().group_send(
                "djust_db_notify_notify_async_2946",
                {"type": "db_notify", "channel": "notify_async_2946", "payload": {}},
            )
            await _drain(socket, quiet=0.8)
            for _ in range(40):
                if "work" in RAN:
                    break
                await asyncio.sleep(0.05)
            assert event in RAN, "the NOTIFY never released the queued event"
            assert "work" in RAN, "start_async work was dropped"
        finally:
            await socket.disconnect()
