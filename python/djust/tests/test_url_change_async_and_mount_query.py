"""Socket paths that differed from the HTTP render.

* ``start_async()`` queued in ``handle_params()`` on a ``url_change`` (browser
  Back/Forward, ``dj-patch``) was never dispatched: the mount and event paths
  run the queued callbacks after their frame, the url_change path flushed every
  other queue and left the tasks in ``_async_tasks``, so the read silently did
  not happen.
* The socket mount must see the page's query string in ``request.GET``, as the
  HTTP render of the same URL does.
"""

from __future__ import annotations

import pytest
from asgiref.sync import sync_to_async

from djust import LiveView


class _AsyncParamsView(LiveView):
    template = (
        '<div dj-view="djust.tests.test_url_change_async_and_mount_query._AsyncParamsView" '
        'dj-id="0">Tab: {{ tab }} / Loaded: {{ loaded }} / Q: {{ q }}</div>'
    )

    def mount(self, request, **kwargs):
        self.tab = "home"
        self.loaded = "none"
        self.q = request.GET.get("q", "")

    def handle_params(self, params, uri):
        tab = params.get("tab", "home")
        self.tab = tab[0] if isinstance(tab, list) else tab
        self.start_async(self._load, self.tab, name="load")

    def _load(self, tab):
        self.loaded = f"loaded-{tab}"


async def _receive_until(communicator, predicate, *, tries=10, timeout=3):
    seen = []
    for _ in range(tries):
        frame = await communicator.receive_json_from(timeout=timeout)
        seen.append(frame)
        if predicate(frame):
            return frame, seen
    return None, seen


async def _connect_and_mount(params=None, url="/tabs/"):
    pytest.importorskip("channels")
    from channels.testing import WebsocketCommunicator
    from django.contrib.sessions.backends.db import SessionStore

    from djust.websocket import LiveViewConsumer

    def _create_session():
        s = SessionStore()
        s.create()
        return s.session_key

    session_key = await sync_to_async(_create_session)()

    class _ScopeSession:
        def __init__(self, key):
            self.session_key = key

    communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    communicator.scope["session"] = _ScopeSession(session_key)
    connected, _ = await communicator.connect()
    assert connected
    await communicator.receive_json_from(timeout=2)  # connect frame
    await communicator.send_json_to(
        {
            "type": "mount",
            "view": f"{__name__}._AsyncParamsView",
            "url": url,
            "params": params or {},
        }
    )
    mount, seen = await _receive_until(communicator, lambda f: f.get("type") == "mount")
    assert mount is not None, seen
    return communicator, mount


def _text(frame):
    return repr(frame.get("patches") or frame.get("html") or "")


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_start_async_queued_by_handle_params_runs_on_url_change():
    from django.test import override_settings

    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        communicator, _mount = await _connect_and_mount()
        # Let the mount's own handle_params work finish first.
        await _receive_until(communicator, lambda f: "loaded-home" in _text(f))

        await communicator.send_json_to(
            {"type": "url_change", "params": {"tab": "settings"}, "uri": "/tabs/?tab=settings"}
        )
        frame, seen = await _receive_until(communicator, lambda f: "loaded-settings" in _text(f))
        assert frame is not None, (
            "the start_async queued by handle_params on url_change never ran: %r" % seen
        )
        await communicator.disconnect()


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_socket_mount_sees_the_query_string_in_request_get():
    from django.test import override_settings

    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        communicator, mount = await _connect_and_mount(params={"q": "lebron"})
        assert "Q: lebron" in (mount.get("html") or ""), mount
        await communicator.disconnect()
