"""Integration tests for Service Worker advanced features (v0.6.0).

End-to-end scenarios that exercise the full server-side flow without the
Rust renderer round-trip or a real Channels runtime. Mirrors the pattern
in ``test_sticky_redirect_flow.py``.

Two scenarios:

1. Multi-lazy mount flows as one ``mount_batch`` frame (client-driven,
   simulated by calling ``handle_mount_batch`` directly with a 2-view
   payload).
2. Back-navigation restores state: ``live_redirect_mount`` with a
   ``state_snapshot`` payload restores public view state on an opt-in
   view.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional

import pytest

from djust.live_view import LiveView
from djust.websocket import LiveViewConsumer


# ---------------------------------------------------------------------------
# Module-scope views — resolvable by dotted path.
# ---------------------------------------------------------------------------


class _FlowCounter(LiveView):
    template = "<div dj-root><span>count={{ count }}</span></div>"

    def mount(self, request, **kwargs):
        self.count = 0

    def get_context_data(self, **kwargs):
        return {"count": self.count}


class _FlowSnapshotView(LiveView):
    enable_state_snapshot = True
    template = "<div dj-root><span>n={{ n }}</span></div>"

    def mount(self, request, **kwargs):
        self.n = 0

    def get_context_data(self, **kwargs):
        return {"n": self.n}


# ---------------------------------------------------------------------------
# Consumer shim — same pattern as tests/integration/test_sticky_redirect_flow.py.
# ---------------------------------------------------------------------------


class _FakeConsumer(LiveViewConsumer):
    def __init__(self):  # type: ignore[no-untyped-def]
        self.view_instance: Optional[LiveView] = None
        self._view_group = None
        self._presence_group = None
        self._tick_task = None
        self._render_lock = asyncio.Lock()
        self._processing_user_event = False
        self._sticky_preserved: Dict[str, Any] = {}
        self._db_notify_channels: set[str] = set()
        self.sent_frames: List[Dict[str, Any]] = []
        self.session_id = "integration-session"
        self.scope = {"path": "/", "query_string": b"", "session": None}
        self.channel_name = "integration-channel"
        self.use_actors = False
        self.actor_handle = None
        self._debug_panel_active = False

    class _NullChannelLayer:
        async def group_add(self, *a, **kw):
            return None

        async def group_discard(self, *a, **kw):
            return None

    @property
    def channel_layer(self):  # type: ignore[override]
        return self._NullChannelLayer()

    async def send_json(self, payload):  # type: ignore[override]
        self.sent_frames.append(payload)

    async def send(self, *a, **kw):  # type: ignore[override]
        return None

    async def send_error(self, message, **kwargs):  # type: ignore[override]
        self.sent_frames.append({"type": "error", "message": message})

    async def close(self, code=None):  # type: ignore[override]
        return None

    async def _flush_push_events(self):
        return None


# ---------------------------------------------------------------------------
# Fixture to widen LIVEVIEW_ALLOWED_MODULES for this integration module.
# ---------------------------------------------------------------------------


@pytest.fixture
def _allow_flow_module(settings):
    existing = list(getattr(settings, "LIVEVIEW_ALLOWED_MODULES", []) or [])
    settings.LIVEVIEW_ALLOWED_MODULES = existing + [
        "tests.integration.test_sw_advanced_flow",
    ]
    yield


# ---------------------------------------------------------------------------
# 1. Multi-lazy hydration → one mount_batch frame.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multi_lazy_mount_flows_as_batch(_allow_flow_module):
    consumer = _FakeConsumer()
    data = {
        "type": "mount_batch",
        "views": [
            {
                "view": "tests.integration.test_sw_advanced_flow._FlowCounter",
                "params": {},
                "url": "/",
                "target_id": "lazy-a",
            },
            {
                "view": "tests.integration.test_sw_advanced_flow._FlowCounter",
                "params": {},
                "url": "/",
                "target_id": "lazy-b",
            },
        ],
    }
    await consumer.handle_mount_batch(data)

    # Exactly one mount_batch frame — no per-view mount frames.
    batch_frames = [f for f in consumer.sent_frames if f.get("type") == "mount_batch"]
    mount_frames = [f for f in consumer.sent_frames if f.get("type") == "mount"]
    assert len(batch_frames) == 1
    assert len(mount_frames) == 0

    batch = batch_frames[0]
    assert batch["session_id"] == "integration-session"
    assert len(batch["views"]) == 2
    assert len(batch["failed"]) == 0
    target_ids = {v["target_id"] for v in batch["views"]}
    assert target_ids == {"lazy-a", "lazy-b"}


# ---------------------------------------------------------------------------
# 2. Back-nav with state snapshot restores public state.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_back_nav_with_state_snapshot_restores_state(_allow_flow_module):
    consumer = _FakeConsumer()

    snapshot = {
        "view_slug": "tests.integration.test_sw_advanced_flow._FlowSnapshotView",
        "state_json": json.dumps({"n": 99}),
        "ts": 0,
    }
    data = {
        "view": "tests.integration.test_sw_advanced_flow._FlowSnapshotView",
        "params": {},
        "url": "/",
        "state_snapshot": snapshot,
    }
    # live_redirect_mount parses state_snapshot and forwards to handle_mount.
    # The sticky-staging branch is a no-op (no old view_instance set).
    await consumer.handle_live_redirect_mount(data)

    # View instance restored n=99 from snapshot, NOT 0 from mount().
    assert consumer.view_instance is not None
    assert consumer.view_instance.n == 99

    # Exactly one mount frame was sent.
    mount_frames = [f for f in consumer.sent_frames if f.get("type") == "mount"]
    assert len(mount_frames) == 1
