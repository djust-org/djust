"""Keep an SSE session a test leaves registered from reaching the next test.

``djust.sse._sse_sessions`` is a process-wide registry. A test that opens an SSE
stream through ``DjustSSEStreamView`` and never closes the stream leaves its
``SSESession`` there. For an explicit view that session holds the runtime's
``_explicit_save_pending`` task, bound to the test's event loop, which closes with
the test. A later test that gathers the pending saves of every registered session
(``python/djust/tests/test_sse_save_teardown_3212.py``) then failed with "The
future belongs to a different loop" whenever such a test ran first in the same
process::

    pytest -p no:randomly -n 0 python/djust/tests/test_push_handler_marker_3002.py \\
           python/djust/tests/test_sse_save_teardown_3212.py

The root ``conftest.py`` runs :func:`release_sse_sessions` after every test.
"""

from __future__ import annotations

import contextlib
import logging
import sys

_MODULE = "djust.sse"

logger = logging.getLogger(__name__)


def release_sse_sessions() -> None:
    """Shut down and drop every session left in the registry; nothing to do when it is empty.

    Never import ``djust.sse`` here: a test that did not load it has no session,
    and importing it would change the import footprint the tests pin.

    Each session goes through its own ``SSESession.shutdown`` (the stream close's
    teardown: the tick, the queued pushes and the view are released). A save or
    catch-up task the runtime still holds is cancelled if it has not finished.
    A task of a loop that has already closed cannot be woken, and cancelling
    it raises ``RuntimeError``; there is then nothing left to stop.
    """
    module = sys.modules.get(_MODULE)
    if module is None or not module._sse_sessions:
        return
    with module._sse_registry_lock:
        sessions = list(module._sse_sessions.values())
        module._sse_sessions.clear()
    for session in sessions:
        try:
            session.shutdown()
        except Exception:  # noqa: BLE001 - app hooks; a failed teardown must not fail the next test
            logger.warning("left SSE session %s did not shut down cleanly", type(session).__name__)
        runtime = getattr(session, "runtime", None)
        for name in ("_explicit_save_pending", "_explicit_catch_up"):
            task = getattr(runtime, name, None)
            if task is None:
                continue
            if not task.done():
                with contextlib.suppress(RuntimeError):
                    task.cancel()
            setattr(runtime, name, None)
