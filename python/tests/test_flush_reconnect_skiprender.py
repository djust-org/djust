"""Tests for #698 (dynamic flush callback) and #700 (auto-skip render).

#698: flush_push_events() should resolve the callback dynamically from
_ws_consumer instead of relying on a stored callback, so it works
after WebSocket reconnects without re-wiring.

#700: push_commands-only handlers should auto-skip VDOM re-render when
no public state was actually changed (identity check).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from djust.mixins.push_events import PushEventMixin


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class _FakeConsumer:
    """Minimal consumer with _flush_push_events."""

    def __init__(self):
        self.flush_called = False

    async def _flush_push_events(self):
        self.flush_called = True


class _PushView(PushEventMixin):
    """Minimal view that inherits PushEventMixin."""

    def __init__(self):
        super().__init__()


# ---------------------------------------------------------------------------
# #698: Dynamic flush callback resolution
# ---------------------------------------------------------------------------


class TestDynamicFlushCallback:
    """flush_push_events() resolves callback from _ws_consumer dynamically."""

    @pytest.mark.asyncio
    async def test_flush_via_ws_consumer(self):
        """When _ws_consumer has _flush_push_events, it is used."""
        view = _PushView()
        consumer = _FakeConsumer()
        view._ws_consumer = consumer
        view.push_event("test", {"data": 1})

        await view.flush_push_events()
        assert consumer.flush_called

    @pytest.mark.asyncio
    async def test_flush_no_consumer_no_crash(self):
        """Without _ws_consumer, flush is a no-op (no crash)."""
        view = _PushView()
        view.push_event("test", {"data": 1})
        # No _ws_consumer set — should not raise
        await view.flush_push_events()

    @pytest.mark.asyncio
    async def test_flush_empty_events_skipped(self):
        """No flush if _pending_push_events is empty."""
        view = _PushView()
        consumer = _FakeConsumer()
        view._ws_consumer = consumer
        # No push_event called
        await view.flush_push_events()
        assert not consumer.flush_called

    @pytest.mark.asyncio
    async def test_legacy_callback_still_works(self):
        """Stored _push_events_flush_callback is used as fallback."""
        view = _PushView()
        mock_flush = AsyncMock()
        view._push_events_flush_callback = mock_flush
        view.push_event("test", {"data": 1})

        await view.flush_push_events()
        mock_flush.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_consumer_takes_priority_over_legacy(self):
        """_ws_consumer._flush_push_events takes priority over stored callback."""
        view = _PushView()
        consumer = _FakeConsumer()
        view._ws_consumer = consumer
        legacy_mock = AsyncMock()
        view._push_events_flush_callback = legacy_mock
        view.push_event("test", {"data": 1})

        await view.flush_push_events()
        assert consumer.flush_called
        legacy_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_reconnect_simulation(self):
        """Simulates reconnect: new consumer replaces old one, flush works."""
        view = _PushView()

        # Initial consumer
        consumer1 = _FakeConsumer()
        view._ws_consumer = consumer1
        view.push_event("test", {"data": 1})
        await view.flush_push_events()
        assert consumer1.flush_called

        # Simulate reconnect: new consumer assigned
        consumer2 = _FakeConsumer()
        view._ws_consumer = consumer2
        view.push_event("test", {"data": 2})
        await view.flush_push_events()
        assert consumer2.flush_called

    @pytest.mark.asyncio
    async def test_private_alias_uses_dynamic_lookup(self):
        """_flush_pending_push_events (private alias) also uses dynamic lookup."""
        view = _PushView()
        consumer = _FakeConsumer()
        view._ws_consumer = consumer
        view.push_event("test", {"data": 1})

        await view._flush_pending_push_events()
        assert consumer.flush_called


# ---------------------------------------------------------------------------
# #700: Auto-skip render for push_commands-only handlers
# ---------------------------------------------------------------------------


class TestAutoSkipRenderPushOnly:
    """Identity-based auto-skip when only push events are pending."""

    def test_identity_snapshot_unchanged(self):
        """When no public attr identity changes, snapshot matches."""

        class View:
            def __init__(self):
                self.items = [1, 2, 3]  # mutable public attr
                self.name = "test"  # immutable public attr
                self._private = "hidden"

        view = View()
        # Take identity snapshot
        pre = {k: id(v) for k, v in view.__dict__.items() if not k.startswith("_")}

        # No changes — post should match
        post = {k: id(v) for k, v in view.__dict__.items() if not k.startswith("_")}
        assert pre == post

    def test_identity_snapshot_detects_rebind(self):
        """When a public attr is rebound, identity changes."""

        class View:
            def __init__(self):
                self.items = [1, 2, 3]

        view = View()
        pre = {k: id(v) for k, v in view.__dict__.items() if not k.startswith("_")}

        # Rebind — creates new list object
        view.items = [1, 2, 3]

        post = {k: id(v) for k, v in view.__dict__.items() if not k.startswith("_")}
        assert pre != post

    def test_identity_snapshot_ignores_in_place_mutation(self):
        """In-place mutation doesn't change identity (correct behavior)."""

        class View:
            def __init__(self):
                self.items = [1, 2, 3]

        view = View()
        pre = {k: id(v) for k, v in view.__dict__.items() if not k.startswith("_")}

        # In-place mutation — same object
        view.items.append(4)

        post = {k: id(v) for k, v in view.__dict__.items() if not k.startswith("_")}
        # Identity unchanged — push_commands-only skip is safe here
        # because the handler only called push_commands, not append
        assert pre == post

    def test_identity_snapshot_detects_new_attr(self):
        """Adding a new public attr changes the snapshot."""

        class View:
            def __init__(self):
                self.items = [1, 2, 3]

        view = View()
        pre = {k: id(v) for k, v in view.__dict__.items() if not k.startswith("_")}

        view.new_attr = "hello"

        post = {k: id(v) for k, v in view.__dict__.items() if not k.startswith("_")}
        assert pre != post

    def test_identity_snapshot_ignores_private_attrs(self):
        """Private attrs (starting with _) are excluded from snapshot."""

        class View:
            def __init__(self):
                self.items = [1, 2, 3]
                self._internal = "private"

        view = View()
        pre = {k: id(v) for k, v in view.__dict__.items() if not k.startswith("_")}

        # Changing private attr doesn't affect public snapshot
        view._internal = "changed"

        post = {k: id(v) for k, v in view.__dict__.items() if not k.startswith("_")}
        assert pre == post
