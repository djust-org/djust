"""Integration tests for the bug-capture Share button's WS handler (#1562).

Exercises the end-to-end path through ``LiveViewConsumer``: dispatch a
``bug_capture_share`` frame, verify the server replies with a
``bug_capture_share_result`` frame carrying a valid ``djbug1.<base64>``
blob decodable by ``BugCapture.decode()``. Mirrors the shape of
``test_time_travel_flow.py`` — same consumer-wiring pattern, same
``_make_consumer`` helper style.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from django.test import override_settings

from djust import LiveView
from djust.bug_capture import BugCapture
from djust.decorators import event_handler
from djust.time_travel import EventSnapshot
from djust.websocket import LiveViewConsumer


class _TTView(LiveView):
    """Minimal LiveView that opts into time travel (mirrors test_time_travel_flow.py)."""

    template = "<div>{{ count }}</div>"
    time_travel_enabled = True

    def mount(self, request=None, **kwargs):
        self.count = 0

    @event_handler()
    def increment(self, n: int = 1, **kwargs):
        self.count += n


def _make_consumer(view):
    consumer = LiveViewConsumer()
    consumer.view_instance = view
    sent: list = []
    consumer.send_json = AsyncMock(side_effect=lambda msg: sent.append(msg))
    consumer.send_error = AsyncMock(
        side_effect=lambda msg, **kw: sent.append({"type": "error", "error": msg})
    )
    consumer._send_update = AsyncMock()
    return consumer, sent


@pytest.mark.asyncio
async def test_bug_capture_share_returns_a_valid_blob():
    view = _TTView()
    view.mount()
    view.count = 5
    consumer, sent = _make_consumer(view)
    view._time_travel_buffer.append(
        EventSnapshot(
            event_name="increment",
            params={"n": 5},
            ref=1,
            ts=0.0,
            state_before={"count": 0},
            state_after={"count": 5},
        )
    )

    with override_settings(DEBUG=True):
        await consumer.handle_bug_capture_share({"type": "bug_capture_share"})

    results = [m for m in sent if m.get("type") == "bug_capture_share_result"]
    assert len(results) == 1
    blob = results[0]["blob"]
    assert blob.startswith("djbug1.")

    decoded = BugCapture.decode(blob)
    assert decoded.event_name == "increment"
    assert decoded.state_before == {"count": 0}
    assert decoded.state_after == {"count": 5}


@pytest.mark.asyncio
async def test_bug_capture_share_rejected_without_mounted_view():
    consumer = LiveViewConsumer()
    consumer.view_instance = None
    sent_errors: list = []
    consumer.send_error = AsyncMock(side_effect=lambda msg, **kw: sent_errors.append(msg))
    consumer.send_json = AsyncMock()

    with override_settings(DEBUG=True):
        await consumer.handle_bug_capture_share({"type": "bug_capture_share"})

    assert any("View not mounted" in e for e in sent_errors)
    consumer.send_json.assert_not_called()


@pytest.mark.asyncio
async def test_bug_capture_share_rejected_when_time_travel_disabled():
    class _NoTTView(LiveView):
        template = "<div>{{ count }}</div>"
        # time_travel_enabled left at the class default (False).

        def mount(self, request=None, **kwargs):
            self.count = 0

    view = _NoTTView()
    view.mount()
    consumer, sent = _make_consumer(view)

    with override_settings(DEBUG=True):
        await consumer.handle_bug_capture_share({"type": "bug_capture_share"})

    assert any(
        m.get("type") == "error" and "time_travel_enabled" in m.get("error", "") for m in sent
    )
    assert not any(m.get("type") == "bug_capture_share_result" for m in sent)


@pytest.mark.asyncio
async def test_bug_capture_share_rejected_when_no_events_recorded():
    """time_travel_enabled=True but nothing has happened yet — the buffer
    is empty, so encode_view_state's own ValueError surfaces as an error
    frame (the WS handler does not duplicate that check)."""
    view = _TTView()
    view.mount()
    consumer, sent = _make_consumer(view)
    assert len(view._time_travel_buffer) == 0

    with override_settings(DEBUG=True):
        await consumer.handle_bug_capture_share({"type": "bug_capture_share"})

    assert any(m.get("type") == "error" for m in sent)
    assert not any(m.get("type") == "bug_capture_share_result" for m in sent)


@pytest.mark.asyncio
async def test_bug_capture_share_rejected_in_production_without_opt_in():
    """The prod gate lives in bug_capture._enforce_prod_gate (encode-time),
    NOT duplicated in the WS handler — this proves the single check still
    protects the WS path."""
    view = _TTView()
    view.mount()
    consumer, sent = _make_consumer(view)
    view._time_travel_buffer.append(
        EventSnapshot(
            event_name="increment",
            params={"n": 1},
            ref=1,
            ts=0.0,
            state_before={"count": 0},
            state_after={"count": 1},
        )
    )

    with override_settings(DEBUG=False):
        await consumer.handle_bug_capture_share({"type": "bug_capture_share"})

    assert any(
        m.get("type") == "error" and "disabled in production" in m.get("error", "") for m in sent
    )
    assert not any(m.get("type") == "bug_capture_share_result" for m in sent)


@pytest.mark.asyncio
async def test_bug_capture_share_applies_configured_default_scrub():
    # `djust.config.config` is a process-lifetime singleton populated once
    # from `settings.LIVEVIEW_CONFIG` at import time (`_load_from_settings`,
    # called only from `__init__`) — `override_settings(LIVEVIEW_CONFIG=...)`
    # does NOT refresh it (no `setting_changed` receiver wired). Drive the
    # singleton directly via its own `.set()`/`.get()` API, matching the
    # pattern other config-dependent tests in this suite use (e.g.
    # test_auto_hot_reload.py's `fresh_config.set(...)`).
    from djust.config import config as djust_config

    view = _TTView()
    view.mount()
    consumer, sent = _make_consumer(view)
    view._time_travel_buffer.append(
        EventSnapshot(
            event_name="increment",
            params={},
            ref=1,
            ts=0.0,
            state_before={"count": 0, "password": "secret"},
            state_after={"count": 1, "password": "secret"},
        )
    )

    previous = djust_config.get("bug_capture_default_scrub", [])
    djust_config.set("bug_capture_default_scrub", ["password"])
    try:
        with override_settings(DEBUG=True):
            await consumer.handle_bug_capture_share({"type": "bug_capture_share"})
    finally:
        djust_config.set("bug_capture_default_scrub", previous)

    results = [m for m in sent if m.get("type") == "bug_capture_share_result"]
    assert len(results) == 1
    decoded = BugCapture.decode(results[0]["blob"])
    assert "password" not in decoded.state_before
    assert "password" not in decoded.state_after
    assert "password" in decoded.scrubbed_fields


@pytest.mark.asyncio
async def test_bug_capture_share_no_default_scrub_leaves_state_intact():
    """Gate-off sibling for the previous test (#1468): with NO configured
    scrub list, the sensitive field survives — proving the scrub
    assertion above isn't a tautology (the field was actually removed
    BECAUSE of the config, not always stripped by something else)."""
    view = _TTView()
    view.mount()
    consumer, sent = _make_consumer(view)
    view._time_travel_buffer.append(
        EventSnapshot(
            event_name="increment",
            params={},
            ref=1,
            ts=0.0,
            state_before={"count": 0, "password": "secret"},
            state_after={"count": 1, "password": "secret"},
        )
    )

    with override_settings(DEBUG=True):
        await consumer.handle_bug_capture_share({"type": "bug_capture_share"})

    results = [m for m in sent if m.get("type") == "bug_capture_share_result"]
    assert len(results) == 1
    decoded = BugCapture.decode(results[0]["blob"])
    assert decoded.state_before["password"] == "secret"


@pytest.mark.asyncio
async def test_bug_capture_share_frame_routed_via_receive():
    """The receive() dispatch table itself routes 'bug_capture_share' to
    the handler — not just a direct-call test of the handler in isolation."""
    import json as _json

    view = _TTView()
    view.mount()
    consumer, sent = _make_consumer(view)
    view._time_travel_buffer.append(
        EventSnapshot(
            event_name="increment",
            params={},
            ref=1,
            ts=0.0,
            state_before={"count": 0},
            state_after={"count": 1},
        )
    )
    # receive() reads scope/session in a few places for other verbs;
    # RUNTIME_OWNED_VERBS membership is checked first, and
    # 'bug_capture_share' must NOT be a member (dev-only WS-only verb).
    assert "bug_capture_share" not in consumer.RUNTIME_OWNED_VERBS

    from djust.websocket import ConnectionRateLimiter

    consumer._rate_limiter = ConnectionRateLimiter(rate=10000, burst=10000, max_warnings=3)

    with override_settings(DEBUG=True):
        await consumer.receive(text_data=_json.dumps({"type": "bug_capture_share"}))

    results = [m for m in sent if m.get("type") == "bug_capture_share_result"]
    assert len(results) == 1
