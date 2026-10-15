"""Channels' short defaults must not make djust's WebSocket tests flaky."""

import asyncio
import inspect

import pytest
from channels.testing import WebsocketCommunicator


async def _silent_app(scope, receive, send):
    await asyncio.Event().wait()


@pytest.mark.parametrize(
    "method_name",
    ("disconnect", "receive_output", "receive_from", "receive_json_from"),
)
def test_communicator_default_is_thirty_seconds(method_name):
    method = getattr(WebsocketCommunicator, method_name)
    assert inspect.signature(method).parameters["timeout"].default == 30


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
