"""Entry point of one harness node (#4000).

``python -m tests.integration.multinode.app.serve --fd N --name A`` serves the harness app
with uvicorn on the listening socket the parent bound and passed as ``fd`` N.
The parent keeps that socket, so a node killed with SIGKILL can be started
again on the same port, and a client that connects before the node is ready
waits in the socket's backlog instead of racing a port probe.

``--setup`` instead creates the shared database's tables and the test users,
once, before any node starts.

Instrumentation, all test-only and in this process:

* ``NODE_STATS`` counts logical session reads and writes (``SessionStore``
  ``load``/``save`` and their async twins, nested calls counted once) and, for
  the database engines, the SQL statements that touch ``django_session``;
* a save gate: once ``/_mn/gate`` is called, the next explicit state save
  writes ``SAVE_HELD`` on stdout and blocks until the process is killed;
* a work gate: background work started by a harness view writes
  ``WORK_STARTED`` and waits for ``/_mn/release``;
* ``DJUST_MN_CLOCK_OFFSET``: this node's view of the wall clock, in seconds,
  as the server-state envelope reads it (a node whose clock is skewed).
"""

from __future__ import annotations

import argparse
import functools
import json
import os
import sys
import threading

if __name__ == "__main__":
    # Views import this module by name: make that the running module, so they
    # see the same counters and gates.
    sys.modules.setdefault("tests.integration.multinode.app.serve", sys.modules[__name__])

NODE_STATS: dict = {}
_STATS_LOCK = threading.Lock()
_DEPTH = threading.local()
SAVE_GATE = threading.Event()
WORK_RELEASE = threading.Event()
NODE_NAME = os.environ.get("DJUST_MN_NODE", "?")


def mark(line: str) -> None:
    """One stdout line, written in one call (the parent waits on these)."""
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def _count(key: str) -> None:
    with _STATS_LOCK:
        NODE_STATS[key] = NODE_STATS.get(key, 0) + 1


def reset_stats() -> None:
    with _STATS_LOCK:
        NODE_STATS.clear()


def _counted(kind: str, fn):
    if getattr(fn, "_mn_counted", False):
        return fn
    if kind.startswith("a"):

        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            depth = getattr(_DEPTH, "n", 0)
            if depth == 0:
                _count("session_" + kind[1:])
            _DEPTH.n = depth + 1
            try:
                return await fn(*args, **kwargs)
            finally:
                _DEPTH.n = depth

    else:

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            depth = getattr(_DEPTH, "n", 0)
            if depth == 0:
                _count("session_" + kind)
            _DEPTH.n = depth + 1
            try:
                return fn(*args, **kwargs)
            finally:
                _DEPTH.n = depth

    wrapper._mn_counted = True
    return wrapper


def _instrument() -> None:
    from importlib import import_module

    from django.conf import settings
    from django.db.backends.signals import connection_created

    store = import_module(settings.SESSION_ENGINE).SessionStore
    for name, kind in (("load", "load"), ("save", "save"), ("aload", "aload"), ("asave", "asave")):
        method = getattr(store, name, None)
        if method is not None:
            setattr(store, name, _counted(kind, method))

    def _sql(execute, sql, params, many, context):
        if "django_session" in sql:
            verb = sql.lstrip().split(None, 1)[0].upper()
            _count("sql_" + verb.lower())
        return execute(sql, params, many, context)

    def _on_connect(sender, connection, **kwargs):
        if _sql not in connection.execute_wrappers:
            connection.execute_wrappers.append(_sql)

    connection_created.connect(_on_connect, weak=False)

    # The save gate: hold the next explicit save until this process dies.
    from djust import _exposure_sessions

    original_save = _exposure_sessions.ServerStateSession.save

    def gated_save(self, values):
        if SAVE_GATE.is_set():
            mark("SAVE_HELD")
            threading.Event().wait()
        return original_save(self, values)

    _exposure_sessions.ServerStateSession.save = gated_save

    offset = int(os.environ.get("DJUST_MN_CLOCK_OFFSET", "0") or 0)
    if offset:
        import time as _real_time

        class _SkewedClock:
            def time(self):
                return _real_time.time() + offset

        _exposure_sessions.time = _SkewedClock()


def setup() -> None:
    import django
    from django.core.management import call_command

    django.setup()
    call_command("migrate", verbosity=0, interactive=False)
    from django.contrib.auth.models import User

    for name in ("alice", "bob"):
        User.objects.get_or_create(username=name)


def _exit_with_parent() -> None:
    sys.stdin.read()
    os._exit(0)


def serve(fd: int) -> None:
    import uvicorn

    from tests.integration.multinode.app.asgi import application

    _instrument()
    # The parent holds this process's stdin open for as long as it lives: when
    # it exits, however it exits, the pipe closes and so does this node.
    threading.Thread(target=_exit_with_parent, daemon=True).start()
    config = uvicorn.Config(
        application,
        fd=fd,
        log_level="warning",
        lifespan="off",
        ws="websockets",
        ws_ping_interval=None,
    )
    server = uvicorn.Server(config)
    # The listening socket already exists (the parent bound it), so a client
    # that connects from here on is queued until uvicorn accepts it.
    import django

    mark("READY %s %d %s" % (NODE_NAME, os.getpid(), django.get_version()))
    server.run()


def main() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tests.integration.multinode.app.settings")
    parser = argparse.ArgumentParser()
    parser.add_argument("--fd", type=int)
    parser.add_argument("--setup", action="store_true")
    args = parser.parse_args()
    if args.setup:
        setup()
        mark(json.dumps({"setup": "ok"}))
        return
    serve(args.fd)


if __name__ == "__main__":
    main()
