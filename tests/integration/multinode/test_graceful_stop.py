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
