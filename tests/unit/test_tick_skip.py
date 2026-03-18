"""
Tests for tick auto-skip logic.

Verifies that handle_tick() calls that don't change state skip the
render cycle, preventing unnecessary VDOM version increments that
cause version mismatch with user-triggered events (#560).
"""

import pytest
from djust import LiveView
from djust.websocket import _snapshot_assigns


class TickView(LiveView):
    """Test view with tick_interval and handle_tick."""

    template = "<div dj-root>{{ count }}</div>"
    tick_interval = 1000

    def mount(self, request, **kwargs):
        self.count = 0

    def handle_tick(self):
        """No-op tick — doesn't change state."""
        pass


class MutatingTickView(LiveView):
    """Test view where tick mutates state."""

    template = "<div dj-root>{{ count }}</div>"
    tick_interval = 1000

    def mount(self, request, **kwargs):
        self.count = 0

    def handle_tick(self):
        """Mutating tick — increments count."""
        self.count += 1


class ConditionalTickView(LiveView):
    """Test view where tick sometimes mutates state."""

    template = "<div dj-root>{{ count }}</div>"
    tick_interval = 1000

    def mount(self, request, **kwargs):
        self.count = 0
        self.should_update = False

    def handle_tick(self):
        if self.should_update:
            self.count += 1


class TestTickAutoSkip:
    """Test that _snapshot_assigns detects no-op ticks correctly."""

    @pytest.mark.django_db
    def test_noop_tick_produces_identical_snapshots(self, get_request):
        """When handle_tick() doesn't change state, snapshots should match."""
        view = TickView()
        view.get(get_request)

        pre = _snapshot_assigns(view)
        view.handle_tick()
        post = _snapshot_assigns(view)

        assert pre == post, "No-op tick should produce identical snapshots"

    @pytest.mark.django_db
    def test_mutating_tick_produces_different_snapshots(self, get_request):
        """When handle_tick() changes state, snapshots should differ."""
        view = MutatingTickView()
        view.get(get_request)

        pre = _snapshot_assigns(view)
        view.handle_tick()
        post = _snapshot_assigns(view)

        assert pre != post, "Mutating tick should produce different snapshots"
        assert post["count"] == 1

    @pytest.mark.django_db
    def test_conditional_tick_skip_then_render(self, get_request):
        """Tick that conditionally mutates: skip when no change, render when changed."""
        view = ConditionalTickView()
        view.get(get_request)

        # First tick: should_update is False → no change
        pre = _snapshot_assigns(view)
        view.handle_tick()
        post = _snapshot_assigns(view)
        assert pre == post, "Conditional tick with should_update=False should be no-op"

        # Enable updates
        view.should_update = True

        # Second tick: should_update is True → count changes
        pre = _snapshot_assigns(view)
        view.handle_tick()
        post = _snapshot_assigns(view)
        assert pre != post, "Conditional tick with should_update=True should change state"
        assert post["count"] == 1

    @pytest.mark.django_db
    def test_snapshot_excludes_private_attrs(self, get_request):
        """Private attrs (starting with _) should not affect snapshot comparison."""
        view = TickView()
        view.get(get_request)

        pre = _snapshot_assigns(view)
        view._internal_counter = 999  # Private attr — should be ignored
        post = _snapshot_assigns(view)

        assert pre == post, "Private attr changes should not affect snapshot"
