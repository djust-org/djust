"""A view that renders deeply nested markup stays up over a real WebSocket.

The VDOM bounds the nesting depth of the HTML it parses (``MAX_VDOM_DEPTH``,
pinned against ``RustLiveView`` in ``python/tests/test_vdom_nesting_bound.py``).
This drives the same markup through the real consumer
(``WebsocketCommunicator`` against ``LiveViewConsumer.as_asgi()``): mount, then
two events that each swap the deep markup, then a plain event that must still
be answered, so the connection and the render loop survived.

The three sources are the ways deep markup reaches a page in practice: raw
HTML passed through ``|safe``, user Markdown (one nested ``<blockquote>`` per
``>``), and terminal output (one nested ``<span>`` per SGR style).
"""

from __future__ import annotations

import pytest
from asgiref.sync import sync_to_async

from djust import LiveView, _rust
from djust.decorators import event_handler

_MODULE = __name__
DEPTH = 40_000  # far past any stack, far below any request-size limit


def _body(kind: str, salt: str) -> str:
    if kind == "spans":
        return "<span>" * DEPTH + f"deep {salt}" + "</span>" * DEPTH
    if kind == "markdown":
        from djust import render_markdown

        return render_markdown("> " * DEPTH + f"deep {salt}")
    from djust.components.components.terminal import Terminal

    return Terminal(output=["\x1b[" + ";".join(["1"] * DEPTH) + f"mdeep {salt}"]).render()


class _DeepView(LiveView):
    template = (
        f'<div dj-view="{_MODULE}._DeepView" dj-root>'
        '{{ body|safe }}<p id="n">{{ n }}</p></div>'
    )

    def mount(self, request, **kwargs):
        self.body = ""
        self.n = 0

    @event_handler()
    def deep(self, kind="spans", **kwargs):
        self.n += 1
        self.body = _body(kind, str(self.n))

    @event_handler()
    def ping(self, **kwargs):
        self.n += 1


async def _connect_and_mount():
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
        {"type": "mount", "view": f"{_MODULE}._DeepView", "url": "/deep/"}
    )
    for _ in range(6):
        frame = await communicator.receive_json_from(timeout=3)
        if frame.get("type") == "mount":
            return communicator
    raise AssertionError("never received a mount frame")


async def _render_for(communicator, ref: int) -> None:
    """Read frames until the render answering event ``ref`` arrives; fail on an
    error frame. The deadline is generous: only a dead connection reaches it."""
    for _ in range(50):
        frame = await communicator.receive_json_from(timeout=20)
        assert frame.get("type") != "error", frame
        if frame.get("type") in ("patch", "html_update") and frame.get("ref") == ref:
            return
    raise AssertionError(f"no render for event {ref}")


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["spans", "markdown", "terminal"])
async def test_deep_markup_is_rendered_and_the_connection_stays_up(kind):
    from django.test import override_settings

    assert _rust.MAX_VDOM_DEPTH == 256
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[_MODULE]):
        communicator = await _connect_and_mount()
        for ref, event_params in enumerate(({"kind": kind}, {"kind": kind}, {}), start=1):
            name = "ping" if not event_params else "deep"
            await communicator.send_json_to(
                {"type": "event", "event": name, "params": event_params, "ref": ref}
            )
            await _render_for(communicator, ref)
        await communicator.disconnect()
