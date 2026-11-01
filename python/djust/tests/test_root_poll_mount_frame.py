"""The mount frame names the page root's own ``dj-poll`` attributes.

A mount sends the root's CONTENT (``_extract_liveview_content``), so after a
live_redirect the browser's root element keeps the departing view's
attributes. A ``dj-poll`` on the root would then keep calling a handler the
new view lacks. ``root_poll`` carries the destination root's ``dj-poll`` and
``dj-poll-interval`` (``{}`` for none) and the client applies it to the root.
"""

from __future__ import annotations

import pytest
from asgiref.sync import sync_to_async

from djust import LiveView
from djust.decorators import event_handler
from djust.mixins.template import _root_poll_attrs

_MODULE = __name__


class _PollingRootView(LiveView):
    template = (
        '<div dj-root dj-poll="tick" dj-poll-interval="2000" title="a > b">'
        '<p dj-poll="inner">{{ n }}</p></div>'
    )

    def mount(self, request, **kwargs):
        self.n = 0

    @event_handler()
    def tick(self, **kwargs):
        self.n += 1


class _PlainRootView(LiveView):
    template = '<div dj-root><p dj-poll="inner">{{ n }}</p></div>'

    def mount(self, request, **kwargs):
        self.n = 0


async def _mount_frame(view_suffix):
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
    await communicator.receive_json_from()  # connect frame
    await communicator.send_json_to(
        {"type": "mount", "view": f"{_MODULE}.{view_suffix}", "url": "/p/"}
    )
    try:
        for _ in range(6):
            frame = await communicator.receive_json_from()
            if frame.get("type") == "mount":
                return frame
        raise AssertionError("never received a mount frame")
    finally:
        await communicator.disconnect()


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_mount_frame_names_the_roots_poll():
    from django.test import override_settings

    with override_settings(LIVEVIEW_ALLOWED_MODULES=[_MODULE]):
        frame = await _mount_frame("_PollingRootView")
    assert frame["root_poll"] == {"dj-poll": "tick", "dj-poll-interval": "2000"}
    assert 'dj-poll="inner"' in frame["html"], "the content is unchanged"


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_mount_frame_of_a_root_without_a_poll_says_so():
    from django.test import override_settings

    with override_settings(LIVEVIEW_ALLOWED_MODULES=[_MODULE]):
        frame = await _mount_frame("_PlainRootView")
    # A nested poll is not the root's: the client must clear the root's.
    assert frame["root_poll"] == {}


@pytest.mark.parametrize(
    "html, expected",
    [
        ('<div dj-root dj-poll="a">x</div>', {"dj-poll": "a"}),
        (
            "<main id=m><div dj-root data-x='>' dj-poll-interval=\"5\" dj-poll=b></div></main>",
            {"dj-poll": "b", "dj-poll-interval": "5"},
        ),
        ('<div dj-view="v.V" dj-poll="c&amp;d"></div>', {"dj-poll": "c&d"}),
        ('<div dj-root><span dj-poll="nested"></span></div>', {}),
        ("<p>no root</p>", {}),
        ("", {}),
    ],
)
def test_root_poll_attrs(html, expected):
    assert _root_poll_attrs(html) == expected
