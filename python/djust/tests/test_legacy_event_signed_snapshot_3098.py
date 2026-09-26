"""Legacy views refresh their signed back-navigation snapshot after events (#3098).

A legacy (``enable_state_snapshot = True``) view shipped its signed snapshot
only on the mount frame. Events never refreshed it, while explicit views get a
fresh token on every committed turn. So when Back had to restore from the
signed snapshot (the server-saved state gone), a legacy view came back at its
mount-time state, not its latest.

Harness: a real ``WebsocketCommunicator`` against ``LiveViewConsumer`` (events
route through ``ViewRuntime.dispatch_event`` since the #1909 flip), a real DB
session, and the restore driven the way the client drives it — echoing the
token the last event frame carried, with the server-saved state dropped so the
signed path is the one taken (the C4 browser test's shape).
"""

from __future__ import annotations

import pytest
from asgiref.sync import sync_to_async
from django.test import override_settings

from djust import LiveView
from djust.decorators import event_handler

_MOD = "djust.tests.test_legacy_event_signed_snapshot_3098"
_ALLOWLIST = override_settings(LIVEVIEW_ALLOWED_MODULES=[_MOD])
VIEW = f"{_MOD}.Counter3098"


class Counter3098(LiveView):
    enable_state_snapshot = True
    template = f'<div dj-root dj-view="{VIEW}" dj-id="0">n={{{{ n }}}}</div>'

    def mount(self, request, **kwargs):
        self.n = 0

    @event_handler()
    def increment(self, **kwargs):
        self.n += 1

    @event_handler()
    def nothing(self, **kwargs):
        pass

    def get_context_data(self, **kwargs):
        return {"n": self.n}


class NoSnapshot3098(Counter3098):
    enable_state_snapshot = False
    template = f'<div dj-root dj-view="{_MOD}.NoSnapshot3098" dj-id="0">n={{{{ n }}}}</div>'


class _ScopeSession:
    def __init__(self, key):
        self.session_key = key


async def _connect():
    pytest.importorskip("channels")
    from channels.testing import WebsocketCommunicator
    from django.contrib.sessions.backends.db import SessionStore

    from djust.websocket import LiveViewConsumer

    def _create():
        s = SessionStore()
        s.create()
        return s.session_key

    session_key = await sync_to_async(_create)()
    communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    communicator.scope["session"] = _ScopeSession(session_key)
    connected, _ = await communicator.connect()
    assert connected
    await communicator.receive_json_from(timeout=2)  # connect ack
    return communicator, session_key


async def _until(communicator, pred):
    with _ALLOWLIST:
        for _ in range(12):
            msg = await communicator.receive_json_from(timeout=3)
            if pred(msg):
                return msg
    raise AssertionError("expected frame never arrived")


async def _mount(communicator, view, url, **extra):
    frame = {"type": "mount", "view": view, "url": url, **extra}
    with _ALLOWLIST:
        await communicator.send_json_to(frame)
    return await _until(communicator, lambda m: m.get("type") == "mount")


async def _event(communicator, name):
    with _ALLOWLIST:
        await communicator.send_json_to({"type": "event", "event": name, "params": {}})
    return await _until(
        communicator, lambda m: m.get("type") in ("patch", "html_update", "noop", "error")
    )


def _drop_saved_state(session_key, url):
    from django.contrib.sessions.backends.db import SessionStore

    s = SessionStore(session_key=session_key)
    s.pop(f"liveview_{url}", None)
    s.save()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
class TestLegacyEventSnapshot:
    async def test_event_frame_carries_a_fresh_signed_snapshot(self):
        from djust.security import unsign_snapshot

        communicator, session_key = await _connect()
        try:
            mounted = await _mount(communicator, VIEW, "/c3098/")
            assert isinstance(mounted.get("state_snapshot_signed"), str)
            frame = await _event(communicator, "increment")
            assert frame["type"] in ("patch", "html_update"), frame
            token = frame.get("state_snapshot_signed")
            assert isinstance(token, str) and token, frame
            assert frame.get("view") == VIEW
            inner = unsign_snapshot(token, VIEW, session_key)
            assert inner is not None and '"n":1' in inner, inner
        finally:
            await communicator.disconnect()

    async def test_back_restores_the_latest_state_not_the_mount_state(self):
        url = "/c3098-back/"
        communicator, session_key = await _connect()
        try:
            await _mount(communicator, VIEW, url)
            await _event(communicator, "increment")
            frame = await _event(communicator, "increment")
            token = frame.get("state_snapshot_signed")
            assert token, frame
        finally:
            await communicator.disconnect()

        # The server-saved state is gone, so Back must use the signed token.
        await sync_to_async(_drop_saved_state)(session_key, url)
        from channels.testing import WebsocketCommunicator

        from djust.websocket import LiveViewConsumer

        again = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        again.scope["session"] = _ScopeSession(session_key)
        connected, _ = await again.connect()
        assert connected
        await again.receive_json_from(timeout=2)
        try:
            restored = await _mount(
                again, VIEW, url, state_snapshot={"view_slug": VIEW, "state_json": token}
            )
            assert "n=2" in restored.get("html", ""), restored.get("html")
        finally:
            await again.disconnect()

    async def test_a_state_unchanging_event_sends_no_snapshot(self):
        communicator, _ = await _connect()
        try:
            await _mount(communicator, VIEW, "/c3098-noop/")
            frame = await _event(communicator, "nothing")
            assert frame["type"] == "noop", frame
            assert "state_snapshot_signed" not in frame
        finally:
            await communicator.disconnect()

    async def test_a_view_that_does_not_opt_in_never_ships_state(self):
        communicator, _ = await _connect()
        try:
            await _mount(communicator, f"{_MOD}.NoSnapshot3098", "/c3098-off/")
            frame = await _event(communicator, "increment")
            assert frame["type"] in ("patch", "html_update"), frame
            assert "state_snapshot_signed" not in frame
        finally:
            await communicator.disconnect()
