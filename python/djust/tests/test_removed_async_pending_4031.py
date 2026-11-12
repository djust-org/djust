"""#4031: ``self._async_pending = (fn, args, kwargs)`` is refused, not dropped.

2.0 removed the readers of the 1.x single-task queue (#3515, see
``changelog.d/3500-dead-protocol.removed.md``). The assignment still worked as
a plain attribute write, so over a WebSocket the work never ran and nothing
said so, while the HTTP fallback (re-added in #3630) still ran it. The
attribute is now a property that refuses a truthy value at the assignment,
naming ``start_async`` / ``@background``, on every transport.
"""

import pytest
from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from djust import LiveView, event_handler, state
from djust._removed_settings import REMOVED_INSTANCE_ATTRIBUTES
from djust.websocket import LiveViewConsumer

from ._ws_frames import drain_extra, receive_until

SETTINGS = dict(DEBUG=False, DJUST_TENANTS=None, DJUST_CONFIG={})


class LegacyQueueView(LiveView):
    exposure_policy = "explicit"
    status = state("idle")
    template = "<div dj-root><span>{{ status }}</span></div>"

    @event_handler()
    def go(self, **kwargs):
        self.status = "queued"
        self._async_pending = (self._work, (), {})

    @event_handler()
    def clear(self, **kwargs):
        self._async_pending = None
        self.status = "cleared"

    def _work(self):
        self.status = "done"


def test_assignment_raises_naming_the_replacement():
    view = LegacyQueueView()
    with pytest.raises(ImproperlyConfigured) as raised:
        view._async_pending = (view._work, (), {})
    message = str(raised.value)
    assert "_async_pending" in message
    assert "start_async" in message and "@background" in message
    assert "LegacyQueueView" in message
    assert view._async_pending is None


def test_none_is_still_accepted_and_reads_back_none():
    """Clearing queued nothing in 1.x; it is not refused."""
    view = LegacyQueueView()
    view._async_pending = None
    assert view._async_pending is None
    assert "_async_pending" not in vars(view)


def test_listed_with_the_other_removed_names():
    assert "_async_pending" in REMOVED_INSTANCE_ATTRIBUTES


def _session():
    session = SessionStore()
    session.save()
    return session


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_websocket_turn_reports_the_error_instead_of_dropping_the_work(caplog):
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], **SETTINGS):
        socket = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        socket.scope.update(
            session=await sync_to_async(_session)(), user=AnonymousUser(), tenant=None
        )
        assert (await socket.connect())[0]
        try:
            await socket.receive_json_from()
            await socket.send_json_to(
                {"type": "mount", "view": __name__ + ".LegacyQueueView", "url": "/p/"}
            )
            assert (await socket.receive_json_from())["type"] == "mount"
            await socket.send_json_to({"type": "event", "event": "go", "params": {}, "ref": 1})
            frames = await receive_until(
                socket,
                lambda fs: any(f.get("type") == "error" or f.get("ref") == 1 for f in fs),
            )
            frames += await drain_extra(socket)
            assert any(f.get("type") == "error" for f in frames), frames
            assert not any(f.get("source") == "async" for f in frames), frames
            assert any(
                "_async_pending" in r.getMessage() and "start_async" in r.getMessage()
                for r in caplog.records
            ), caplog.text
        finally:
            await socket.disconnect()
