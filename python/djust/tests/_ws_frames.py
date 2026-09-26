"""Event-driven frame collection for ``WebsocketCommunicator`` tests (#3130).

The old collectors in this tree read frames "until the socket has been quiet
for 0.5 s". That is a wall-clock assertion: under ``-n auto`` saturation the
frame a test waits for can arrive after the window, so the collector returns
``[]`` (or the late frame lands in the NEXT collection). CLAUDE.md: a
concurrency test asserts a logical ordering invariant, never a duration.

The shape here:

* :func:`receive_until` blocks until a CONDITION holds (the expected frame
  arrived, or a log line was written), with a generous deadline that only a
  hung turn can reach. It never returns early because the machine was slow.
* :func:`drain_extra` keeps the "and nothing else arrived" check as a trailing
  quiet window. A late frame can make it MISS an extra frame; it can never make
  a correct run fail.

Both poll with ``receive_nothing`` and only call ``receive_output`` when a frame
is already queued. ``receive_output``'s own timeout path cancels the
application under test, so it must never be the thing that waits.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable, Dict, List

#: Deadline for a frame that WILL arrive. Only a hung turn reaches it.
WAIT_S = 10.0

#: Quiet window for the trailing "nothing else arrived" check.
TRAILING_QUIET_S = 0.3

Frame = Dict[str, Any]


def _decode(output: Dict[str, Any]) -> Frame:
    """A text frame as its JSON object; a close as the raw ASGI message.

    djust never sends a frame whose ``type`` is ``websocket.close``, so a
    caller tells a close apart by that type.
    """
    if output.get("type") == "websocket.close":
        return dict(output)
    return json.loads(output["text"])


async def receive_until(
    socket,
    done: Callable[[List[Frame]], bool],
    *,
    timeout: float = WAIT_S,
    what: str = "the expected frames",
    allow_close: bool = False,
) -> List[Frame]:
    """Frames, in order, up to the one after which ``done(frames)`` holds.

    ``done`` is re-checked on every poll, not only when a frame arrives, so it
    may also wait on state outside the socket (a log line, a handler having
    run). ``done`` sees a close as a ``{"type": "websocket.close", ...}``
    entry, so a caller that expects the socket to close says so in ``done``.
    A close that arrives while ``done`` is still false fails loudly: nothing
    can follow it, and returning quietly would let a caller that dropped its
    own assertion pass on a socket that closed without the expected frame.
    ``allow_close=True`` returns the frames instead, for a caller that reads
    the close itself. Also fails loudly, naming what it waited for and what it
    got, if the deadline passes first.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    frames: List[Frame] = []
    while not done(frames):
        if frames and frames[-1].get("type") == "websocket.close":
            if allow_close:
                break
            raise AssertionError(f"the socket closed before {what}; received {frames!r}")
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise AssertionError(f"waited {timeout}s for {what}; received {frames!r}")
        if not await socket.receive_nothing(timeout=min(remaining, 0.05), interval=0.01):
            frames.append(_decode(await socket.receive_output(timeout=3)))
    return frames


def types_of(frames: List[Frame]) -> List[Any]:
    return [frame.get("type") for frame in frames]


def has_type(*types: str) -> Callable[[List[Frame]], bool]:
    """``done`` predicate: a frame of one of ``types`` has arrived."""
    wanted = set(types)
    return lambda frames: any(frame.get("type") in wanted for frame in frames)


async def receive_type(socket, *types: str, timeout: float = WAIT_S) -> List[Frame]:
    """Frames up to and including the first one whose type is in ``types``."""
    return await receive_until(socket, has_type(*types), timeout=timeout, what=f"a {types} frame")


async def drain_extra(socket, quiet: float = TRAILING_QUIET_S) -> List[Frame]:
    """Whatever else is queued, until the socket is quiet for ``quiet`` s.

    Only for "nothing else arrived" checks: a slow machine can make this miss a
    late frame, never fail a correct run. Bounded by ``WAIT_S`` so a socket
    that never goes quiet (a ticking view) cannot hang the test.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + WAIT_S
    frames: List[Frame] = []
    while loop.time() < deadline and not await socket.receive_nothing(timeout=quiet, interval=0.01):
        frame = _decode(await socket.receive_output(timeout=3))
        frames.append(frame)
        if frame.get("type") == "websocket.close":
            break
    return frames


async def wait_until(
    condition: Callable[[], bool], *, timeout: float = WAIT_S, what: str = "the condition"
) -> None:
    """Yield to the loop until ``condition()`` holds; fail loudly at the deadline.

    The event-driven replacement for ``await asyncio.sleep(0.2)`` followed by
    an assertion that the awaited thing has happened by then.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not condition():
        if loop.time() >= deadline:
            raise AssertionError(f"waited {timeout}s for {what}")
        await asyncio.sleep(0.01)
