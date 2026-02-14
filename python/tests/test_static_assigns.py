"""Tests for static_assigns — render optimization for unchanging context."""

import pytest
from unittest.mock import Mock
from django.test import RequestFactory

from djust import LiveView
from djust.websocket import _snapshot_assigns


# ---------------------------------------------------------------------------
# Test views
# ---------------------------------------------------------------------------


class StaticView(LiveView):
    """View with static_assigns configured."""

    template = "<div data-djust-root>{{ demos }} {{ count }}</div>"
    static_assigns = ["demos"]

    def mount(self, request, **kwargs):
        self.demos = "<h1>Big static HTML</h1>"
        self.count = 0


class OverlappingView(LiveView):
    """View with overlapping static and temporary assigns (invalid)."""

    template = "<div data-djust-root>{{ items }}</div>"
    static_assigns = ["items"]
    temporary_assigns = {"items": []}

    def mount(self, request, **kwargs):
        self.items = ["a", "b"]


class NoStaticView(LiveView):
    """View without static_assigns (baseline)."""

    template = "<div data-djust-root>{{ count }}</div>"

    def mount(self, request, **kwargs):
        self.count = 0


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_request():
    factory = RequestFactory()
    request = factory.get("/")
    request.session = Mock()
    request.session.session_key = "test-session"
    return request


# ---------------------------------------------------------------------------
# ContextMixin: static assigns in get_context_data
# ---------------------------------------------------------------------------


class TestStaticAssignsContext:
    """Test that static assigns are included/excluded from context."""

    def test_first_render_includes_static_assigns(self, mock_request):
        """On first render, static assigns should be in the context."""
        view = StaticView()
        view.mount(mock_request)
        # _static_assigns_sent is False by default
        assert not getattr(view, "_static_assigns_sent", False)
        context = view.get_context_data()
        assert "demos" in context
        assert context["demos"] == "<h1>Big static HTML</h1>"
        assert "count" in context

    def test_subsequent_render_excludes_static_assigns(self, mock_request):
        """After _static_assigns_sent is set, static assigns should be excluded."""
        view = StaticView()
        view.mount(mock_request)
        # Simulate first sync setting the flag
        view._static_assigns_sent = True
        context = view.get_context_data()
        assert "demos" not in context
        assert "count" in context
        assert context["count"] == 0

    def test_no_static_assigns_always_included(self, mock_request):
        """Views without static_assigns should always include all keys."""
        view = NoStaticView()
        view.mount(mock_request)
        view._static_assigns_sent = True  # Should have no effect
        context = view.get_context_data()
        assert "count" in context


# ---------------------------------------------------------------------------
# RustBridgeMixin: _static_assigns_sent flag
# ---------------------------------------------------------------------------


class TestStaticAssignsSentFlag:
    """Test that _sync_state_to_rust sets the flag after first sync."""

    def test_flag_set_after_first_sync(self, mock_request):
        """_static_assigns_sent should be True after first _sync_state_to_rust."""
        view = StaticView()
        view.mount(mock_request)
        assert not getattr(view, "_static_assigns_sent", False)

        # Mock the Rust view so _sync_state_to_rust can run
        mock_rust = Mock()
        view._rust_view = mock_rust

        view._sync_state_to_rust()

        assert view._static_assigns_sent is True
        # Rust update_state should have been called with demos in context
        call_args = mock_rust.update_state.call_args[0][0]
        assert "demos" in call_args

    def test_flag_not_set_without_static_assigns(self, mock_request):
        """Views without static_assigns should not set the flag."""
        view = NoStaticView()
        view.mount(mock_request)

        mock_rust = Mock()
        view._rust_view = mock_rust

        view._sync_state_to_rust()

        assert not getattr(view, "_static_assigns_sent", False)

    def test_second_sync_skips_static_keys(self, mock_request):
        """Second _sync_state_to_rust should not include static assign keys."""
        view = StaticView()
        view.mount(mock_request)

        mock_rust = Mock()
        view._rust_view = mock_rust

        # First sync — includes demos
        view._sync_state_to_rust()
        first_call = mock_rust.update_state.call_args[0][0]
        assert "demos" in first_call

        # Change count to ensure it's detected as changed
        view.count = 99
        view._changed_keys = {"count"}

        # Second sync — should exclude demos (static) but include count (changed)
        view._sync_state_to_rust()
        second_call = mock_rust.update_state.call_args[0][0]
        assert "demos" not in second_call
        assert "count" in second_call


# ---------------------------------------------------------------------------
# _snapshot_assigns: skip static assign keys
# ---------------------------------------------------------------------------


class TestSnapshotAssigns:
    """Test that _snapshot_assigns skips static assign keys."""

    def test_snapshot_skips_static_keys(self):
        """Static assign keys should be excluded from snapshot."""
        view = StaticView()
        view.demos = "<h1>Big HTML</h1>"
        view.count = 42

        snapshot = _snapshot_assigns(view)
        assert "demos" not in snapshot
        assert "count" in snapshot
        assert snapshot["count"] == 42

    def test_snapshot_includes_all_without_static(self):
        """Without static_assigns, all public keys should be in snapshot."""
        view = NoStaticView()
        view.count = 7

        snapshot = _snapshot_assigns(view)
        assert "count" in snapshot
        assert snapshot["count"] == 7

    def test_snapshot_still_excludes_private(self):
        """Private keys (underscore prefix) should still be excluded."""
        view = StaticView()
        view.demos = "html"
        view.count = 1
        view._private = "secret"

        snapshot = _snapshot_assigns(view)
        assert "_private" not in snapshot
        assert "demos" not in snapshot
        assert "count" in snapshot


# ---------------------------------------------------------------------------
# System check: overlapping static/temporary assigns
# ---------------------------------------------------------------------------


class TestStaticAssignsCheck:
    """Test system check for overlapping static and temporary assigns."""

    def test_overlapping_assigns_produces_warning(self, settings):
        """Q007 should flag keys in both static_assigns and temporary_assigns."""
        from djust.checks import check_liveviews

        # Make the overlapping view discoverable
        errors = check_liveviews(None)
        q007_errors = [e for e in errors if e.id == "djust.Q007"]
        # OverlappingView should trigger a warning
        overlap_msgs = [e for e in q007_errors if "items" in str(e.msg)]
        assert len(overlap_msgs) >= 1
        assert "static" in overlap_msgs[0].hint
        assert "temporary" in overlap_msgs[0].hint

    def test_no_overlap_no_warning(self, settings):
        """Views without overlapping assigns should not trigger Q007."""
        from djust.checks import check_liveviews

        errors = check_liveviews(None)
        q007_errors = [e for e in errors if e.id == "djust.Q007"]
        # StaticView has static_assigns=['demos'] but no temporary_assigns overlap
        static_view_warnings = [e for e in q007_errors if "StaticView" in str(e.msg)]
        assert len(static_view_warnings) == 0
