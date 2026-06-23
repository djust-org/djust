"""Regression tests for the systemic global-isolation fixture (#1883, #1882).

The autouse ``_reset_djust_globals`` fixture (``tests/conftest.py`` and
``python/djust/tests/conftest.py``) resets djust's leak-prone process-globals
before every test, retiring the shared-process-global flaky-test class that
produced #1862 (PR #1874), #1875 (PR #1881), and #1882.

This file pins:

1. The fixture is wired and resets each global it claims to (unit-level pins on
   ``djust.test_isolation.reset_djust_globals`` — gate-off-verifiable per-global).
2. The #1882 cure end-to-end: a deterministic, gate-off reproduction of the
   channel-layer wire-version drift, proving the channel-layer reset is what
   fixes it. With the reset, a stray ``djust_hotreload`` frame on a STALE shared
   layer cannot reach the victim consumer (it's on a fresh layer), so the
   time-travel jump lands at version 3; without the reset, it lands at 4 — the
   exact ``got 4`` #1882 symptom.
"""

from __future__ import annotations

import asyncio
import itertools

import pytest
from asgiref.sync import sync_to_async
from django.test import override_settings


# ---------------------------------------------------------------------------
# (1) Unit pins on reset_djust_globals — one per global it resets.
# ---------------------------------------------------------------------------


def test_reset_clears_channel_layer_backends():
    """The Channels layer cache is dropped (the #1875/#1882 mechanism)."""
    pytest.importorskip("channels")
    from channels.layers import channel_layers, get_channel_layer

    from djust.test_isolation import reset_djust_globals

    # Force a cached backend to exist, then assert reset drops it.
    get_channel_layer()
    assert channel_layers.backends, "expected a cached channel-layer backend"
    reset_djust_globals()
    assert not channel_layers.backends, (
        "reset_djust_globals must clear channel_layers.backends so each test "
        "connects to a fresh, unpolluted InMemoryChannelLayer (#1875/#1882)"
    )


def test_reset_resets_view_id_counter():
    """``mixins.sticky._view_id_counter`` is reset to a fresh ``count(1)``."""
    from djust.mixins import sticky
    from djust.test_isolation import reset_djust_globals

    # Advance the counter, then assert reset rewinds it to 1.
    next(sticky._view_id_counter)
    next(sticky._view_id_counter)
    reset_djust_globals()
    assert next(sticky._view_id_counter) == 1, (
        "reset_djust_globals must rewind the child-view id counter to 1 so "
        "auto-generated child_N ids are deterministic per test"
    )


def test_reset_resets_tooltip_id_counter():
    """``djust_components._tooltip_id_counter`` is reset to a fresh ``count(1)``."""
    from djust.components.templatetags import djust_components
    from djust.test_isolation import reset_djust_globals

    next(djust_components._tooltip_id_counter)
    next(djust_components._tooltip_id_counter)
    reset_djust_globals()
    assert next(djust_components._tooltip_id_counter) == 1, (
        "reset_djust_globals must rewind the tooltip id counter to 1"
    )


def test_reset_clears_route_map_cache():
    """djust's URLconf-derived route-map cache is cleared (#1862-adjacent)."""
    from djust import routing
    from djust.test_isolation import reset_djust_globals

    routing._route_map_cache[("sentinel",)] = {"x": "y"}
    reset_djust_globals()
    assert routing._route_map_cache == {}, (
        "reset_djust_globals must clear the route-map cache so a stale "
        "URLconf-derived map doesn't leak across tests"
    )


def test_reset_is_optional_dep_safe(monkeypatch):
    """A missing optional dependency never errors the reset.

    Simulate Channels being unavailable by making the import raise; the reset
    must still complete (and reset the other globals) without propagating.
    """
    import builtins

    from djust.test_isolation import reset_djust_globals

    real_import = builtins.__import__

    def _boom(name, *args, **kwargs):
        if name == "channels.layers":
            raise ImportError("simulated missing Channels")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _boom)
    # Must not raise even though channels.layers import fails.
    reset_djust_globals()


# ---------------------------------------------------------------------------
# (2) #1882 cure end-to-end — deterministic + gate-off (#1468).
# ---------------------------------------------------------------------------

_TT_MOD = "djust.tests.test_recovery_version_staleness_1817"


async def _recv_until(comm, wanted, *, tries=8, timeout=3):
    last = None
    for _ in range(tries):
        last = await comm.receive_json_from(timeout=timeout)
        if last.get("type") == wanted:
            return last
    return last


async def _drive_jump_with_stale_layer_sender(reset_layer: bool):
    """mount → (stray hotreload from a STALE shared layer) → bump → jump.

    Models the #1882 race: a sibling test captured the cached channel layer and
    fires ``group_send("djust_hotreload", ...)`` to it DURING the victim's
    session. The fixture's ``channel_layers.backends.clear()`` (here, gated on
    ``reset_layer``) makes the victim connect to a FRESH layer, so the
    stale-layer send cannot reach it.
    """
    pytest.importorskip("channels")
    from channels.layers import channel_layers, get_channel_layer
    from channels.testing import WebsocketCommunicator
    from django.contrib.sessions.backends.db import SessionStore

    from djust.websocket import LiveViewConsumer

    # A sibling test touched the layer first -> a cached default backend exists.
    # The sibling SENDER holds this reference and sends to it mid-session.
    stale_layer = get_channel_layer()

    # THE FIXTURE (gated): drop the cached backend so the victim connects fresh.
    if reset_layer:
        channel_layers.backends.clear()

    def _mk():
        s = SessionStore()
        s.create()
        return s.session_key

    key = await sync_to_async(_mk)()

    class _S:
        def __init__(self, k):
            self.session_key = k

    comm = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    comm.scope["session"] = _S(key)
    ok, _ = await comm.connect()
    assert ok
    await comm.receive_json_from(timeout=2)  # connect frame

    await comm.send_json_to({"type": "mount", "view": f"{_TT_MOD}._TTRecoveryView", "url": "/tt/"})
    mount = await _recv_until(comm, "mount")
    v_mount = mount["version"]

    # SIBLING SENDER fires now (victim has joined djust_hotreload on connect),
    # sending to the STALE cached layer it captured before the fixture ran.
    await stale_layer.group_send("djust_hotreload", {"type": "hotreload", "file": "sibling.html"})
    await asyncio.sleep(0.05)  # let the consumer process any stray re-render

    # Arming event — first patch, read EXACTLY as the real #1882 test does.
    await comm.send_json_to({"type": "event", "event": "bump", "params": {}, "ref": 1})
    ev1 = await _recv_until(comm, "patch")
    v_arm = ev1["version"]

    # Jump (the render-send drift path).
    await comm.send_json_to({"type": "time_travel_jump", "index": 0, "which": "before"})
    v_jump = None
    for _ in range(8):
        f = await comm.receive_json_from(timeout=3)
        if f.get("type") in ("patch", "html_update"):
            v_jump = f["version"]
            break

    await comm.disconnect()
    return v_mount, v_arm, v_jump


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_channel_layer_reset_keeps_wire_version_clean_1882():
    """WITH the channel-layer reset, the jump lands at the clean version 3.

    The fixture's ``channel_layers.backends.clear()`` puts the victim on a fresh
    layer, so the stale-layer sibling send is a no-op for it and the wire-version
    chain stays 1 -> 2 -> 3.
    """
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[_TT_MOD], DEBUG=True):
        v_mount, v_arm, v_jump = await _drive_jump_with_stale_layer_sender(reset_layer=True)
    assert (v_mount, v_arm, v_jump) == (1, 2, 3), (
        "with the channel-layer reset the wire-version chain must stay clean "
        f"(1 -> 2 -> 3); got mount={v_mount} arm={v_arm} jump={v_jump}"
    )


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_gate_off_without_reset_reproduces_1882_drift():
    """GATE-OFF (#1468): WITHOUT the reset, the stray hotreload drifts the version.

    The victim shares the stale layer with the sibling sender, receives the
    stray re-render, bumps ``_next_version()``, and the jump lands at 4 — the
    exact ``got 4`` #1882 symptom. This proves the reset (not something else) is
    what keeps the companion test clean.
    """
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[_TT_MOD], DEBUG=True):
        v_mount, v_arm, v_jump = await _drive_jump_with_stale_layer_sender(reset_layer=False)
    assert v_jump == 4, (
        "gate-off: without the channel-layer reset the stray hotreload frame "
        "must bump the wire version so the jump lands at 4 (the #1882 drift); "
        f"got mount={v_mount} arm={v_arm} jump={v_jump}. If this no longer "
        "reproduces, the reproduction has drifted from the real mechanism."
    )


# ---------------------------------------------------------------------------
# (3) The autouse fixture is actually active in this worker.
# ---------------------------------------------------------------------------


def test_autouse_fixture_left_counters_clean():
    """A plain test (no explicit fixture request) starts with reset counters.

    Proves the autouse fixture ran before this test: the child-view counter is
    at 1 even though earlier tests in the worker advanced it.
    """
    from djust.mixins import sticky

    # The autouse fixture reset it to count(1) before this test body ran.
    assert next(sticky._view_id_counter) == 1, (
        "autouse _reset_djust_globals must have reset the child-view counter "
        "before this test — got a non-1 first value, fixture not active"
    )
    # Leave it advanced; the next test's autouse fixture re-resets it.
    next(sticky._view_id_counter)
    _ = itertools  # silence unused-import lints in stripped builds
