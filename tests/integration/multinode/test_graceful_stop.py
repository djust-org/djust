"""Save frequency: a deferred save across real processes (design: save frequency).

With ``DJUST_STATE_SAVE_INTERVAL`` set on every node, a WebSocket turn of a
view with no declared persistence answers before its state is stored. Here,
with real uvicorn processes sharing one session database:

* SIGTERM (a deploy's graceful stop): uvicorn closes the socket, the close
  flushes, and the reconnect to the other process restores every change;
* SIGKILL before any flush (an OOM kill): the reconnect restores the last
  save, at most the interval old — the documented bound;
* the same SIGKILL with the interval at 0 (today's default): every change is
  restored, as ``test_kill_after_save`` pins for the counter.

The SIGKILL pair is the gate-off of deferral: the first cannot pass if turns
were still saved before their frames, the second cannot pass if the default
deferred anything.
"""

from __future__ import annotations

import pytest

from tests.integration.multinode.harness import Browser, Cluster, kinds, probe_from

pytestmark = pytest.mark.slow

PLAIN = "tests.integration.multinode.app.views.PlainCounterView"


def _two_events_on_a(cluster: Cluster):
    browser = Browser(cluster)
    status, body, _ = browser.get("A", "/plain/")
    assert status == 200, body[:300]
    socket_a = browser.socket("A")
    assert kinds(socket_a.mount(PLAIN, "/plain/")) == ["mount"]
    socket_a.event("increment")
    socket_a.event("increment")
    return browser, socket_a


def _reconnect_to_b(browser: Browser, socket_a, cluster: Cluster):
    socket_b = browser.socket("B")
    probe = probe_from(socket_b.mount(PLAIN, "/plain/", reconnect_from=socket_a))
    socket_b.close()
    assert probe["mounted_pid"] == cluster["B"].pid
    return probe["plain_attr"], probe["private_attr"]


def test_a_graceful_stop_keeps_deferred_state():
    with Cluster("db", "memory", None, save_interval=60) as cluster:
        browser, socket_a = _two_events_on_a(cluster)
        cluster["A"].terminate()
        assert socket_a.recv() is None
        assert _reconnect_to_b(browser, socket_a, cluster) == (2, 2)


def test_a_kill_between_saves_restores_the_last_save():
    with Cluster("db", "memory", None, save_interval=60) as cluster:
        browser, socket_a = _two_events_on_a(cluster)
        cluster["A"].kill()
        assert socket_a.recv() is None
        assert _reconnect_to_b(browser, socket_a, cluster) == (0, 0)


def test_with_the_interval_at_zero_a_kill_loses_nothing():
    with Cluster("db", "memory", None) as cluster:
        browser, socket_a = _two_events_on_a(cluster)
        cluster["A"].kill()
        assert socket_a.recv() is None
        assert _reconnect_to_b(browser, socket_a, cluster) == (2, 2)


# ---------------------------------------------------------------------------
# What a graceful stop keeps, per ASGI server (#4159)
# ---------------------------------------------------------------------------

#: What each server does on SIGTERM while a socket holds two unsaved changes,
#: as recorded by the test below; the deployment guide's table states the same.
#: ``exits``: the process stops on its own with the socket still open (else it
#: waits until the client leaves). ``lifespan``: the ASGI lifespan shutdown
#: reaches the application. ``restored``: what the reconnect to another
#: process shows (2, 2: both changes; 0, 0: the last save, before them).
SERVER_ROWS = {
    # Closes each socket (1012), whose close saves, then runs lifespan.
    "uvicorn": {"exits": True, "lifespan": True, "restored": (2, 2)},
    # Keeps an open WebSocket open: the stop waits until the client leaves
    # (its close saves) or a supervisor's SIGKILL ends it (the kill rows).
    "hypercorn": {"exits": False, "lifespan": True, "restored": (2, 2)},
    # gunicorn with uvicorn's worker: the arbiter forwards SIGTERM to the
    # worker, which stops as uvicorn does.
    "gunicorn": {"exits": True, "lifespan": True, "restored": (2, 2)},
    # Cancels the application instances and runs no lifespan: the unsaved
    # changes are lost, at most one interval of them.
    "daphne": {"exits": True, "lifespan": False, "restored": (0, 0)},
}

#: How long a server gets to stop on its own before it is taken as waiting.
STOP_WAIT_S = 8


def _server_available(server: str) -> bool:
    import importlib.util
    import os

    if importlib.util.find_spec(server) is not None:
        return True
    extra = os.environ.get("DJUST_MN_SERVER_PATH")
    return bool(extra) and os.path.isdir(os.path.join(extra, server))


def _printed_after_exit(node) -> list:
    """Every line the (exited) node printed."""
    import queue

    lines = list(node.lines.seen)
    while True:
        try:
            line = node.lines.queue.get(timeout=5)
        except queue.Empty:
            return lines
        if line is None:
            return lines
        lines.append(line)


@pytest.mark.parametrize("server", sorted(SERVER_ROWS))
def test_what_a_graceful_stop_keeps_per_server(server):
    """SIGTERM a node holding two deferred changes, under each ASGI server,
    with the application wrapped in ``with_lifespan``; the reconnect to the
    other (uvicorn) node shows what was saved. A server that is not installed
    is skipped (``DJUST_MN_SERVER_PATH`` names a directory holding one that is
    not in the venv)."""
    import os
    import signal
    import subprocess

    if not _server_available(server):
        pytest.skip("%s is not installed" % server)
    row = SERVER_ROWS[server]
    with Cluster(
        "db",
        "memory",
        None,
        save_interval=60,
        lifespan=True,
        node_env={"A": {"DJUST_MN_CONFIG_SERVER": server}},
    ) as cluster:
        browser, socket_a = _two_events_on_a(cluster)
        process = cluster["A"].process
        os.kill(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=STOP_WAIT_S)
            exited = True
        except subprocess.TimeoutExpired:
            exited = False
        assert exited is row["exits"], (server, "exits on SIGTERM with a socket open")
        if exited:
            assert socket_a.recv() is None
        else:
            socket_a.close()  # the client leaves; the server can finish stopping
            process.wait(timeout=STOP_WAIT_S)
        printed = _printed_after_exit(cluster["A"])
        assert ("LIFESPAN_SHUTDOWN" in printed) is row["lifespan"], (server, printed)
        assert _reconnect_to_b(browser, socket_a, cluster) == row["restored"]
