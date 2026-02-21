"""Tests for automatic change tracking — Phoenix-style render optimization."""

import pytest
from functools import lru_cache
from unittest.mock import Mock
from django.test import RequestFactory

from djust import LiveView
from djust.websocket import _snapshot_assigns, _compute_changed_keys


# ---------------------------------------------------------------------------
# Test views
# ---------------------------------------------------------------------------


class CounterView(LiveView):
    """Simple view to test changed/unchanged attribute tracking."""

    template = "<div dj-root>{{ count }}</div>"

    def mount(self, request, **kwargs):
        self.count = 0


class CachedDemoView(LiveView):
    """View using @lru_cache for expensive computed data."""

    template = "<div dj-root>{{ demos }} {{ count }}</div>"

    def mount(self, request, **kwargs):
        self.count = 0

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["demos"] = _get_cached_demos()
        return context


@lru_cache(maxsize=1)
def _get_cached_demos():
    """Simulates an expensive computation cached at module level."""
    return [{"id": "counter", "html": "<h1>Counter Demo</h1>"}]


class MultiAttrView(LiveView):
    """View with multiple attributes for selective change testing."""

    template = "<div dj-root>{{ a }} {{ b }} {{ c }}</div>"

    def mount(self, request, **kwargs):
        self.a = "alpha"
        self.b = [1, 2, 3]
        self.c = {"key": "value"}


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
# _compute_changed_keys
# ---------------------------------------------------------------------------


class TestComputeChangedKeys:
    """Test _compute_changed_keys correctness."""

    def test_no_changes(self):
        """Identical snapshots produce empty set."""
        pre = {"a": 1, "b": "hello"}
        post = {"a": 1, "b": "hello"}
        assert _compute_changed_keys(pre, post) == set()

    def test_modified_key(self):
        """Changed value is detected."""
        pre = {"count": 0, "name": "test"}
        post = {"count": 1, "name": "test"}
        assert _compute_changed_keys(pre, post) == {"count"}

    def test_added_key(self):
        """New key in post is detected."""
        pre = {"a": 1}
        post = {"a": 1, "b": 2}
        assert _compute_changed_keys(pre, post) == {"b"}

    def test_removed_key(self):
        """Key missing from post is detected."""
        pre = {"a": 1, "b": 2}
        post = {"a": 1}
        assert _compute_changed_keys(pre, post) == {"b"}

    def test_multiple_changes(self):
        """Multiple adds, removes, and modifications."""
        pre = {"a": 1, "b": 2, "c": 3}
        post = {"a": 1, "b": 99, "d": 4}
        result = _compute_changed_keys(pre, post)
        assert result == {"b", "c", "d"}

    def test_empty_snapshots(self):
        """Both empty produces no changes."""
        assert _compute_changed_keys({}, {}) == set()

    def test_list_mutation(self):
        """In-place list mutation detected via deep copy comparison."""
        pre = {"items": [1, 2]}
        post = {"items": [1, 2, 3]}
        assert _compute_changed_keys(pre, post) == {"items"}


# ---------------------------------------------------------------------------
# _snapshot_assigns: immutable type optimization
# ---------------------------------------------------------------------------


class TestSnapshotAssigns:
    """Test _snapshot_assigns immutable optimization and correctness."""

    def test_includes_all_public_attrs(self):
        """All non-underscore instance attributes are included."""
        view = CounterView()
        view.count = 42
        snapshot = _snapshot_assigns(view)
        assert "count" in snapshot
        assert snapshot["count"] == 42

    def test_excludes_private_attrs(self):
        """Underscore-prefixed attributes are excluded."""
        view = CounterView()
        view.count = 1
        view._private = "secret"
        snapshot = _snapshot_assigns(view)
        assert "_private" not in snapshot
        assert "count" in snapshot

    def test_immutable_types_not_deepcopied(self):
        """Immutable types should be stored directly (same id)."""
        view = LiveView()
        view.name = "hello"
        view.count = 42
        view.ratio = 3.14
        view.active = True
        view.data = None
        view.raw = b"bytes"
        view.coords = (1, 2, 3)
        view.tags = frozenset(["a", "b"])

        snapshot = _snapshot_assigns(view)

        # For immutables, the snapshot value should be the exact same object
        assert snapshot["name"] is view.name
        assert snapshot["count"] is view.count
        assert snapshot["ratio"] is view.ratio
        assert snapshot["active"] is view.active
        assert snapshot["data"] is view.data
        assert snapshot["raw"] is view.raw
        assert snapshot["coords"] is view.coords
        assert snapshot["tags"] is view.tags

    def test_mutable_types_deepcopied(self):
        """Mutable types should be deep-copied (different id)."""
        view = LiveView()
        view.items = [1, 2, 3]
        view.config = {"key": "value"}

        snapshot = _snapshot_assigns(view)

        # Mutable values should be copies, not the same object
        assert snapshot["items"] is not view.items
        assert snapshot["items"] == view.items
        assert snapshot["config"] is not view.config
        assert snapshot["config"] == view.config

    def test_non_copyable_uses_sentinel(self):
        """Non-copyable objects get unique sentinel (never equals anything)."""

        class NoCopy:
            def __deepcopy__(self, memo):
                raise TypeError("Cannot copy")

        view = LiveView()
        view.weird = NoCopy()

        snapshot = _snapshot_assigns(view)
        assert "weird" in snapshot
        # Sentinel should never equal itself or the original
        assert snapshot["weird"] != view.weird

    def test_no_static_assigns_skip(self):
        """After removing static_assigns, all public attrs are always included."""
        view = LiveView()
        view.demos = "<h1>Big HTML</h1>"
        view.count = 42

        snapshot = _snapshot_assigns(view)
        assert "demos" in snapshot
        assert "count" in snapshot


# ---------------------------------------------------------------------------
# RustBridgeMixin: change-aware _sync_state_to_rust
# ---------------------------------------------------------------------------


class TestSyncStateChangeTracking:
    """Test that _sync_state_to_rust only sends changed values."""

    def test_first_render_sends_everything(self, mock_request):
        """First render (no prev_refs) sends all context to Rust."""
        view = CounterView()
        view.mount(mock_request)

        mock_rust = Mock()
        view._rust_view = mock_rust

        view._sync_state_to_rust()

        call_args = mock_rust.update_state.call_args[0][0]
        assert "count" in call_args

    def test_unchanged_attrs_skipped(self, mock_request):
        """Unchanged attributes are not sent on subsequent syncs."""
        view = MultiAttrView()
        view.mount(mock_request)

        mock_rust = Mock()
        view._rust_view = mock_rust

        # First sync — sends everything
        view._sync_state_to_rust()
        first_call = mock_rust.update_state.call_args[0][0]
        assert "a" in first_call
        assert "b" in first_call
        assert "c" in first_call

        # Mark only 'a' as changed (simulate handle_event result)
        view._changed_keys = {"a"}
        # Change 'a' so id() differs
        view.a = "beta"

        view._sync_state_to_rust()
        second_call = mock_rust.update_state.call_args[0][0]
        assert "a" in second_call
        # b and c should be skipped (same id, not in _changed_keys)
        assert "b" not in second_call
        assert "c" not in second_call

    def test_lru_cache_value_skipped_on_rerender(self, mock_request):
        """@lru_cache returns same object — id() unchanged → skipped."""
        view = CachedDemoView()
        view.mount(mock_request)

        mock_rust = Mock()
        view._rust_view = mock_rust

        # First sync — sends everything including demos
        view._sync_state_to_rust()
        first_call = mock_rust.update_state.call_args[0][0]
        assert "demos" in first_call

        # Second sync — demos from lru_cache has same id(), should be skipped
        # Only count is still in _changed_keys or new
        view._changed_keys = {"count"}
        view.count = 1

        view._sync_state_to_rust()
        second_call = mock_rust.update_state.call_args[0][0]
        assert "count" in second_call
        assert "demos" not in second_call

    def test_new_key_always_sent(self, mock_request):
        """Keys not in prev_refs are always sent."""
        view = CounterView()
        view.mount(mock_request)

        mock_rust = Mock()
        view._rust_view = mock_rust

        # First sync
        view._sync_state_to_rust()

        # Add a new attribute after first render
        view.new_attr = "hello"
        view._changed_keys = {"new_attr"}

        view._sync_state_to_rust()
        second_call = mock_rust.update_state.call_args[0][0]
        assert "new_attr" in second_call

    def test_changed_keys_cleared_after_sync(self, mock_request):
        """_changed_keys should be None after _sync_state_to_rust."""
        view = CounterView()
        view.mount(mock_request)

        mock_rust = Mock()
        view._rust_view = mock_rust

        view._changed_keys = {"count"}
        view._sync_state_to_rust()
        assert view._changed_keys is None

    def test_server_push_sends_changed_refs(self, mock_request):
        """Server push path (no _changed_keys) uses id() comparison.

        Note: dict values pass through _deep_serialize_dict() which creates a
        new dict, but Python may reuse the same memory address after GC — so
        id() comparison for dicts is non-deterministic. We only assert on
        reliably deterministic cases.
        """
        view = MultiAttrView()
        view.mount(mock_request)

        mock_rust = Mock()
        view._rust_view = mock_rust

        # First sync — sends everything
        view._sync_state_to_rust()

        # Simulate server push: no _changed_keys set, but b changed reference
        view.b = [4, 5, 6]  # new list object

        view._sync_state_to_rust()
        second_call = mock_rust.update_state.call_args[0][0]
        assert "b" in second_call
        # 'a' is a str (immutable) — same object, same id() → skipped
        assert "a" not in second_call
        # Note: 'c' (dict) is not asserted — _deep_serialize_dict() creates a
        # new dict each time, but Python's allocator may reuse the same address
        # after GC frees the previous one, making id() comparison flaky.

    def test_prev_context_refs_stored(self, mock_request):
        """_prev_context_refs is populated after sync."""
        view = CounterView()
        view.mount(mock_request)

        mock_rust = Mock()
        view._rust_view = mock_rust

        assert not hasattr(view, "_prev_context_refs") or not view._prev_context_refs

        view._sync_state_to_rust()
        assert hasattr(view, "_prev_context_refs")
        assert "count" in view._prev_context_refs


# ---------------------------------------------------------------------------
# ContextMixin: always includes all keys (no static_assigns filtering)
# ---------------------------------------------------------------------------


class TestContextAlwaysComplete:
    """After removing static_assigns, context always includes all keys."""

    def test_all_attrs_in_context(self, mock_request):
        """get_context_data includes all public attributes."""
        view = MultiAttrView()
        view.mount(mock_request)
        context = view.get_context_data()
        assert "a" in context
        assert "b" in context
        assert "c" in context

    def test_repeated_calls_always_include_all(self, mock_request):
        """Multiple get_context_data calls always return all keys."""
        view = MultiAttrView()
        view.mount(mock_request)

        ctx1 = view.get_context_data()
        ctx2 = view.get_context_data()

        for key in ("a", "b", "c"):
            assert key in ctx1
            assert key in ctx2
