"""#3248: the legacy private-state session save honours the framework-internal list.

The snapshot, assigns, time-travel and exposure paths all skip
``_FRAMEWORK_INTERNAL_ATTRS`` (``live_view.py``). The private-state capture
(``_snapshot_user_private_attrs`` / ``_get_private_state``) and restore
(``_restore_private_state``) checked only the init-time ``_framework_attrs``,
so a framework attribute first set during ``mount()`` was recorded as user
private state, saved to ``liveview_<path>__private`` and restored on the next
mount.

The issue called this unreachable. It is reachable today, twice over:

- ``start_async()`` in ``mount()`` creates ``_async_tasks`` and
  ``_async_task_counter``, both in the framework list, and both were saved.
- On the WebSocket, the transport writes per-connection identity
  (``_websocket_session_id``, ``_django_session_key``, ...) after
  ``__init__`` and before ``mount()``. Those names were missing from the
  list, so the event save carried them, and a reconnect's session restore
  replaced the new connection's values with the previous connection's. They
  are now in the list.

All private-state saves and restores (the HTTP GET, the runtime event save,
the reconnect restore and the sticky-child save) go through these three
methods, so the filter lives there. The WebSocket tests below drive the real
event save and the real reconnect restore.
"""

from __future__ import annotations

import pytest
from asgiref.sync import sync_to_async
from django.test import RequestFactory, override_settings
from django.urls import path

from djust import LiveView
from djust.decorators import event_handler
from djust.live_view import _FRAMEWORK_INTERNAL_ATTRS

pytest.importorskip("channels")

_MOD = __name__
VIEW = f"{_MOD}.Private3248Page"
URL = "/p3248/"
#: Framework flags a (future) mount could set; ``False`` keeps the view usable.
FLAGS = ("_djust_waiters_closed", "_djust_child_disposed")
#: Created by ``start_async()`` in ``mount()``: the case reachable today.
ASYNC_ATTRS = ("_async_tasks", "_async_task_counter")
#: Per-connection identity the transports set before ``mount()``. The event
#: save carried them, and a reconnect restored the previous connection's.
CONNECTION_ATTRS = (
    "_websocket_session_id",
    "_websocket_path",
    "_websocket_query_string",
    "_websocket_host",
    "_websocket_secure",
    "_django_session_key",
    "_djust_mount_view_path",
)
#: Every instance that rendered, so a test can inspect the one a mount built.
RENDERED: list = []


class Private3248Page(LiveView):
    exposure_policy = "legacy"
    enable_state_snapshot = True
    template = f'<div dj-root dj-view="{VIEW}" dj-id="0"><span>count={{{{ count }}}}</span></div>'

    def mount(self, request, **kwargs):
        self.count = 0
        self._mine = 1
        for flag in FLAGS:
            setattr(self, flag, False)
        self.start_async(self._work)

    def _work(self):
        return None

    def get_context_data(self, **kwargs):
        RENDERED.append(self)  # the instance a WebSocket mount built
        return super().get_context_data(count=self.count, **kwargs)

    @event_handler()
    def bump(self, **kwargs):
        self.count += 1
        self._mine += 1


urlpatterns = [path("p3248/", Private3248Page.as_view())]
_SETTINGS = override_settings(LIVEVIEW_ALLOWED_MODULES=[_MOD], ROOT_URLCONF=_MOD)


def _mounted_view():
    view = Private3248Page()
    request = RequestFactory().get(URL)
    view.request = request
    view.mount(request)
    view._snapshot_user_private_attrs()
    return view


def test_the_capture_skips_framework_attributes_set_during_mount():
    view = _mounted_view()
    for name in FLAGS + ASYNC_ATTRS:
        assert name in view.__dict__, f"precondition: mount set {name}"
        assert name not in view._user_private_keys, name
    private = view._get_private_state()
    assert private.get("_mine") == 1, private
    assert not set(private) & _FRAMEWORK_INTERNAL_ATTRS, private


def test_a_framework_name_already_tracked_is_still_not_saved():
    """A session written before this fix restored the flags into
    ``_user_private_keys``; the save must not carry them forward."""
    view = _mounted_view()
    view._user_private_keys.update(FLAGS + ASYNC_ATTRS)
    private = view._get_private_state()
    assert "_mine" in private
    assert not set(private) & _FRAMEWORK_INTERNAL_ATTRS, private


def test_the_restore_skips_framework_attributes():
    view = Private3248Page()
    view._user_private_keys = set()
    view._restore_private_state(
        {
            "_mine": 7,
            "_djust_waiters_closed": True,
            "_djust_child_disposed": True,
            "_async_task_counter": 41,
        }
    )
    assert view._mine == 7
    for name in ("_djust_waiters_closed", "_djust_child_disposed", "_async_task_counter"):
        assert name not in view.__dict__, name
        assert name not in view._user_private_keys, name


# --------------------------------------------------------------------------- #
# The real paths: the WebSocket event save and the reconnect restore.
# --------------------------------------------------------------------------- #


class _ScopeSession:
    def __init__(self, key):
        self.session_key = key


def _new_session(private=None):
    from django.contrib.sessions.backends.db import SessionStore

    session = SessionStore()
    if private is not None:
        session[f"liveview_{URL}"] = {"count": 5}
        session[f"liveview_{URL}__private"] = private
    session.create()
    return session.session_key


def _stored(session_key):
    from django.contrib.sessions.backends.db import SessionStore

    return dict(SessionStore(session_key).load())


async def _connect(session_key):
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    communicator.scope["session"] = _ScopeSession(session_key)
    connected, _ = await communicator.connect()
    assert connected
    await communicator.receive_json_from(timeout=2)  # connect ack
    return communicator


async def _frames(communicator):
    frames = []
    while not await communicator.receive_nothing(timeout=0.3):
        frames.append(await communicator.receive_json_from(timeout=2))
    return frames


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_the_event_save_does_not_write_framework_attributes():
    with _SETTINGS:
        session_key = await sync_to_async(_new_session)()
        communicator = await _connect(session_key)
        try:
            await communicator.send_json_to({"type": "mount", "view": VIEW, "url": URL})
            await _frames(communicator)
            await communicator.send_json_to({"type": "event", "event": "bump", "params": {}})
            frames = await _frames(communicator)
            assert not [f for f in frames if f.get("type") == "error"], frames
        finally:
            await communicator.disconnect()
        stored = await sync_to_async(_stored)(session_key)
    private = stored.get(f"liveview_{URL}__private")
    assert private is not None, "the event save never wrote the private state; vacuous"
    # Exactly the user's private state. ``_action_state`` is deliberately not
    # framework-internal (templates read it, ``live_view.py``).
    assert set(private) == {"_mine", "_action_state"}, sorted(private)
    assert private["_mine"] == 2, private


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_the_reconnect_restore_does_not_set_framework_attributes():
    with _SETTINGS:
        RENDERED.clear()
        session_key = await sync_to_async(_new_session)(
            {
                "_mine": 7,
                "_djust_waiters_closed": True,
                "_djust_child_disposed": True,
                **{name: "stale" for name in CONNECTION_ATTRS},
            }
        )
        communicator = await _connect(session_key)
        try:
            await communicator.send_json_to({"type": "mount", "view": VIEW, "url": URL})
            frames = await _frames(communicator)
            assert [f for f in frames if f.get("type") == "mount"], frames
            # Read while connected: the disconnect closes the waiters and so
            # sets ``_djust_waiters_closed`` itself.
            view = RENDERED[-1]
            assert view.count == 5, "the session restore did not run; vacuous"
            assert view._mine == 7
            for name in CONNECTION_ATTRS:
                assert view.__dict__.get(name) != "stale", name
            for flag in FLAGS:
                assert flag not in view.__dict__, flag
        finally:
            await communicator.disconnect()
