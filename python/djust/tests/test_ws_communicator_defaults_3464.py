"""Channels' short defaults must not make djust's WebSocket tests flaky."""

import asyncio
import inspect
import threading
import time

import pytest
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.testing import HttpCommunicator, WebsocketCommunicator


async def _silent_app(scope, receive, send):
    await asyncio.Event().wait()


#: The one wait that is a window for ABSENCE; see the root ``conftest.py``.
ABSENCE_WINDOWS = {"receive_nothing"}


def _timeout_waits():
    """Every ``(class, name, default)`` of a Channels communicator wait.

    Read off the classes, not listed: the list this test used to carry named
    four methods, which is exactly how ``connect`` (a 1 s wait for the consumer
    to accept) stayed at Channels' default and timed out on a loaded runner
    (#3692).
    """
    seen = {}
    for communicator in (WebsocketCommunicator, HttpCommunicator):
        for cls in communicator.__mro__:
            for name, func in vars(cls).items():
                if name.startswith("_") or not inspect.iscoroutinefunction(func):
                    continue
                param = inspect.signature(func).parameters.get("timeout")
                if param is None or not isinstance(param.default, (int, float)):
                    continue
                seen[id(func)] = (cls.__name__, name, param.default)
    return sorted(seen.values())


def test_the_derived_list_is_not_empty_and_names_connect():
    """The derivation finds the methods; ``connect`` is the one a hand list lost."""
    names = {name for _, name, _ in _timeout_waits()}
    assert {"connect", "disconnect", "receive_output", "receive_json_from"} <= names
    assert "receive_nothing" in names, "the absence window is derived too, and exempted by name"


def test_every_communicator_wait_defaults_to_thirty_seconds():
    """Read at run time: the root conftest patches the defaults after collection."""
    short = [
        f"{owner}.{name} waits {default} s"
        for owner, name, default in _timeout_waits()
        if name not in ABSENCE_WINDOWS and default != 30
    ]
    assert short == []


def test_the_absence_window_is_left_short():
    defaults = {name: default for _, name, default in _timeout_waits()}
    assert defaults["receive_nothing"] < 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method_name",
    ("disconnect", "receive_output", "receive_from", "receive_json_from"),
)
async def test_explicit_communicator_timeout_still_wins(method_name):
    communicator = WebsocketCommunicator(_silent_app, "/ws/")
    if method_name == "disconnect":
        seen = []

        async def record_wait(timeout):
            seen.append(timeout)

        communicator.wait = record_wait
        await communicator.disconnect(timeout=0.01)
        assert seen == [0.01]
        communicator.future.cancel()
        return
    with pytest.raises(asyncio.TimeoutError):
        await getattr(communicator, method_name)(timeout=0.01)


# ── #3692: the defaults reach the one call that failed on CI ──


class _AcceptingConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        await self.accept()


@pytest.mark.django_db  # ``close_old_connections`` runs on the executor thread
@pytest.mark.asyncio
async def test_connect_waits_out_a_stalled_thread_sensitive_executor():
    """``communicator.connect()`` survived a 1.5 s stall before the consumer ran.

    Channels (4.2+) awaits ``aclose_old_connections()`` before it dispatches
    ``websocket.connect``, and that is a thread-sensitive ``sync_to_async``: it
    runs on the ONE executor thread every such call in the process shares. A
    job already on that thread (an earlier test's handler, a slow session save)
    holds the consumer back until it ends. ``test_anonymous_ws_mount_is_refused
    [RaiseAfterCheckView]`` timed out in ``connect()`` on CI at Channels' 1 s
    default (#3692); the conftest hook that lengthens the waits had left
    ``connect`` out.

    The stall is forced as state, not as luck: the executor is occupied for
    longer than the old default, so a ``connect()`` on the old default fails on
    every run, whatever the machine.
    """
    from asgiref.sync import SyncToAsync

    release = threading.Event()
    try:
        SyncToAsync.single_thread_executor.submit(release.wait, 30)
        communicator = WebsocketCommunicator(_AcceptingConsumer.as_asgi(), "/ws/")
        asyncio.get_running_loop().call_later(1.5, release.set)
        started = time.monotonic()
        connected, _ = await communicator.connect()
        waited = time.monotonic() - started
        await communicator.disconnect()
    finally:
        release.set()
    assert connected
    assert waited >= 1.4, "the stall did not delay connect(): the test no longer forces it"
