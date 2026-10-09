"""Weak serving-loop registry; importing this never creates clock resources."""

import asyncio
import threading
import weakref

_lock = threading.Lock()
_loops = weakref.WeakSet()


def register_serving_loop() -> None:
    with _lock:
        _loops.add(asyncio.get_running_loop())


def is_serving_loop(loop: asyncio.AbstractEventLoop) -> bool:
    with _lock:
        return loop in _loops and loop.is_running() and not loop.is_closed()
